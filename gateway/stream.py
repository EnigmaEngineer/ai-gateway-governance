"""Redaction of a response that arrives in pieces.

A completion comes back a few tokens at a time and the obvious thing to do is
redact each chunk as it lands. That leaks. An identifier split across a chunk
boundary is not present in either half, so neither half matches and both go out
in the clear. The fix is to hold back the tail of what has arrived until enough
has followed it to decide.

Three numbers control that and the plan for this work assumed one.

`holdback` is how much of the arrived text is withheld from the reader. It is
the right edge. An identifier longer than the holdback can still straddle the
emit point, so the holdback is a bound on the longest identifier this catches
across a boundary rather than a guarantee.

`lookbehind` is how much already emitted text is kept so a pattern can still
see it. It is the left edge and it exists because most of the classes here are
labelled. `MRN` arrives in one chunk and `884213` in the next, and a detector
run on the second chunk alone sees six digits and nothing that says what they
are. Widening the holdback does not help with that at all.

The cost of the lookbehind is that the window holds unredacted text after the
reader has already been given it. That is a real exposure and it is bounded by
the lookbehind rather than by the length of the response.

`max_window` is the third and it exists because the first two bound nothing on
their own. The redactor refuses to emit through a span, so a span that keeps
extending keeps the buffer from draining and every chunk re-scans everything
held. That is quadratic in the length of the span and one long URL produces it,
on an endpoint that accepts a megabyte. Past the window the redactor stops
waiting, redacts what it is holding under the class that was extending, and
then suppresses everything up to the next whitespace. Both halves are needed.

Whitespace is the right boundary only while the class that forced carries none
of it. Today the only class with no length cap is the URL and a URL has no
spaces in it, so the rule holds. That is a property of the current pattern set
rather than of the design, so `forced_on_a_span_carrying_whitespace` counts the
case and the suite fails on it rather than leaving it to be noticed later.
Redacting the held part alone leaves the rest of the identifier arriving with
no prefix for any pattern to match, which was the first version of this and it
leaked the tail of every URL it was supposed to bound.

Over redacting is the direction to fail in. A reader loses the end of a long
link. Nothing that was supposed to be withheld is sent.
"""

import re

from . import phi

_BOUNDARY = re.compile(r"\s")


class StreamRedactor:
    """Feed chunks in, get redacted text out, call close to flush the tail.

    `clear_ranges` accumulates the ranges of the original response that were
    emitted without redaction, in original coordinates. A gateway that forwards
    anything in the clear should be able to say afterwards what it forwarded,
    and the sweep uses the same field to count what leaked.
    """

    def __init__(self, holdback=64, lookbehind=128, max_window=1024):
        if holdback < 0:
            raise ValueError("holdback is a character count and cannot be negative")
        if lookbehind < 0:
            raise ValueError("lookbehind is a character count and cannot be negative")
        if max_window <= holdback:
            raise ValueError("the window has to be wider than the holdback or nothing drains")
        self.holdback = holdback
        self.lookbehind = lookbehind
        self.max_window = max_window
        self.clear_ranges = []
        self.redaction_count = 0
        # Spans found to have opened in text the reader already has. This can
        # only fire while there is a lookbehind. With none the redactor cannot
        # tell a late match from a new one, so a count of nought there means
        # nothing was observed rather than nothing leaked.
        self.head_leaks = 0
        self.forced_redactions = 0
        self.forced_on_a_span_carrying_whitespace = 0
        # How much reached the reader while the body was still arriving. A
        # payload shorter than the holdback emits nothing until close, and then
        # the whole body redactor runs over the whole body. That case is not a
        # stream and any figure taken from it is a figure about something else.
        self.emitted_before_close = 0
        self._pending = ""
        self._origin = 0
        self._context = ""
        self._suppressing = False
        self._closed = False

    def feed(self, chunk):
        if self._closed:
            raise RuntimeError("feed after close, which is a caller bug")
        if not isinstance(chunk, str):
            raise TypeError("the redactor reads text chunks, got %r" % type(chunk).__name__)
        self._pending += chunk
        return self._drain(len(self._pending) - self.holdback)

    def close(self):
        """Flush everything left. Nothing is held back once the body is done."""
        self.emitted_before_close = self._origin
        out = self._drain(len(self._pending))
        self._closed = True
        return out

    def _drain(self, limit):
        prefix = self._suppress() if self._suppressing else ""
        limit = max(0, min(limit, len(self._pending)))
        if self._suppressing:
            return prefix

        window = self._context + self._pending
        base = len(self._context)

        # Spans are found over the window and then moved into pending
        # coordinates. A negative start means the span opened in text the
        # reader already has.
        spans = []
        for s in phi.detect(window):
            start, end = s.start - base, s.end - base
            if end > 0:
                spans.append((start, end, s.category))
        spans.sort()

        # Never emit through a span. Pulling the emit point back to the start of
        # one is what buys the next chunk a chance to complete it.
        blocking = None
        for start, end, category in spans:
            if start < limit < end:
                limit = max(0, start)
                blocking = (start, end, category)

        forced = blocking is not None and len(self._pending) > self.max_window
        if forced:
            limit = len(self._pending) - self.holdback
            spans = [(a, b, c) for a, b, c in spans if b <= limit]
            spans.append((max(blocking[0], 0), limit, blocking[2]))
            spans.sort()
            self.forced_redactions += 1
            if _BOUNDARY.search(self._pending[max(blocking[0], 0):limit]):
                self.forced_on_a_span_carrying_whitespace += 1

        emit = [(a, b, c) for a, b, c in spans if b <= limit]
        out = [prefix]
        cursor = 0
        for start, end, category in emit:
            if start < 0:
                # The head of this one went out before it was matchable. What
                # is left gets redacted so the rest does not follow it.
                self.head_leaks += 1
                start = 0
            if start < cursor:
                continue
            if start > cursor:
                out.append(self._pending[cursor:start])
                self._note_clear(cursor, start)
            out.append("[" + category.upper() + "]")
            self.redaction_count += 1
            cursor = end
        if cursor < limit:
            out.append(self._pending[cursor:limit])
            self._note_clear(cursor, limit)

        self._advance(limit)
        if forced:
            # The tag covers what was held. The rest of that identifier is
            # still arriving and it has lost the prefix every pattern needs,
            # so it is dropped up to the next whitespace rather than emitted.
            self._suppressing = True
            out.append(self._suppress())
        return "".join(out)

    def _suppress(self):
        """Drop the tail of a forced span. Returns anything past its end."""
        match = _BOUNDARY.search(self._pending)
        if match is None:
            self._advance(len(self._pending))
            return ""
        self._advance(match.start())
        self._suppressing = False
        return ""

    def _advance(self, count):
        emitted_source = self._pending[:count]
        if self.lookbehind:
            self._context = (self._context + emitted_source)[-self.lookbehind:]
        else:
            self._context = ""
        self._pending = self._pending[count:]
        self._origin += count

    def _note_clear(self, start, end):
        if end <= start:
            return
        a, b = self._origin + start, self._origin + end
        if self.clear_ranges and self.clear_ranges[-1][1] == a:
            self.clear_ranges[-1] = (self.clear_ranges[-1][0], b)
        else:
            self.clear_ranges.append((a, b))


def redact_stream(chunks, holdback=64, lookbehind=128, max_window=1024):
    """Convenience for a whole iterable. Returns the text and the redactor."""
    r = StreamRedactor(holdback=holdback, lookbehind=lookbehind, max_window=max_window)
    parts = [r.feed(c) for c in chunks]
    parts.append(r.close())
    return "".join(parts), r


def leaked_spans(text, clear_ranges):
    """Which identifiers in `text` had any character emitted in the clear.

    The oracle is the non streaming redactor over the whole body. A span counts
    as leaked when any part of it reached the reader unredacted, because half a
    social security number next to a tag is still a disclosure.
    """
    leaked = []
    for s in phi.detect(text):
        for a, b in clear_ranges:
            if s.start < b and a < s.end:
                leaked.append(s)
                break
    return leaked
