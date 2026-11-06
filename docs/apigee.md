# The Apigee layer, and the defaults that decide what it enforces

This layer is Apigee X proxies with OAuth client credentials and JWT
verification, API products and keys, spike arrest and caching. There is no GCP
project yet, so nothing here has been applied. What follows was built and
graded by reading.

## Why reading is the whole job on this layer

An earlier version of the Helm chart here set three environment variables the
proxy image does not read. They were invented so a diagram would work, and
nothing in this repository could see it. `helm lint` passed. A rendered
manifest passed. The failure would have appeared at runtime as a service with
no credentials.

An Apigee bundle is a worse version of that problem, because most of its
security behaviour is decided by elements you leave out rather than by values
you get wrong. A `Quota` with no `Distributed` element counts per message
processor. A `VerifyJWT` with no `Issuer` verifies a signature and nothing
about who issued the token. A `ResponseCache` with no caller in its key serves
one tenant's completion to another. All three deploy, and no gate in this
repository would have said anything.

So `apigee/semantics.py` holds every default the checker reasons about, each
one with the sentence it was read from and the URL. `semantics.fact` raises on
a key it does not carry, which means a rule cannot be written on a default
nobody looked up. That is the mechanism. The list below is the content.

## What the vendor's pages actually say

**Both rate controls count per message processor by default.** The Quota page
says that with `Distributed` at its default of false you might exceed your
quota, because the count for each message processor is not shared. The spike
arrest page says each processor handles its own throttling independently, and
tells you to multiply by the number of processors to get the aggregate. So
`<Allow count="2000"/>` is 2000 times however many processors there are, and
the file says 2000.

**A quota counter is asynchronous by default.** `Synchronous` defaults to
false and the page says some calls exceeding the quota will go through. The
default update interval is ten seconds. So even once the count is shared, the
budget is exceeded by whatever arrives inside a ten second window.

**`VerifyJWT` cannot require a token to expire.** The page defines the
policy's own `valid` variable as the signature verifying and the current time
being inside expiry and not-before *if they are present*. A token carrying no
`exp` claim is therefore valid. The element that would require a claim,
`AdditionalClaims`, says not to use registered claim names. `exp` is one of
the seven it lists. There is no element for this. `RF-NoExpiry` in the
proxy PreFlow is the refusal, and it is a separate step because it has to be.

**`Issuer` and `Audience` are both Presence Optional.** A `VerifyJWT` without
them accepts any token signed by the configured key, whoever issued it and
whoever it was minted for.

**A response cache stores failures by default.** `ExcludeErrorResponse`
defaults to false and the page says the policy caches responses with any
possible status code. A provider's 503 cached for the expiry window outlives
the failure that produced it. Setting it true limits caching to 200 through
205.

**A cache key contains exactly what you put in it.** The page's own key
example is the request URI and it names no caller identity. Two tenants
posting the same prompt share an entry.

**A `Prefix` silently disables `Scope`.** The page says a `Prefix` in the
`CacheKey` supersedes the `Scope` value. So a policy carrying both reads as
though it isolates entries and does not.

**An API product naming no proxies grants everything.** The products page says
that if you add no API proxies to a product, developers can access all of your
proxies and resource paths. An omission from an allow list is a grant, a
shape this repository has already met in a warehouse rule list. The same
page says less specific paths take precedence, so a `/` beside any other path
makes the others decoration.

**Flow placement decides coverage, and it is readable.** Policies on the
PreFlow always execute. Policies on a conditional flow execute only when that
flow's condition is true. One conditional flow runs per segment, the first
whose condition is true. Those three sentences are why every control in this
bundle is in the PreFlow and why `scripts/policy_check.py` refuses a Step on a
conditional flow.

## What the pages did not answer

Three questions, recorded in `semantics.UNANSWERED` rather than guessed. In
each case the bundle is arranged so the answer cannot change what is enforced.

**Does a cache hit skip the rest of the request flow?** The page says the
cached response returns with no request forwarded to the backend. It says
nothing about the other steps. If they are skipped, a cache lookup placed
before the guardrail skips the guardrail. The lookup is the last step in the
PreFlow for that reason.

**Does `VerifyAccessToken` enforce the API product's resource paths?** The
OAuthV2 page says the operation verifies that the token is valid. It does not
say it checks paths. Nothing here relies on it for path authorisation and the
products carry explicit paths rather than a wildcard.

**What happens when no conditional flow condition matches?** The flows page
does not say. No control depends on the answer.

## These are Apigee Edge pages

The day's line says Apigee X. The pages that answered are Apigee Edge, because
the Apigee X documentation on `docs.cloud.google.com` renders its article body
client side and three separate fetches returned the navigation menu and
nothing else. The two products share the policy engine and these are the same
policies, which is an argument and not a verification.

`semantics.DEFAULTS_ARE_EDGE_SOURCED` is true and a test asserts it stays
true. It flips when somebody checks the defaults against an Apigee X
organisation, which needs a project that does not exist yet. Treat every
default above as sourced from Edge and unconfirmed on X.

## What terraform validate can and cannot see here

`terraform validate` accepted the first version of `apigee.tf`, which was
missing the Service Networking allocation the instance takes its range from.
The organisation would have created and the instance would have failed.
Nothing about that is syntactically wrong.

It also does not evaluate a variable's `validation` block. Measured: a
variable carrying an always false condition passes `terraform validate` with
`Success! The configuration is valid.` and fails only on `terraform plan`.
Plan needs credentials. So on every day this project can actually run, a
validation block is documentation. The `/22` requirement on the Apigee range
is asserted in `tests/test_apigee.py` instead, because the suite runs on a
clone with no cloud.

The environment resource has three separate optional fields that all sound
like each other, `type`, `deployment_type` and `api_proxy_type`. The first
version of `apigee.tf` set `deployment_type` and carried a comment describing
`type`. All three are now named explicitly, because each one left out is an
API side default the file would be silent about. Which policies the `BASE`
tier carries is not verified.

## The measurement

`scripts/policy_check.py` came back clean on the bundle the same run wrote.
This repository has scored a detector at 272 of 281 against a corpus its own
author produced, and that figure measured agreement rather than detection. A checker reporting clean on its author's bundle is the
same measurement.

`scripts/bundle_sweep.py` builds a bundle per rule, each one carrying the
default a careless author lands on first, which for most of them is deleting
an element. Three things are asserted per arm. The rule fires on its mutation.
The rule does not fire on the untouched bundle. And the mutated bundle still
parses and still presents a request path, because an arm whose mutation broke
the XML reports no findings and that reads exactly like a bundle with no
problem. The render sweep's control arm failed sixteen renderings and reported
clean for that reason.

The first version of the sweep reported sixteen of sixteen rules fired. The
checker has nineteen rules, so three were unmeasured and the summary said
nothing about it. The sweep now reads the rule codes out of the checker's
source and fails on any rule with no arm. A gate can be correct over the set
it was handed when the set itself is the defect.

## Reproducing both manifests

Neither needs a cloud project, a cluster or a credential. Both run on a clone
with nothing installed beyond the standard library.

```
python scripts/policy_check.py --json measurements/apigee-bundle.json
python scripts/bundle_sweep.py --out measurements/apigee-sweep.json
```

The first grades the committed bundle and exits 0 clean, 1 with findings, and
2 when it had nothing to grade. The third exit code is the one that matters.
A checker pointed at an empty bundle that reports clean is a defect this
repository has shipped twice. `tests/test_apigee.py` asserts the refusal by
pointing the checker at an empty directory.

The second builds a bundle per rule and exits 1 if any rule fails to fire, if
any rule has no arm, or if any arm could not be graded.

The infrastructure needs terraform and grades without credentials:

```
terraform -chdir=terraform init -backend=false
terraform -chdir=terraform validate
```

Bear in mind what the second one does not do. It does not evaluate a
variable's validation block, and it cannot see a missing Service Networking
allocation. Both of those are covered by `tests/test_apigee.py` instead.
