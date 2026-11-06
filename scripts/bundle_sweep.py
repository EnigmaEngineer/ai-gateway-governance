"""Break the bundle on purpose, one default at a time, and see what fires.

`policy_check.py` came back clean on the bundle the same run wrote. This
repository has scored a detector at 272 of 281 against a corpus its own author
produced, and that number measured agreement rather than detection. A checker
reporting clean on its author's bundle is the same measurement. It says the two
agree.

So each rule gets a bundle built to trip it. The mutation is the default a
careless author lands on first, which for most of these is deleting an
element rather than writing a wrong value. That is the point. Every mutation
below is a bundle that deploys.

Three things are asserted per arm, and the third is the one that matters.

1. The rule fires on its mutation. A rule that does not is dead code.
2. The rule does NOT fire on the untouched bundle. A rule that does is a
   false positive and the clean run above was luck.
3. The mutated bundle still parses and still presents a request path to
   grade. Without this an arm whose mutation broke the XML reports no
   findings, which reads exactly like a bundle with no problem. The render
   sweep's control arm failed sixteen renderings for that reason and reported
   clean, and a streaming probe once graded 152 body and arm pairs that could
   not reach the thing under test. An arm that cannot be graded FAILS here
   rather than scoring.

    python scripts/bundle_sweep.py --out measurements/apigee-sweep.json

There is also a control arm with every control stripped out at once. It
exists because a run of mutations that each trip one rule still says nothing
about a bundle that is wrong in every way, and because a checker that cannot
find anything in that bundle is broken in a way no single mutation shows.
"""

import argparse
import copy
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BUNDLE = ROOT / "apiproxy"
PRODUCTS = ROOT / "config" / "api-products.json"


def policy(bundle, name):
    return bundle / "policies" / ("%s.xml" % name)


def rewrite(path, mutate):
    """Parse, hand the root to `mutate`, write it back.

    ElementTree drops the comments, which is fine for a throwaway arm and
    would not be fine for the committed bundle. Nothing here writes into the
    repository.
    """
    tree = ET.parse(path)
    mutate(tree.getroot())
    tree.write(path, encoding="utf-8", xml_declaration=True)


def drop(el, tag):
    node = el.find(tag)
    if node is None:
        raise SystemExit("mutation wanted to remove <%s> and it was not there, so the arm "
                         "is not the mutation it claims to be" % tag)
    el.remove(node)


# Each mutation returns the rule code it is built to trip. The name is what a
# person would call the mistake, not what the checker calls the finding.
def m_quota_default_distribution(b):
    rewrite(policy(b, "Q-TenantBudget"), lambda el: drop(el, "Distributed"))
    return "quota-not-distributed"


def m_quota_async_counter(b):
    rewrite(policy(b, "Q-TenantBudget"), lambda el: drop(el, "Synchronous"))
    return "quota-not-synchronous"


def m_quota_one_counter_for_everyone(b):
    rewrite(policy(b, "Q-TenantBudget"), lambda el: drop(el, "Identifier"))
    return "quota-has-no-identifier"


def m_arrest_per_processor(b):
    rewrite(policy(b, "SA-PerClient"), lambda el: drop(el, "UseEffectiveCount"))
    return "spikearrest-not-distributed"


def m_arrest_one_limit_for_everyone(b):
    rewrite(policy(b, "SA-PerClient"), lambda el: drop(el, "Identifier"))
    return "spikearrest-has-no-identifier"


def m_jwt_any_issuer(b):
    rewrite(policy(b, "VJ-Tenant"), lambda el: drop(el, "Issuer"))
    return "jwt-no-issuer"


def m_jwt_any_audience(b):
    rewrite(policy(b, "VJ-Tenant"), lambda el: drop(el, "Audience"))
    return "jwt-no-audience"


def m_jwt_require_exp_the_obvious_way(b):
    """The mistake a careful author makes.

    Having read that expiry is checked only when present, the obvious move is
    to require it through AdditionalClaims. That element refuses registered
    claim names and exp is one, so this does not deploy. It is in the sweep
    because it is the fix somebody reaches for first.
    """
    def mutate(el):
        extra = ET.SubElement(el, "AdditionalClaims")
        ET.SubElement(extra, "Claim", {"name": "exp"})
    rewrite(policy(b, "VJ-Tenant"), mutate)
    return "jwt-registered-claim-in-additionalclaims"


def m_nothing_refuses_an_eternal_token(b):
    """Remove the step that refuses a token with no exp claim."""
    def mutate(el):
        req = el.find("./PreFlow/Request")
        for s in req.findall("Step"):
            if s.findtext("Name", "").strip() == "RF-NoExpiry":
                req.remove(s)
                return
        raise SystemExit("RF-NoExpiry was not in the PreFlow, so this arm removed nothing")
    rewrite(b / "proxies" / "default.xml", mutate)
    return "nothing-refuses-a-token-with-no-expiry"


def m_cache_errors_too(b):
    rewrite(policy(b, "RC-Completions"), lambda el: drop(el, "ExcludeErrorResponse"))
    return "cache-stores-error-responses"


def m_cache_shared_across_tenants(b):
    """Key on the request only, which is the key the vendor's example shows."""
    def mutate(el):
        key = el.find("CacheKey")
        for frag in list(key.findall("KeyFragment")):
            ref = frag.get("ref") or ""
            if "claim" in ref:
                key.remove(frag)
    rewrite(policy(b, "RC-Completions"), mutate)
    return "cache-key-has-no-caller-identity"


def m_cache_prefix_kills_scope(b):
    def mutate(el):
        key = el.find("CacheKey")
        prefix = ET.Element("Prefix")
        prefix.text = "completions"
        key.insert(0, prefix)
    rewrite(policy(b, "RC-Completions"), mutate)
    return "cache-prefix-overrides-scope"


def m_control_moved_to_a_conditional_flow(b):
    """Move the guardrail onto the chat flow, which is the plausible version.

    Somebody reads that only chat bodies need redacting and attaches the
    callout to the chat flow. Embeddings then reach the target with no
    detector, and the file reads as though the gateway is guarded.
    """
    def mutate(el):
        req = el.find("./PreFlow/Request")
        moved = None
        for s in req.findall("Step"):
            if s.findtext("Name", "").strip() == "SC-Guardrail":
                moved = s
                req.remove(s)
                break
        if moved is None:
            raise SystemExit("SC-Guardrail was not in the PreFlow, so this arm moved nothing")
        for flow in el.findall("./Flows/Flow"):
            if flow.get("name") == "chat-completions":
                flow.find("Request").append(moved)
                return
        raise SystemExit("no chat-completions flow to move the control onto")
    rewrite(b / "proxies" / "default.xml", mutate)
    return "control-in-conditional-flow"


def m_duplicate_condition(b):
    def mutate(el):
        flows = el.find("Flows")
        first = flows.find("Flow")
        clone = copy.deepcopy(first)
        clone.set("name", "chat-completions-v2")
        flows.append(clone)
    rewrite(b / "proxies" / "default.xml", mutate)
    return "unreachable-conditional-flow"


def m_product_with_no_proxies(b):
    doc = json.loads((b / "api-products.json").read_text(encoding="utf-8"))
    doc["products"][0]["apiProxies"] = []
    (b / "api-products.json").write_text(json.dumps(doc, indent=2))
    return "product-names-no-proxies"


def m_product_wildcard(b):
    doc = json.loads((b / "api-products.json").read_text(encoding="utf-8"))
    doc["products"][0]["resourcePaths"] = ["/", "/chat/completions"]
    (b / "api-products.json").write_text(json.dumps(doc, indent=2))
    return "product-wildcard-beats-its-siblings"


def m_cache_with_no_key(b):
    """Remove the key block. The policy still deploys and the entry is shared."""
    rewrite(policy(b, "RC-Completions"), lambda el: drop(el, "CacheKey"))
    return "cache-has-no-key"


def m_product_with_no_paths(b):
    doc = json.loads((b / "api-products.json").read_text(encoding="utf-8"))
    doc["products"][0]["resourcePaths"] = []
    (b / "api-products.json").write_text(json.dumps(doc, indent=2))
    return "product-names-no-paths"


def m_quota_left_out_of_the_preflow(b):
    """Delete the quota Step and keep the policy file.

    This is what a half finished edit looks like. The policy is in the bundle,
    a reviewer greps for Quota and finds it, and no request is counted.
    """
    def mutate(el):
        req = el.find("./PreFlow/Request")
        for s in req.findall("Step"):
            if s.findtext("Name", "").strip() == "Q-TenantBudget":
                req.remove(s)
                return
        raise SystemExit("Q-TenantBudget was not in the PreFlow, so this arm removed nothing")
    rewrite(b / "proxies" / "default.xml", mutate)
    return "control-not-in-preflow"


MUTATIONS = [
    ("quota counts per message processor", m_quota_default_distribution),
    ("quota counter is asynchronous", m_quota_async_counter),
    ("quota is one counter for every tenant", m_quota_one_counter_for_everyone),
    ("arrest is per message processor", m_arrest_per_processor),
    ("arrest is one limit for every tenant", m_arrest_one_limit_for_everyone),
    ("any issuer is accepted", m_jwt_any_issuer),
    ("any audience is accepted", m_jwt_any_audience),
    ("exp required through AdditionalClaims", m_jwt_require_exp_the_obvious_way),
    ("nothing refuses a token with no expiry", m_nothing_refuses_an_eternal_token),
    ("error responses are cached", m_cache_errors_too),
    ("cache is shared across tenants", m_cache_shared_across_tenants),
    ("a prefix turns off the cache scope", m_cache_prefix_kills_scope),
    ("the guardrail moved to a conditional flow", m_control_moved_to_a_conditional_flow),
    ("a second flow repeats a condition", m_duplicate_condition),
    ("a product names no proxies", m_product_with_no_proxies),
    ("a product carries a wildcard path", m_product_wildcard),
    ("the cache has no key at all", m_cache_with_no_key),
    ("a product names no resource paths", m_product_with_no_paths),
    ("the quota policy is left out of the PreFlow", m_quota_left_out_of_the_preflow),
]


def strip_every_control(b):
    """The control arm. Everything comes out at once.

    A checker that finds little here is not strict, it is broken, and no
    single mutation above would show that.
    """
    for name, tags in (
        ("Q-TenantBudget", ("Distributed", "Synchronous", "Identifier")),
        ("SA-PerClient", ("UseEffectiveCount", "Identifier")),
        ("VJ-Tenant", ("Issuer", "Audience")),
        ("RC-Completions", ("ExcludeErrorResponse",)),
    ):
        def mutate(el, tags=tags):
            for tag in tags:
                drop(el, tag)
        rewrite(policy(b, name), mutate)

    def cache_key(el):
        key = el.find("CacheKey")
        for frag in list(key.findall("KeyFragment")):
            if "claim" in (frag.get("ref") or ""):
                key.remove(frag)
    rewrite(policy(b, "RC-Completions"), cache_key)

    def proxy(el):
        req = el.find("./PreFlow/Request")
        for s in req.findall("Step"):
            if s.findtext("Name", "").strip() in ("RF-NoExpiry", "SC-Guardrail"):
                req.remove(s)
    rewrite(b / "proxies" / "default.xml", proxy)

    doc = json.loads((b / "api-products.json").read_text(encoding="utf-8"))
    for p in doc["products"]:
        p["apiProxies"] = []
    (b / "api-products.json").write_text(json.dumps(doc, indent=2))


def run_checker(bundle_dir):
    """Run policy_check.py over a bundle copy and return its manifest.

    Shelled out rather than imported so each arm gets a fresh parse and the
    exit code is the real one. The exit code is what separates an arm with no
    findings from an arm that could not be graded, and that distinction is
    the whole reason this script has a precondition check.
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        out = tmp.name
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "policy_check.py"),
         "--bundle", str(bundle_dir),
         "--products", str(bundle_dir / "api-products.json"),
         "--json", out],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    manifest = None
    path = pathlib.Path(out)
    if path.is_file() and path.stat().st_size:
        manifest = json.loads(path.read_text())
    path.unlink(missing_ok=True)
    return proc.returncode, manifest, proc.stderr.strip()


def make_copy(tmp, label):
    dst = pathlib.Path(tmp) / label
    shutil.copytree(BUNDLE, dst)
    shutil.copy(PRODUCTS, dst / "api-products.json")
    return dst


def rule_codes_in_the_checker():
    """Every finding code policy_check.py can emit, read out of its source.

    Read rather than listed, because a list here goes stale on the day a rule
    is added and the sweep would then under report its own coverage without
    saying anything.
    """
    src = (ROOT / "scripts" / "policy_check.py").read_text(encoding="utf-8")
    codes = set(re.findall(r"Finding\(\s*\n?\s*\"([a-z0-9-]+)\"", src))
    if not codes:
        raise SystemExit("found no rule codes in policy_check.py, so coverage cannot be "
                         "measured and this sweep has no verdict")
    return codes


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="measurements/apigee-sweep.json")
    args = ap.parse_args()

    arms = []
    with tempfile.TemporaryDirectory() as tmp:
        baseline_dir = make_copy(tmp, "baseline")
        base_code, base_manifest, base_err = run_checker(baseline_dir)
        if base_manifest is None:
            print("the untouched bundle could not be graded, so nothing below means "
                  "anything: %s" % base_err, file=sys.stderr)
            return 2
        baseline_codes = {f["code"] for f in base_manifest["findings"]}

        for i, (label, mutate) in enumerate(MUTATIONS):
            d = make_copy(tmp, "arm%02d" % i)
            expected = mutate(d)
            code, manifest, err = run_checker(d)

            arm = {"mutation": label, "expects": expected}
            # Precondition. Exit 2 is the checker saying it had nothing to
            # grade, which is what a mutation that broke the bundle produces.
            # An arm in that state is not a measurement.
            if code == 2 or manifest is None:
                arm["gradeable"] = False
                arm["why_not"] = err or "the checker returned %d with no manifest" % code
                arms.append(arm)
                continue
            if manifest["graded"]["preflow_request_steps"] == 0:
                arm["gradeable"] = False
                arm["why_not"] = "the mutated bundle presents no request path"
                arms.append(arm)
                continue

            found = {f["code"] for f in manifest["findings"]}
            arm["gradeable"] = True
            arm["fired"] = expected in found
            arm["findings"] = sorted(found)
            arm["only_the_expected_rule"] = found == {expected}
            arms.append(arm)

        control_dir = make_copy(tmp, "control")
        strip_every_control(control_dir)
        ccode, cmanifest, cerr = run_checker(control_dir)
        if cmanifest is None:
            print("the control arm could not be graded, so it is not a control: %s" % cerr,
                  file=sys.stderr)
            return 2
        control_codes = sorted({f["code"] for f in cmanifest["findings"]})

    ungradeable = [a for a in arms if not a["gradeable"]]
    if ungradeable:
        for a in ungradeable:
            print("arm %r could not be graded: %s" % (a["mutation"], a["why_not"]),
                  file=sys.stderr)
        print("%d of %d arms were not measurements, so the sweep is not reporting a result"
              % (len(ungradeable), len(arms)), file=sys.stderr)
        return 1

    dead = [a["expects"] for a in arms if not a["fired"]]
    every_rule = rule_codes_in_the_checker()
    exercised = {a["expects"] for a in arms if a["fired"]}
    unexercised = sorted(every_rule - {a["expects"] for a in arms})
    # A rule the baseline already reports cannot be credited to its mutation.
    false_positives = sorted(baseline_codes)

    manifest = {
        "baseline_exit": base_code,
        "baseline_findings": sorted(baseline_codes),
        "arms_total": len(arms),
        "arms_gradeable": len(arms) - len(ungradeable),
        "rules_exercised": len({a["expects"] for a in arms if a["fired"]}),
        "rules_in_the_checker": len(every_rule),
        "rules_dead": sorted(dead),
        "rules_no_arm_exercises": unexercised,
        "rules_firing_on_the_untouched_bundle": false_positives,
        "arms_tripping_only_their_own_rule": sum(1 for a in arms if a.get("only_the_expected_rule")),
        "control_finding_codes": control_codes,
        "control_finding_count": cmanifest["finding_count"],
        "arms": arms,
    }
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    print("baseline      %d findings, exit %d" % (len(baseline_codes), base_code))
    print("arms          %d, all gradeable" % len(arms))
    print("rules fired   %d of %d in the checker" % (manifest["rules_exercised"], len(every_rule)))
    print("only its own  %d of %d" % (manifest["arms_tripping_only_their_own_rule"], len(arms)))
    print("control       %d findings over %d distinct rules"
          % (cmanifest["finding_count"], len(control_codes)))
    if dead:
        print()
        print("these rules did not fire on a bundle built to trip them:")
        for code in dead:
            print("  %s" % code)
    if unexercised:
        print()
        print("these rules have no arm built to trip them, so they are unmeasured:")
        for code in unexercised:
            print("  %s" % code)
    print("written to %s" % out)
    return 1 if (dead or unexercised) else 0


if __name__ == "__main__":
    raise SystemExit(main())
