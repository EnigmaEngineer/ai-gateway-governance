# How the guardrail is scored

Two scoring rules and one procedure. All three are choices and all three change
the number, so they are written down rather than left in the scorer.

## The slices

`corpus/generate.py` takes a seed and emits a deterministic corpus. Two slices
exist. Seed 11 is the development slice and seed 23 is the scored slice. Every
pattern in `gateway/phi.py` and `gateway/injection.py` was written from the
class definition and from the shape of the identifier, before either slice was
read. Three defects were found afterwards by reading the development slice and
all three were fixed. None of them was fixed by reading the scored slice.

The three were a label alternation that captured the word `number` as the
identifier it was supposed to introduce, a composite address being graded as
one hit and one false positive, and a URL inside an attack payload that the
label set had not recorded.

The scored slice is therefore held out with respect to the patterns. It is not
held out with respect to the generator. Both slices come from the same templates
and the same value formats, so the figure below says the patterns generalise
across draws. It does not say they generalise across writing styles. The number
that speaks to that is in `evasion.md` and it is much worse.

## Identifier spans, two denominators

A predicted span counts as correct when it overlaps a labelled span of the same
class. Overlap rather than exact bounds, because a detector that redacts nine
of the ten digits of a phone number has found the phone number, and grading it
as a miss would report a bounds bug as a recall problem.

A labelled identifier can be covered by more than one span and both can be
right. A composite address is one label, and the detectors return a street span
and a ZIP span. Counting that one to one made the second span a false positive,
which reported a correct redaction as an error. So the two rates have separate
denominators.

- Recall counts labels with at least one correct span over all labels.
- Precision counts correct predictions over all predictions.

The manifest also carries the fraction of labelled characters actually covered,
because the overlap rule would otherwise let a one character span inside a real
identifier score as a hit.

## Attacks, one verdict per record

Injection is scored per record. The label is whether an attack was planted at
all, and per class recall is reported separately so a weak class cannot hide
inside a headline.

A payload that fires and also reads as a description of an attack is counted as
a false positive. It is not cleared. A filter an attacker can switch off by
writing the words `for instance` is worth nothing, so the service blocks and
attaches the hedge for a reviewer. Every false positive on the scored slice is
one of these.

## The floor

`scripts/score.py --baseline` grades a deliberately crude detector. Any run of
five or more digits is an identifier of unknown class, and three keywords stand
in for the attack patterns. This is what existed before the first pattern was
written and it is the only figure in the repository that nothing was fitted to.

## What a manifest may carry

Counts and hashes. Not timings. A count reproduces on another machine and a
millisecond does not, and `tests/test_measurements.py` fails on a key whose name
suggests one. Each scored manifest records the hash of the corpus it read, and a
check rehashes the file so a figure cannot outlive the data behind it.
