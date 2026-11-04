"""Drive concurrent traffic at the gateway and settle the quota against it.

What this answers. A budget admitted on an estimate can be overspent, and the
question is by how much and where the overspend comes from. Four arms separate
it, and two things vary across them.

The reservation policy is the first. `ceiling` reserves the whole declared
output ceiling, which is sound and idles whatever the caller does not use.
`forecast` reserves a quarter of it, which is what a deployment does when it
would rather not refuse traffic it has room for.

Serialisation is the second. The counter read, the admission decision and the
counter write are one critical section or they are three steps another request
can land in the middle of. The second is what a gateway gets when the counter
lives in a store and check and set are two round trips.

`forecast-capped` adds a limit on how many requests are in detection at once,
which is the bound on a service whose detection cost is linear in the payload.
The limit sweep then puts a curve under that one point.

What the sweep cannot do is choose the limit, and the reason is the generator.
This is a closed loop: a fixed pool of workers, each sending the next request
as soon as the last one answered. So the offered concurrency is the worker
count, a cap at or above that count refuses nothing by construction, and a cap
below it refuses almost everything because a refused worker comes straight
back. The curve is a step at the worker count and the step is a property of
this harness. Choosing a real limit needs an open loop generator with an
arrival rate, which is a different day's work.

The peak can read one or two above the worker count and that is not a worker
appearing out of nowhere. A request leaves the critical section after it has
answered, so a client is free to open its next connection while the thread
that served the last one is still counted. The gate asserts the peak reaches
the worker count and records by how much it passed it.

Latency is reported per status and not pooled. A refused request answers in
single digit milliseconds, so an arm refusing ninety percent of its traffic
has a median that is the refusal path rather than the service.

Two quantities come out and they are not the same. The shortfall is the sum
over settled requests of what the reservation missed, and it is a property of
the policy. The overshoot is what is left of the shortfall after the budget's
slack has absorbed some of it, and on a corpus carrying attacks that slack is
large, because a blocked request hands its whole reservation back without ever
being charged.

They also differ in how stable they are, which is why the arms repeat. The
shortfall is a sum over every settled request and it barely moves. The
overshoot depends on which request happened to be the one that crossed the
boundary, so it is a small number off a single interleaving and it moves by
multiples between runs. Both are published as a spread over the repeats and
the single run figure is not quoted anywhere.

Preconditions, because an arm handed nothing to do reports clean. The offered
estimate has to exceed the budget or there is no quota to overspend. Some arm
has to overshoot or the reconciliation graded nothing. The ceiling arm has to
refuse something or the quota boundary was never reached. The capped arm has
to reach more than one request in detection at once or it graded a sequential
run. And the upstream has to report serving every admitted request the
guardrail did not block, or the traffic took the in process path.

    python scripts/quota_load.py --out measurements/quota-load.json

Timings are printed and never written to the manifest. A count reproduces on
another machine and a millisecond does not.
"""

import argparse
import hashlib
import json
import pathlib
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from gateway import app, tokens, upstream  # noqa: E402
from gateway.quota import CounterStore  # noqa: E402

# The width of the window between reading the counter and writing it. A shared
# counter in Redis on the same network is a round trip of this order.
#
# It is a chosen number and the race term is a function of it, so the whole
# finding would rest on it if nothing else were measured. The round trip sweep
# below is what stops that. It runs the unserialised arm at several widths
# including none at all, so whether the race needs the delay is answered rather
# than assumed.
STORE_ROUND_TRIP = 0.002

# Nought is the arm that matters. If the quota still stops refusing with no
# artificial window at all, the race is in the code and not in the constant.
ROUND_TRIP_SWEEP = (0.0, 0.0002, 0.0005, 0.002)

# What each caller declares it will accept back. Small enough that the prompt
# estimate is a real share of the reservation, because a ceiling that dwarfs
# the prompt hides the estimator error inside it.
OUTPUT_CEILING = 32

# What the forecast policy reserves, as a share of that ceiling. A quarter is
# the shape of the problem rather than a fitted number. A deployment would set
# it from its own history of how much of a declared ceiling gets used, and this
# repository has no such history.
RESERVE_FRACTION = 0.25


class _Server(ThreadingHTTPServer):
    """A listen backlog wide enough that it is not the thing being measured.

    The default is five. Sixteen workers connecting at once overflow it and the
    kernel answers the overflow with a reset, so the first run of the sweep
    died on a connection reset rather than on anything the gateway did. The
    admission limit is the bound this script is about and the socket backlog
    has to sit out of its way.
    """

    request_queue_size = 128
    daemon_threads = True


def _serve(handler_cls, port):
    server = _Server(("127.0.0.1", port), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _bodies(path, limit):
    rows = [json.loads(line)["text"] for line in path.read_text().splitlines() if line.strip()]
    return rows[:limit]


def _post(url, payload):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read())
            status = response.status
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read() or b"{}")
        status = exc.code
    except (urllib.error.URLError, OSError) as exc:
        # A transport failure is not a refusal and must not be counted as one.
        # Status 0 keeps it visible in the status table, and the gate below
        # fails the run rather than letting a published count quietly lose
        # requests that never reached the service.
        return 0, {"error": str(exc)}, time.perf_counter() - started
    return status, body, time.perf_counter() - started


def run_arm(name, bodies, budget, workers, serialise, admission_limit, upstream_url, port,
            reserve_fraction, round_trip=STORE_ROUND_TRIP):
    """One arm: a fresh gateway, the same traffic, one setting changed."""
    store = CounterStore(round_trip=round_trip)
    settings = app.Settings(budget=budget, admission_limit=admission_limit,
                            upstream=upstream_url, serialise=serialise,
                            reserve_fraction=reserve_fraction)
    settings.store = store
    app.SETTINGS = settings
    server = _serve(app.Handler, port)
    url = "http://127.0.0.1:%d/v1/chat" % port
    before = upstream.served()

    def send(text):
        return _post(url, {"tenant": "acme", "text": text,
                           "max_output_tokens": OUTPUT_CEILING})

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(send, bodies))
    finally:
        server.shutdown()
        server.server_close()

    statuses = {}
    latencies = {}
    for status, _, elapsed in results:
        statuses[status] = statuses.get(status, 0) + 1
        latencies.setdefault(status, []).append(elapsed * 1000.0)

    budget_obj = settings.budgets.get("acme")
    offered = sum(tokens.estimate(b) + OUTPUT_CEILING for b in bodies)
    arm = {
        "arm": name,
        "workers": workers,
        "serialised": serialise,
        "reserve_fraction": reserve_fraction,
        "store_round_trip_setting": round_trip,
        "admission_limit": admission_limit,
        "requests_offered": len(bodies),
        "offered_estimate_tokens": offered,
        "budget_tokens": budget,
        "admitted": budget_obj.admitted if budget_obj else 0,
        "refused_over_budget": budget_obj.refused_over_budget if budget_obj else 0,
        "refused_overloaded": settings.admission.refused_overloaded if settings.admission else 0,
        "peak_in_detection": settings.admission.peak_held if settings.admission else None,
        "spent_tokens": budget_obj.spent if budget_obj else 0,
        "overshoot_tokens": budget_obj.overshoot if budget_obj else 0,
        "shortfall_tokens": budget_obj.shortfall_tokens if budget_obj else 0,
        "headroom_returned_tokens": budget_obj.headroom_returned_tokens if budget_obj else 0,
        "blocked_by_guardrail": settings.blocked,
        "reserved_tokens_outstanding": budget_obj.reserved if budget_obj else 0,
        "upstream_served": upstream.served() - before,
        "store_reads": store.reads,
        "store_writes": store.writes,
        "status_counts": {str(k): v for k, v in sorted(statuses.items())},
        "transport_failures": statuses.get(0, 0),
        "peak_above_worker_count": (
            settings.admission.peak_held - workers if settings.admission is not None else None),
    }
    return arm, latencies


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="measurements/quota-load.json")
    ap.add_argument("--requests", type=int, default=240)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--corpus", default="corpus/data/labelled-holdout.jsonl")
    ap.add_argument("--base-port", type=int, default=8390)
    ap.add_argument("--repeats", type=int, default=3,
                    help="times to repeat the policy arms, because the overshoot is one "
                         "interleaving and the spread is the honest figure")
    args = ap.parse_args()

    root = pathlib.Path(__file__).resolve().parent.parent
    corpus = root / args.corpus
    bodies = _bodies(corpus, args.requests)
    if len(bodies) < args.requests:
        raise SystemExit("the corpus holds %d bodies and the run asked for %d"
                         % (len(bodies), args.requests))

    # Half of what the traffic would reserve under the ceiling policy, so the
    # quota binds partway through and there is a refusal boundary for the race
    # to cross. A budget the load never reaches reports perfect reconciliation.
    offered = sum(tokens.estimate(b) + OUTPUT_CEILING for b in bodies)
    budget = offered // 2

    upstream_server = _serve(upstream.Handler, args.base_port)
    upstream_url = "http://127.0.0.1:%d/" % args.base_port
    arms = []
    sweep = []
    round_trips = []
    timings = {}
    try:
        plan = [
            ("ceiling", True, 0, 1.0),
            ("forecast", True, 0, RESERVE_FRACTION),
            ("forecast-unserialised", False, 0, RESERVE_FRACTION),
            ("forecast-capped", True, max(2, args.workers // 4), RESERVE_FRACTION),
        ]
        print("%-22s %4s %8s %8s %8s %9s %10s %9s %5s"
              % ("arm", "rep", "admitted", "refused", "blocked", "spent", "shortfall",
                 "overshoot", "peak"))
        for rep in range(args.repeats):
            for i, (name, serialise, limit, fraction) in enumerate(plan):
                arm, latencies = run_arm(name, bodies, budget, args.workers, serialise,
                                         limit, upstream_url,
                                         args.base_port + 1 + i + rep * len(plan),
                                         fraction)
                arm["repeat"] = rep
                arms.append(arm)
                timings.setdefault(name, {})
                for status, series in latencies.items():
                    timings[name].setdefault(status, []).extend(series)
                print("%-22s %4d %8d %8d %8d %9d %10d %9d %5s"
                      % (name, rep, arm["admitted"], arm["refused_over_budget"],
                         arm["blocked_by_guardrail"], arm["spent_tokens"],
                         arm["shortfall_tokens"], arm["overshoot_tokens"],
                         arm["peak_in_detection"]))

        # A cap refuses rather than queues, so each limit buys a different
        # amount of protection at a different cost in refusals. The widest arm
        # is above the worker count on purpose, so it refuses nothing and its
        # peak is what the load actually reached.
        limits = [2, 4, 8, args.workers, args.workers * 4]
        for j, limit in enumerate(limits):
            arm, latencies = run_arm("limit-%d" % limit, bodies, budget, args.workers,
                                     True, limit, upstream_url,
                                     args.base_port + 20 + j, RESERVE_FRACTION)
            sweep.append({
                "limit": limit,
                "admitted": arm["admitted"],
                "refused_overloaded": arm["refused_overloaded"],
                "refused_over_budget": arm["refused_over_budget"],
                "peak_in_detection": arm["peak_in_detection"],
                "transport_failures": arm["transport_failures"],
                "peak_above_worker_count": arm["peak_above_worker_count"],
            })
            timings["limit-%d" % limit] = latencies

        # Whether the race needs the window the constant opens. The arm at
        # nought has no artificial delay in it at all, so if the quota still
        # stops refusing there, the defect is the missing critical section and
        # not the number above it. The locked control runs at every width,
        # because an unserialised arm admitting everything proves nothing unless
        # the locked one at the same width refused.
        for j, round_trip in enumerate(ROUND_TRIP_SWEEP):
            arm, _ = run_arm("rt-%g" % round_trip, bodies, budget, args.workers,
                             False, 0, upstream_url, args.base_port + 40 + j,
                             RESERVE_FRACTION, round_trip=round_trip)
            control, _ = run_arm("rt-%g-locked" % round_trip, bodies, budget, args.workers,
                                 True, 0, upstream_url, args.base_port + 60 + j,
                                 RESERVE_FRACTION, round_trip=round_trip)
            round_trips.append({
                "store_round_trip_setting": round_trip,
                "admitted_without_the_lock": arm["admitted"],
                "admitted_with_the_lock": control["admitted"],
                "transport_failures": arm["transport_failures"] + control["transport_failures"],
            })
    finally:
        upstream_server.shutdown()
        upstream_server.server_close()

    def spread(name, field):
        series = sorted(a[field] for a in arms if a["arm"] == name)
        return {"low": series[0], "high": series[-1],
                "median": series[len(series) // 2], "runs": len(series)}

    by_name = {}
    for a in arms:
        by_name.setdefault(a["arm"], a)
    problems = []
    widest = sweep[-1]
    if widest["refused_overloaded"] != 0:
        problems.append("the widest sweep arm refused %d on the limit, so its peak is the "
                        "limit rather than the concurrency the load reached"
                        % widest["refused_overloaded"])
    if widest["peak_in_detection"] < 2:
        problems.append("the widest sweep arm peaked at %d in detection, so the load was "
                        "sequential and the sweep compares nothing"
                        % widest["peak_in_detection"])
    if not any(row["refused_overloaded"] > 0 for row in sweep[:-1]):
        problems.append("no sweep arm refused on the limit, so every cap was above the load "
                        "and the sweep measured nothing")
    for row in round_trips:
        if row["admitted_with_the_lock"] >= len(bodies):
            problems.append("the locked control at round trip %g admitted every request, so "
                            "there was nothing for the unserialised arm to get past"
                            % row["store_round_trip_setting"])
    if widest["peak_in_detection"] < args.workers:
        problems.append("the widest sweep arm peaked at %d against %d workers, so the load "
                        "never reached full concurrency and a cap at the worker count was "
                        "never tested against anything"
                        % (widest["peak_in_detection"], args.workers))
    if offered <= budget:
        problems.append("the offered estimate %d does not exceed the budget %d, so nothing "
                        "could overspend" % (offered, budget))
    if not any(a["overshoot_tokens"] > 0 for a in arms):
        problems.append("no arm overshot, so the reconciliation graded nothing")
    for a in (arms
              + [{"arm": r["limit"], "transport_failures": r["transport_failures"]}
                 for r in sweep]
              + [{"arm": "rt-%g" % r["store_round_trip_setting"],
                  "transport_failures": r["transport_failures"]} for r in round_trips]):
        if a["transport_failures"]:
            problems.append("%s lost %d requests to a transport failure, so its counts are "
                            "short by an unknown amount" % (a["arm"], a["transport_failures"]))
    for a in arms:
        forwarded = a["admitted"] - a["blocked_by_guardrail"]
        if forwarded and a["upstream_served"] != forwarded:
            problems.append("%s forwarded %d and the upstream served %d, so some traffic did "
                            "not go over the socket" % (a["arm"], forwarded,
                                                        a["upstream_served"]))
        if a["shortfall_tokens"] == 0:
            problems.append("%s settled every request inside its reservation, so there was "
                            "no shortfall for the budget to absorb" % a["arm"])
    if not any(a["overshoot_tokens"] > 0 for a in arms if a["arm"] == "forecast"):
        problems.append("the forecast arm never overspent across %d repeats, so the policy "
                        "this day is about produced no overshoot at all" % args.repeats)
    if by_name["ceiling"]["refused_over_budget"] == 0:
        problems.append("the ceiling arm refused nothing, so the quota boundary was never "
                        "reached and no arm can overspend it")
    capped_peak = by_name["forecast-capped"]["peak_in_detection"]
    if capped_peak is None or capped_peak < 2:
        problems.append("the capped arm never had two requests in detection at once, so it "
                        "graded a sequential run")
    if problems:
        print("\nthe run did not grade what it claims to grade:")
        for problem in problems:
            print("  " + problem)
        raise SystemExit(1)

    # The race does not show up in the spend on this corpus and it shows up
    # plainly in the admissions. Without the critical section two requests
    # read the same counter and both write their own total over the other's,
    # so the reservation never accumulates and the quota stops refusing.
    lost_refusals = (spread("forecast-unserialised", "admitted")["median"]
                     - spread("forecast", "admitted")["median"])
    spend_delta = (spread("forecast-unserialised", "spent_tokens")["median"]
                   - spread("forecast", "spent_tokens")["median"])
    manifest = {
        "corpus_path": args.corpus,
        "corpus_sha256": hashlib.sha256(corpus.read_bytes()).hexdigest(),
        "output_ceiling_tokens": OUTPUT_CEILING,
        "requests_graded": len(bodies),
        "workers": args.workers,
        "budget_tokens": budget,
        "offered_estimate_tokens": offered,
        "reserve_fraction_forecast": RESERVE_FRACTION,
        "arms": arms,
        "repeats": args.repeats,
        "prompt_estimator_short_by_tokens": spread("ceiling", "shortfall_tokens"),
        "forecast_short_by_tokens": spread("forecast", "shortfall_tokens"),
        "overshoot_under_ceiling_tokens": spread("ceiling", "overshoot_tokens"),
        "overshoot_under_forecast_tokens": spread("forecast", "overshoot_tokens"),
        "admitted_with_the_lock": spread("forecast", "admitted"),
        "admitted_without_the_lock": spread("forecast-unserialised", "admitted"),
        "admissions_the_lock_refused": lost_refusals,
        "spend_moved_by_dropping_the_lock_tokens": spend_delta,
        "admission_limit_sweep": sweep,
        "store_round_trip_sweep": round_trips,
        "widest_arm_peak_above_worker_count": widest["peak_in_detection"] - args.workers,
        "overshoot_under_forecast_capped_tokens": by_name["forecast-capped"]["overshoot_tokens"],
        "prompt_tokens_estimated_short_on_the_whole_corpus": sum(
            max(0, tokens.count(b) - tokens.estimate(b)) for b in bodies),
    }
    out = root / args.out
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print("\nwrote %s" % args.out)
    for key in ("prompt_estimator_short_by_tokens", "forecast_short_by_tokens",
                "overshoot_under_ceiling_tokens", "overshoot_under_forecast_tokens",
                "admitted_with_the_lock", "admitted_without_the_lock"):
        band = manifest[key]
        print("  %-38s %6d to %6d, median %6d over %d runs"
              % (key, band["low"], band["high"], band["median"], band["runs"]))
    print("dropping the lock admitted %d requests the quota should have refused, and moved "
          "the settled spend by %d tokens" % (lost_refusals, spend_delta))
    print("\nstore round trip sweep, the lock against no lock at each width")
    for row in round_trips:
        print("  round trip %-8g admitted without the lock %4d  with it %4d"
              % (row["store_round_trip_setting"], row["admitted_without_the_lock"],
                 row["admitted_with_the_lock"]))
    print("\nadmission limit sweep at %d workers" % args.workers)
    for row in sweep:
        print("  limit %3d  admitted %4d  refused overloaded %4d  peak in detection %3d"
              % (row["limit"], row["admitted"], row["refused_overloaded"],
                 row["peak_in_detection"]))
    print("\nper request wall time by status, this machine only")
    for name, by_status in timings.items():
        for status in sorted(by_status):
            series = sorted(by_status[status])
            print("  %-22s %3d  median %7.1f ms  p95 %7.1f ms  worst %8.1f ms  %4d samples"
                  % (name, status, statistics.median(series),
                     series[min(len(series) - 1, int(len(series) * 0.95))],
                     series[-1], len(series)))


if __name__ == "__main__":
    main()
