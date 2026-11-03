"""A front door for the guardrail, on the standard library only.

No web framework here. The thing worth getting right is the decision, and a
dependency that has to be installed before anything can be graded makes the
suite unrunnable on a fresh clone. The handler is small enough
to replace with whatever the deployment already runs.

    python -m gateway.app --port 8080
    curl -s localhost:8080/v1/guard -d '{"text":"SSN 123-45-6789"}'
    curl -sN localhost:8080/v1/guard/stream -d '{"text":"SSN 123-45-6789"}'

The streaming path exists because a completion arrives in pieces and redacting
each piece on its own leaks every identifier that straddles a boundary. There
is no upstream behind it. The request body stands in for the completion and the
handler hands it to the redactor in pieces, which exercises the geometry and
proves nothing about a real provider connection. gateway/stream.py carries the
argument and the measured cost.

That path runs redaction and nothing else. It is the response direction and the
text is the model talking rather than the caller, so an injection pattern in it
is not an attack arriving. Blocking a response for carrying the words of one
would refuse a summariser asked about prompt injection. The request direction is
where injection is assessed and that is /v1/guard. A response header says so,
because the difference is easy to assume away.
"""

import argparse
import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer

from .guardrail import apply
from .policy import DECLARED_OMISSIONS, ENFORCED, SAFE_HARBOR
from .stream import StreamRedactor

MAX_BODY = 1 << 20

# Defaults for the streaming path, measured on 2026-11-03. The holdback reaches
# zero leaked identifiers at 32 over the holdout bodies and 48 against the
# aimed cuts, so 64 carries a margin over both. The lookbehind reaches zero at
# 16 and costs almost nothing, so it is set well past that.
STREAM_HOLDBACK = 64
STREAM_LOOKBEHIND = 128
# The longest identifier either corpus slice contains is 38 characters and the
# longest in the aimed cuts is 91. A window of 1024 is well past both and it is
# what stops one long link turning a request into a quadratic scan.
STREAM_MAX_WINDOW = 1024

# What one read off the socket stands in for. A real deployment reads whatever
# the upstream sends and this is only the shape of the loop.
UPSTREAM_CHUNK = 4


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

    def _read_text(self):
        length = int(self.headers.get("content-length") or 0)
        if length > MAX_BODY:
            self._json(413, {"error": "payload above %d bytes" % MAX_BODY})
            return None
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw or b"{}")
            return payload, payload["text"]
        except (ValueError, KeyError, TypeError):
            self._json(400, {"error": "body must be json carrying a text field"})
            return None

    def do_POST(self):
        if self.path == "/v1/guard/stream":
            self._stream()
            return
        if self.path != "/v1/guard":
            self._json(404, {"error": "no such path"})
            return
        read = self._read_text()
        if read is None:
            return
        payload, text = read
        try:
            decision = apply(text, block_on_injection=payload.get("block_on_injection", True))
        except TypeError as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(200, asdict(decision))

    def _stream(self):
        read = self._read_text()
        if read is None:
            return
        _, text = read
        if not isinstance(text, str):
            self._json(400, {"error": "the gateway reads text payloads"})
            return
        redactor = StreamRedactor(holdback=STREAM_HOLDBACK, lookbehind=STREAM_LOOKBEHIND,
                                  max_window=STREAM_MAX_WINDOW)
        self.send_response(200)
        self.send_header("content-type", "text/plain; charset=utf-8")
        self.send_header("transfer-encoding", "chunked")
        # A reader has to be told the output is not the whole body yet, because
        # a client that stops early has read text the redactor had not finished
        # deciding about.
        self.send_header("x-guardrail-holdback-chars", str(STREAM_HOLDBACK))
        self.send_header("x-guardrail-injection-assessed", "false")
        self.end_headers()
        for i in range(0, len(text), UPSTREAM_CHUNK):
            self._chunk(redactor.feed(text[i:i + UPSTREAM_CHUNK]))
        self._chunk(redactor.close())
        self.wfile.write(b"0\r\n\r\n")

    def _chunk(self, piece):
        if not piece:
            return
        body = piece.encode("utf-8")
        self.wfile.write(b"%x\r\n" % len(body) + body + b"\r\n")

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
