"""Version routing through the real handler, over a socket.

The registry is graded in test_versioning.py. What is here is whether the
handler consults it, which is a different question and the one that decides
whether an unlisted version gets the current version's behaviour.
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from gateway import app, versioning

CLEAN = "please summarise the admission note for the care team"


@pytest.fixture(scope="module")
def gateway():
    previous = app.SETTINGS
    app.SETTINGS = app.Settings()
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    app.SETTINGS = previous
    server.shutdown()
    server.server_close()


def call(port, path, payload=None):
    """Status, headers and body. The headers are the point of this file."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path), data=data,
        headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), json.loads(exc.read() or b"{}")


def test_an_unlisted_version_is_refused_rather_than_served(gateway):
    """The reason the registry is an allow list. A prefix rule would route this."""
    for path in ("/v3/chat", "/v9/guard", "/v0/policy"):
        status, _, body = call(gateway, path, {"text": CLEAN,
                                               "max_output_tokens": 32})
        assert status == 404, path
        assert body["versions"] == sorted(versioning.VERSIONS)
        assert body["current"] == versioning.CURRENT


def test_a_path_with_no_version_at_all_is_refused(gateway):
    """Guessing which version somebody meant pins a client to whatever shipped."""
    status, _, body = call(gateway, "/chat", {"text": CLEAN,
                                              "max_output_tokens": 32})
    assert status == 404
    assert "versions" in body


def test_the_deprecated_version_answers_and_says_it_is_going_away(gateway):
    status, headers, _ = call(gateway, "/v1/policy")
    assert status == 200
    assert headers["deprecation"] == "@%d" % versioning.epoch("2026-10-01T00:00:00Z")
    assert headers["sunset"] == "Thu, 01 Apr 2027 00:00:00 GMT"
    assert 'rel="deprecation"' in headers["link"]


def test_the_current_version_carries_no_deprecation_fields(gateway):
    status, headers, _ = call(gateway, "/v2/policy")
    assert status == 200
    assert "deprecation" not in headers
    assert "sunset" not in headers


def test_the_version_does_not_leak_from_one_request_to_the_next(gateway):
    """A handler instance can serve more than one request, so the field is
    cleared on every route rather than left from the last one."""
    call(gateway, "/v1/policy")
    _, headers, _ = call(gateway, "/v2/policy")
    assert "deprecation" not in headers


def test_the_health_check_does_not_move_with_the_api_version(gateway):
    """A health check behind a version is one nobody can configure once."""
    status, headers, body = call(gateway, "/healthz")
    assert status == 200 and body["ok"] is True
    assert "deprecation" not in headers


def test_v2_refuses_a_request_that_declares_no_ceiling(gateway):
    """The breaking change the two versions exist for. An omitted ceiling was
    charged against a default the caller never chose."""
    status, _, body = call(gateway, "/v2/chat", {"text": CLEAN})
    assert status == 400
    assert "max_output_tokens" in body["error"]
    assert str(app.DEFAULT_OUTPUT_CEILING) in body["v1_behaviour"]


def test_v1_still_applies_the_default_for_the_callers_built_against_it(gateway):
    status, headers, body = call(gateway, "/v1/chat", {"text": CLEAN})
    assert status == 200
    assert body["action"] == "pass"
    assert "deprecation" in headers


def test_v2_accepts_the_same_request_with_the_ceiling_declared(gateway):
    status, _, body = call(gateway, "/v2/chat", {"text": CLEAN,
                                                 "max_output_tokens": 32})
    assert status == 200
    assert body["action"] == "pass"


def test_the_streaming_path_carries_the_version_fields_too(gateway):
    """Those headers go out through send_header rather than the json helper,
    so they are a second place the version has to be applied."""
    request = urllib.request.Request(
        "http://127.0.0.1:%d/v1/guard/stream" % gateway,
        data=json.dumps({"text": CLEAN}).encode("utf-8"),
        headers={"content-type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        headers = dict(response.headers)
        response.read()
    assert headers["deprecation"].startswith("@")
    assert headers["x-guardrail-holdback-chars"]


def test_an_unknown_path_under_a_known_version_is_still_a_refusal(gateway):
    status, _, body = call(gateway, "/v2/nope", {"text": CLEAN})
    assert status == 404
    assert body["error"] == "no such path"
