"""The contexts, the published probe, and one real round trip over mutual TLS.

The context checks grade configuration and need no certificates, so they run
on a clone with nothing in it. The round trip needs a key pair and private
keys are never committed here, so it makes its own with openssl into a
temporary directory. That is better than skipping, because it travels to any
machine with openssl on it, and the one case it cannot cover prints a line
naming what is missing rather than passing quietly.
"""

import json
import shutil
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from gateway import app, mtls, upstream

ROOT = Path(__file__).resolve().parents[1]
MAKE_CERTS = ROOT / "scripts" / "make_certs.py"


def test_describe_reads_the_settings_back_off_the_context():
    """The suite has to grade configuration on a clone with no certificates,
    so the manifest carries what a context was set to rather than a handshake."""
    described = mtls.describe(ssl.create_default_context(ssl.Purpose.SERVER_AUTH))
    assert described == {"verify_mode": "CERT_REQUIRED", "check_hostname": True,
                         "minimum_version": "TLSv1_2"}


def test_the_default_purpose_for_a_server_does_not_verify_the_client():
    """Measured rather than asserted from the docs, because it is the finding.

    ssl.Purpose.CLIENT_AUTH is the purpose you build a server with and it
    leaves verify_mode at CERT_NONE. The same call for a client gives
    CERT_REQUIRED, so the asymmetry is invisible at the call site.
    """
    server_side = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    client_side = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    assert server_side.verify_mode == ssl.CERT_NONE
    assert client_side.verify_mode == ssl.CERT_REQUIRED
    assert server_side.check_hostname is False
    assert mtls.DEFAULT_SERVER_VERIFY_MODE_IS_NONE


def test_the_published_probe_refused_and_admitted_something(manifests):
    """Every arm refused is also what a probe pointed at a dead port looks like."""
    m = manifests["mtls"]
    assert m["arms_answered"] >= 1
    assert m["arms_refused"] >= 1
    assert m["arms_as_expected"] == m["arms_total"]


def test_the_probe_recorded_the_client_identity(manifests):
    """A backend behind mutual TLS that cannot say who called gained nothing."""
    callers = manifests["mtls"]["callers_the_backend_recorded"]
    assert callers.get("ai-gateway", 0) >= 1


def test_the_two_no_certificate_arms_differ_only_in_the_servers_verify_mode(manifests):
    m = manifests["mtls"]
    assert m["hardened_server_context"]["verify_mode"] == "CERT_REQUIRED"
    assert m["default_server_context"]["verify_mode"] == "CERT_NONE"
    arms = {a["arm"]: a for a in m["arms"]}
    hard = arms["hardened backend, no client certificate"]
    soft = arms["default server context, no client certificate"]
    assert hard["exchange"] == "refused"
    assert soft["exchange"] == "answered"
    assert soft["caller"] is None, "the permissive arm should have no identity"


def test_a_completed_handshake_is_not_evidence_the_client_was_accepted(manifests):
    """Under TLS 1.3 the server's verdict on a client certificate arrives after
    wrap_socket has already returned, so a probe reading the handshake alone
    reports a refusal as a success."""
    m = manifests["mtls"]
    late = m["handshake_completed_but_exchange_refused"]
    assert late, "no arm showed the split, so this claim is not measured here"
    for arm in m["arms"]:
        if arm["arm"] in late:
            assert arm["handshake"] == "completed"
            assert arm["exchange"] == "refused"


def test_the_client_rejects_the_server_during_the_handshake(manifests):
    """The other half of the asymmetry. A client side verification failure does
    surface at wrap_socket, which is why reading only the handshake catches
    some of these and not the ones that matter here."""
    arms = {a["arm"]: a for a in manifests["mtls"]["arms"]}
    distrust = arms["hardened backend, client does not trust the backend's CA"]
    assert distrust["handshake"] == "refused"


def test_the_client_context_verifies_the_backend_and_checks_its_name(manifests):
    described = manifests["mtls"]["client_context"]
    assert described["verify_mode"] == "CERT_REQUIRED"
    assert described["check_hostname"] is True
    assert described["minimum_version"] == "TLSv1_2"


@pytest.fixture
def certs(tmp_path):
    if not shutil.which("openssl"):
        pytest.skip("openssl is not on PATH, so the round trip cannot make a key pair")
    out = tmp_path / "tls"
    done = subprocess.run([sys.executable, "-I", str(MAKE_CERTS), "--out", str(out)],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return out


def _serve(context):
    server = upstream.serve("127.0.0.1", 0, context=context, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def _serve_gateway(settings):
    from http.server import ThreadingHTTPServer
    previous = app.SETTINGS
    app.SETTINGS = settings
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1], previous


def test_the_gateway_reaches_the_backend_over_mutual_tls(certs):
    """The whole hop, through the real handler and the real pool.

    Not the probe's raw sockets. This is the path a request takes.
    """
    ca = str(certs / "ca.crt")
    server_ctx = mtls.server_context(ca, str(certs / "backend.crt"),
                                     str(certs / "backend.key"))
    client_ctx = mtls.client_context(ca, str(certs / "gateway.crt"),
                                     str(certs / "gateway.key"))
    upstream.reset_counters()
    backend, backend_port = _serve(server_ctx)
    settings = app.Settings(upstream="https://localhost:%d/" % backend_port,
                            upstream_context=client_ctx, attempt_timeout=5.0)
    gateway, gateway_port, previous = _serve_gateway(settings)
    try:
        request = urllib.request.Request(
            "http://127.0.0.1:%d/v2/chat" % gateway_port,
            data=json.dumps({"text": "follow up in two weeks",
                             "max_output_tokens": 32}).encode("utf-8"),
            headers={"content-type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read())
        assert body["action"] == "pass"
        assert body["usage"]["total_tokens"] > 0
        assert upstream.callers().get("ai-gateway", 0) == 1, upstream.callers()
    finally:
        app.SETTINGS = previous
        gateway.shutdown()
        gateway.server_close()
        backend.shutdown()
        backend.server_close()


def test_the_gateway_without_a_client_certificate_cannot_reach_the_backend(certs):
    """The control for the test above. Same backend, same request, no identity."""
    ca = str(certs / "ca.crt")
    server_ctx = mtls.server_context(ca, str(certs / "backend.crt"),
                                     str(certs / "backend.key"))
    anonymous = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=ca)
    upstream.reset_counters()
    backend, backend_port = _serve(server_ctx)
    settings = app.Settings(upstream="https://localhost:%d/" % backend_port,
                            upstream_context=anonymous, attempt_timeout=5.0)
    gateway, gateway_port, previous = _serve_gateway(settings)
    try:
        request = urllib.request.Request(
            "http://127.0.0.1:%d/v2/chat" % gateway_port,
            data=json.dumps({"text": "follow up in two weeks",
                             "max_output_tokens": 32}).encode("utf-8"),
            headers={"content-type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        assert caught.value.code == 502
        assert upstream.callers().get("ai-gateway") is None
    finally:
        app.SETTINGS = previous
        gateway.shutdown()
        gateway.server_close()
        backend.shutdown()
        backend.server_close()


def test_server_context_turns_client_verification_on_and_off(certs):
    """Both ends of the switch the two no certificate arms rest on."""
    ca = str(certs / "ca.crt")
    cert = str(certs / "backend.crt")
    key = str(certs / "backend.key")
    assert mtls.server_context(ca, cert, key).verify_mode == ssl.CERT_REQUIRED
    permissive = mtls.server_context(ca, cert, key, require_client_cert=False)
    assert permissive.verify_mode == ssl.CERT_NONE
