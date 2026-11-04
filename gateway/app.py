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
import urllib.error
import urllib.request
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

from . import upstream
from .guardrail import apply
from .policy import DECLARED_OMISSIONS, ENFORCED, SAFE_HARBOR
from .quota import (Admission, CounterStore, Overloaded, QuotaExceeded,
                    TokenBudget)
from .stream import StreamRedactor

MAX_BODY = 1 << 20

# What a caller gets if it does not declare one. A ceiling is part of the
# reservation, so a request without one cannot be admitted on an estimate at
# all, and refusing those would make the quota opt in.
DEFAULT_OUTPUT_CEILING = 256

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


class Settings:
    """What the admission and quota behaviour is set to for this process.

    The load test drives these, so the arms it compares are the same code
    under different settings rather than different code paths.
    """

    def __init__(self, budget=0, admission_limit=0, upstream=None,
                 serialise=True, store_round_trip=0.0, reserve_fraction=1.0):
        self.budget = budget
        self.upstream = upstream
        self.serialise = serialise
        self.store_round_trip = store_round_trip
        self.reserve_fraction = reserve_fraction
        self.admission = Admission(admission_limit) if admission_limit else None
        self.budgets = {}
        self.store = None
        # Requests the guardrail stopped. They never reach the upstream, so
        # they settle nothing and hand their whole reservation back, which is
        # a term in the budget's arithmetic rather than a logging detail.
        self.blocked = 0
        if budget:
            self.store = CounterStore(round_trip=store_round_trip)

    def budget_for(self, tenant):
        if not self.budget:
            return None
        if tenant not in self.budgets:
            self.budgets[tenant] = TokenBudget(tenant, self.budget, store=self.store,
                                               serialise=self.serialise,
                                               reserve_fraction=self.reserve_fraction)
        return self.budgets[tenant]

    def counters(self):
        out = {"tenants": {}}
        for name, b in sorted(self.budgets.items()):
            out["tenants"][name] = {
                "budget_tokens": b.budget,
                "spent_tokens": b.spent,
                "reserved_tokens": b.reserved,
                "overshoot_tokens": b.overshoot,
                "admitted": b.admitted,
                "refused_over_budget": b.refused_over_budget,
                "shortfall_tokens": b.shortfall_tokens,
                "headroom_returned_tokens": b.headroom_returned_tokens,
            }
        out["blocked"] = self.blocked
        if self.admission is not None:
            out["admission"] = {
                "limit": self.admission.limit,
                "peak_held": self.admission.peak_held,
                "refused_overloaded": self.admission.refused_overloaded,
            }
        if self.store is not None:
            out["store"] = {"reads": self.store.reads, "writes": self.store.writes}
        return out


SETTINGS = Settings()


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
        elif self.path == "/v1/counters":
            self._json(200, SETTINGS.counters())
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
        if self.path == "/v1/chat":
            self._chat()
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

    def _chat(self):
        """Guard a request, forward it, and settle the quota on what it cost.

        The order is deliberate. Admission comes first because it is the only
        thing that bounds how much detection work is in flight, and detection
        is the expensive part. The reservation comes next, on the text as the
        caller sent it, because that is what the caller is charged for. The
        guardrail runs after both, and the redacted text is what goes out.
        """
        read = self._read_text()
        if read is None:
            return
        payload, text = read
        if not isinstance(text, str):
            self._json(400, {"error": "the gateway reads text payloads"})
            return
        tenant = str(payload.get("tenant") or "anonymous")
        # Absent and nought are different answers and `or` cannot tell them
        # apart. A caller that declares nought is saying something wrong and
        # gets told so, rather than quietly receiving the default.
        declared = payload.get("max_output_tokens")
        try:
            ceiling = DEFAULT_OUTPUT_CEILING if declared is None else int(declared)
        except (TypeError, ValueError):
            self._json(400, {"error": "max_output_tokens must be a whole number of tokens"})
            return
        if ceiling <= 0:
            self._json(400, {"error": "max_output_tokens must be above zero"})
            return

        if SETTINGS.admission is None:
            self._guarded_chat(tenant, text, ceiling)
            return
        try:
            with SETTINGS.admission:
                self._guarded_chat(tenant, text, ceiling)
        except Overloaded as exc:
            self._json(503, {"error": str(exc), "retry": True})

    def _guarded_chat(self, tenant, text, ceiling):
        budget = SETTINGS.budget_for(tenant)
        reservation = None
        if budget is not None:
            try:
                reservation = budget.reserve(text, ceiling)
            except QuotaExceeded as exc:
                self._json(429, {"error": str(exc), "retry": False})
                return

        decision = apply(text, block_on_injection=True)
        if decision.action == "block":
            # A blocked request never reaches the upstream, so it costs the
            # provider nothing and the estimate goes back. Charging for it
            # would make the quota a tax on being attacked.
            SETTINGS.blocked += 1
            if budget is not None:
                budget.release(reservation)
            self._json(200, {"action": decision.action, "reasons": decision.reasons,
                             "usage": None})
            return

        answer = self._forward(decision.forwarded_text, ceiling)
        if answer is None:
            if budget is not None:
                budget.release(reservation)
            return

        usage = answer["usage"]
        settlement = None
        if budget is not None:
            settlement = budget.reconcile(reservation, usage["total_tokens"])
        body = {
            "action": decision.action,
            "redacted_classes": decision.redacted_classes,
            "redaction_count": decision.redaction_count,
            "completion": answer["text"],
            "usage": usage,
        }
        if settlement is not None:
            body["quota"] = {
                "reserved_tokens": settlement.reserved,
                "actual_tokens": settlement.actual,
                "shortfall_tokens": settlement.shortfall,
                "spent_tokens": budget.spent,
                "budget_tokens": budget.budget,
            }
        self._json(200, body)

    def _forward(self, text, ceiling):
        """Send the redacted text on and return the answer, or answer the error.

        With no upstream configured this answers in process, which is what the
        suite runs against. The socket path is what the load test uses, because
        a quota measured without the serialisation a real request goes through
        is measuring something easier than the service.
        """
        if not SETTINGS.upstream:
            return upstream.complete(text, ceiling)
        request = urllib.request.Request(
            SETTINGS.upstream,
            data=json.dumps({"prompt": text, "max_output_tokens": ceiling}).encode("utf-8"),
            headers={"content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self._json(502, {"error": "upstream did not answer: %s" % exc})
            return None

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
    ap.add_argument("--budget", type=int, default=0,
                    help="token budget per tenant for the window, 0 leaves the quota off")
    ap.add_argument("--admission-limit", type=int, default=0,
                    help="requests allowed in detection at once, 0 leaves it unbounded")
    ap.add_argument("--upstream", default=None,
                    help="url of the completion endpoint, in process when absent")
    ap.add_argument("--no-serialise", action="store_true",
                    help="read and write the counter without a critical section")
    ap.add_argument("--store-round-trip", type=float, default=0.0,
                    help="seconds a counter read or write takes, which is the race width")
    ap.add_argument("--reserve-fraction", type=float, default=1.0,
                    help="share of the declared output ceiling the reservation covers")
    ap.add_argument("--threaded", action="store_true",
                    help="one thread per request, which is what the load test needs")
    args = ap.parse_args()

    global SETTINGS
    SETTINGS = Settings(budget=args.budget, admission_limit=args.admission_limit,
                        upstream=args.upstream, serialise=not args.no_serialise,
                        store_round_trip=args.store_round_trip,
                        reserve_fraction=args.reserve_fraction)
    server = ThreadingHTTPServer if args.threaded else HTTPServer
    server((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
