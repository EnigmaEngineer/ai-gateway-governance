"""Render the chart across every combination of its risky values and grade each one.

The quota load run swept a concurrency constant and the sweep reversed what
the run had found. The same shape is sitting in a values file. Four values decide
whether the gateway is on the internet, whether the pods hold the node
identity, and whether the network policy admits the whole cluster. Each of
them has a plausible default and none of them produces an error when it is
wrong.

So the values get swept rather than chosen. The combinations are rendered
with `helm template`, and every rendering graded against the invariants a
reviewer would check by hand.

There are three arms. The chart as it stands, the chart with the two
acknowledgement conditions removed, and the chart with its whole refusal
block emptied. The middle one is the guard as it was before this sweep ran,
and it is built rather than remembered, so the claim about it is a
measurement every run reproduces. It refused every empty value and nothing
about the value that decides internet exposure, because that one ships as a
plausible `true` rather than as empty.

The arm that matters is the control. `_helpers.tpl` carries a `require` block
that refuses to render on three of those values, and a sweep over a chart that
already refuses the bad combinations measures the guard rather than the chart.
So the same sixteen are rendered a second time with the guard stubbed out, and
the difference between the two arms is what the guard is worth.

    python scripts/render_sweep.py --out measurements/helm-render.json

Needs `helm` on PATH. It is not installed on the machine that writes the
documents, so this writes a manifest and `tests/test_infra.py` reads it. That
split is what keeps the suite runnable on a clone with nothing installed.
"""

import argparse
import itertools
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

CHART = pathlib.Path("helm/ai-gateway")

# The four values, each with the setting a careless deployment would land on
# first. `internal: false` is what you get by deleting an annotation you did
# not understand. The two empty strings are what the file ships with, because
# neither value can be known before terraform has run.
DIMENSIONS = {
    "service.internal": [True, False],
    "networkPolicy.enabled": [True, False],
    "workload_identity_set": [True, False],
    "guardrail_image_set": [True, False],
    # Added after the first sweep. The two acknowledgements move together
    # here, which keeps the sweep at 32 arms instead of 64 and still covers
    # the case the first version missed.
    "exposure_acknowledged": [True, False],
}

GCP_SA = "ai-gateway-workload@example-project.iam.gserviceaccount.com"
GUARDRAIL_IMAGE = "us-central1-docker.pkg.dev/example-project/gateway/guardrail"


def helm_version():
    path = shutil.which("helm")
    if not path:
        return None
    out = subprocess.run([path, "version", "--short"], capture_output=True, text=True)
    return out.stdout.strip() or None


def sets_for(combo):
    """Turn one point of the sweep into the flag and value pairs helm takes.

    Booleans go through --set and strings through --set-string, because --set
    reads an empty string as null and a null service account annotation
    renders differently from an empty one.
    """
    ack = str(combo["exposure_acknowledged"]).lower()
    booleans = [
        "service.internal=%s" % str(combo["service.internal"]).lower(),
        "networkPolicy.enabled=%s" % str(combo["networkPolicy.enabled"]).lower(),
        "service.acknowledgeInternetExposure=%s" % ack,
        "networkPolicy.acknowledgeClusterWideAccess=%s" % ack,
    ]
    strings = [
        # The caller ranges are always supplied. The sweep is about the four
        # named values and leaving this one empty would make every policy arm
        # fail for a reason the sweep is not asking about.
        "networkPolicy.callerCidrs[0]=10.0.0.0/8",
        "serviceAccount.gcpServiceAccount=%s" % (GCP_SA if combo["workload_identity_set"] else ""),
        "guardrail.image=%s" % (GUARDRAIL_IMAGE if combo["guardrail_image_set"] else ""),
        "guardrail.tag=%s" % ("0.1.0" if combo["guardrail_image_set"] else ""),
    ]
    return booleans, strings


def render(chart, combo):
    booleans, strings = sets_for(combo)
    args = ["helm", "template", "sweep", str(chart)]
    for b in booleans:
        args += ["--set", b]
    for s in strings:
        args += ["--set-string", s]
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        return None, proc.stderr.strip()
    return proc.stdout, None


def split_documents(rendered):
    """Separate the rendered stream into documents without a yaml parser.

    PyYAML is not in requirements.txt and adding it to read our own output
    would put a dependency in front of the suite. These documents are ours, so
    the kind and the fields the grader needs are findable by line.
    """
    docs = []
    current = []
    for line in rendered.splitlines():
        if line.startswith("---"):
            if current:
                docs.append("\n".join(current))
            current = []
        else:
            current.append(line)
    if current:
        docs.append("\n".join(current))
    return [d for d in docs if d.strip()]


def kind_of(doc):
    m = re.search(r"^kind:\s*(\S+)", doc, re.M)
    return m.group(1) if m else None


def grade(rendered):
    """Every finding here is a configuration helm and terraform both accept.

    Each one is phrased as a failure so the count is a count of problems.
    """
    docs = split_documents(rendered)
    kinds = [kind_of(d) for d in docs]
    findings = []

    services = [d for d in docs if kind_of(d) == "Service"]
    for svc in services:
        if "networking.gke.io/load-balancer-type: \"Internal\"" not in svc:
            findings.append("a LoadBalancer Service with no internal annotation, which gets a public address")
        # The proxy's port on a Service is a route round the detector. This
        # check was the other way round until the proxy turned out to apply
        # its webhook only when a caller asks it to.
        if "targetPort: portkey" in svc or "port: 8787" in svc:
            findings.append("the proxy port is published on a Service, so a caller can skip the detector")

    accounts = [d for d in docs if kind_of(d) == "ServiceAccount"]
    for sa in accounts:
        m = re.search(r"iam\.gke\.io/gcp-service-account:\s*(\S+)", sa)
        if not m or m.group(1).strip("\"'") == "":
            findings.append("the service account carries no workload identity annotation, so pods hold the node identity")

    deployments = [d for d in docs if kind_of(d) == "Deployment"]
    for dep in deployments:
        images = re.findall(r'^\s*image:\s*"?([^"\n]*)"?\s*$', dep, re.M)
        if "runAsNonRoot: true" not in dep:
            findings.append("the pod does not declare runAsNonRoot")
        # One per container, counted from the image lines rather than from
        # the name lines, because a volume mount is also a name.
        if dep.count("readOnlyRootFilesystem: true") < len(images):
            findings.append("a container is missing readOnlyRootFilesystem")
        for ref in images:
            name, _, tag = ref.rpartition(":")
            if not name:
                findings.append("a container image has no name")
            elif not tag:
                findings.append("a container image has no tag")

    if "NetworkPolicy" not in kinds:
        findings.append("no NetworkPolicy, so any pod in the cluster can reach the gateway")
    else:
        for pol in [d for d in docs if kind_of(d) == "NetworkPolicy"]:
            if "ipBlock" not in pol:
                findings.append("a NetworkPolicy with no ipBlock, which admits the whole cluster")

    return {
        "documents": len(docs),
        "kinds": sorted(k for k in kinds if k),
        "findings": sorted(set(findings)),
    }


def end_of_define(text, start):
    """Find the `end` that closes the define at `start`, not the first one.

    The first version of this took the first end after a string inside the
    block, which was the inner conditional's end. The stubbed chart then
    carried an orphan end, every one of the sixteen control renderings failed
    to parse, and the arm reported no findings. A control that refuses
    everything looks exactly like a guard that blocks everything.
    """
    # Depth starts at one because `start` is already inside the define. The
    # first version started at nought, so the inner conditional's end took it
    # back to nought and the walk returned there.
    depth = 1
    i = start
    openers = ("{{- if", "{{if", "{{- range", "{{range", "{{- with", "{{with",
               "{{- define", "{{define")
    while i < len(text):
        # Search from i and advance past the match afterwards. Searching from
        # i plus two skipped the first opening tag, which is how the walk
        # returned the inner end a second time.
        nxt = text.find("{{", i)
        if nxt == -1:
            break
        tag = text[nxt:nxt + 12]
        if tag.startswith(("{{- end", "{{end")):
            depth -= 1
            if depth == 0:
                close = text.find("}}", nxt) + 2
                return close
        elif any(tag.startswith(o) for o in openers):
            depth += 1
        i = nxt + 2
    raise SystemExit("could not find the end of the require block, so the control cannot be built")


def stub_the_guard(src, dst):
    """Copy the chart with the `require` block emptied, which is the control.

    Everything else is byte identical, so the only thing that moves between
    the two arms is whether the chart refuses.
    """
    shutil.copytree(src, dst)
    helpers = dst / "templates" / "_helpers.tpl"
    text = helpers.read_text()
    marker = '{{- define "ai-gateway.require" -}}'
    start = text.index(marker)
    end = end_of_define(text, start + len(marker))
    stubbed = text[:start] + marker + "\n{{- end -}}" + text[end:]
    if stubbed == text:
        raise SystemExit("the control arm did not change the chart, so it is not a control")
    helpers.write_text(stubbed)


def drop_the_acknowledgements(src, dst):
    """Copy the chart with the guard as it was before the acknowledgements.

    This arm exists so the claim about the earlier version is a measurement
    rather than a memory. The first guard refused every empty value and
    nothing about the value that exposes the service, and that is a property
    of a chart that can still be built on demand rather than something that
    happened once this afternoon.
    """
    shutil.copytree(src, dst)
    helpers = dst / "templates" / "_helpers.tpl"
    text = helpers.read_text()
    kept = []
    for condition in re.findall(r"\{\{- if [^\n]*\n\{\{- fail [^\n]*\n\{\{- end -\}\}\n", text):
        if "acknowledge" in condition:
            kept.append(condition)
    if len(kept) != 2:
        raise SystemExit(
            "expected two acknowledgement conditions to remove and found %d, so this arm is not the earlier guard"
            % len(kept))
    for condition in kept:
        text = text.replace(condition, "")
    helpers.write_text(text)


def sweep(chart, label):
    names = list(DIMENSIONS)
    arms = []
    for values in itertools.product(*(DIMENSIONS[n] for n in names)):
        combo = dict(zip(names, values))
        rendered, error = render(chart, combo)
        arm = {"arm": label, "values": combo}
        if rendered is None:
            arm["rendered"] = False
            # The refusal message is the chart's own sentence. Keeping the
            # first line of it means a changed message fails the check rather
            # than passing quietly.
            arm["refused_because"] = next(
                (ln.strip() for ln in error.splitlines() if "execution error" in ln or "fail" in ln.lower()),
                error.splitlines()[0] if error else "unknown",
            )[:200]
        else:
            arm["rendered"] = True
            arm.update(grade(rendered))
        arms.append(arm)
    return arms


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="measurements/helm-render.json")
    args = ap.parse_args()

    version = helm_version()
    if version is None:
        # Same shape as the cloud probes. Name what is missing and write
        # nothing, so a stale manifest cannot be read as today's run.
        print("helm is not on PATH, so nothing was rendered and no manifest was written",
              file=sys.stderr)
        return 2
    if not CHART.is_dir():
        print("run this from the repository root, %s is not there" % CHART, file=sys.stderr)
        return 2

    guarded = sweep(CHART, "guarded")
    with tempfile.TemporaryDirectory() as tmp:
        control_chart = pathlib.Path(tmp) / "ai-gateway"
        stub_the_guard(CHART, control_chart)
        control = sweep(control_chart, "guard_stubbed")

        earlier_chart = pathlib.Path(tmp) / "ai-gateway-earlier"
        drop_the_acknowledgements(CHART, earlier_chart)
        earlier = sweep(earlier_chart, "guard_without_acknowledgements")

    # The control exists to render the combinations the guard refuses. One
    # that refuses anything is a broken chart copy rather than a measurement,
    # and the first version of the stub did exactly that.
    broke = [a for a in control if not a["rendered"]]
    if broke:
        print("the control arm failed to render %d of %d combinations, so it is not a control: %s"
              % (len(broke), len(control), broke[0]["refused_because"]), file=sys.stderr)
        return 1

    def counts(arms):
        return {
            "rendered": sum(1 for a in arms if a["rendered"]),
            "refused": sum(1 for a in arms if not a["rendered"]),
            "renderings_with_a_finding": sum(1 for a in arms if a["rendered"] and a["findings"]),
            "findings_total": sum(len(a["findings"]) for a in arms if a["rendered"]),
            "exposed_renderings_nobody_acknowledged": sum(
                1 for a in arms if a["rendered"]
                and any("public address" in f for f in a["findings"])
                and not a["values"]["exposure_acknowledged"]),
        }

    manifest = {
        "helm_version": version,
        "dimensions": {k: [str(v).lower() for v in vs] for k, vs in DIMENSIONS.items()},
        "combinations": len(guarded),
        "guarded": counts(guarded),
        "guard_without_acknowledgements": counts(earlier),
        "guard_stubbed": counts(control),
        "arms": guarded + earlier + control,
    }
    distinct = sorted({f for a in manifest["arms"] if a.get("findings") for f in a["findings"]})
    manifest["distinct_findings"] = distinct
    manifest["distinct_finding_count"] = len(distinct)

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print("%d combinations over %d values" % (manifest["combinations"], len(DIMENSIONS)))
    for label in ("guarded", "guard_without_acknowledgements", "guard_stubbed"):
        c = manifest[label]
        print("  %-32s rendered %2d  refused %2d  with a finding %2d  findings %2d  exposed unacknowledged %d"
              % (label, c["rendered"], c["refused"], c["renderings_with_a_finding"],
                 c["findings_total"], c["exposed_renderings_nobody_acknowledged"]))
    print("written to %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
