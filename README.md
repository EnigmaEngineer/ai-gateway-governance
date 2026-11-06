# ai-gateway-governance

A governed front door for LLM traffic. It redacts the identifier classes the
HIPAA Safe Harbor rule enumerates, detects prompt injection by attack class,
and refuses to start when the set of classes it enforces and the set it
deliberately ignores do not add up to the regulation's list.

```
pip install -r requirements-dev.txt
python -m gateway.app --port 8080
curl -s localhost:8080/v1/guard -d '{"text":"Patient: Rosa Delacroix, MRN 884213"}'
curl -sN localhost:8080/v1/guard/stream -d '{"text":"Patient: Rosa Delacroix, MRN 884213"}'
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

## Redacting a streamed response

A completion arrives a few tokens at a time and redacting each chunk on its own
leaks every identifier that straddles a boundary. `gateway/stream.py` holds the
tail back until enough has followed it to decide, and `/v1/guard/stream` is the
same guardrail over a chunked response.

```
python scripts/stream_sweep.py --out measurements/stream-buffers.json
```

The plan for this called the buffer one number. It is three. The second one is
where the identifiers go and the third one is what stops a single long link
turning one request into a scan that is quadratic in its own length.

```
holdback  lookbehind  leaked  diverged
      64           0     124        34
      64           4      60        28
      64           8      51        28
      64          16       0         1
```

A holdback of 64 characters is twice what the leak curve needs on its own and
it still loses 124 of 281 identifiers when nothing of the already emitted text
stays in the matching window. Most of the enforced classes are labelled, and a
label that has already gone out to the reader cannot introduce the value that
follows it. The full curves and the measured cost are in `docs/streaming.md`.

## The token budget, and what deciding early costs

A gateway says yes or no before it knows what a request will cost. Admission
charges an estimate and reconciliation replaces it with the usage the provider
reported, so a tenant can finish a window having spent more than its budget.
The amount is a quantity the design owns rather than a bug.

Measured against 240 held out bodies on a budget of 6,808 tokens. The load ran
at 16 concurrent callers over three repeats.

```
  prompt_estimator_short_by_tokens          882 to    891, median    882 over 3 runs
  forecast_short_by_tokens                 3478 to   3507, median   3478 over 3 runs
  overshoot_under_forecast_tokens           117 to    171, median    117 over 3 runs
```

Reserving the whole declared output ceiling is sound and it holds budget the
caller will not use. Forecasting a quarter of it took the shortfall from about
882 tokens to about 3,478. The shortfall is a sum over every settled request
and it barely moves. The overshoot is one number off one interleaving and it
moves by multiples, so both are published as a spread.

Under the ceiling policy the estimator was short by about 882 tokens and the
budget was overspent by nought, because 67 of the 240 bodies are blocked by the
injection filter and each hands its whole reservation back unspent. That slack
belongs to the traffic and not to the quota.

**Dropping the critical section around the counter is free in one process and
total the moment the counter is a network hop away.** Two callers read the same
counter and each writes its own total over the other's, so the reservation
never accumulates. How often that happens is a function of how far apart the
read and the write are, so the load run sweeps that width with a locked control
at each one.

```
  round trip 0        admitted without the lock  177  with it  176
  round trip 0.0002   admitted without the lock  238  with it  177
  round trip 0.0005   admitted without the lock  240  with it  175
  round trip 0.002    admitted without the lock  240  with it  176
```

With no artificial window the unserialised arm admits about what the locked one
admits. Open two tenths of a millisecond, which is less than any shared counter
costs, and the quota stops refusing almost anything. The first draft of this
section said the lock was load bearing full stop. The sweep is the only reason
it does not say that.

Detection is linear in payload size, so `Admission` caps how many requests are
in it at once and refuses rather than queues. Caps of 2, 4, 8 and 16 at 16
workers take the served median from 27.6 ms to 140.1 ms on two cores. Anything
below 16 refuses about nine requests in ten. That is a step and the harness put
it there, because a closed loop generator with 16 workers offers 16 concurrent
requests and no more. The sweep says what a cap costs and cannot
choose one. `docs/quota.md` carries the tables and the argument.

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
- **Streaming costs about 48 times the whole body redactor** over the same
  text, because the patterns are re-run on every chunk. Draining less often
  would cut it and would also move the leak curve, so it is a third dimension
  and nothing has measured it.
- **The lookbehind keeps unredacted text in memory after the reader has it.**
  The window is bounded by the lookbehind rather than by the response length,
  and it is still raw identifiers sitting in a buffer.
- **The admission limit has no number behind it.** The sweep is a step at the
  load generator's worker count, so it says what a cap costs and not what the
  cap should be. That needs an open loop generator with an arrival rate.
- **`MAX_BODY` is still a megabyte and nothing measured what a real payload
  needs.** The largest body either corpus slice carries is 233 characters,
  which is a fact about a generator. Lowering the limit to make the worst case
  look better would be choosing a bound from the sample it is judged on.
- **The reference tokeniser never merges, so it is at least as dear as the
  character rule on every input.** A real vocabulary is cheaper on long common
  words, so every shortfall figure here is a bound on what a real tokeniser
  would disagree by rather than a prediction of it.
- **The upstream honours the output ceiling and the quota window never
  rolls.** So one term of the overshoot is absent, and a budget is a counter
  with no clock on it, no refill and no sliding window.

## What I would do differently

Writing the detectors before the corpus felt like the careful order and it hid
the thing worth knowing. The corpus score came back high and nothing about it
was wrong. It took freezing the patterns and attacking them on purpose to find
out that the figure was an upper bound. The evasion pass costs about forty
lines and it should have been the first thing built rather than the last.

## Where it runs

A private GKE cluster and the gateway behind an internal load balancer, in
`terraform/` and `helm/`. None of it has been applied, because there is no
project yet.

`terraform validate` and `helm lint` both pass without credentials and neither
can see a configuration that is valid and wrong. So the chart is rendered at
all 32 combinations of the five values that decide whether the gateway is
exposed, in three arms, and every rendering is graded.

```
                                   rendered  refused  findings  exposed and unacknowledged
  the chart as it stands                  5       27         4                           0
  without the acknowledgements            8       24         8                           2
  with the refusal block emptied         32        0        64                           8
```

The middle arm is the chart's own refusal as it was first written. It caught
every empty value and let through two renderings with a public address,
because the value that exposes the service ships as `true` rather than empty.

The detector sits in front of the proxy, not behind it as a webhook. Portkey
takes the webhook's address from an account in its own application or from a
request header, so a detector behind it runs when the caller asks for it.
`docs/infrastructure.md` has that, and the three environment variables the
first chart invented.

## Layout

```
gateway/      policy enumeration, detectors, the request decision, the quota, HTTP front door
corpus/       the seeded generator, the two labelled slices, response bodies
scripts/      scoring, the evasion pass, the buffer sweep, the quota load run, the render sweep
measurements/ counts manifests, one per scored run
docs/         scoring, declared omissions, evasion, streaming, the quota, the cluster
terraform/    budget alert, network, private cluster, the two identities
helm/         Portkey and the guardrail in one pod, behind an internal load balancer
```


Nothing in `corpus/data` came from a real record. Every value is generated, so
the labelled slices are committed whole rather than withheld.

## The Apigee layer

One governed front door in front of the guardrail, as an Apigee X proxy bundle
in `apiproxy/` and its infrastructure in `terraform/apigee.tf`. Nothing is
applied, because there is no GCP project yet.

Most of an Apigee policy's security behaviour is decided by elements you leave
out. A `Quota` with no `Distributed` counts per message processor, so a budget
of 2000 is 2000 times the processor count. A `VerifyJWT` with no `Issuer`
verifies a signature and nothing about who signed it. A `ResponseCache` with
no caller in its key serves one tenant's completion to another. All three
deploy and `terraform validate` cannot see any of them.

So `apigee/semantics.py` carries 23 documented defaults, each with the sentence
it came from and its URL, and `scripts/policy_check.py` refuses to write a rule
on a default that is not in there. `scripts/bundle_sweep.py` then breaks the
bundle 19 ways, one per rule, and fails if any rule does not fire or if any
rule has no arm built to trip it. With every control stripped at once the
checker reports 14 findings over 12 distinct rules.

`VerifyJWT` is the sharpest one. Expiry is checked only when the claim is
present, and the element that could require a claim refuses registered names,
`exp` among them. So the policy admits a token that never expires and has no
setting that refuses it. `RF-NoExpiry` is a separate step for that reason.

The argument is in `docs/apigee.md` with the 23 sourced quotes. It also carries
the three questions the vendor's pages did not answer and why these are Edge
pages rather than X pages.
