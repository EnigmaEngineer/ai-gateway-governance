import json

import pytest

from gateway import phi
from gateway.stream import StreamRedactor, leaked_spans, redact_stream

BODY = (
    "Patient: Dr. Jane Alvarez, MRN 884213, social 123-45-6789, reachable on "
    "415-555-0132 or jane.alvarez@example.org. Discharge to 4417 North Waverly "
    "Crossing Boulevard. The rest of this summary carries nothing the policy "
    "module enumerates and it runs on for long enough that the emit point "
    "passes every identifier well before the body has finished arriving."
)


def tokens(text, size=4):
    return [text[i:i + size] for i in range(0, len(text), size)]


def test_the_body_this_module_grades_actually_carries_identifiers():
    """An empty comparison is not a passing comparison."""
    assert len(phi.detect(BODY)) >= 6


def test_a_generous_buffer_reproduces_the_whole_body_redactor():
    out, _ = redact_stream(tokens(BODY), holdback=64, lookbehind=128)
    assert out == phi.redact(BODY)


def test_the_body_is_long_enough_to_be_graded_as_a_stream():
    """Shorter than the holdback and the test grades the close, not the stream."""
    _, r = redact_stream(tokens(BODY), holdback=64, lookbehind=128)
    last_end = max(s.end for s in phi.detect(BODY))
    assert r.emitted_before_close >= last_end


def test_holding_nothing_back_leaks():
    """If this ever passes clean the harness cannot see a leak at all."""
    _, r = redact_stream(tokens(BODY), holdback=0, lookbehind=0)
    assert leaked_spans(BODY, r.clear_ranges)


def test_the_lookbehind_catches_what_the_holdback_cannot():
    """The finding of 2026-11-03, asserted rather than written down."""
    _, with_context = redact_stream(tokens(BODY), holdback=64, lookbehind=128)
    _, without = redact_stream(tokens(BODY), holdback=64, lookbehind=0)
    assert not leaked_spans(BODY, with_context.clear_ranges)
    assert leaked_spans(BODY, without.clear_ranges), (
        "a generous holdback with no lookbehind used to leak the labelled classes, "
        "so either the detectors changed or this check stopped testing anything")


def test_a_labelled_class_is_the_one_that_needs_the_lookbehind():
    text = "The chart shows MRN 884213 and " + "filler text that goes on. " * 12
    _, without = redact_stream(tokens(text), holdback=64, lookbehind=0)
    leaked = {s.category for s in leaked_spans(text, without.clear_ranges)}
    assert "medical_record_numbers" in leaked


def test_one_chunk_carrying_the_whole_body_is_the_batch_case():
    out, _ = redact_stream([BODY], holdback=64, lookbehind=128)
    assert out == phi.redact(BODY)


def test_chunk_size_does_not_change_the_answer_at_the_shipped_setting():
    reference = phi.redact(BODY)
    for size in (1, 2, 3, 7, 16, 64):
        out, _ = redact_stream(tokens(BODY, size), holdback=64, lookbehind=128)
        assert out == reference, "chunk size %d changed the output" % size


def test_an_empty_stream_closes_without_complaint():
    r = StreamRedactor()
    assert r.feed("") == ""
    assert r.close() == ""


def test_clear_ranges_never_cover_a_redacted_identifier_at_the_shipped_setting():
    _, r = redact_stream(tokens(BODY), holdback=64, lookbehind=128)
    covered = sum(b - a for a, b in r.clear_ranges)
    redacted = sum(s.end - s.start for s in phi.detect(BODY))
    assert covered + redacted == len(BODY)


def test_feed_after_close_is_a_caller_bug():
    r = StreamRedactor()
    r.feed("something")
    r.close()
    with pytest.raises(RuntimeError):
        r.feed("more")


def test_a_negative_buffer_is_refused():
    with pytest.raises(ValueError):
        StreamRedactor(holdback=-1)
    with pytest.raises(ValueError):
        StreamRedactor(lookbehind=-1)


def test_bytes_are_refused_rather_than_decoded():
    r = StreamRedactor()
    with pytest.raises(TypeError):
        r.feed(b"SSN 123-45-6789")


def test_the_service_default_is_at_or_past_the_knee_the_sweep_found(manifests):
    """The shipped setting has to sit where the measurement says it should."""
    from gateway import app
    arms = manifests["stream-buffers"]["by_holdback"]
    clean = [a["holdback_chars"] for a in arms if a["leaked_spans"] == 0]
    assert clean, "no holdback in the sweep reached zero, so there is no knee to ship at"
    assert app.STREAM_HOLDBACK >= min(clean)
    lb = [a["lookbehind_chars"] for a in manifests["stream-buffers"]["by_lookbehind"]
          if a["leaked_spans"] == 0]
    assert lb and app.STREAM_LOOKBEHIND >= min(lb)


def test_the_streamed_path_says_it_did_not_assess_injection():
    """A reader should not have to infer the difference from the path name."""
    from gateway import app
    source = (__import__("pathlib").Path(app.__file__)).read_text(encoding="utf-8")
    assert "x-guardrail-injection-assessed" in source
    assert "response direction" in source


def _longest_identifier(root):
    from corpus import bodies
    longest = 0
    for name in ("labelled-holdout.jsonl", "labelled-dev.jsonl"):
        for text in bodies.rows(root / "corpus" / "data" / name):
            for s in phi.detect(text):
                longest = max(longest, s.end - s.start)
    return longest


def test_the_window_is_derived_from_the_corpus_and_not_typed(root):
    """A number in a comment cannot fail. This can."""
    from gateway import app
    longest = _longest_identifier(root)
    assert longest > 0, "no identifiers in either slice, so this graded nothing"
    assert app.STREAM_MAX_WINDOW >= longest * 8, (
        "the window is %d and the longest identifier either slice carries is %d, "
        "so an ordinary one would be force redacted" % (app.STREAM_MAX_WINDOW, longest))


def test_a_span_that_keeps_extending_is_bounded_rather_than_held():
    """One long link used to make the scan quadratic in its own length."""
    body = "See https://example.org/" + "a" * 8000 + " for details."
    out, r = redact_stream(tokens(body), holdback=64, lookbehind=128, max_window=1024)
    assert r.forced_redactions == 1
    assert "aaaa" not in out, "the tail of the forced span reached the reader"
    assert out == "See [WEB_URLS] for details."


def test_forcing_does_not_fire_on_an_ordinary_body():
    _, r = redact_stream(tokens(BODY), holdback=64, lookbehind=128)
    assert r.forced_redactions == 0


def test_a_window_no_wider_than_the_holdback_is_refused():
    with pytest.raises(ValueError):
        StreamRedactor(holdback=64, max_window=64)


def test_the_head_leak_counter_fires_when_a_head_leaks():
    """A counter nothing reads is a counter nobody can trust.

    It needs a lookbehind to fire at all. A span is only known to have leaked
    its head when the window still holds the text the head went out in, so with
    no left context the redactor cannot tell a late match from a new one and
    the count stays at nought while identifiers are still going out.
    """
    _, bare = redact_stream(tokens(BODY), holdback=0, lookbehind=128)
    _, blind = redact_stream(tokens(BODY), holdback=0, lookbehind=0)
    _, held = redact_stream(tokens(BODY), holdback=64, lookbehind=128)
    assert bare.head_leaks > 0
    assert blind.head_leaks == 0 and leaked_spans(BODY, blind.clear_ranges)
    assert held.head_leaks == 0


def test_nothing_in_the_corpus_forces_on_a_span_carrying_whitespace(root):
    """The suppress rule cuts at whitespace and that is only safe while the
    class with no length cap has none in it."""
    from corpus import bodies
    built, _ = bodies.build(root / "corpus" / "data" / "labelled-holdout.jsonl")
    assert built, "no bodies, so this graded nothing"
    for body in built:
        _, r = redact_stream(tokens(body), holdback=64, lookbehind=128, max_window=256)
        assert r.forced_on_a_span_carrying_whitespace == 0
