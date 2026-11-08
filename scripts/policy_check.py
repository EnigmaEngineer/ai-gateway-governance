"""Grade the Apigee bundle by reading it, against defaults somebody published.

The gates this repository already has cannot see the class of defect that cost
it a rewrite of the Helm chart. `terraform validate` grades a schema, `helm
lint` grades a template, and both pass on a configuration that is valid and
wrong. An Apigee bundle is
worse than a Helm chart for this, because most of its security behaviour is
decided by elements you can leave out. A Quota with no `Distributed` counts
per message processor. A VerifyJWT with no `Issuer` verifies a signature and
nothing about who signed it. A ResponseCache with no identity in its key
serves one tenant's completion to another. All three deploy.

So every rule here is a sentence from the vendor's own page, and the sentence
travels with the finding. `apigee/semantics.py` holds the quotes and the URLs,
and a rule that cannot name a sourced default cannot be written, because
`semantics.fact` raises rather than guessing.

    python scripts/policy_check.py
    python scripts/policy_check.py --bundle apiproxy --products config/api-products.json --json out.json

Exit 0 clean, 1 with findings, 2 when there was nothing to grade. The third
one matters: a checker pointed at an empty directory that reports clean is the
defect this program found twice in October.
"""

import argparse
import json
import pathlib
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from apigee import semantics

# Policy types that decide whether a request is allowed to proceed or what it
# is allowed to spend. A Step naming one of these is a control, and where the
# Step sits decides which requests it covers.
ENFORCING = {
    "SpikeArrest",
    "Quota",
    "VerifyJWT",
    "OAuthV2",
    "ServiceCallout",
    "RaiseFault",
}

# Variables that identify the caller rather than the request. A cache key made
# only of request shaped fragments is shared across callers.
IDENTITY_HINTS = ("claim.sub", "claim.aud", "client_id", "clientid", "developer.app",
                  "access_token", "apiproduct")


class Finding:
    def __init__(self, code, where, says, fact_key=None):
        self.code = code
        self.where = where
        self.says = says
        self.fact_key = fact_key

    def as_dict(self):
        row = {"code": self.code, "where": self.where, "says": self.says}
        if self.fact_key:
            f = semantics.fact(self.fact_key)
            row["because"] = f.quote
            row["source"] = f.url
        return row


class Bundle:
    """The parts of a proxy bundle a reachability question needs.

    Parsed with the standard library. An Apigee bundle is small and ours, and
    adding a dependency to read our own files is what the render sweep avoided
    for the same reason.
    """

    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.policies = {}
        self.parse_errors = []
        for path in sorted((self.root / "policies").glob("*.xml")):
            try:
                tree = ET.parse(path)
            except ET.ParseError as exc:
                self.parse_errors.append("%s: %s" % (path.name, exc))
                continue
            el = tree.getroot()
            name = el.get("name") or path.stem
            self.policies[name] = el

        # Every endpoint, not only default.xml. The bundle grew a second one
        # for the token mint, and a checker reading one file would have graded
        # the governed path and said nothing about the path that hands out
        # credentials.
        self.endpoints = []
        for path in sorted((self.root / "proxies").glob("*.xml")):
            try:
                el = ET.parse(path).getroot()
            except ET.ParseError as exc:
                self.parse_errors.append("proxies/%s: %s" % (path.name, exc))
                continue
            self.endpoints.append({
                "file": "proxies/%s" % path.name,
                "name": el.get("name") or path.stem,
                "el": el,
            })

    @property
    def proxy(self):
        """The governed request endpoint, or None.

        Kept as a name because the rules written before there was a second
        endpoint read it, and because the precondition in main is about there
        being a request path at all.
        """
        for ep in self.endpoints:
            if ep["file"] == "proxies/default.xml":
                return ep["el"]
        return self.endpoints[0]["el"] if self.endpoints else None

    def kind(self, step_name):
        el = self.policies.get(step_name)
        return el.tag if el is not None else None

    def preflow_request_steps(self, el=None):
        """The PreFlow request steps of one endpoint, or of every endpoint.

        With no argument this is the union across endpoints, which is what a
        question like `is this policy on a request path` means now. A question
        about which requests a control covers has to name its endpoint, and
        the callers that do pass one.
        """
        if el is None:
            out = []
            for ep in self.endpoints:
                out += self.preflow_request_steps(ep["el"])
            return out
        pre = el.find("./PreFlow/Request")
        if pre is None:
            return []
        return [s.findtext("Name", "").strip() for s in pre.findall("Step")]

    def base_path(self, el):
        return (el.findtext("./HTTPProxyConnection/BasePath") or "").strip()

    def route_targets(self, el):
        """Each RouteRule with the TargetEndpoint it names, empty for a null route."""
        return [
            {"rule": r.get("name") or "",
             "target": (r.findtext("TargetEndpoint") or "").strip(),
             "condition": (r.findtext("Condition") or "").strip()}
            for r in el.findall("RouteRule")
        ]

    def oauth_operations(self, step_names):
        """The OAuthV2 Operation of each named step, for the steps that are OAuthV2."""
        out = {}
        for name in step_names:
            el = self.policies.get(name)
            if el is not None and el.tag == "OAuthV2":
                out[name] = (el.findtext("Operation") or "").strip()
        return out

    def conditional_flows(self, el=None):
        """Each conditional flow with the steps attached to it.

        The flows documentation says one conditional flow runs per segment,
        the first whose condition is true, and that its policies run only when
        that condition is true. Both halves matter. The first means a later
        flow can be unreachable. The second means anything here is skippable.
        """
        if el is None:
            el = self.proxy
        if el is None:
            return []
        out = []
        for flow in el.findall("./Flows/Flow"):
            req = flow.find("Request")
            steps = [s.findtext("Name", "").strip() for s in req.findall("Step")] if req is not None else []
            out.append({
                "name": flow.get("name") or "",
                "condition": (flow.findtext("Condition") or "").strip(),
                "steps": steps,
            })
        return out

    def steps_with_conditions(self, el=None):
        """PreFlow request steps that carry their own Condition.

        A conditioned Step inside the PreFlow is always reached and does not
        always fire, which is a different thing from a conditional flow and
        the same hazard. RF-NoExpiry is deliberately one of these, so the
        checker reports them rather than failing on them.
        """
        if el is None:
            el = self.proxy
        if el is None:
            return []
        pre = el.find("./PreFlow/Request")
        if pre is None:
            return []
        out = []
        for s in pre.findall("Step"):
            cond = (s.findtext("Condition") or "").strip()
            if cond:
                out.append({"step": s.findtext("Name", "").strip(), "condition": cond})
        return out


def text_of(el, path):
    node = el.find(path)
    return None if node is None else (node.text or "").strip()


def check_flow_placement(b):
    """Anything enforcing must be in the PreFlow and nowhere conditional."""
    findings = []
    pre = b.preflow_request_steps()

    for ep in b.endpoints:
        for flow in b.conditional_flows(ep["el"]):
            for step in flow["steps"]:
                if b.kind(step) in ENFORCING:
                    findings.append(Finding(
                        "control-in-conditional-flow",
                        "%s flow %s step %s" % (ep["file"], flow["name"], step),
                        "a control on a conditional flow runs only when that flow's condition is "
                        "true, so requests matching an earlier flow skip it",
                        "flow.conditional_runs_only_when_true",
                    ))

        # A second flow whose condition repeats an earlier one never executes,
        # so a reviewer reading it as covered is reading a dead branch. The
        # scope is one endpoint, because two endpoints are two segments.
        seen = {}
        for flow in b.conditional_flows(ep["el"]):
            cond = flow["condition"]
            if cond and cond in seen:
                findings.append(Finding(
                    "unreachable-conditional-flow",
                    "%s flow %s" % (ep["file"], flow["name"]),
                    "its condition repeats flow %s, and one conditional flow runs per segment, "
                    "so this one never executes" % seen[cond],
                    "flow.one_conditional_per_segment",
                ))
            elif cond:
                seen[cond] = flow["name"]

    for name, el in sorted(b.policies.items()):
        if el.tag in ENFORCING and name not in pre:
            findings.append(Finding(
                "control-not-in-preflow",
                "policies/%s.xml" % name,
                "a %s policy that no PreFlow step names is not on every request path" % el.tag,
                "flow.preflow_always_runs",
            ))
    return findings


def check_rate_controls(b):
    """The two policies whose configured number is not the enforced number."""
    findings = []
    for name, el in sorted(b.policies.items()):
        if el.tag == "Quota":
            if (text_of(el, "Distributed") or "false").lower() != "true":
                findings.append(Finding(
                    "quota-not-distributed",
                    "policies/%s.xml" % name,
                    "Distributed defaults to false, so the count is per message processor and "
                    "the configured budget is multiplied by however many there are",
                    "quota.distributed.default",
                ))
            if (text_of(el, "Synchronous") or "false").lower() != "true":
                findings.append(Finding(
                    "quota-not-synchronous",
                    "policies/%s.xml" % name,
                    "Synchronous defaults to false, so the counter syncs on an interval and "
                    "calls over the budget go through inside it",
                    "quota.synchronous.default",
                ))
            if el.find("Identifier") is None:
                findings.append(Finding(
                    "quota-has-no-identifier",
                    "policies/%s.xml" % name,
                    "with no Identifier one counter covers every caller, so the budget is "
                    "shared rather than per tenant",
                    "quota.identifier.absent_means",
                ))
        if el.tag == "SpikeArrest":
            if (text_of(el, "UseEffectiveCount") or "false").lower() != "true":
                findings.append(Finding(
                    "spikearrest-not-distributed",
                    "policies/%s.xml" % name,
                    "without UseEffectiveCount each message processor throttles "
                    "independently, so the configured rate is per processor",
                    "spikearrest.distributed.default",
                ))
            if el.find("Identifier") is None:
                findings.append(Finding(
                    "spikearrest-has-no-identifier",
                    "policies/%s.xml" % name,
                    "with no Identifier one rate limit covers every caller, so one noisy "
                    "tenant arrests the rest",
                    "quota.identifier.absent_means",
                ))
    return findings


def check_token_verification(b):
    """Issuer and Audience are optional, and expiry cannot be required here."""
    findings = []
    jwts = [(n, e) for n, e in sorted(b.policies.items()) if e.tag == "VerifyJWT"]
    for name, el in jwts:
        if el.find("Issuer") is None:
            findings.append(Finding(
                "jwt-no-issuer",
                "policies/%s.xml" % name,
                "Issuer is optional, so without it any token signed by the configured key "
                "passes whoever issued it",
                "verifyjwt.issuer.presence",
            ))
        if el.find("Audience") is None:
            findings.append(Finding(
                "jwt-no-audience",
                "policies/%s.xml" % name,
                "Audience is optional, so without it a token minted for another service "
                "passes here",
                "verifyjwt.audience.presence",
            ))
        for claim in el.findall("./AdditionalClaims/Claim"):
            if (claim.get("name") or "") in semantics.value("verifyjwt.additionalclaims.rejects_registered_names"):
                findings.append(Finding(
                    "jwt-registered-claim-in-additionalclaims",
                    "policies/%s.xml claim %s" % (name, claim.get("name")),
                    "AdditionalClaims refuses registered claim names, so this does not "
                    "deploy and does not check what it looks like it checks",
                    "verifyjwt.additionalclaims.rejects_registered_names",
                ))

    # The structural rule. Expiry is checked only when the claim is there, and
    # the one element that could require a claim refuses registered names. So a
    # bundle carrying VerifyJWT needs a separate refusal or it admits tokens
    # that never expire.
    if jwts:
        conditioned = []
        for ep in b.endpoints:
            conditioned += b.steps_with_conditions(ep["el"])
        guards_expiry = any(
            "exp" in c["condition"] and ("null" in c["condition"] or "!=" in c["condition"])
            for c in conditioned
        )
        if not guards_expiry:
            findings.append(Finding(
                "nothing-refuses-a-token-with-no-expiry",
                "proxies/default.xml",
                "VerifyJWT checks expiry only when the claim is present and cannot require "
                "it, so with no step testing exp a token that never expires is admitted",
                "verifyjwt.expiry.checked_only_if_present",
            ))
    return findings



def check_token_endpoint(b):
    """The mint. Three defaults, two placements, and the absence that was the gap.

    VerifyAccessToken shipped before anything that issues a token. No check in
    this repository could see that, which is the point worth keeping. Every
    other rule here reads an element that is present and asks whether its
    value is the one somebody chose. This one asks whether a step exists at
    all, and a gate that only grades what is in the file cannot ask that.
    """
    findings = []
    minting = []
    for ep in b.endpoints:
        steps = b.preflow_request_steps(ep["el"])
        ops = b.oauth_operations(steps)
        if "GenerateAccessToken" in ops.values():
            minting.append((ep, steps, ops))

    verifies = any(
        "VerifyAccessToken" in b.oauth_operations(b.preflow_request_steps(ep["el"])).values()
        for ep in b.endpoints
    )
    if verifies and not minting:
        findings.append(Finding(
            "nothing-in-the-bundle-mints-a-token",
            "proxies/",
            "a step verifies access tokens and no step generates one, so every caller has "
            "to already hold a token this bundle does not issue",
            "oauthv2.supportedgranttypes.absent_means",
        ))

    for ep, steps, ops in minting:
        for step, op in sorted(ops.items()):
            if op != "GenerateAccessToken":
                continue
            el = b.policies[step]
            if el.find("./SupportedGrantTypes/GrantType") is None:
                findings.append(Finding(
                    "mint-falls-back-to-the-default-grants",
                    "policies/%s.xml" % step,
                    "with no SupportedGrantTypes the only allowed grants are the two "
                    "interactive ones, so a service to service caller is refused and a "
                    "browser redirect flow is what deployed",
                    "oauthv2.supportedgranttypes.absent_means",
                ))
            if el.find("ExpiresIn") is None:
                findings.append(Finding(
                    "mint-lifetime-is-a-system-default",
                    "policies/%s.xml" % step,
                    "with no ExpiresIn the lifetime comes from a platform setting the "
                    "page gives no number for, so the token's expiry is not a value "
                    "anybody here chose",
                    "oauthv2.expiresin.absent_means",
                ))
            gen = el.find("GenerateResponse")
            if gen is None or (gen.get("enabled") or "true").lower() != "true":
                findings.append(Finding(
                    "mint-hands-the-caller-nothing",
                    "policies/%s.xml" % step,
                    "GenerateResponse defaults to false, so the policy populates flow "
                    "variables and returns no token, and every step still reports success",
                    "oauthv2.generateresponse.default",
                ))

        if any(op in ("VerifyAccessToken",) for op in ops.values()):
            findings.append(Finding(
                "mint-sits-behind-token-verification",
                "%s PreFlow" % ep["file"],
                "the endpoint that issues a token also verifies one on the way in, so a "
                "caller needs a token to get a token",
                "flow.preflow_always_runs",
            ))
        for step in steps:
            if b.kind(step) == "VerifyJWT":
                findings.append(Finding(
                    "mint-sits-behind-jwt-verification",
                    "%s PreFlow step %s" % (ep["file"], step),
                    "the endpoint that issues a token verifies a JWT first, so nothing can "
                    "reach the mint without already holding a credential",
                    "flow.preflow_always_runs",
                ))
        routed = [r for r in b.route_targets(ep["el"]) if r["target"]]
        if routed:
            findings.append(Finding(
                "mint-request-is-forwarded-to-a-target",
                "%s RouteRule %s" % (ep["file"], routed[0]["rule"]),
                "the mint endpoint routes to %s, so a request carrying a client secret "
                "reaches a backend that has no use for it. A null RouteRule is the "
                "documented shape" % routed[0]["target"],
                "routerule.null_forwards_nothing",
            ))
    return findings


def check_endpoints(b):
    """Two endpoints, and the two things that go wrong between them."""
    findings = []
    paths = []
    for ep in b.endpoints:
        bp = b.base_path(ep["el"])
        if not bp:
            findings.append(Finding(
                "endpoint-declares-no-base-path",
                "%s HTTPProxyConnection" % ep["file"],
                "BasePath is required and defaults to a bare slash, which puts this "
                "endpoint in front of every path in the environment",
                "proxyendpoint.basepath_unique_in_environment",
            ))
            continue
        paths.append((ep, bp))

    for i, (ep, bp) in enumerate(paths):
        for other, obp in paths[i + 1:]:
            if bp == obp:
                findings.append(Finding(
                    "endpoints-share-a-base-path",
                    "%s and %s" % (ep["file"], other["file"]),
                    "both declare %s and a base path has to be unique in an environment, "
                    "so one of them does not deploy" % bp,
                    "proxyendpoint.basepath_unique_in_environment",
                ))
            elif obp.startswith(bp.rstrip("/") + "/") or bp.startswith(obp.rstrip("/") + "/"):
                findings.append(Finding(
                    "one-base-path-is-inside-another",
                    "%s %s and %s %s" % (ep["file"], bp, other["file"], obp),
                    "the reference does not say which endpoint serves a request when one "
                    "base path is a prefix of another, so which controls run is decided "
                    "by something nobody here can read",
                    "proxyendpoint.basepath_unique_in_environment",
                ))

    names = {}
    for ep in b.endpoints:
        if ep["name"] in names:
            findings.append(Finding(
                "endpoints-share-a-name",
                "%s and %s" % (names[ep["name"]], ep["file"]),
                "a ProxyEndpoint name has to be unique within the proxy configuration",
                "proxyendpoint.name_unique_across_endpoints",
            ))
        names[ep["name"]] = ep["file"]
    return findings


def check_response_cache(b):
    """A cache key with no caller in it, and two defaults that bite."""
    findings = []
    for name, el in sorted(b.policies.items()):
        if el.tag != "ResponseCache":
            continue
        if (text_of(el, "ExcludeErrorResponse") or "false").lower() != "true":
            findings.append(Finding(
                "cache-stores-error-responses",
                "policies/%s.xml" % name,
                "ExcludeErrorResponse defaults to false, so an upstream failure is cached "
                "like a success and outlives the failure",
                "responsecache.excludeerrorresponse.default",
            ))
        key = el.find("CacheKey")
        if key is None:
            findings.append(Finding(
                "cache-has-no-key",
                "policies/%s.xml" % name,
                "CacheKey is required and absent",
                "responsecache.key_has_no_identity_by_default",
            ))
        else:
            refs = [f.get("ref") or (f.text or "") for f in key.findall("KeyFragment")]
            if not any(h in r for r in refs for h in IDENTITY_HINTS):
                findings.append(Finding(
                    "cache-key-has-no-caller-identity",
                    "policies/%s.xml" % name,
                    "the key names nothing about the caller, so two tenants sending the same "
                    "request share an entry and one reads the other's response",
                    "responsecache.key_has_no_identity_by_default",
                ))
            if key.find("Prefix") is not None and el.find("Scope") is not None:
                findings.append(Finding(
                    "cache-prefix-overrides-scope",
                    "policies/%s.xml" % name,
                    "a Prefix supersedes the Scope value, so the Scope element below it is "
                    "doing nothing while appearing to isolate the entry",
                    "responsecache.prefix_supersedes_scope",
                ))
    return findings


def check_products(path):
    """An empty product is a grant, which is the 11-01 rule in a product model."""
    findings = []
    doc = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    products = doc.get("products", [])
    for p in products:
        where = "%s product %s" % (path, p.get("name", "?"))
        if not p.get("apiProxies"):
            findings.append(Finding(
                "product-names-no-proxies",
                where,
                "a product naming no proxies lets a developer reach every proxy and every "
                "resource path, so the empty list is the widest grant available",
                "product.empty_proxy_list_grants_everything",
            ))
        paths = p.get("resourcePaths") or []
        if not paths:
            findings.append(Finding(
                "product-names-no-paths",
                where,
                "a product naming no resource paths constrains nothing",
                "product.empty_proxy_list_grants_everything",
            ))
        wide = [x for x in paths if x in ("/", "/**")]
        if wide and len(paths) > len(wide):
            findings.append(Finding(
                "product-wildcard-beats-its-siblings",
                where,
                "a less specific path takes precedence, so %s makes the other paths here "
                "decoration" % wide[0],
                "product.less_specific_wins",
            ))
    return findings, len(products)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundle", default="apiproxy")
    ap.add_argument("--products", default="config/api-products.json")
    ap.add_argument("--json", default=None, help="write the findings to a manifest")
    args = ap.parse_args()

    b = Bundle(args.bundle)
    if b.parse_errors:
        for err in b.parse_errors:
            print("could not parse %s" % err, file=sys.stderr)
        return 2

    # NothingToCheck. A run over a bundle with no policies and no proxy would
    # produce no findings, which is indistinguishable from a clean bundle.
    # This is the shape that let a broken control arm report clean once, and an
    # empty input report clean before that.
    if not b.policies or not b.endpoints:
        print("graded nothing: %d policies and %d proxy endpoints, so no verdict is available"
              % (len(b.policies), len(b.endpoints)), file=sys.stderr)
        return 2
    if not b.preflow_request_steps():
        print("no proxy endpoint has PreFlow request steps, so there is no request path "
              "to grade", file=sys.stderr)
        return 2
    bare = [ep["file"] for ep in b.endpoints if not b.preflow_request_steps(ep["el"])]
    if bare:
        print("these endpoints present no PreFlow request path, so they were not graded: %s"
              % ", ".join(bare), file=sys.stderr)
        return 2

    findings = []
    findings += check_flow_placement(b)
    findings += check_rate_controls(b)
    findings += check_token_verification(b)
    findings += check_token_endpoint(b)
    findings += check_endpoints(b)
    findings += check_response_cache(b)
    product_count = 0
    if pathlib.Path(args.products).is_file():
        pf, product_count = check_products(args.products)
        findings += pf

    graded = {
        "policies": len(b.policies),
        "enforcing_policies": sum(1 for e in b.policies.values() if e.tag in ENFORCING),
        "proxy_endpoints": len(b.endpoints),
        "preflow_request_steps": len(b.preflow_request_steps()),
        "conditional_flows": sum(len(b.conditional_flows(ep["el"])) for ep in b.endpoints),
        "conditioned_preflow_steps": sum(
            len(b.steps_with_conditions(ep["el"])) for ep in b.endpoints),
        "null_route_rules": sum(
            1 for ep in b.endpoints for r in b.route_targets(ep["el"]) if not r["target"]),
        "products": product_count,
    }

    manifest = {
        "defaults_are_edge_sourced": semantics.DEFAULTS_ARE_EDGE_SOURCED,
        "sourced_facts": len(semantics.FACTS),
        "unanswered_questions": sorted(semantics.UNANSWERED),
        "graded": graded,
        "finding_count": len(findings),
        "findings": [f.as_dict() for f in findings],
        "rules": sorted({f.code for f in findings}) or [],
    }
    if args.json:
        out = pathlib.Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    for key, value in sorted(graded.items()):
        print("  %-26s %s" % (key, value))
    if not findings:
        print("clean over %d policies and %d products" % (len(b.policies), product_count))
        return 0
    print()
    for f in findings:
        row = f.as_dict()
        print("%s" % f.code)
        print("  where   %s" % f.where)
        print("  says    %s" % f.says)
        if "because" in row:
            print("  because %s" % row["because"])
    print()
    print("%d findings" % len(findings))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
