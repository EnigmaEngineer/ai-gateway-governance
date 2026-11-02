# ai-gateway-governance

A governed front door for LLM traffic. It redacts the identifier classes the
HIPAA Safe Harbor rule enumerates, detects prompt injection by attack class,
and refuses to start when the set of classes it enforces and the set it
deliberately ignores do not add up to the regulation's list.

```
pip install -r requirements-dev.txt
python -m gateway.app --port 8080
curl -s localhost:8080/v1/guard -d '{"text":"Patient: Rosa Delacroix, MRN 884213"}'
```

## How a request moves through it

```
          POST /v1/guard
                |
                v
     +----------------------+
     |  injection.assess    |   runs on the ORIGINAL text, before redaction,
     |  7 attack classes    |   because redacting first replaces the attacker's
     +----------------------+   identifiers with tags and destroys the evidence
                |
        flagged | clean
                |
      +---------+----------+
      |                    |
      v                    v
   BLOCK              phi.detect + redact
   nothing            15 enforced classes
   forwarded          3 declared omissions
                           |
                           v
                   forwarded upstream
```

`gateway/policy.py` holds the enumeration. It calls `validate()` at import
time, so a deployment whose two sets do not partition the eighteen classes
fails to start rather than passing a whole category silently.

## What it measures

Every figure below is a pair of counts from a run on this machine. The commands
that produced them are under the table and the manifests are in
`measurements/`. `tests/test_readme.py` re-derives each one and fails with the
correct value in the message.

| | identifier recall | identifier precision | attack recall | attack precision |
|---|---|---|---|---|
| held out slice, 400 rows | 272 / 281 | 286 / 286 | 146 / 152 | 146 / 151 |
| crude floor, same rows | 99 / 281 | 99 / 114 | 27 / 152 | 27 / 40 |

```
python corpus/generate.py --seed 23 --rows 400 --out corpus/data/labelled-holdout.jsonl
python scripts/score.py --corpus corpus/data/labelled-holdout.jsonl --label holdout --out measurements/holdout-patterns.json
python scripts/score.py --corpus corpus/data/labelled-holdout.jsonl --label holdout --baseline --out measurements/holdout-baseline.json
python scripts/score.py --corpus corpus/data/labelled-dev.jsonl --label development --out measurements/development-patterns.json
```

The precision column is the weakest figure here and a perfect score should be
read as one. It says no prediction landed outside a labelled identifier on
these 400 rows, and the rows carrying no identifiers are 12 phrases written to
be plausible rather than adversarial. One was missing until a timestamp
turned out to redact as an address, which is covered below.

The floor row is a deliberately crude detector. Any run of five or more digits
is an identifier and three keywords stand in for the attack patterns. It is the
only figure here that nothing was fitted to.

All five false positives on the attack side are payloads that describe an attack
instead of attempting one. The service blocks them and attaches a note saying
so, rather than clearing them, because a filter an attacker switches off by
writing `for instance` is worth nothing.

Both slices come from the same generator. The patterns were written from the
class definitions before either slice was read, and the three defects found
afterwards were found by reading the development slice at seed 11 and never the
scored one at seed 23. So the table says the patterns generalise across draws
of one generator. It does not say they generalise across writing styles.

## The number that says what a stranger gets through

The patterns were frozen and then twenty two evasion attempts were written with
the pattern file open. Twenty of them still get through.

```
python scripts/evade.py --out measurements/evasion.json
```

That is a lower bound rather than an estimate, because the attempts were
written by somebody holding the implementation. The gap between the table above
and this paragraph is not a measurement error. Both are correct and they
measure different things. `docs/evasion.md` groups the twenty by what they
exploit and only one of the four groups is a pattern problem.

## Known limitations

- **9 of the 9 missed identifiers are the same case.** A five digit
  number with no state in front of it and no label beside it. The pattern
  refuses it on purpose, because taking bare five digit numbers turns invoice
  totals and procedure codes into redactions.
- **Instruction override is the weakest attack class at 18 of 24.** The misses
  are one phrasing that carries the intent and shares no verb with the pattern.
  The evasion pass finds the same hole from the other direction.
- **No input normalisation.** A Greek capital iota in place of a Latin I walks
  straight through, and widening a pattern cannot fix that. The input has to be
  normalised before it is matched.
- **Names are only found in the labelled form.** An unlabelled capitalised
  pair catches a name and also catches every drug and hospital in the payload,
  and a redactor with that precision gets switched off.
- **Three Safe Harbor classes have no detector**, named with reasons in
  `docs/policy-omissions.md`. The third of them is the reason this service
  redacts enumerated classes and does not claim de-identification.
- **Attachments are not read.** A payload field carrying base64 is forwarded
  whole.
- **A timestamp used to redact as an address.** Three colon separated groups
  is the shape of a clinical timestamp and of an address, and the pattern took
  both while splitting a real compressed address in half. Fixed, and the
  negatives that should have caught it are in the set now.
- **Nothing here has run under load.** There is no latency figure and no
  throughput figure, so the cost of the guardrail is unmeasured.

## What I would do differently

Writing the detectors before the corpus felt like the careful order and it hid
the thing worth knowing. The corpus score came back high and nothing about it
was wrong. It took freezing the patterns and attacking them on purpose to find
out that the figure was an upper bound. The evasion pass costs about forty
lines and it should have been the first thing built rather than the last.

## Layout

```
gateway/      policy enumeration, detectors, the request decision, HTTP front door
corpus/       the seeded generator and the two labelled slices
scripts/      scoring, the evasion pass
measurements/ counts manifests, one per scored run
docs/         the scoring rules, the declared omissions, the evasion results
terraform/    budget alert for the cloud work
```

The budget alert is written and validated and not applied. `terraform init
-backend=false` and `terraform validate` and `terraform fmt -check` all pass
without credentials, which is as far as a configuration can be graded before
the project exists.


Nothing in `corpus/data` came from a real record. Every value is generated, so
the labelled slices are committed whole rather than withheld.
