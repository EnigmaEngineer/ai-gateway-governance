"""Grade the Apigee bundle and its infrastructure by reading them.

Two gates cannot see most of what matters here.

`terraform validate` grades the schema. It accepts every optional argument
left out, which for an Apigee environment is three fields with API side
defaults, and it does not evaluate a variable's validation block at all.
That last one was measured rather than assumed: a variable carrying an
always false condition passes validate with Success and fails only on plan.
On a day with no credentials plan cannot run, so a validation block is not a
gate and the assertions here are.

`policy_check.py` grades the bundle, and it came back clean on the bundle the
same run wrote. That is agreement rather than detection, which is the 11-02
lesson. `bundle_sweep.py` is the answer and the checks below re-derive its
manifest rather than trusting the summary it printed.
"""

import ipaddress
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apigee import semantics  # noqa: E402

BUNDLE = ROOT / "apiproxy"
TF = ROOT / "terraform"
PRODUCTS = ROOT / "config" / "api-products.json"
SWEEP = ROOT / "measurements" / "apigee-sweep.json"
CHECK = ROOT / "measurements" / "apigee-bundle.json"


def tf_text():
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(TF.glob("*.tf")))


def apigee_tf():
    return (TF / "apigee.tf").read_text(encoding="utf-8")



def test_every_sourced_default_carries_a_quote_and_a_url():
    """A row with no source is a row somebody remembered."""
    for f in semantics.FACTS:
        assert f.quote.strip(), "%s has no quote" % f.key
        assert f.url.startswith("https://docs.apigee.com/"), "%s has no source URL" % f.key


def test_asking_for_an_unsourced_default_raises():
    """So a rule cannot be written on a default nobody read."""
    with pytest.raises(KeyError):
        semantics.fact("quota.something.nobody.looked.up")


def test_the_defaults_are_still_flagged_as_edge_sourced():
    """The pages that answered were Apigee Edge and this deploys Apigee X.

    This flips when somebody checks an X organisation, which needs a
    deployment. Until then the repository says so rather than implying the
    defaults were confirmed on the product it deploys.
    """
    assert semantics.DEFAULTS_ARE_EDGE_SOURCED is True


def test_the_unanswered_questions_are_still_recorded():
    """Three things the vendor's pages did not settle.

    Each one is a place the bundle is arranged so the answer cannot change
    what is enforced. If one gets answered this list is the diff.
    """
    assert set(semantics.UNANSWERED) == {
        "responsecache.hit_skips_rest_of_request_flow",
        "oauthv2.verifyaccesstoken_checks_product_paths",
        "flow.no_conditional_matches",
    }



def test_the_bundle_parses_and_the_checker_is_clean():
    proc = subprocess.run(
        [sys.executable, "scripts/policy_check.py"],
        cwd=str(ROOT), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_no_enforcing_policy_sits_on_a_conditional_flow():
    """The reason every control is in the PreFlow.

    Asserted here as well as in the checker, because this is the property a
    reviewer would check by hand and it should fail in the suite rather than
    only in a script somebody has to remember to run.
    """
    proxy = (BUNDLE / "proxies" / "default.xml").read_text(encoding="utf-8")
    flows = re.search(r"<Flows>(.*)</Flows>", proxy, re.S)
    assert flows, "no Flows block"
    assert "<Step>" not in flows.group(1), (
        "a Step on a conditional flow runs only when its condition is true")


def test_the_checker_rejects_an_empty_bundle_rather_than_passing_it(tmp_path):
    """NothingToCheck. The defect this program found twice in October."""
    empty = tmp_path / "apiproxy"
    (empty / "policies").mkdir(parents=True)
    (empty / "proxies").mkdir()
    proc = subprocess.run(
        [sys.executable, "scripts/policy_check.py", "--bundle", str(empty),
         "--products", str(PRODUCTS)],
        cwd=str(ROOT), capture_output=True, text=True)
    assert proc.returncode == 2, (
        "a checker pointed at an empty bundle must refuse, not report clean")


def test_verifyjwt_names_an_issuer_and_an_audience():
    """Both are Presence Optional, so leaving them out verifies a signature."""
    jwt = (BUNDLE / "policies" / "VJ-Tenant.xml").read_text(encoding="utf-8")
    assert "<Issuer>" in jwt
    assert "<Audience>" in jwt


def test_something_refuses_a_token_that_never_expires():
    """VerifyJWT cannot do this and the bundle has to.

    Expiry is checked only when the claim is present, and AdditionalClaims
    refuses registered claim names, which includes exp. So the refusal is a
    separate step or it does not exist.
    """
    proxy = (BUNDLE / "proxies" / "default.xml").read_text(encoding="utf-8")
    assert "RF-NoExpiry" in proxy
    assert "exp" in proxy and "null" in proxy
    assert (BUNDLE / "policies" / "RF-NoExpiry.xml").is_file()


def test_the_cache_key_carries_the_caller():
    """Otherwise two tenants sending the same prompt share an entry."""
    cache = (BUNDLE / "policies" / "RC-Completions.xml").read_text(encoding="utf-8")
    assert "claim.sub" in cache
    assert "<ExcludeErrorResponse>true</ExcludeErrorResponse>" in cache
    # A Prefix supersedes Scope, so the two together are a Scope doing nothing.
    assert "<Prefix>" not in cache


def test_both_rate_controls_are_distributed():
    """Each one defaults to counting per message processor."""
    quota = (BUNDLE / "policies" / "Q-TenantBudget.xml").read_text(encoding="utf-8")
    arrest = (BUNDLE / "policies" / "SA-PerClient.xml").read_text(encoding="utf-8")
    assert "<Distributed>true</Distributed>" in quota
    assert "<Synchronous>true</Synchronous>" in quota
    assert "<UseEffectiveCount>true</UseEffectiveCount>" in arrest
    for text in (quota, arrest):
        assert "<Identifier" in text, "one counter for every caller"



def test_every_rule_in_the_checker_has_an_arm_built_to_trip_it():
    """The first version of the sweep reported 16 of 16 with 19 rules.

    Three rules had no arm, so the sweep's own coverage number was measuring
    the set it was handed rather than the set that exists. This reads both
    numbers out of the manifest and compares them.
    """
    m = json.loads(SWEEP.read_text(encoding="utf-8"))
    assert m["rules_no_arm_exercises"] == [], m["rules_no_arm_exercises"]
    assert m["rules_exercised"] == m["rules_in_the_checker"]


def test_no_rule_is_dead():
    m = json.loads(SWEEP.read_text(encoding="utf-8"))
    assert m["rules_dead"] == [], m["rules_dead"]


def test_every_arm_was_gradeable():
    """An arm whose mutation broke the bundle reports no findings.

    That reads exactly like a bundle with no problem, which is how the render
    sweep's control arm reported clean over sixteen failed renderings.
    """
    m = json.loads(SWEEP.read_text(encoding="utf-8"))
    assert m["arms_gradeable"] == m["arms_total"]
    for arm in m["arms"]:
        assert arm["gradeable"], arm


def test_no_rule_fires_on_the_untouched_bundle():
    m = json.loads(SWEEP.read_text(encoding="utf-8"))
    assert m["baseline_findings"] == []
    assert m["rules_firing_on_the_untouched_bundle"] == []


def test_the_control_arm_finds_more_than_any_single_mutation():
    """A checker that finds little with every control removed is broken."""
    m = json.loads(SWEEP.read_text(encoding="utf-8"))
    worst_single = max(len(a["findings"]) for a in m["arms"])
    assert len(m["control_finding_codes"]) > worst_single
    assert m["control_finding_count"] >= 12


def test_the_published_sweep_numbers_match_the_manifest():
    """The README quotes four figures. All four come from here.

    This exists because block_check.py can only grade a figure against the
    capture of the run that wrote it, so a number published today is
    ungraded tomorrow without a check that re-derives it.
    """
    m = json.loads(SWEEP.read_text(encoding="utf-8"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for value in (m["arms_total"], m["rules_in_the_checker"],
                  m["control_finding_count"], len(semantics.FACTS)):
        assert str(value) in readme, "%s is not in the README" % value



def test_the_apigee_resources_are_all_declared():
    text = apigee_tf()
    for resource in ("google_apigee_organization", "google_apigee_instance",
                     "google_apigee_environment", "google_apigee_envgroup",
                     "google_apigee_envgroup_attachment",
                     "google_apigee_instance_attachment"):
        assert 'resource "%s"' % resource in text


def test_the_peering_range_is_allocated_before_apigee_asks_for_it():
    """The gap the first version of apigee.tf had.

    The organisation creates and the instance fails, and nothing about that
    is visible to terraform validate.
    """
    text = apigee_tf()
    assert 'resource "google_compute_global_address" "apigee_peering"' in text
    assert 'resource "google_service_networking_connection" "apigee"' in text
    assert 'purpose       = "VPC_PEERING"' in text
    assert "servicenetworking.googleapis.com" in text
    assert "depends_on = [google_service_networking_connection.apigee]" in text


def test_the_apigee_range_is_a_slash_22():
    """terraform validate does not run the variable's validation block.

    Measured, not assumed. A variable with an always false condition passes
    validate with Success and fails only on plan, and plan needs credentials
    this project does not have. So this assertion is the gate.
    """
    text = (TF / "variables.tf").read_text(encoding="utf-8")
    m = re.search(r'variable "apigee_peering_cidr".*?default\s*=\s*"([^"]+)"', text, re.S)
    assert m, "no default for apigee_peering_cidr"
    net = ipaddress.ip_network(m.group(1))
    assert net.prefixlen == 22, "the instance schema says length 22, got /%d" % net.prefixlen


def test_the_apigee_range_overlaps_nothing_else():
    text = (TF / "variables.tf").read_text(encoding="utf-8")
    ranges = {}
    for name in ("node_cidr", "pod_cidr", "service_cidr", "master_cidr",
                 "apigee_peering_cidr"):
        m = re.search(r'variable "%s".*?default\s*=\s*"([^"]+)"' % name, text, re.S)
        assert m, "no default for %s" % name
        ranges[name] = ipaddress.ip_network(m.group(1))
    names = sorted(ranges)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            assert not ranges[a].overlaps(ranges[b]), "%s overlaps %s" % (a, b)


def test_all_three_environment_type_fields_are_named():
    """Each one is optional, so each one has a default this file would be
    silent about. The first version set deployment_type and wrote a comment
    describing type, which is a different field.
    """
    text = apigee_tf()
    for field in ("type", "deployment_type", "api_proxy_type"):
        assert re.search(r"^\s+%s\s+=" % field, text, re.M), field


def test_the_unverified_billing_type_is_marked_as_such():
    """The provider gives no value list for it, so it is the one value here
    an apply could reject. A reader should not have to find that out.
    """
    text = apigee_tf()
    assert "NOT schema verified" in text
    assert 'billing_type = "EVALUATION"' in text


def test_the_blocked_apply_is_named_rather_than_implied():
    text = apigee_tf()
    assert "no GCP project" in text
    assert "has been applied" in text
