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
EDGE_PROXY_REF = "https://docs.apigee.com/api-platform/reference/api-proxy-configuration-reference"

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
    Fact(
        "verifyjwt.decoded_claim_variable",
        "jwt.{policy_name}.decoded.claim.{name}",
        "if the policy name is `jwt-parse-token` , then the policy will store the "
        "subject specified in the JWT to the context variable named "
        "`jwt.jwt-parse-token.decoded.claim.sub`",
        EDGE_DOCS + "verify-jwt-policy",
        "Singular claim, and the last segment is the raw JWT claim name. The "
        "page's other example is decoded.claim.iat. This was a pattern match on "
        "the jwt.POLICYNAME prefix for one day before anybody read it.",
    ),
    Fact(
        "verifyjwt.legacy_claim_variable_renames_registered_claims",
        "jwt.{policy_name}.claim.expiry",
        "The expiration date/time, expressed in milliseconds since epoch.",
        EDGE_DOCS + "verify-jwt-policy",
        "The older claim family is not a shorter alias. It renames the "
        "registered claims, so the legacy spelling of exp is claim.expiry and "
        "of sub is claim.subject. Its times are milliseconds where the decoded "
        "family is seconds, so swapping the names moves a figure by a thousand.",
    ),
    Fact(
        "oauthv2.supportedgranttypes.absent_means",
        "authorization_code and implicit",
        "If no supported grant types are specified, then the only allowed grant types "
        "are `authorization_code` and `implicit`.",
        EDGE_DOCS + "oauthv2-policy",
        "Presence is Required on the page and the page still names a default, so a "
        "GenerateAccessToken policy written without the element mints tokens for two "
        "interactive grants and refuses the one a service caller uses.",
    ),
    Fact(
        "oauthv2.granttype.ref.default",
        "request.formparam.grant_type",
        "Tells the policy where to find the grant type parameter that is passed in a request.",
        EDGE_DOCS + "oauthv2-policy",
        "The page's own example reads it from request.queryparam.grant_type instead, so "
        "the element is doing work even when it is set to its default.",
    ),
    Fact(
        "oauthv2.clientid.only_supported_variable",
        "request.formparam.client_id",
        "Setting `ClientId` to any other variable is not supported.",
        EDGE_DOCS + "oauthv2-policy",
    ),
    Fact(
        "oauthv2.expiresin.units",
        "milliseconds",
        "Enforces the expiry time of access tokens and authorization codes in milliseconds.",
        EDGE_DOCS + "oauthv2-policy",
        "The response field is seconds. The page says the value is in milliseconds and "
        "that expires_in is expressed in seconds, two sentences apart. Same factor of a "
        "thousand as the legacy claim family.",
    ),
    Fact(
        "oauthv2.expiresin.absent_means",
        "a system level default with no published number",
        "If `<ExpiresIn>` is not specified, the system applies a default value configured "
        "at the system level.",
        EDGE_DOCS + "oauthv2-policy",
        "So a token endpoint shipped without the element has a lifetime this repository "
        "cannot state. A value of -1 takes the platform maximum and reports expires_in as "
        "0, and the page says any other negative integer or zero causes a deployment error.",
    ),
    Fact(
        "oauthv2.generateresponse.default",
        False,
        "If set to `true`, the policy generates and returns a response.",
        EDGE_DOCS + "oauthv2-policy",
        "Default false. With it off the policy populates flow variables and hands the "
        "caller nothing, so a mint that worked looks like a mint that did nothing.",
    ),
    Fact(
        "oauthv2.generateerrorresponse.default",
        False,
        "If set to `true`, the policy generates and returns a response if the "
        "ContinueOnError attribute is set to true.",
        EDGE_DOCS + "oauthv2-policy",
    ),
    Fact(
        "oauthv2.externalauthorization.default",
        False,
        "If this element is false or not present, then Edge validates the client_id and "
        "client_secret normally against the Apigee Edge authorization store.",
        EDGE_DOCS + "oauthv2-policy",
        "Left absent on purpose. The alternative hands validation to something this "
        "repository does not build, and the page says StoreToken has to be true with it.",
    ),
    Fact(
        "oauthv2.token_type_is_not_the_rfc_value",
        "BearerToken",
        "Apigee Edge returns the value BearerToken, which is not RFC-compliant.",
        EDGE_DOCS + "oauthv2-policy",
        "The vendor says so on its own page. RFC 6750 section 4 spells the type Bearer. "
        "A client matching token_type exactly against the RFC value refuses a token the "
        "platform just minted, and nothing in a bundle can change the field.",
    ),
    Fact(
        "proxyendpoint.name_unique_across_endpoints",
        True,
        "Must be unique within the API proxy configuration, when (in rare cases) multiple "
        "ProxyEndpoints are defined.",
        EDGE_PROXY_REF,
        "The page acknowledges more than one endpoint per bundle without recommending it.",
    ),
    Fact(
        "proxyendpoint.basepath_unique_in_environment",
        True,
        "BasePath must be unique within an environment.",
        EDGE_PROXY_REF,
    ),
    Fact(
        "routerule.null_forwards_nothing",
        True,
        "A null RouteRule can be defined to support scenarios in which the request "
        "message does not need to be forwarded to the TargetEndpoint.",
        EDGE_PROXY_REF,
        "The page's example is <RouteRule name=\"GoNowhere\"/>. It says the request is "
        "not forwarded and does not say how the response is produced, which is why the "
        "mint policy sets GenerateResponse rather than relying on the route.",
    ),
    Fact(
        "apiproxy.proxyendpoints_is_a_manifest",
        "manifest",
        "This is simply a 'manifest' setting",
        EDGE_PROXY_REF,
        "The page says you normally only see the element on a proxy created in the "
        "management UI. It does not say whether an endpoint deploys without being named.",
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
    "oauthv2.client_secret_source": (
        "The OAuthV2 page gives ClientId an element, a default of "
        "request.formparam.client_id and a sentence saying any other variable is "
        "unsupported. There is no element for the client secret and no sentence naming "
        "where it is read from. The only mention is in the invalid_client fault row, "
        "which says these values are typically sent as a Base64 encoded Basic "
        "Authorization header. Typically is not a specification, so OA-GenerateToken "
        "adds no BasicAuthentication policy on the strength of it. This is the first "
        "gap here that is an absence in the documentation of a required input rather "
        "than an absence in the configuration."
    ),
    "oauthv2.generateaccesstoken_success_status": (
        "The page shows the JSON body GenerateResponse produces and never gives the HTTP "
        "status of a successful mint, and never says whether a target is reached. The "
        "token endpoint therefore carries a null RouteRule, so the answer to the second "
        "half cannot matter."
    ),
    "oauthv2.generateaccesstoken_needs_a_product": (
        "The page does not say whether the app a mint is for has to be associated with an "
        "API product, or what happens when it is not. api_product_list is documented as "
        "the products associated with the token's developer app, and the no product match "
        "fault is documented under verification rather than minting."
    ),
    "product.resource_path_with_two_base_paths": (
        "A product names proxies and resource paths, and this proxy now has two base "
        "paths. The products page says a resource path of / supports the base path and "
        "all subpaths, in the singular. It does not say which base path a resource path "
        "is relative to when an endpoint has more than one. Splitting the mint onto its "
        "own endpoint to keep every control in a PreFlow moved this ambiguity into the "
        "product model, and the mitigation is that no product here names the mint path "
        "and nothing on that endpoint verifies a token."
    ),
    "apiproxy.second_endpoint_must_be_listed": (
        "The configuration reference calls ProxyEndpoints a manifest setting normally "
        "seen only on a proxy built in the management UI. It does not say whether a "
        "second endpoint deploys when the element does not name it. The bundle names "
        "both, which is the reading that cannot be wrong in the direction that matters."
    ),
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
