"""Check that the hop to the backend refuses everything it should refuse.

An arm showing a valid client getting through says nothing on its own. What
makes mutual TLS a control rather than a decoration is the set of connections
it turns away, and every one of those refusals has to be shown next to a
control that got through for the same reason reversed. So the no certificate
arm runs twice, once against a hardened backend and once against a backend
built the way the standard library's own constructor leaves it.

    python3 scripts/make_certs.py --out tls
    python3 scripts/mtls_probe.py --tls tls --out measurements/mtls.json

The handshake and the exchange are recorded separately on purpose. They do not
always agree, and which one a probe reads decides whether it believes mutual
TLS is on.
"""

import argparse
import json
import socket
import ssl
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gateway import mtls, upstream  # noqa: E402

PROMPT = "summarise the visit note for the care team"
BODY = json.dumps({"prompt": PROMPT, "max_output_tokens": 32}).encode("utf-8")


def request_bytes(host):
    return (b"POST / HTTP/1.0\r\n"
            b"host: " + host.encode("ascii") + b"\r\n"
            b"content-type: application/json\r\n"
            b"content-length: " + str(len(BODY)).encode("ascii") + b"\r\n"
            b"\r\n" + BODY)


def start(context, threaded=True):
    server = upstream.serve("127.0.0.1", 0, context=context, threaded=threaded)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def exchange(port, context, server_hostname):
    """One connection, reported in two halves.

    `handshake` is whether wrap_socket returned. `exchange` is whether a
    request got an answer back. Under TLS 1.3 the server's verdict on a client
    certificate can arrive after the handshake has already returned, so a
    probe that stops at the first half reads a refusal as a success.

    `failure` is the exception class and it is NOT stable. A re-measure ran
    the same seven arms on the same machine and two leaf values moved out of
    86. Both are this field, on the two arms where the server rejects the
    client. Both went from SSLError to SSLEOFError. That is
    the late alert above arriving as a close rather than as an alert record,
    and which one lands first is a race. So the field is kept for a reader and
    `refused_after_the_handshake` is what a check reads, because a figure a
    manifest publishes has to re-derive and a class name here does not.
    """
    out = {"handshake": None, "exchange": None, "failure": None,
           "refused_after_the_handshake": None,
           "status": None, "caller": None}
    try:
        raw = socket.create_connection(("127.0.0.1", port), timeout=5)
    except OSError as exc:
        out["handshake"] = "refused"
        out["failure"] = type(exc).__name__
        return out
    try:
        tls = context.wrap_socket(raw, server_hostname=server_hostname)
    except ssl.SSLError as exc:
        out["handshake"] = "refused"
        out["exchange"] = "refused"
        out["failure"] = type(exc).__name__
        raw.close()
        return out
    except OSError as exc:
        out["handshake"] = "refused"
        out["exchange"] = "refused"
        out["failure"] = type(exc).__name__
        raw.close()
        return out
    out["handshake"] = "completed"
    out["negotiated"] = tls.version()
    try:
        tls.sendall(request_bytes(server_hostname))
        raw_response = b""
        while True:
            piece = tls.recv(4096)
            if not piece:
                break
            raw_response += piece
    except (ssl.SSLError, OSError) as exc:
        out["exchange"] = "refused"
        out["failure"] = type(exc).__name__
        return out
    finally:
        try:
            tls.close()
        except OSError:
            pass
    if not raw_response:
        out["exchange"] = "refused"
        out["failure"] = "the backend closed without answering"
        return out
    head, _, payload = raw_response.partition(b"\r\n\r\n")  # noqa: E501
    out["exchange"] = "answered"
    out["status"] = int(head.split(b" ")[1])
    try:
        out["caller"] = json.loads(payload).get("caller")
    except ValueError:
        out["caller"] = None
    return out


def with_stable_refusal(out):
    """Add the field a check is allowed to read.

    True means the TLS handshake returned and the request was still refused,
    which is the server rejecting the client. False means the refusal arrived
    at the handshake, which is the client rejecting the server. None means
    nothing was refused.
    """
    if out["exchange"] == "refused":
        out["refused_after_the_handshake"] = out["handshake"] == "completed"
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tls", default="tls", help="directory make_certs wrote")
    ap.add_argument("--out", default="measurements/mtls.json")
    args = ap.parse_args()

    d = Path(args.tls)
    needed = ["ca.crt", "backend.crt", "backend.key", "gateway.crt",
              "gateway.key", "rogue-ca.crt", "rogue-client.crt", "rogue-client.key"]
    missing = [n for n in needed if not (d / n).exists()]
    if missing:
        raise SystemExit("run scripts/make_certs.py first, missing: %s" % missing)

    ca = str(d / "ca.crt")
    rogue_ca = str(d / "rogue-ca.crt")

    hardened = mtls.server_context(ca, str(d / "backend.crt"), str(d / "backend.key"))
    # What ssl.create_default_context(Purpose.CLIENT_AUTH) leaves you with.
    # This is the control arm, not a deployment option.
    permissive = mtls.server_context(ca, str(d / "backend.crt"),
                                     str(d / "backend.key"),
                                     require_client_cert=False)

    good_client = mtls.client_context(ca, str(d / "gateway.crt"), str(d / "gateway.key"))
    rogue_client = mtls.client_context(ca, str(d / "rogue-client.crt"),
                                       str(d / "rogue-client.key"))
    distrusting = mtls.client_context(rogue_ca, str(d / "gateway.crt"),
                                      str(d / "gateway.key"))
    unchecked_name = mtls.client_context(ca, str(d / "gateway.crt"),
                                         str(d / "gateway.key"),
                                         check_hostname=False)

    def no_client_cert(cafile):
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=cafile)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        return context

    upstream.reset_counters()
    hard_server, hard_port = start(hardened)
    soft_server, soft_port = start(permissive)

    arms = []
    try:
        plan = [
            ("hardened backend, the gateway's own certificate",
             hard_port, good_client, "localhost", "answered"),
            ("hardened backend, no client certificate",
             hard_port, no_client_cert(ca), "localhost", "refused"),
            ("hardened backend, a valid certificate from an unrelated CA",
             hard_port, rogue_client, "localhost", "refused"),
            ("hardened backend, client does not trust the backend's CA",
             hard_port, distrusting, "localhost", "refused"),
            ("hardened backend, a name the backend certificate does not carry",
             hard_port, good_client, "backend.invalid", "refused"),
            ("hardened backend, the same wrong name with the check turned off",
             hard_port, unchecked_name, "backend.invalid", "answered"),
            ("default server context, no client certificate",
             soft_port, no_client_cert(ca), "localhost", "answered"),
        ]
        for label, port, context, name, expected in plan:
            result = with_stable_refusal(exchange(port, context, name))
            result["arm"] = label
            result["server_hostname_sent"] = name
            result["expected"] = expected
            result["as_expected"] = result["exchange"] == expected
            arms.append(result)
    finally:
        for server in (hard_server, soft_server):
            server.shutdown()
            server.server_close()

    by_label = {a["arm"]: a for a in arms}
    answered = [a for a in arms if a["exchange"] == "answered"]
    refused = [a for a in arms if a["exchange"] == "refused"]

    manifest = {
        "arms": arms,
        "arms_total": len(arms),
        "arms_answered": len(answered),
        "arms_refused": len(refused),
        "arms_as_expected": sum(1 for a in arms if a["as_expected"]),
        "callers_the_backend_recorded": upstream.callers(),
        "client_context": mtls.describe(good_client),
        "hardened_server_context": mtls.describe(hardened),
        "default_server_context": mtls.describe(permissive),
        "handshake_completed_but_exchange_refused": sorted(
            a["arm"] for a in arms
            if a["handshake"] == "completed" and a["exchange"] == "refused"),
        "refusals_after_the_handshake": sum(
            1 for a in arms if a.get("refused_after_the_handshake") is True),
        "refusals_at_the_handshake": sum(
            1 for a in arms if a.get("refused_after_the_handshake") is False),
        "failure_class_is_not_stable": True,
        "note": (
            "Every refusal is reported beside a control that got through for "
            "the mirrored reason. The two no certificate arms differ only in "
            "the server's verify_mode, and the two wrong name arms differ only "
            "in the client's check_hostname. The failure field on each arm is "
            "the exception class and it moved on a re-measure without "
            "any arm changing its verdict, so a check reads "
            "refused_after_the_handshake instead."),
    }

    problems = check(manifest, by_label)
    if problems:
        for line in problems:
            sys.stderr.write("precondition: %s\n" % line)
        raise SystemExit("the probe did not grade what it claims to grade")

    out = Path(args.out)
    out.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("wrote %s" % out)

    print("\n  %-62s %-10s %-9s %s" % ("arm", "handshake", "exchange", "detail"))
    for a in arms:
        detail = a["failure"] or ("caller %s" % a["caller"])
        print("  %-62s %-10s %-9s %s" % (a["arm"][:62], a["handshake"],
                                         a["exchange"], detail))
    print("\ncallers the backend recorded: %s" % json.dumps(manifest[
        "callers_the_backend_recorded"], sort_keys=True))
    print("handshake completed and the exchange still refused: %d of %d arms"
          % (len(manifest["handshake_completed_but_exchange_refused"]), len(arms)))
    return 0


def check(manifest, by_label):
    """Refuse to publish a run whose refusals could be a broken harness.

    Every arm getting turned away is also what a probe pointed at a dead port
    looks like, so the controls are preconditions rather than extra rows.
    """
    problems = []
    for arm in manifest["arms"]:
        if not arm["as_expected"]:
            problems.append("%s expected to be %s and was %s"
                            % (arm["arm"], arm["expected"], arm["exchange"]))
    good = by_label["hardened backend, the gateway's own certificate"]
    if good["caller"] != "ai-gateway":
        problems.append("the hardened backend recorded the caller as %r, so the "
                        "client identity did not arrive" % good["caller"])
    if manifest["arms_answered"] < 1:
        problems.append("nothing got through, so every refusal here is also "
                        "what a broken probe looks like")
    if manifest["arms_refused"] < 1:
        problems.append("nothing was refused, so mutual TLS is not on")
    hardened = manifest["hardened_server_context"]
    if hardened["verify_mode"] != "CERT_REQUIRED":
        problems.append("the hardened server context is %s"
                        % hardened["verify_mode"])
    if manifest["default_server_context"]["verify_mode"] != "CERT_NONE":
        problems.append("the control server context is not the default any "
                        "more, so the two no certificate arms differ in "
                        "something else as well")
    recorded = manifest["callers_the_backend_recorded"]
    if "no client certificate" not in recorded:
        problems.append("the permissive backend never served an anonymous "
                        "caller, so its arm proved nothing")
    return problems


if __name__ == "__main__":
    raise SystemExit(main())
