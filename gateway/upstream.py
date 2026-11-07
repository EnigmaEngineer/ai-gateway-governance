"""A stand in for the model provider, so the quota has something to settle on.

The gateway's interesting behaviour is what it does between admitting a
request and hearing the cost, and that cannot be exercised without something
on the other side that reports a usage. A real provider is not available to a
scheduled build and would make every figure here depend on somebody else's
capacity, so this answers in its place.

It is deterministic. The completion length is a function of the prompt, so the
same load run charges the same tokens twice and a moved overshoot figure means
the gateway moved. It honours the output ceiling, which means the overshoot
this repository measures carries no term for a provider that overruns. A real
one can and that limit is named rather than modelled.

    python -m gateway.upstream --port 8081

The usage block is shaped like the one the common chat APIs return, because
the reconciliation path should not have to be rewritten to meet a real one.
"""

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

from . import mtls, tokens

MAX_BODY = 1 << 20

# How much the answer runs to, against the prompt it answers. Chat completions
# are usually longer than their prompt and this is the middle of the range the
# published pricing examples use. It is a property of this stand in and not a
# measurement of anything.
ANSWER_RATIO = 1.5

_FILLER = ("the", "record", "shows", "a", "visit", "with", "no", "further",
           "detail", "available", "for", "review")

# How many completions this process has answered. A load run has to be able to
# prove the traffic went over the socket and not through the in process path,
# because a run that quietly took the shortcut measures a different service.
_served_lock = threading.Lock()
_served = 0
# Which client identities have been through. A backend behind mutual TLS that
# does not record who called is throwing away the only thing the certificate
# bought it.
_callers = {}


def served():
    return _served


def callers():
    with _served_lock:
        return dict(_callers)


def reset_counters():
    """So one process can run several arms without carrying counts across."""
    global _served
    with _served_lock:
        _served = 0
        _callers.clear()


def complete(prompt, output_ceiling):
    """Answer a prompt and report what it cost, in the shape an API would.

    The ceiling is honoured. A caller asking for 32 tokens gets at most 32,
    which keeps the reservation sound and leaves the estimate as the only
    place overshoot can come from.
    """
    wanted = min(int(tokens.count(prompt) * ANSWER_RATIO), output_ceiling)
    words = []
    produced = 0
    i = 0
    while produced < wanted:
        word = _FILLER[i % len(_FILLER)]
        cost = tokens.count(word)
        if produced + cost > wanted:
            break
        words.append(word)
        produced += cost
        i += 1
    text = " ".join(words)
    return {
        "text": text,
        "usage": {
            "prompt_tokens": tokens.count(prompt),
            "completion_tokens": tokens.count(text),
            "total_tokens": tokens.count(prompt) + tokens.count(text),
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "mock-upstream"
    sys_version = ""

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        if length > MAX_BODY:
            self._json(413, {"error": "payload above %d bytes" % MAX_BODY})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            prompt = payload["prompt"]
            ceiling = int(payload["max_output_tokens"])
        except (ValueError, KeyError, TypeError):
            self._json(400, {"error": "body must carry prompt and max_output_tokens"})
            return
        global _served
        caller = mtls.peer_name(self.connection) if self.tls else None
        with _served_lock:
            _served += 1
            key = caller or "no client certificate"
            _callers[key] = _callers.get(key, 0) + 1
        answer = complete(prompt, ceiling)
        answer["caller"] = caller
        self._json(200, answer)

    @property
    def tls(self):
        return hasattr(self.connection, "getpeercert")

    def do_GET(self):
        if self.path == "/counters":
            self._json(200, {"served": served(), "callers": callers()})
        else:
            self._json(404, {"error": "no such path"})

    def _json(self, status, payload):
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


def serve(host, port, context=None, threaded=False):
    """Bring up the backend, over mutual TLS when a context is handed in.

    The listening socket is wrapped rather than each connection, so a client
    that fails the handshake never reaches the handler at all. That is the
    behaviour worth having and it is also why a refused client shows up here
    as nothing rather than as a 403.
    """
    cls = ThreadingHTTPServer if threaded else HTTPServer
    server = cls((host, port), Handler)
    if context is not None:
        server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--tls-ca", help="CA that client certificates must chain to")
    ap.add_argument("--tls-cert", help="this backend's certificate")
    ap.add_argument("--tls-key", help="this backend's private key")
    args = ap.parse_args()
    context = None
    if args.tls_cert:
        if not args.tls_ca:
            raise SystemExit("serving TLS without --tls-ca accepts any client, "
                             "which is one way TLS wearing the word mutual")
        context = mtls.server_context(args.tls_ca, args.tls_cert, args.tls_key)
    serve(args.host, args.port, context=context).serve_forever()


if __name__ == "__main__":
    main()
