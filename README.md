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

This page says what the thing is and what it does not do. Every measurement
sits in `docs/`, indexed at the bottom, after this page grew from 278 lines to
365 one defensible addition at a time.

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

## The two numbers worth leading with

On a held out slice of 400 generated rows the detectors find 272 of 281
identifiers and 146 of 152 planted attacks. Then the patterns were frozen and
attacked on purpose, and 20 of 22 evasion attempts walked through.

Both are correct and they answer different questions. The first is an upper
bound, because the same person wrote the class definitions and the generator.
The second is a lower bound, because the attempts were written with the
pattern file open. Neither one alone is honest. `docs/scoring.md` and
`docs/evasion.md` have the breakdown.

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
- **Streaming costs about 49 times the whole body redactor** over the same
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
  which is a fact about a generator.
- **The reference tokeniser never merges, so it is at least as dear as the
  character rule on every input.** A real vocabulary is cheaper on long common
  words, so every shortfall figure here is a bound on what a real tokeniser
  would disagree by rather than a prediction of it.
- **The upstream honours the output ceiling and the quota window never
  rolls.** So one term of the overshoot is absent, and a budget is a counter
  with no clock on it, no refill and no sliding window.
- **Nothing here has been applied to a cloud project.** The Terraform, the
  Helm chart and the Apigee bundle are graded by rendering and by reading, and
  no resource has ever existed. So the Apigee defaults are read off the Edge
  documentation rather than confirmed on Apigee X, and the chart's mounted
  paths are unverified against a container that started.
- **The token endpoint reads the client secret from somewhere the vendor does
  not document.** `ClientId` has an element and a stated default and a
  sentence saying no other variable is supported. There is no element for the
  secret and no sentence naming its source.

## What I would do differently

Writing the detectors before the corpus felt like the careful order and it hid
the thing worth knowing. The corpus score came back high and nothing about it
was wrong. It took freezing the patterns and attacking them on purpose to find
out that the figure was an upper bound. The evasion pass costs about forty
lines and it should have been the first thing built rather than the last.

The second one is the same mistake elsewhere. Four of the seven days wrote
configuration for somebody else's product, and on three of them the finding
was a default nobody had chosen rather than a value anybody got wrong. On the
one day the vendor's pages were read first, it changed the architecture before
the chart existed instead of after.

## Where it runs

A private GKE cluster with the gateway behind an internal load balancer, and
an Apigee X proxy in front of it. None of it has been applied, because there
is no project yet.

`terraform validate` and `helm lint` both pass without credentials and neither
can see a configuration that is valid and wrong. So the chart is rendered at
all 96 combinations of the six values that decide whether the gateway is
exposed or has an identity, in three arms, and every rendering is graded.

The detector sits in front of the proxy rather than behind it as a webhook,
because Portkey takes the webhook's address from a request header and a
detector behind it runs when the caller asks for it. Both are in
`docs/infrastructure.md`.

## The Apigee layer

One governed front door in front of the guardrail, as a proxy bundle in
`apiproxy/` with its infrastructure in `terraform/apigee.tf`.

Most of an Apigee policy's security behaviour is decided by elements you leave
out. A `Quota` with no `Distributed` counts per message processor, so a budget
of 2000 is 2000 times the processor count. A `VerifyJWT` with no `Issuer`
verifies a signature and nothing about who signed it. Both deploy and
`terraform validate` cannot see either one.

So `apigee/semantics.py` carries 38 documented defaults, each with the
sentence it came from and its URL, and `scripts/policy_check.py` refuses to
write a rule on a default that is not in there. `scripts/bundle_sweep.py` then
breaks the bundle 30 ways, one per rule, and fails if any rule does not fire
or if any rule has no arm built to trip it. With every control stripped at
once the checker reports 19 findings over 17 distinct rules.

The bundle has two proxy endpoints, the governed path and the token mint, each
keeping every control in its own PreFlow. `docs/apigee.md` has the sourced
quotes, the five questions the vendor's pages did not answer, and why the mint
got an endpoint rather than a flow.

## The hop to the backend, and the two headers that retire a version

The gateway presents a client certificate to the backend, the backend refuses
anything else, and the version a caller asked for decides what it gets.

`ssl.create_default_context(Purpose.CLIENT_AUTH)` is the call you build a
server with and it leaves `verify_mode` at `CERT_NONE`. So a backend built the
obvious way serves real TLS, presents a real certificate, and accepts a caller
presenting nothing. Every tool that reports a cipher says it is fine.

A failover time is not one number either. Against a primary still accepting
connections and no longer answering, the median is the attempt timeout to
within about two milliseconds at every width swept. Against one whose listener
is gone the timeout never appears in it at all.

`Deprecation` and `Sunset` do not share a date format. RFC 9745's own worked
example of the pair writes the zone as `UTC`, which the HTTP-date grammar
refuses, so copying the authority produces a broken field.

## Layout

```
gateway/      policy enumeration, detectors, the request decision, the quota, HTTP front door
corpus/       the seeded generator, the two labelled slices, response bodies
scripts/      scoring, the evasion pass, the buffer sweep, the quota load run, the two config sweeps
measurements/ counts manifests, one per scored run
docs/         every measurement and the argument behind it
apiproxy/     the Apigee bundle, two proxy endpoints and seven policies
terraform/    budget alert, network, private cluster, Apigee, the two identities
helm/         Portkey and the guardrail in one pod, behind an internal load balancer
```

Nothing in `corpus/data` came from a real record. Every value is generated, so
the labelled slices are committed whole rather than withheld.

## Measured, in full

- `docs/scoring.md` the held out table, the crude floor, and why a perfect precision column is the weakest figure here
- `docs/evasion.md` the 22 attempts grouped by what they exploit
- `docs/policy-omissions.md` the three Safe Harbor classes with no detector
- `docs/streaming.md` the three buffers, the leak curves and the measured cost
- `docs/quota.md` the estimate and reconcile spreads, the round trip sweep, the admission cap
- `docs/infrastructure.md` the cluster, the chart and the 96 rendering sweep
- `docs/apigee.md` the sourced defaults, the bundle sweep and the two endpoints
- `docs/mtls.md` the seven arms and the refusal that arrives after the handshake
- `docs/failover.md` the sixteen arms and the constant that decides the cost
- `docs/versioning.md` the four spellings of a sunset date and which two are wrong
