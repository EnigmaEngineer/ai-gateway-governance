"""Apigee policy defaults, each one carried with the sentence it came from.

An earlier version of the Helm chart in this repository set three environment
variables the proxy image does not read. They were invented so a diagram would
work, and nothing here could see it, because helm lint and a rendered manifest
both pass on a name the container has never heard of. The rule that came out of
it was to read the vendor's own files before writing the configuration.

This module is that lesson with somewhere to live. Every default the checker
reasons about is here as a row, and every row carries the URL and the quoted
sentence it was read from. A rule in policy_check.py that cannot name its
source does not get written, and tests/test_semantics.py fails on a row whose
quote or URL is missing.

The source is Apigee EDGE documentation. The day's line says Apigee X. The two
share the policy engine and these are the same policies, and that is an
argument rather than a verification, so DEFAULTS_ARE_EDGE_SOURCED stays true
until somebody checks an X organisation. See docs/apigee.md.
"""

EDGE_DOCS = "https://docs.apigee.com/api-platform/reference/policies/"
EDGE_FLOWS = "https://docs.apigee.com/api-platform/fundamentals/flow-configurations"
EDGE_PRODUCTS = "https://docs.apigee.com/api-platform/publish/create-api-products"

# True while the defaults below are read from Edge documentation rather than
# confirmed against an Apigee X organisation. Nothing here can flip it, because
# flipping it needs a deployed organisation and there is no project yet.
DEFAULTS_ARE_EDGE_SOURCED = True


class Fact:
    """One documented default, and where it was read.

    `quote` is short on purpose. It is here so a reviewer can find the sentence
    on the page, not to reproduce the page.
    """

    def __init__(self, key, value, quote, url, note=""):
        self.key = key
        self.value = value
        self.quote = quote
        self.url = url
        self.note = note

    def __repr__(self):
        return "Fact(%s=%r)" % (self.key, self.value)


FACTS = [
    # Rate controls. Both of these count per message processor unless told
    # otherwise, and both read as a global number in the file.
    Fact(
        "quota.distributed.default",
        False,
        "If you use the default value of `false`, then you might exceed your quota",
        EDGE_DOCS + "quota-policy",
        "The page continues that the count for each Message Processor is not shared.",
    ),
    Fact(
        "quota.synchronous.default",
        False,
        "it is possible that some API calls exceeding the quota will go through",
        EDGE_DOCS + "quota-policy",
    ),
    Fact(
        "quota.async.sync_interval.default_seconds",
        10,
        "The default asynchronous update interval is 10 seconds.",
        EDGE_DOCS + "quota-policy",
    ),
    Fact(
        "quota.identifier.absent_means",
        "one counter for every caller",
        "the policy uses a single counter that is applied against the quota",
        EDGE_DOCS + "quota-policy",
    ),
    Fact(
        "quota.scope",
        "per proxy",
        "Quotas apply to individual API proxies and are not distributed among API proxies.",
        EDGE_DOCS + "quota-policy",
    ),
    Fact(
        "spikearrest.distributed.default",
        False,
        "each handles its own Spike Arrest throttling independently",
        EDGE_DOCS + "spike-arrest-policy",
        "To get the aggregate the page says to multiply by the number of Message Processors.",
    ),
    Fact(
        "spikearrest.is_a_smoother",
        True,
        "Spike Arrest smooths the number of full requests allowed",
        EDGE_DOCS + "spike-arrest-policy",
        "The page's worked example is 10ps, which it gives as 1 request every 100 milliseconds.",
    ),
    # Token verification. Two of the three claims a reader assumes are checked
    # are optional, and the third cannot be required at all.
    Fact(
        "verifyjwt.issuer.presence",
        "Optional",
        "Presence: Optional",
        EDGE_DOCS + "verify-jwt-policy",
    ),
    Fact(
        "verifyjwt.audience.presence",
        "Optional",
        "Presence: Optional",
        EDGE_DOCS + "verify-jwt-policy",
    ),
    Fact(
        "verifyjwt.expiry.checked_only_if_present",
        True,
        "the current time is before the token expiry, and after the token notBefore value, if they are present",
        EDGE_DOCS + "verify-jwt-policy",
        "This is the page's definition of the policy's own valid variable.",
    ),
    Fact(
        "verifyjwt.additionalclaims.rejects_registered_names",
        ("iss", "sub", "aud", "iat", "exp", "nbf", "jti"),
        "Do not use any of the registered claim names in this element.",
        EDGE_DOCS + "verify-jwt-policy",
        "So exp cannot be required through AdditionalClaims, because exp is registered.",
    ),
    Fact(
        "verifyjwt.source.default",
        "request.header.authorization",
        "By default, the JWT is retrieved from the variable request.header.authorization.",
        EDGE_DOCS + "verify-jwt-policy",
    ),
    # Caching. The key is whatever you put in it, the scope knob is overridden
    # by a sibling, and an error response is cached like any other.
    Fact(
        "responsecache.excludeerrorresponse.default",
        False,
        "by default, this policy caches HTTP responses with any possible Status code",
        EDGE_DOCS + "response-cache-policy",
        "Setting it true limits caching to status codes 200 to 205.",
    ),
    Fact(
        "responsecache.prefix_supersedes_scope",
        True,
        "If a `<Prefix>` element is present in `<CacheKey>`, it supersedes a `<Scope>` element value.",
        EDGE_DOCS + "response-cache-policy",
    ),
    Fact(
        "responsecache.scope.default",
        "Exclusive",
        "Default: Exclusive",
        EDGE_DOCS + "response-cache-policy",
    ),
    Fact(
        "responsecache.key_has_no_identity_by_default",
        True,
        "The key is often set using a value from entity headers or query params.",
        EDGE_DOCS + "response-cache-policy",
        "The page names no caller identity in the key. Its own sample adds client_id by hand.",
    ),
    Fact(
        "responsecache.attachment_limit",
        "once per direction",
        "You are limited to attaching this policy once in the proxy request flow",
        EDGE_DOCS + "response-cache-policy",
    ),
    # Flow execution. This is what makes reachability decidable by reading.
    Fact(
        "flow.preflow_always_runs",
        True,
        "Any policies attached to the PreFlow or PostFlow are always executed.",
        EDGE_FLOWS,
    ),
    Fact(
        "flow.conditional_runs_only_when_true",
        True,
        "the policies in a conditional flow are executed only if the flow's condition evaluates to true",
        EDGE_FLOWS,
    ),
    Fact(
        "flow.one_conditional_per_segment",
        True,
        "only one conditional flow is executed per segment",
        EDGE_FLOWS,
        "The first whose condition is true. A later flow with the same condition never runs.",
    ),
    # Products. An empty allow list is a grant, which is the 11-01 rule
    # arriving in somebody else's product model.
    Fact(
        "product.empty_proxy_list_grants_everything",
        True,
        "app developers will be able to access all of your API proxies and resource paths",
        EDGE_PRODUCTS,
        "The sentence is conditioned on adding no API proxies to the product.",
    ),
    Fact(
        "product.slash_covers_subpaths",
        True,
        "a resource path of / in an API product supports the base path and all subpaths",
        EDGE_PRODUCTS,
    ),
    Fact(
        "product.less_specific_wins",
        True,
        "More inclusive, less specific resource paths take predence over those that are more specific.",
        EDGE_PRODUCTS,
        "The typo is the page's. Quoted as printed so the sentence can be found.",
    ),
]

BY_KEY = {f.key: f for f in FACTS}


def fact(key):
    """Look up a default, failing loudly rather than returning a guess.

    A rule that reaches for a default this module does not carry is a rule
    reasoning from memory, which is the thing this module exists to stop.
    """
    if key not in BY_KEY:
        raise KeyError(
            "no sourced default for %r, so a rule cannot be written on it. Add a Fact "
            "with its quote and URL first." % key
        )
    return BY_KEY[key]


def value(key):
    return fact(key).value


# Questions this repository asked and could not answer from the vendor's pages.
# They are here rather than in a comment because a reviewer should be able to
# find what was unresolved without reading the audit, and because an answered
# one is a diff against this list.
UNANSWERED = {
    "responsecache.hit_skips_rest_of_request_flow": (
        "On a cache hit the page says the response returns with no request forwarded to "
        "the backend. It does not say whether the remaining request flow steps run. If "
        "they do not, a cache lookup placed before the guardrail skips it. This "
        "repository places the lookup after every control so the answer cannot matter, "
        "and the question stays open."
    ),
    "oauthv2.verifyaccesstoken_checks_product_paths": (
        "The OAuthV2 page says VerifyAccessToken verifies that the token is valid. It "
        "does not say the operation enforces the API product's resource paths. The "
        "bundle therefore does not rely on it for path authorisation."
    ),
    "flow.no_conditional_matches": (
        "The flows page does not say what happens when no conditional flow condition is "
        "true. The bundle keeps every control in the PreFlow, so no control depends on "
        "the answer."
    ),
}
