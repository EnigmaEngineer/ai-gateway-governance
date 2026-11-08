# The cluster, the chart, and what a values file decides

Nothing in here has been applied. There is no GCP project, no billing account
and no workload identity federation trust, so `terraform plan` cannot run and
the cluster has never existed. What has been done is everything that can be
done without credentials, and the interesting half of that is not the
validation.

```
$ terraform fmt -check -diff .
exit 0
$ terraform init -backend=false
providers resolved from the registry, exit 0
$ terraform validate
Success! The configuration is valid.
exit 0
$ helm lint helm/ai-gateway --set-string serviceAccount.gcpServiceAccount=sa@p.iam.gserviceaccount.com --set-string guardrail.image=reg/guardrail --set-string guardrail.tag=0.1.0 --set-string networkPolicy.callerCidrs[0]=10.0.0.0/8
==> Linting helm/ai-gateway
[INFO] Chart.yaml: icon is recommended
1 chart(s) linted, 0 chart(s) failed
```

The lint takes four values because the chart refuses to render without them.
Three of those refusals are the subject of the sweep below.

Four commands, no credentials, and between them they grade the schema and the
template syntax. Neither of them can see a configuration that is valid and
wrong, which on a day with no project is the entire gap.

## The shape, after it was built the other way round first

```
  callers in the VPC
          |
          v
  internal load balancer          Service, type LoadBalancer with the
          |                       GKE internal annotation
          v
  +-------------------------+
  | pod                     |
  |  guardrail  :8080       |     this repository, every request goes through it
  |     |                   |
  |     v  127.0.0.1:8787   |
  |  portkey                |     the proxy, reachable from inside the pod
  +-------------------------+
          |
          v  through Cloud NAT
  the model provider
```

Two containers in one pod rather than two Deployments. Both are on the request
path, so a second pod would add a network hop per request, and the quota load
run measured that path closely enough that a hop would have to be carried in
every figure. The cost is that the two scale together.

**The first version had the proxy in front.** Portkey supports an external
HTTP guardrail, so the obvious shape is the proxy at the front door calling
this repository as a webhook. That is what got built, and then the plugin got
read.

The webhook check is `default.webhook` and it takes `webhookURL` as a
parameter. A parameter arrives one of two ways. Saved against an account in
Portkey's own application, or inline in the `x-portkey-config` header on the
request. Neither of those is a property of the deployment. The first needs an
account this gateway does not have, and the second means **the detector runs
when the caller asks for it**.

A front door whose guardrail is a header the caller controls is not a front
door. So the order is inverted. The detector listens on the load balancer's
target port, every request passes through it because there is nowhere else to
arrive, and it forwards to the proxy on loopback. `gateway/app.py` already
took an `--upstream`, so the code did not change. The chart did.

What is left of the proxy's own guardrail support is unused, and that is the
right outcome. It is a feature for the hosted product.

The proxy still listens on the pod address and nothing documented changes
that, so a pod inside the cluster could reach it and skip the detector. The
NetworkPolicy admits the detector's port only, and the firewall rule for
callers names the same one. Those two are the whole defence and they are
worth knowing about rather than assuming.

## Three environment variables that do not exist

Worth writing down because it is the same mistake as publishing an unmeasured
number, wearing different clothes.

The first chart set `PORT`, `GUARDRAIL_URL` and `PROVIDER_API_KEY` on the
proxy container. None of the three is a variable that image reads. The
gateway's own compose file sets no environment at all and maps `8787:8787`.
Its reference Kubernetes manifest sets no environment either and hardcodes
`containerPort: 8787`. So the port is a fact about the image rather than a
setting, and the other two were invented to make the diagram work.

A chart carrying them renders, lints, and deploys. The container ignores them
and the gateway comes up with no provider credentials and no guardrail. The
only thing that would have caught it is reading the image's own deployment
files, which took one fetch and should have come before the chart rather than
after it.

The credentials actually live in a JSON config file with an `integrations`
list and a `credentials` block per provider. The chart mounts that file from a
Secret. **The path it is mounted at is a guess.** The example config in the
gateway's repository does not say where the process loads it from, so there
is a TODO on the mount saying so, and nothing here should be called working
until a container has started with it.

## The sweep, and the value it found

Six values in `values.yaml` decide four things. Whether the gateway is
reachable from the internet. Whether the pods hold the node identity. Whether
any pod in the cluster can reach the front door. And whether the hop to the
model backend carries an identity. Every one of them has a plausible setting
and none of them produces an error when it is wrong. That is the same shape as the store round
trip constant in the quota load run, which turned out to be setting the size of
the effect it was measuring. So it got the same treatment.

`scripts/render_sweep.py` renders the chart at every combination and grades
each rendering against the checks a reviewer would run by hand. Three arms,
the same 96 combinations each.

```
                                   rendered  refused  findings  exposed and unacknowledged
  the chart as it stands                  9       87        12                           0
  without the acknowledgements           16       80        24                           4
  with the refusal block emptied         96        0       288                          24
```

The backend hop was added on the last day of the build. The service had carried
a client certificate for a day and the chart had carried none of it, so mutual
TLS was in the test suite and not in the thing that would run. Three values for
that dimension rather than two, because a boolean cannot express the half
configured pairing the service refuses at startup.

```
                                   plaintext hop  of those unacknowledged  would not start
  the chart as it stands                       4                        0                0
  without the acknowledgements                 8                        4                0
  with the refusal block emptied              32                       16               32
```

**32 of the 96 renderings the stubbed chart produces are a pod that cannot
boot.** `gateway/app.py` refuses `--upstream-cert` without `--upstream-ca`, so
the chart refuses the combination that would produce it. The first version of
that grader read the flag names and found none of the 32, because emptying the
CA key still renders the flag. What it renders is the mount directory with a
trailing slash. A check reading argument names cannot see a path with no file
behind it, so the grader reads the values.

The middle arm is the guard as it was when this sweep first ran, and it is
built from the current chart rather than remembered, so it reproduces every
time. **It refused 24 of 32 and let through two renderings with a public
address.** The guard had been written against the two values that ship empty,
the workload identity annotation and the guardrail image. Both are unknown
until terraform has run, so both are obvious. The value that decides whether
the gateway is reachable from the internet ships as `true` instead, and a
guard written against emptiness cannot see it. Delete one annotation you do
not recognise and the Service gets an external forwarding rule, and it still
reports healthy.

So exposing the gateway takes two values rather than one.
`service.internal: false` alone refuses to render and names
`service.acknowledgeInternetExposure` in the refusal. Switching the network
policy off takes the same second value. Neither is the sort of line a copied
values file carries by accident.

The last column is the invariant and the rest of the table is context. A
rendering with a public address that nothing in its values asked for went from
two to nought, and `tests/test_infra.py` asserts it stays at nought rather
than asserting the other numbers.

The seven findings the sweep produces, each a configuration helm and terraform
both accept:

- a LoadBalancer Service with no internal annotation, which gets a public address
- a service account with no workload identity annotation, so pods hold the node identity
- no NetworkPolicy, so any pod in the cluster can reach the gateway
- a container image with no name
- the guardrail passes no client certificate, so the hop to the backend has no identity
- a certificate path passed with no filename behind it, which the service refuses at startup
- a secret volume naming an empty key, which the API server rejects

## The control arm reported clean before it worked

Worth writing down because it is the second one of these this week. The
control is a copy of the chart with the refusal block emptied. The first
version found the end of that block by taking the first `end` after a string
inside it, which is the inner conditional's end rather than the block's. The
copy carried an orphan `end`, every control rendering failed to parse, and the
arm reported no findings.

A control that refuses everything and a guard that blocks everything produce
the same number. `scripts/render_sweep.py` now fails the run if any control
rendering is refused, and `tests/test_infra.py` asserts it from the manifest.

## What is not here

- **No plan and no apply.** There is no project, no billing account and no
  workload identity federation trust for this repository, so there is nothing
  to authenticate against and nothing to plan against.
- **No image.** `guardrail.image` is empty and no image has ever been built
  or pushed, so the chart refuses to render with the values as they ship.
- **No secret.** The chart names the Secret Manager secret and does not
  create it. In a real deployment the value arrives through the CSI driver.
- **The Portkey container has never run here.** The image is pinned at 1.9.4
  and nothing in this repository has started it. The port comes from its own
  compose file and reference manifest. The config file path does not, and it
  is marked as a guess in the chart.
- **The webhook plugin is fail-closed at the plugin and that is as far as it
  was read.** `plugins/default/webhook.ts` starts its verdict at false and
  leaves it false on a timeout or an error, with a default timeout of 3000 ms.
  What a false verdict does further up is decided elsewhere in that codebase
  and was not read. None of it matters now that the detector is in front, and
  it would matter immediately if anyone moved it back.
- **The private endpoint is off.** The authorised network list is the control
  instead. A private endpoint is stronger and it also means CI cannot reach
  the cluster without a runner inside the VPC, which is a decision about the
  delivery path rather than about the cluster. It is a variable with a
  default of false and this paragraph is the argument, rather than a default
  somebody reads as considered.
