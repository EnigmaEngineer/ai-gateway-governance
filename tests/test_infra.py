"""Grade the cluster and the chart by reading them.

`terraform validate` grades the schema and `helm lint` grades the templates.
Neither can see a configuration that is valid and wrong, and on a day with no
cloud project that is the whole difference between writing infrastructure and
having it reviewed. Every check here is a finding somebody would make in a
review, written down so it fails instead.

The render sweep manifest is read rather than produced. `helm` is not on the
machine that writes these documents, so a suite that shelled out to it would
pass nowhere.
"""

import ipaddress
import json
import re
from pathlib import Path

TF = Path(__file__).resolve().parents[1] / "terraform"
CHART = Path(__file__).resolve().parents[1] / "helm" / "ai-gateway"


def tf_text():
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(TF.glob("*.tf")))


def variable_block(name):
    """The body of one variable block, so a default can be asserted absent."""
    text = (TF / "variables.tf").read_text(encoding="utf-8")
    m = re.search(r'variable\s+"%s"\s*\{(.*?)\n\}' % re.escape(name), text, re.S)
    assert m, "no variable named %s" % name
    return m.group(1)


def default_of(name):
    body = variable_block(name)
    m = re.search(r'^\s*default\s*=\s*"?([^"\n]+)"?', body, re.M)
    return m.group(1).strip() if m else None


# The four below are the findings worth having on GCP, and the first is the
# one a reviewer reaches for first.

def test_the_node_pool_does_not_run_as_the_default_compute_service_account():
    """The default compute account is project editor.

    A node pool left on it turns any container escape into a project
    compromise, and the only visible sign is the absence of a line.
    """
    text = tf_text()
    m = re.search(r'resource\s+"google_container_node_pool".*?\n\}', text, re.S)
    assert m, "no node pool declared"
    pool = m.group(0)
    assert "service_account" in pool, "the node pool declares no service account, so it gets the default compute one"
    assert "google_service_account.nodes.email" in pool, (
        "the node pool is not on the dedicated node identity")
    assert "compute@developer.gserviceaccount.com" not in text


def test_the_nodes_are_private_and_the_workload_has_its_own_identity():
    text = tf_text()
    assert "enable_private_nodes = true" in text, "the nodes would get public addresses"
    assert "workload_identity_config" in text, (
        "without workload identity the pods authenticate as the node service account")
    assert "mode = \"GKE_METADATA\"" in text, (
        "the metadata server is unconcealed, so a pod can read the node token")


def test_the_legacy_metadata_endpoints_are_off_and_boot_is_measured():
    text = tf_text()
    assert 'disable-legacy-endpoints = "true"' in text, (
        "the v1 metadata endpoints need no header, so request forgery in a pod reads the node token")
    assert "enable_secure_boot          = true" in text or "enable_secure_boot = true" in text
    assert "enable_integrity_monitoring = true" in text


def test_no_variable_that_identifies_a_real_place_carries_a_default():
    """A default on one of these is a configuration that applies somewhere.

    `project_id` and `billing_account` name a real account. `caller_cidrs`
    and `control_plane_cidrs` decide who reaches the gateway and the API, so
    a default on either is a guess about somebody's network that nobody would
    notice was a guess.
    """
    for name in ("project_id", "billing_account", "caller_cidrs",
                 "control_plane_cidrs", "alert_emails"):
        assert default_of(name) is None, (
            "%s carries a default, so a plan would run against it unasked" % name)


def test_the_ranges_do_not_overlap():
    """Terraform validates each of these and compares none of them.

    An overlapping master range is rejected by the API at apply time, which
    on a blocked day is a defect nobody finds for as long as the block lasts.
    """
    named = {}
    for name in ("node_cidr", "pod_cidr", "service_cidr", "master_cidr"):
        value = default_of(name)
        assert value, "%s has no default to check" % name
        named[name] = ipaddress.ip_network(value)
    pairs = [(a, b) for a in named for b in named if a < b]
    for a, b in pairs:
        assert not named[a].overlaps(named[b]), (
            "%s %s overlaps %s %s" % (a, named[a], b, named[b]))


def test_the_budget_is_declared_and_the_cluster_cannot_autoscale():
    """Two cost controls, and the second is the one a load test needs.

    A budget alert arrives after the money is spent. A cluster that cannot
    add nodes cannot spend it in the first place.
    """
    text = tf_text()
    assert "google_billing_budget" in text
    assert re.search(r"cluster_autoscaling\s*\{\s*enabled\s*=\s*false", text), (
        "the cluster can add nodes on its own, so a load test can outrun the budget alert")
    assert "deletion_protection = false" in text, (
        "the cluster is torn down at the end of the week and protection on blocks that")


def test_callers_are_allowed_the_detector_port_and_not_the_proxy_port():
    text = tf_text()
    m = re.search(r'resource\s+"google_compute_firewall"\s+"callers_to_gateway".*?\n\}', text, re.S)
    assert m, "no caller rule"
    rule = m.group(0)
    assert "var.guardrail_port" in rule
    assert "portkey_port" not in rule, (
        "callers are allowed the proxy port, which is a route round the detector")
    assert "portkey_port" not in text, (
        "the proxy port is no longer a terraform concern and a variable nothing uses is padding")


def test_the_health_check_rule_names_the_prober_ranges_and_not_the_vpc():
    text = tf_text()
    assert '"130.211.0.0/22"' in text and '"35.191.0.0/16"' in text, (
        "the internal load balancer health checks arrive from Google's ranges and need their own rule")
    m = re.search(r'resource\s+"google_compute_firewall"\s+"control_plane_to_nodes".*?\n\}', text, re.S)
    assert m, "no control plane rule"
    assert "var.master_cidr" in m.group(0), (
        "the control plane rule should name the master range and not the whole VPC")


# The chart. These read the templates rather than a rendering, because a
# rendering only covers the values it was given.

def uncommented(path):
    text = path.read_text(encoding="utf-8")
    return "\n".join(l for l in text.splitlines() if not l.strip().startswith("#"))


def test_the_service_publishes_the_detector_and_not_the_proxy():
    """The detector is the front door, so the Service targets it.

    This check was the other way round when it was written, because the chart
    was. The proxy applies its webhook guardrail only when a caller sends the
    config asking for it, so a proxy in front is a detector a caller can
    decline. An earlier version of this check also searched the whole file for
    a word and failed on its own explanatory comment, which is why it reads
    the ports block.
    """
    svc = uncommented(CHART / "templates" / "service.yaml")
    assert "targetPort: guardrail" in svc
    m = re.search(r"\n  ports:\n(.*?)\n  selector:", svc, re.S)
    assert m, "no ports block in the Service"
    assert "portkey" not in m.group(1), (
        "publishing the proxy port lets a caller reach it and skip the detector")
    assert "8787" not in m.group(1)


def test_the_detector_is_in_front_and_forwards_to_the_proxy():
    dep = uncommented(CHART / "templates" / "deployment.yaml")
    m = re.search(r"- name: guardrail(.*?)(?=\n        - name: )", dep, re.S)
    assert m, "no guardrail container, or it is no longer the first one"
    guardrail = m.group(1)
    assert '"0.0.0.0"' in guardrail, (
        "the load balancer targets this container, so loopback would make it unreachable")
    assert "--upstream" in guardrail, "the detector forwards nowhere, so it is not in front of anything"
    assert "127.0.0.1" in guardrail, "the detector should reach the proxy on loopback and not over the network"
    assert "containerPort" in guardrail, (
        "without a container port there is nothing for the Service to target")


def test_the_proxy_is_given_no_environment_variable_it_does_not_read():
    """Three invented variables shipped in the first version of this chart.

    PORT, GUARDRAIL_URL and PROVIDER_API_KEY. The proxy's own compose file
    and its reference Kubernetes manifest both set no environment at all and
    hardcode the port, so every one of the three was a configuration
    interface this repository made up. A chart that sets them renders, lints
    and deploys, and the container ignores them.
    """
    dep = uncommented(CHART / "templates" / "deployment.yaml")
    m = re.search(r"- name: portkey(.*?)(?=\n      volumes:)", dep, re.S)
    assert m, "no portkey container"
    portkey = m.group(1)
    for invented in ("GUARDRAIL_URL", "PROVIDER_API_KEY", "PORT"):
        assert invented not in portkey, (
            "%s is not a variable the proxy image reads" % invented)
    assert "env:" not in portkey, (
        "the proxy reads no environment variable this repository has verified")
    assert "subPath: conf.json" in portkey, (
        "the provider credentials go in the config file the proxy reads, not in the environment")


def test_the_network_policy_admits_the_detector_port_only():
    """The proxy listens on the pod address and nothing documented changes it.

    So the policy is the only thing standing between a pod in the cluster and
    a route round the detector.
    """
    pol = uncommented(CHART / "templates" / "networkpolicy.yaml")
    assert ".Values.guardrail.port" in pol
    assert "portkey" not in pol, "the policy admits the proxy port, which is the bypass"


def test_the_chart_carries_the_stream_settings_the_module_was_measured_at():
    """A chart value that disagrees with the module is a leak in a manifest.

    The holdback is what stops an identifier split across two chunks going
    out in the first one. Setting it lower in the chart than the module was
    measured at is invisible in a rendered manifest and visible in the
    output of the service.
    """
    app = (CHART.parents[1] / "gateway" / "app.py").read_text(encoding="utf-8")
    values = (CHART / "values.yaml").read_text(encoding="utf-8")
    for constant, key in (("STREAM_HOLDBACK", "streamHoldbackChars"),
                          ("STREAM_LOOKBEHIND", "streamLookbehindChars")):
        in_module = int(re.search(r"^%s = (\d+)" % constant, app, re.M).group(1))
        in_chart = int(re.search(r"%s: (\d+)" % key, values).group(1))
        assert in_chart >= in_module, (
            "%s is %d in the chart and %d in gateway/app.py, and lower in the chart leaks"
            % (key, in_chart, in_module))


def test_exposing_the_gateway_takes_a_second_value():
    helpers = (CHART / "templates" / "_helpers.tpl").read_text(encoding="utf-8")
    assert "acknowledgeInternetExposure" in helpers, (
        "service.internal false alone renders a public gateway")
    assert "acknowledgeClusterWideAccess" in helpers


# The render sweep manifest.

def test_the_render_sweep_control_rendered_every_combination(manifests):
    """A control that refuses everything is a broken copy, not a measurement.

    The first version of the chart copy left an orphan end, all sixteen
    control renderings failed to parse, and the arm reported no findings at
    all. That is the shape the 11-03 rule names, so it gets a check rather
    than a comment.
    """
    m = manifests["helm-render"]
    assert m["guard_stubbed"]["refused"] == 0, (
        "%d of %d control renderings failed, so the control arm graded nothing"
        % (m["guard_stubbed"]["refused"], m["combinations"]))
    assert m["guard_stubbed"]["rendered"] == m["combinations"]


ARMS = ("guarded", "guard_without_acknowledgements", "guard_stubbed")


def test_the_sweep_counts_re_derive_from_its_own_arms(manifests):
    m = manifests["helm-render"]
    for label in ARMS:
        arms = [a for a in m["arms"] if a["arm"] == label]
        assert len(arms) == m["combinations"]
        assert m[label]["rendered"] == sum(1 for a in arms if a["rendered"])
        assert m[label]["refused"] == sum(1 for a in arms if not a["rendered"])
        assert m[label]["findings_total"] == sum(len(a.get("findings", [])) for a in arms)
        assert m[label]["renderings_with_a_finding"] == sum(
            1 for a in arms if a["rendered"] and a["findings"])
        assert m[label]["exposed_renderings_nobody_acknowledged"] == sum(
            1 for a in arms if a["rendered"]
            and any("public address" in f for f in a["findings"])
            and not a["values"]["exposure_acknowledged"])


def test_the_guard_refuses_more_than_it_lets_through_and_the_stubbed_arm_refuses_nothing(manifests):
    m = manifests["helm-render"]
    assert m["guarded"]["refused"] > m["guarded"]["rendered"], (
        "the guard refused %d of %d, which is not a guard doing much"
        % (m["guarded"]["refused"], m["combinations"]))
    assert m["guard_stubbed"]["findings_total"] > m["guarded"]["findings_total"], (
        "removing the guard did not increase the findings, so the guard catches nothing")


def test_no_rendering_of_the_chart_is_exposed_without_the_acknowledgement(manifests):
    """The invariant the acknowledgement was added for, asserted per rendering.

    The count in the manifest is asserted above. This walks the arms, because
    a count can be right while the arm it came from is empty.
    """
    m = manifests["helm-render"]
    graded = 0
    for arm in m["arms"]:
        if arm["arm"] != "guarded" or not arm["rendered"]:
            continue
        graded += 1
        if any("public address" in f for f in arm["findings"]):
            assert arm["values"]["exposure_acknowledged"], (
                "a rendering has a public address and nothing in its values said so: %s"
                % arm["values"])
    assert graded >= 4, "only %d renderings of the chart to grade" % graded
    assert m["guarded"]["exposed_renderings_nobody_acknowledged"] == 0


def test_the_earlier_guard_arm_is_a_weaker_guard_and_not_a_broken_copy(manifests):
    """The middle arm has to refuse some things and let the exposure through.

    An arm that refused nothing would be the stubbed control under another
    name, and one that refused everything would be a broken chart copy. Both
    have happened in this sweep already, so both get a check.
    """
    m = manifests["helm-render"]
    earlier = m["guard_without_acknowledgements"]
    assert earlier["refused"] > 0, "the earlier guard refused nothing, so it is the stubbed arm"
    assert earlier["rendered"] > m["guarded"]["rendered"], (
        "the earlier guard rendered no more than the current one, so the fix changed nothing")
    assert earlier["exposed_renderings_nobody_acknowledged"] >= 1, (
        "the earlier guard let no unacknowledged exposure through, which is the whole claim about it")
    assert earlier["refused"] < m["combinations"], (
        "the earlier guard refused every combination, which is a broken copy rather than a guard")


def test_the_published_sweep_table_matches_the_manifest(manifests, root):
    """Every number in the two tables is re-derived from the manifest.

    `block_check.py` grades a figure against the capture of the run that
    wrote it, so a table published today is ungraded tomorrow unless
    something committed compares it to a measurement. These are those
    comparisons.
    """
    rows = {
        "the chart as it stands": "guarded",
        "without the acknowledgements": "guard_without_acknowledgements",
        "with the refusal block emptied": "guard_stubbed",
    }
    m = manifests["helm-render"]
    graded = 0
    for rel in ("README.md", "docs/infrastructure.md"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "%d combinations" % m["combinations"] in text, (
            "%s does not state the number of combinations the manifest holds" % rel)
        for label, key in rows.items():
            line = next((l for l in text.splitlines() if l.strip().startswith(label)), None)
            assert line, "%s has no row for %s" % (rel, label)
            numbers = [int(n) for n in line.split()[len(label.split()):]]
            c = m[key]
            expected = [c["rendered"], c["refused"], c["findings_total"],
                        c["exposed_renderings_nobody_acknowledged"]]
            assert numbers == expected, (
                "%s row %r reads %s and the manifest says %s"
                % (rel, label, numbers, expected))
            graded += 1
    assert graded == 6, "expected two tables of three rows and graded %d" % graded


def test_the_sweep_found_the_four_findings_it_was_written_for(manifests):
    m = manifests["helm-render"]
    wanted = ("public address", "workload identity", "NetworkPolicy", "no name")
    found = " | ".join(m["distinct_findings"])
    for w in wanted:
        assert w in found, "the sweep never produced a finding about %s" % w
