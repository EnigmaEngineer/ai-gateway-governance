"""A front door for the guardrail, on the standard library only.

No web framework here. The thing worth getting right is the decision, and a
dependency that has to be installed before anything can be graded makes the
suite unrunnable on a fresh clone. The handler is small enough
to replace with whatever the deployment already runs.

    python -m gateway.app --port 8080
    curl -s localhost:8080/v1/guard -d '{"text":"SSN 123-45-6789"}'
"""

import argparse
import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer

from .guardrail import apply
from .policy import DECLARED_OMISSIONS, ENFORCED, SAFE_HARBOR

MAX_BODY = 1 << 20


class Handler(BaseHTTPRequestHandler):
    server_version = "ai-gateway-governance"
    # The default sends the Python version to every caller, which is a free
    # gift to anyone fingerprinting the service.
    sys_version = ""

    def _json(self, status, payload):
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/v1/policy":
            self._json(200, {
                "enumeration": "45 CFR 164.514(b)(2)",
                "enumerated": list(SAFE_HARBOR),
                "enforced": sorted(ENFORCED),
                "declared_omissions": DECLARED_OMISSIONS,
            })
        elif self.path == "/healthz":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "no such path"})

    def do_POST(self):
        if self.path != "/v1/guard":
            self._json(404, {"error": "no such path"})
            return
        length = int(self.headers.get("content-length") or 0)
        if length > MAX_BODY:
            self._json(413, {"error": "payload above %d bytes" % MAX_BODY})
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw or b"{}")
            text = payload["text"]
        except (ValueError, KeyError, TypeError):
            self._json(400, {"error": "body must be json carrying a text field"})
            return
        try:
            decision = apply(text, block_on_injection=payload.get("block_on_injection", True))
        except TypeError as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(200, asdict(decision))

    def log_message(self, fmt, *args):
        # The access log would otherwise carry the request line. On this
        # service the request line is fine and the body is the problem, so
        # nothing here ever touches the body.
        return


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    HTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
