"""Measure what failover costs, across the constant that decides the cost.

A single failover figure is not a thing. Which number you get depends on how
the primary is broken, and the two ordinary ways differ by three orders of
magnitude on this machine. A primary whose listener is gone refuses the
connection and failover is nearly free. A primary that still accepts
connections and has stopped answering costs the configured attempt timeout,
per request, until the breaker opens.

So the attempt timeout is swept rather than picked, and every width runs with
the breaker on and off, because a width with no control arm cannot say whether
the breaker did anything.

    python3 scripts/failover_sweep.py --out measurements/failover.json

The manifest carries counts only. The timings print to stdout and belong in
prose that names the machine, per the standing rule that a committed figure
has to re-derive somewhere else and a millisecond does not.
"""

import argparse
import json
import socket
import statistics
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gateway import failover, upstream  # noqa: E402

PROMPT = "summarise the visit note for the care team"
CEILING = 64

# Four widths spanning the range somebody would plausibly configure, and the
# point of the low end is that the effect has to survive the constant being
# small rather than merely move across three comfortable values.
WIDTHS = (0.05, 0.1, 0.25, 0.5)
REQUESTS = 40
BREAKER_THRESHOLD = 3
# Long enough that the breaker does not reopen inside one arm. A cooldown
# short enough to half open mid arm would mix two behaviours into one count.
BREAKER_COOLDOWN = 60.0


def free_port():
    """A port nothing is listening on, which is the refusing primary."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class StallServer:
    """Accepts a connection and never answers it.

    This is the failure mode that costs something. It has to count its
    accepts, because an arm where this socket was never reached is
    indistinguishable in the request counts from the refusing arm, and it
    would report the refusing arm's cost under this arm's name.
    """

    def __init__(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(128)
        self.port = self.sock.getsockname()[1]
        self.accepted = 0
        self.held = []
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        while self.running:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.accepted += 1
            # Held rather than closed. A closed socket sends a reset and the
            # client sees that as an error straight away, which is the
            # refusing case wearing this case's name.
            self.held.append(conn)

    def close(self):
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass
        for conn in self.held:
            try:
                conn.close()
            except OSError:
                pass


def healthy_upstream():
    server = ThreadingHTTPServer(("127.0.0.1", 0), upstream.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def arm(primary_url, secondary_url, width, breaker, requests=REQUESTS):
    pool = failover.Pool(
        [failover.Provider("primary", primary_url),
         failover.Provider("secondary", secondary_url)],
        attempt_timeout=width,
        breaker_threshold=BREAKER_THRESHOLD if breaker else 0,
        breaker_cooldown=BREAKER_COOLDOWN,
    )
    per_request = []
    for _ in range(requests):
        started = time.monotonic()
        pool.send(PROMPT, CEILING)
        per_request.append(time.monotonic() - started)
    return pool, per_request


def spread(values):
    return {
        "low": min(values),
        "median": statistics.median(values),
        "high": max(values),
    }


def check(manifest):
    """Refuse to publish an arm that did not exercise what its name claims.

    An arm whose stalled primary was never connected to reports the refusing
    case's cost under the stalled label, and the request counts look identical
    either way. This is the only place that difference is visible, so it fails
    here rather than being printed next to the figure.
    """
    problems = []
    requests = manifest["requests_per_arm"]
    for row in manifest["arms"]:
        where = "%s/%s/breaker-%s" % (row["primary_failure"], row["attempt_timeout"],
                                      row["breaker"])
        if row["primary_attempts"] < 1:
            problems.append("%s never attempted the primary" % where)
        if row["secondary_answered"] < 1:
            problems.append("%s was never answered by the secondary" % where)
        failovers = (row["failovers_after_an_attempt"]
                     + row["failovers_past_an_open_breaker"])
        if failovers != requests:
            problems.append("%s failed over on %d of %d requests"
                            % (where, failovers, requests))
        if row["primary_failure"] == "stalled":
            if row["stall_accepts_during_arm"] < 1:
                problems.append("%s reached no socket that accepted, so it "
                                "measured the refusing case" % where)
            if row["primary_timed_out"] != row["primary_attempts"]:
                problems.append("%s attempted the stalled primary %d times and "
                                "timed out %d" % (where, row["primary_attempts"],
                                                  row["primary_timed_out"]))
        if row["primary_failure"] == "refused" and row["primary_timed_out"]:
            problems.append("%s timed out against a port with no listener" % where)
    down = manifest["both_providers_down"]
    if down["requests_lost"] != requests:
        problems.append("the both-down arm lost %d of %d, so requests_lost is "
                        "not a column this sweep produced"
                        % (down["requests_lost"], requests))
    if down["answered"]:
        problems.append("the both-down arm was answered %d times" % down["answered"])
    if manifest["upstream_served"] < 1:
        problems.append("the healthy upstream served nothing over the socket")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="measurements/failover.json")
    ap.add_argument("--requests", type=int, default=REQUESTS)
    args = ap.parse_args()

    server, healthy_port = healthy_upstream()
    secondary = "http://127.0.0.1:%d/" % healthy_port
    stall = StallServer()
    refusing = "http://127.0.0.1:%d/" % free_port()

    modes = (
        ("refused", refusing),
        ("stalled", "http://127.0.0.1:%d/" % stall.port),
    )

    rows = []
    timings = {}
    try:
        for mode, primary in modes:
            for width in WIDTHS:
                for breaker in (False, True):
                    before = stall.accepted
                    pool, per_request = arm(primary, secondary, width, breaker,
                                            args.requests)
                    counts = pool.counters()
                    row = {
                        "primary_failure": mode,
                        "attempt_timeout": width,
                        "breaker": "on" if breaker else "off",
                        "requests": args.requests,
                        "answered": counts["answered"],
                        "failovers_after_an_attempt": counts["failovers_after_an_attempt"],
                        "failovers_past_an_open_breaker":
                            counts["failovers_past_an_open_breaker"],
                        "requests_lost": counts["requests_lost"],
                        "breaker_short_circuits": counts["breaker_short_circuits"],
                        "breaker_opens_on_primary": counts["breaker_opens"]["primary"],
                        "primary_attempts": counts["per_provider"]["primary"]["attempts"],
                        "primary_refused": counts["per_provider"]["primary"]["refused"],
                        "primary_timed_out": counts["per_provider"]["primary"]["timed_out"],
                        "secondary_answered": counts["per_provider"]["secondary"]["answered"],
                        "stall_accepts_during_arm": stall.accepted - before,
                    }
                    rows.append(row)
                    timings[(mode, width, "on" if breaker else "off")] = per_request

        # Both providers gone, so that requests_lost is a number this sweep
        # has actually produced rather than a column that is zero everywhere.
        lost_pool, lost_timings = arm(refusing,
                                      "http://127.0.0.1:%d/" % free_port(),
                                      0.1, True, args.requests)
        lost_counts = lost_pool.counters()
        both_down = {
            "primary_failure": "refused",
            "secondary_failure": "refused",
            "attempt_timeout": 0.1,
            "breaker": "on",
            "requests": args.requests,
            "answered": lost_counts["answered"],
            "requests_lost": lost_counts["requests_lost"],
            "failovers_after_an_attempt": lost_counts["failovers_after_an_attempt"],
            "failovers_past_an_open_breaker":
                lost_counts["failovers_past_an_open_breaker"],
            "breaker_opens_on_primary": lost_counts["breaker_opens"]["primary"],
            "breaker_short_circuits": lost_counts["breaker_short_circuits"],
        }
        timings[("both-down", 0.1, "on")] = lost_timings
    finally:
        stall.close()
        server.shutdown()
        server.server_close()

    manifest = {
        "requests_per_arm": args.requests,
        "widths_swept": list(WIDTHS),
        "breaker_threshold": BREAKER_THRESHOLD,
        "breaker_cooldown": BREAKER_COOLDOWN,
        "arms": rows,
        "both_providers_down": both_down,
        "upstream_served": upstream.served(),
        "attempt_timeout_note": (
            "attempt_timeout and breaker_cooldown are configured inputs to an "
            "arm, given in seconds. They are not measured times. The measured "
            "times are not in this file because a wall time does not re-derive "
            "on another machine."),
        "preconditions_note": (
            "stall_accepts_during_arm proves the stalled arms reached a socket "
            "that accepted. An arm reporting zero there measured the refusing "
            "case under the stalled name."),
    }

    problems = check(manifest)
    if problems:
        for line in problems:
            sys.stderr.write("precondition: %s\n" % line)
        raise SystemExit("the sweep graded something other than what it claims")

    out = Path(args.out)
    out.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("wrote %s, %d arms, every precondition held" % (out, len(manifest["arms"])))

    print("\ncounts")
    head = ("mode", "width", "breaker", "answered", "fo-asked", "fo-past",
            "lost", "prim-att", "prim-to", "accepts")
    print("  %-8s %6s %8s %9s %9s %8s %5s %9s %8s %8s" % head)
    for row in rows:
        print("  %-8s %6s %8s %9d %9d %8d %5d %9d %8d %8d" % (
            row["primary_failure"], row["attempt_timeout"], row["breaker"],
            row["answered"], row["failovers_after_an_attempt"],
            row["failovers_past_an_open_breaker"], row["requests_lost"],
            row["primary_attempts"], row["primary_timed_out"],
            row["stall_accepts_during_arm"]))
    print("  both providers down: answered %d lost %d opens %d short %d" % (
        both_down["answered"], both_down["requests_lost"],
        both_down["breaker_opens_on_primary"], both_down["breaker_short_circuits"]))

    print("\nper request wall time in seconds, this machine only, NOT in the manifest")
    print("  %-10s %6s %8s %10s %10s %10s" % ("mode", "width", "breaker",
                                              "low", "median", "high"))
    for key in sorted(timings, key=lambda k: (str(k[0]), k[1], k[2])):
        s = spread(timings[key])
        print("  %-10s %6s %8s %10.6f %10.6f %10.6f" % (
            key[0], key[1], key[2], s["low"], s["median"], s["high"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
