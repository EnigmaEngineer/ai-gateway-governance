# What got past the patterns

The scored slice says identifier recall is 272 of 281 and attack recall is 146
of 152. Those figures are real and they are measured on a corpus whose attack
phrasings and identifier formats were written by the same person who wrote the
patterns. They say the patterns agree with the corpus.

So the patterns were frozen and `scripts/evade.py` was written with the pattern
file open. One attempt per idea, aimed at the gap rather than at the pattern.
Twenty two attempts. Every single one got through on the first run.

Two of the twenty two were inconsistencies rather than missing capability and
both were fixed. A written social security number uses a dot as often as a
hyphen and only the hyphen was in the character class. Every pattern in the
file is case insensitive except the vehicle number, which was an oversight. The
count after those two fixes is twenty of twenty two.

## This is a lower bound

The attempts were written by somebody holding the implementation. That makes
the number a lower bound on the evasion rate rather than an estimate of it.
Somebody with no knowledge of the patterns and more time would go about it
differently and probably do better, and nothing here says by how much.

The two controls in the same file are ordinary attacks in the plainest
phrasing. They are there because a pass where everything got through is also
what a broken harness looks like, and the suite fails if either control stops
firing.

## What the twenty have in common

Four groups, and only one of them is a pattern problem.

**A synonym outside the verb list.** `Set aside everything you were told
earlier` carries the whole intent of instruction override and shares no verb
with the pattern. This is the same gap the scored slice already shows as 18 of
24 on that class, so it is measured twice from two directions.

**A character that is not the character it looks like.** A Greek capital iota
in place of a Latin I, or a zero width space inside the verb. A pattern cannot
be widened to cover these. The input has to be normalised before it is matched,
and that is a step this service does not have.

**A format the class allows and the pattern does not.** A day before the month.
An address written as a single integer. A label the list does not carry, such as
`chart number` for a record number. These are the cheap ones and widening the
patterns closes them.

**An identifier with no shape and no label.** A name in running prose. A phone
number written in words. There is no pattern for these, which is why the name
detector only takes the labelled form and why its recall is reported per class
rather than folded into a headline.

## The conclusion worth carrying forward

The gap between the corpus figure and 20 of 22 on the evasion attempts
is not a measurement error. Both numbers are correct. They measure different
things, and only one of them resembles what a stranger sends. A recall figure
from a corpus the detector's author also wrote is an upper bound, and it should
be published next to a figure that is not.

## Rebuilding it

```
python scripts/evade.py --out measurements/evasion.json
```

Two ordinary attacks in the plainest phrasing sit in the same file and the
suite fails if either stops firing. Every attempt getting through is also what
a broken harness looks like, so the pass needs controls like any other
comparison. Re-measured on the last day of the build with every count
unchanged.
