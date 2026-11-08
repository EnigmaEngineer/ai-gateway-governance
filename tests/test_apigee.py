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
    """Eight things the vendor's pages did not settle.

    Each one is a place the bundle is arranged so the answer cannot change
    what is enforced. If one gets answered this list is the diff.

    Five arrived with the token endpoint and one of them is a different kind
    from the rest. Every other entry is a question about behaviour.
    `oauthv2.client_secret_source` is a required input with no documented
    source, which is an absence in the vendor's own reference rather than in
    this bundle.
    """
    assert set(semantics.UNANSWERED) == {
        "responsecache.hit_skips_rest_of_request_flow",
        "oauthv2.verifyaccesstoken_checks_product_paths",
        "flow.no_conditional_matches",
        "oauthv2.client_secret_source",
        "oauthv2.generateaccesstoken_success_status",
        "oauthv2.generateaccesstoken_needs_a_product",
        "product.resource_path_with_two_base_paths",
        "apiproxy.second_endpoint_must_be_listed",
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



def without_comments(text):
    """The configuration with every XML comment removed."""
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def test_every_claim_reference_uses_the_documented_variable_family():
    """The legacy family is not a shorter alias and it renames exp and sub.

    A reference to jwt.VJ-Tenant.claim.exp would resolve to nothing, which
    makes the RF-NoExpiry condition false and admits a token with no expiry.
    The one in the bundle is the documented spelling and this is what keeps a
    later tidy up from shortening it.
    """
    pattern = semantics.fact("verifyjwt.decoded_claim_variable").value
    assert pattern == "jwt.{policy_name}.decoded.claim.{name}"
    refs = []
    for path in sorted((ROOT / "apiproxy").rglob("*.xml")):
        text = without_comments(path.read_text(encoding="utf-8"))
        for match in re.finditer(r"jwt\.[A-Za-z0-9\-]+\.[A-Za-z0-9_.]+", text):
            refs.append((path.name, match.group(0)))
    assert refs, "no jwt flow variable in the bundle, so this check graded nothing"
    for where, ref in refs:
        parts = ref.split(".")
        assert parts[2:4] == ["decoded", "claim"], (
            "%s references %s, which is not the documented family" % (where, ref))
        assert parts[4] in ("sub", "aud", "exp", "iss", "nbf", "iat"), ref


def test_the_legacy_claim_names_appear_nowhere_in_the_bundle():
    """claim.expiry is milliseconds and decoded.claim.exp is seconds."""
    legacy = ("claim.expiry", "claim.subject", "claim.audience", "claim.issuer",
              "claim.issuedat", "claim.notbefore")
    for path in sorted((ROOT / "apiproxy").rglob("*.xml")):
        # The VJ-Tenant comment names these on purpose, as the trap beside the
        # documented family, so the prose is stripped before the search.
        text = without_comments(path.read_text(encoding="utf-8"))
        for name in legacy:
            assert name not in text, "%s references %s" % (path.name, name)


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


def token_endpoint():
    return (BUNDLE / "proxies" / "token.xml").read_text(encoding="utf-8")


def mint_policy():
    return (BUNDLE / "policies" / "OA-GenerateToken.xml").read_text(encoding="utf-8")


def test_something_in_the_bundle_mints_a_token():
    """The gap. Verification shipped and nothing issued a token.

    Asserted here as well as in the checker because this is the kind of
    absence a reviewer notices and a schema never does.
    """
    text = without_comments(mint_policy())
    assert "<Operation>GenerateAccessToken</Operation>" in text
    assert "<GrantType>client_credentials</GrantType>" in text, (
        "the mint has to name the grant, because the default is the two interactive ones")


def test_the_mint_names_the_three_elements_whose_absence_is_the_default():
    text = without_comments(mint_policy())
    for element in ("SupportedGrantTypes", "ExpiresIn", "GenerateResponse"):
        assert "<%s" % element in text, (
            "%s is absent, and its default is the behaviour nobody chose" % element)
    expires = re.search(r"<ExpiresIn>(-?\d+)</ExpiresIn>", text)
    assert expires, "no ExpiresIn value to grade"
    value = int(expires.group(1))
    assert value > 0, (
        "the page says any negative integer other than -1, and zero, causes a deployment "
        "error, and this reads %d" % value)
    assert value % 1000 == 0, (
        "ExpiresIn is milliseconds and the response field is seconds, so a value that is "
        "not a whole number of seconds publishes a lifetime nobody intended")


def test_the_client_id_uses_the_only_supported_variable():
    documented = semantics.value("oauthv2.clientid.only_supported_variable")
    text = without_comments(mint_policy())
    found = re.search(r"<ClientId>([^<]+)</ClientId>", text)
    assert found, "the mint names no ClientId, so it falls back to a default nobody read"
    assert found.group(1).strip() == documented, (
        "the page says setting ClientId to any other variable is not supported, and this "
        "reads %r" % found.group(1).strip())


def test_the_undocumented_client_secret_source_is_recorded_rather_than_invented():
    """The gap that is an absence in the vendor's reference.

    A BasicAuthentication policy here would be configuration written on the
    strength of the word `typically` in a fault table, which is inventing a
    setting from a different source.
    """
    assert "oauthv2.client_secret_source" in semantics.UNANSWERED
    # Read the root tag of every policy rather than its filename. A check on a
    # name prefix passes on the same policy called something else, which is a
    # check on a convention and not on the bundle.
    import xml.etree.ElementTree as ET
    kinds = sorted(ET.parse(path).getroot().tag
                   for path in (BUNDLE / "policies").glob("*.xml"))
    assert kinds, "no policies parsed, so this check graded nothing"
    assert "BasicAuthentication" not in kinds, (
        "a BasicAuthentication policy appeared and the page still does not say the mint "
        "reads the secret from there: %r" % kinds)
    assert "TODO" in mint_policy(), "the gap has to be named in the file a reader opens"


def test_the_mint_endpoint_asks_for_no_credential_on_the_way_in():
    """A caller must not need a token to get a token."""
    text = without_comments(token_endpoint())
    steps = re.findall(r"<Name>([^<]+)</Name>", text)
    assert "OA-GenerateToken" in steps, "the mint is not on this endpoint's PreFlow"
    for forbidden in ("OA-VerifyToken", "VJ-Tenant", "RF-NoExpiry", "Q-TenantBudget"):
        assert forbidden not in steps, (
            "%s runs before the mint, so nothing can reach it without already holding a "
            "credential or a claim" % forbidden)


def test_the_mint_endpoint_forwards_nothing():
    text = without_comments(token_endpoint())
    assert "<TargetEndpoint>" not in text, (
        "a mint request carrying a client secret would reach the model backend")
    assert re.search(r"<RouteRule [^>]*/>", text), (
        "the documented null route is a self closing RouteRule and this endpoint has none")


def test_neither_base_path_sits_inside_the_other():
    """The reference does not say which endpoint wins, so neither overlaps."""
    paths = []
    for path in sorted((BUNDLE / "proxies").glob("*.xml")):
        found = re.search(r"<BasePath>([^<]+)</BasePath>", without_comments(
            path.read_text(encoding="utf-8")))
        assert found, "%s declares no BasePath, which defaults to a bare slash" % path.name
        paths.append(found.group(1).strip())
    assert len(paths) == len(set(paths)), "a base path has to be unique in an environment"
    for a in paths:
        for b in paths:
            if a is b:
                continue
            assert not b.startswith(a.rstrip("/") + "/"), (
                "%s sits inside %s and the reference does not say which endpoint serves a "
                "request in that case" % (b, a))


def test_both_endpoints_keep_every_control_in_their_own_preflow():
    for path in sorted((BUNDLE / "proxies").glob("*.xml")):
        text = without_comments(path.read_text(encoding="utf-8"))
        flows = re.search(r"<Flows\s*/>|<Flows>(.*)</Flows>", text, re.S)
        assert flows, "%s has no Flows block" % path.name
        body = flows.group(1) or ""
        assert "<Step>" not in body, (
            "%s puts a Step on a conditional flow, which some requests skip" % path.name)
        pre = re.search(r"<PreFlow[^>]*>(.*?)</PreFlow>", text, re.S)
        assert pre and "<Step>" in pre.group(1), (
            "%s presents no PreFlow request path, so it is not graded" % path.name)


def test_the_manifest_records_both_endpoints_and_the_null_route():
    m = json.loads(CHECK.read_text(encoding="utf-8"))
    assert m["graded"]["proxy_endpoints"] == 2, (
        "the checker read %d endpoints" % m["graded"]["proxy_endpoints"])
    assert m["graded"]["null_route_rules"] >= 1, (
        "no endpoint routes nowhere, so nothing is standing in for the mint's null route")
