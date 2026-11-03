"""What streaming loses against redacting the whole body, per buffer size.

The question here is narrow on purpose. Whether the detectors find an
identifier at all is graded against the labelled corpus in scripts/score.py.
This grades only what the chunking costs on top of that, so the oracle is the
whole body redactor over the same text rather than the labels. A span
counts as leaked when any character of it reached the reader unredacted, and a
body counts as diverged when the streamed output is not character for character
what the whole body redactor produced. The second one is weaker and it still
matters, because one identifier emerging as three tags makes a downstream count
of redactions wrong.

Bodies come from corpus/bodies.py. They are holdout rows joined to completion
length with a tail after the last identifier, because a body whose identifiers
all sit inside the final holdback emits nothing until close and then grades the
whole body redactor against itself.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from corpus import bodies as corpus_bodies  # noqa: E402
from gateway import phi  # noqa: E402
from gateway.stream import leaked_spans, redact_stream  # noqa: E402

TOKEN_CHARS = 4

HOLDBACKS = (0, 2, 4, 8, 16, 32, 48, 64, 96, 128)
LOOKBEHINDS = (0, 4, 8, 16, 32, 64, 128)
FIXED_LOOKBEHIND = 128
FIXED_HOLDBACK = 64


def chunks(text):
    return [text[i:i + TOKEN_CHARS] for i in range(0, len(text), TOKEN_CHARS)]


def arm(bodies, holdback, lookbehind):
    oracle_spans = leaked = diverged = ungraded = 0
    for body in bodies:
        spans = phi.detect(body)
        oracle_spans += len(spans)
        out, r = redact_stream(chunks(body), holdback=holdback, lookbehind=lookbehind)
        leaked += len(leaked_spans(body, r.clear_ranges))
        if out != phi.redact(body):
            diverged += 1
        last_end = max((s.end for s in spans), default=0)
        if last_end and r.emitted_before_close < last_end:
            ungraded += 1
    return {
        "holdback_chars": holdback,
        "lookbehind_chars": lookbehind,
        "oracle_spans": oracle_spans,
        "leaked_spans": leaked,
        "bodies_diverged": diverged,
        "bodies_not_graded_as_a_stream": ungraded,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", default="corpus/data/labelled-holdout.jsonl")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    bodies, unbuilt = corpus_bodies.build(args.corpus)
    # An empty comparison is not a passing comparison. Assert the size of the
    # set before reading any verdict off it.
    if not bodies:
        raise SystemExit("no bodies were built, so every arm graded nothing")
    total_spans = sum(len(phi.detect(b)) for b in bodies)
    if total_spans == 0:
        raise SystemExit("the bodies carry no identifiers, so a leak count of zero means nothing")

    by_holdback = [arm(bodies, h, FIXED_LOOKBEHIND) for h in HOLDBACKS]
    by_lookbehind = [arm(bodies, FIXED_HOLDBACK, lb) for lb in LOOKBEHINDS]

    ungraded = sum(a["bodies_not_graded_as_a_stream"] for a in by_holdback + by_lookbehind)

    manifest = {
        "note": ("Bodies are holdout rows joined to completion length. The rows are "
                 "requests and a request is not a response, so the join is a stand in "
                 "for response shape and the figures are about chunking rather than "
                 "about detection quality."),
        "corpus_path": args.corpus,
        "corpus_sha256": hashlib.sha256(Path(args.corpus).read_bytes()).hexdigest(),
        "token_chars": TOKEN_CHARS,
        "body_target_chars": corpus_bodies.TARGET_CHARS,
        "rows_left_over_after_the_last_body": unbuilt,
        "bodies": len(bodies),
        "oracle_spans_total": total_spans,
        "bodies_not_graded_as_a_stream": ungraded,
        "by_holdback": by_holdback,
        "by_lookbehind": by_lookbehind,
    }
    Path(args.out).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print("%d bodies, %d identifiers found by the whole body redactor" % (len(bodies), total_spans))
    print("holdback  lookbehind  leaked  diverged")
    for a in by_holdback:
        print("  %6d  %10d  %6d  %8d" % (a["holdback_chars"], a["lookbehind_chars"],
                                         a["leaked_spans"], a["bodies_diverged"]))
    print("holdback  lookbehind  leaked  diverged")
    for a in by_lookbehind:
        print("  %6d  %10d  %6d  %8d" % (a["holdback_chars"], a["lookbehind_chars"],
                                         a["leaked_spans"], a["bodies_diverged"]))


if __name__ == "__main__":
    main()
