"""Chunk boundaries placed where they do the most damage, and what got out.

Scoring a detector against a corpus and then attacking it on purpose answer
different questions, and the attack is the one worth having first. The streamed
redactor has a geometry problem rather than a phrasing problem, so the attack
here is the geometry. Every payload is cut at the worst offset available rather
than at an arbitrary one.

This is a LOWER bound on the leak rate and not an estimate of it. The cuts were
chosen with gateway/stream.py open and aimed at the gap. A real upstream picks
its boundaries by tokeniser and by network timing and nothing here models that.

Worst offset means the middle of an identifier the whole body redactor finds.
A cut there is the one case neither half can match on its own. Where a payload
carries a labelled class the probe also cuts at the start of the value, because
that one is invisible to the holdback and is answered by the lookbehind.

Those cuts alone are not enough and the second version of this file found out
why. Cutting only at identifier boundaries leaves the rest of the body in large
pieces, so the emit point jumps past the whole identifier in one step and never
lands inside it. A real completion arrives a token at a time. The probe now
streams at `TOKEN_CHARS` and adds the aimed cuts on top of that.

The first version of this file reported eight of eight held and it had graded
nothing. Every payload was shorter than the holdback, so the redactor emitted
nothing until close and then ran over the complete body. Each payload now
carries a tail long enough that the identifier is emitted while the body is
still arriving, and `graded_as_a_stream` fails the pass if that stops being
true.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gateway import phi  # noqa: E402
from gateway.stream import leaked_spans, redact_stream  # noqa: E402

# What the service would ship. Frozen before the attempts were written.
SHIPPED_HOLDBACK = 64
SHIPPED_LOOKBEHIND = 128

# A completion keeps going after the identifier. Without this the emit point
# never reaches the identifier and the probe grades the close rather than the
# stream.
TAIL = (" The remainder of the summary covers the admission course and the "
        "discharge plan, neither of which carries anything the policy module "
        "enumerates, and it runs on for long enough that the emit point passes "
        "the identifier well before the body is finished arriving.")

# About one token of English. The point of the probe is the geometry a real
# upstream produces and a real upstream does not hand over a paragraph.
TOKEN_CHARS = 4

ATTEMPTS = (
    ("long_url", "a url longer than the holdback, so its head emits before the tail arrives",
     "Imaging is at https://records.hospital-network.example.org/patients/884213/imaging/2026/march/series-four and nowhere else." + TAIL),
    ("long_email", "an address longer than the holdback with no matchable prefix",
     "Forward to jane.marie.alvarez.scheduling@appointments.northside.clinic.example.org before noon." + TAIL),
    ("label_then_value", "the cut falls between the label and the digits it introduces",
     "Chart shows MRN 884213 for the transport." + TAIL),
    ("ssn_midpoint", "the cut falls inside the group separator of a social security number",
     "Social 123-45-6789 on the intake sheet." + TAIL),
    ("name_after_title", "the cut falls between the title and the surname",
     "Reviewed by Dr. Jane Alvarez this morning." + TAIL),
    ("two_in_one_chunk", "two identifiers sit together and the second one straddles the edge",
     "Member id HP44821903 and policy number BX77301442 both expired." + TAIL),
    ("address_run", "a street address is long and has no prefix that matches on its own",
     "Discharge to 4417 North Waverly Crossing Boulevard for follow up." + TAIL),
    ("ipv6_colons", "a compressed address cut at the double colon looks like two timestamps",
     "Edge session from 2001:db8::8a2e:370:7334 at the London node." + TAIL),
)

CONTROL_TEXT = "Social 123-45-6789 on the intake sheet." + TAIL


def worst_cuts(text):
    """Token sized boundaries plus one aimed at every identifier in the body."""
    cuts = set(range(TOKEN_CHARS, len(text), TOKEN_CHARS))
    for s in phi.detect(text):
        if s.end - s.start >= 2:
            cuts.add(s.start + (s.end - s.start) // 2)
        if s.start > 0:
            cuts.add(s.start)
    return sorted(c for c in cuts if 0 < c < len(text))


def chunks_at(text, cuts):
    parts, prev = [], 0
    for c in cuts:
        parts.append(text[prev:c])
        prev = c
    parts.append(text[prev:])
    return [p for p in parts if p]


def probe(text, holdback, lookbehind):
    cuts = worst_cuts(text)
    out, r = redact_stream(chunks_at(text, cuts), holdback=holdback, lookbehind=lookbehind)
    leaked = leaked_spans(text, r.clear_ranges)
    spans = phi.detect(text)
    last_end = max((s.end for s in spans), default=0)
    return {
        "cut_count": len(cuts),
        "leaked_classes": sorted({s.category for s in leaked}),
        "leaked_span_count": len(leaked),
        "matches_whole_body_redactor": out == phi.redact(text),
        "tag_count": r.redaction_count,
        "whole_body_tag_count": len(spans),
        # False means every identifier was still in the buffer at close, so
        # this row graded the whole body redactor and not the stream.
        "graded_as_a_stream": r.emitted_before_close >= last_end > 0,
    }


def controls():
    """Caught at the shipped setting and leaking with no buffer at all."""
    held = probe(CONTROL_TEXT, SHIPPED_HOLDBACK, SHIPPED_LOOKBEHIND)
    bare = probe(CONTROL_TEXT, 0, 0)
    return {
        "shipped_setting_catches_the_control": held["leaked_span_count"] == 0,
        "no_buffer_at_all_leaks_the_control": bare["leaked_span_count"] > 0,
        "the_control_was_graded_as_a_stream": held["graded_as_a_stream"],
    }


def run():
    rows = []
    for name, exploits, text in ATTEMPTS:
        result = probe(text, SHIPPED_HOLDBACK, SHIPPED_LOOKBEHIND)
        result.update({"name": name, "exploits": exploits})
        result["got_through"] = result["leaked_span_count"] > 0
        rows.append(result)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rows = run()
    if not rows:
        raise SystemExit("no attempts were graded, which is not a passing result")

    ungraded = [r["name"] for r in rows if not r["graded_as_a_stream"]]
    if ungraded:
        raise SystemExit("these rows never reached the emit point, so they graded the "
                         "whole body redactor: %s" % ", ".join(ungraded))

    checks = controls()
    if not all(checks.values()):
        raise SystemExit("a control did not fire, so this pass graded nothing: %r" % checks)

    through = sum(1 for r in rows if r["got_through"])
    reshaped = sum(1 for r in rows if not r["matches_whole_body_redactor"])

    manifest = {
        "note": ("Cuts chosen with the implementation open, so this is a lower bound "
                 "on the leak rate and not an estimate of it."),
        "holdback_chars": SHIPPED_HOLDBACK,
        "lookbehind_chars": SHIPPED_LOOKBEHIND,
        "controls": checks,
        "attempts": len(rows),
        "got_through": through,
        "diverged_from_whole_body_redactor": reshaped,
        "detail": rows,
    }
    Path(args.out).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("%d of %d leaked at holdback %d, %d diverged from the whole body redactor"
          % (through, len(rows), SHIPPED_HOLDBACK, reshaped))
    for r in rows:
        if r["got_through"]:
            mark = "leaked"
        elif not r["matches_whole_body_redactor"]:
            mark = "reshaped"
        else:
            mark = "held"
        print("  %-8s %-18s %s" % (mark, r["name"], r["exploits"]))


if __name__ == "__main__":
    main()
