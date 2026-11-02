"""Scores the guardrail against a labelled corpus and writes a counts manifest.

Two scoring rules, written down because both of them are choices.

PHI is scored per span. A predicted span is a true positive when it overlaps a
labelled span of the same class. Overlap rather than exact bounds, because a
detector that redacts nine of the ten digits of a phone number has still found
the phone number, and grading it as a miss would report a bounds bug as a
recall problem. The cost of that choice is that a span one character wide
inside a real identifier scores as a hit, so the manifest also carries the
mean covered fraction and the suite asserts a floor on it.

Injection is scored per record. The label is whether an attack was planted at
all. Per class recall is reported separately so a weak class cannot hide.

The manifest carries counts and corpus hashes. It carries no timings, because a
count reproduces on another machine and a millisecond does not.
"""

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gateway import injection, phi, policy  # noqa: E402

BASELINE_HELP = (
    "Grade a deliberately crude detector instead of the real one. This is the "
    "number that existed before any pattern was written and it is the only "
    "figure in the repository that nothing was fitted to."
)


def load(path):
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise SystemExit("%s held no rows, so this run graded nothing" % path)
    return rows


def corpus_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _baseline_phi(text):
    """Any run of five or more digits, class unknown.

    This is what a first pass looks like before anybody reads the regulation.
    It is scored against every class at once, so its class level precision is
    the thing the real detectors have to beat.
    """
    import re
    return [phi.Span(m.start(), m.end(), "unknown", m.group(0))
            for m in re.finditer(r"\d{5,}", text)]


def _baseline_injection(text):
    """Three words that appear in most of these attacks and in much else."""
    low = text.lower()
    return any(w in low for w in ("ignore", "system prompt", "you are now"))


def score_phi(rows, baseline=False):
    """Per class counts, with recall over labels and precision over predictions.

    A labelled identifier can legitimately be covered by more than one span. A
    composite address is one label and the detectors return a street span and a
    ZIP span, and both of them are right. Counting that as one hit and one false
    positive would report a correct redaction as an error, so the two rates get
    their own denominators. `found` and `missed` are over labels. `correct` and
    `spurious` are over predictions.
    """
    counts = defaultdict(lambda: {"found": 0, "missed": 0, "correct": 0, "spurious": 0})
    covered_num = 0
    covered_den = 0
    for r in rows:
        predicted = _baseline_phi(r["text"]) if baseline else phi.detect(r["text"])
        labelled = r["phi_spans"]

        for p in predicted:
            matched = None
            for lab in labelled:
                same = True if baseline else (p.category == lab["category"])
                if same and p.start < lab["end"] and lab["start"] < p.end:
                    matched = lab
                    break
            if matched is None:
                counts[p.category]["spurious"] += 1
            else:
                counts[matched["category"]]["correct"] += 1

        for lab in labelled:
            covering = [
                p for p in predicted
                if p.start < lab["end"] and lab["start"] < p.end
                and (baseline or p.category == lab["category"])
            ]
            if covering:
                counts[lab["category"]]["found"] += 1
                reach = max(min(p.end, lab["end"]) for p in covering) - \
                    min(max(p.start, lab["start"]) for p in covering)
                covered_num += max(reach, 0)
            else:
                counts[lab["category"]]["missed"] += 1
            covered_den += lab["end"] - lab["start"]
    return counts, covered_num, covered_den


def score_injection(rows, baseline=False):
    tp = fp = fn = tn = 0
    per_class = defaultdict(lambda: {"planted": 0, "caught": 0})
    quoted_fp = 0
    for r in rows:
        planted = bool(r["attack_classes"])
        if baseline:
            flagged = _baseline_injection(r["text"])
            quoted = False
        else:
            verdict = injection.assess(r["text"])
            flagged = verdict["flagged"]
            quoted = verdict["reads_as_quotation"]
        for cls in r["attack_classes"]:
            per_class[cls]["planted"] += 1
            if flagged:
                per_class[cls]["caught"] += 1
        if planted and flagged:
            tp += 1
        elif planted:
            fn += 1
        elif flagged:
            fp += 1
            if quoted:
                quoted_fp += 1
        else:
            tn += 1
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "false_positives_reading_as_quotation": quoted_fp}, per_class


def ratio(num, den):
    """Counts in, counts out. The caller formats, so nothing rounds twice."""
    return {"numerator": num, "denominator": den}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", required=True,
                    help="what this slice is, for example holdout or development")
    ap.add_argument("--baseline", action="store_true", help=BASELINE_HELP)
    args = ap.parse_args()

    policy.validate()
    rows = load(args.corpus)
    pcounts, cov_num, cov_den = score_phi(rows, args.baseline)
    icounts, per_class = score_injection(rows, args.baseline)

    found = sum(c["found"] for c in pcounts.values())
    missed = sum(c["missed"] for c in pcounts.values())
    correct = sum(c["correct"] for c in pcounts.values())
    spurious = sum(c["spurious"] for c in pcounts.values())

    manifest = {
        "detector": "baseline" if args.baseline else "patterns",
        "slice": args.label,
        "corpus_path": args.corpus,
        "corpus_sha256": corpus_digest(args.corpus),
        "rows": len(rows),
        "phi": {
            "per_class": {k: dict(v) for k, v in sorted(pcounts.items())},
            "totals": {"found": found, "missed": missed,
                       "correct": correct, "spurious": spurious},
            "precision": ratio(correct, correct + spurious),
            "recall": ratio(found, found + missed),
            "covered_characters": ratio(cov_num, cov_den),
        },
        "injection": {
            "record_level": icounts,
            "per_class_recall": {k: dict(v) for k, v in sorted(per_class.items())},
            "precision": ratio(icounts["tp"], icounts["tp"] + icounts["fp"]),
            "recall": ratio(icounts["tp"], icounts["tp"] + icounts["fn"]),
        },
        "policy": policy.validate(),
    }
    Path(args.out).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("%s / %s  phi found=%d missed=%d correct=%d spurious=%d  "
          "injection tp=%d fp=%d fn=%d"
          % (manifest["detector"], args.label, found, missed, correct, spurious,
             icounts["tp"], icounts["fp"], icounts["fn"]))


if __name__ == "__main__":
    main()
