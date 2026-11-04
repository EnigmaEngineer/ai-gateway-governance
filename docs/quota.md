# The token budget, and what deciding early costs

A gateway has to say yes or no before it knows what a request will cost. The
provider counts the tokens and reports them afterwards. So admission charges a
guess and reconciliation replaces the guess with the fact, and the difference
is a quantity the service owns rather than an error to be removed.

This is what that difference measured, on a budget of 6,808 tokens against 240
bodies from the held out corpus at 16 concurrent callers.

## Two numbers, and only one of them is stable

The shortfall is the sum over settled requests of what the reservation missed.
It is a property of the reservation policy.

The overshoot is what is left of the shortfall after the budget's slack has
absorbed some of it. It is one number off one interleaving, so it moves between
runs by multiples. Both are published as a spread over three repeats and
neither is quoted from a single run.

```
  prompt_estimator_short_by_tokens          882 to    891, median    882 over 3 runs
  forecast_short_by_tokens                 3478 to   3507, median   3478 over 3 runs
  overshoot_under_ceiling_tokens              3 to     62, median      3 over 3 runs
  overshoot_under_forecast_tokens           117 to    171, median    117 over 3 runs
```

Read the first and third rows together. The estimate of the prompt was short by
about 879 tokens across the run and the budget was overspent by nought. The
slack came from somewhere, and the somewhere is the attacks. Sixty seven of the
240 bodies are blocked by the injection filter, they never reach the upstream,
and each one hands its whole reservation back unspent. A corpus where a quarter
of the traffic is an attack funds the estimator's error out of requests nobody
was charged for.

That is not a property of the quota. Point the same gateway at traffic with no
attacks in it and the slack is gone.

## Reserving the ceiling is sound and almost nobody can afford it

A caller declares what it will accept back. Reserving all of it is correct and
it holds budget the caller will not use. The forecast policy reserves a quarter
of the declared ceiling instead, which is what a deployment does when it would
rather not refuse traffic it has room for.

The cost of that choice is the second row above against the first. Forecasting
a quarter took the shortfall from about 882 tokens to about 3,478, which is
four times as much budget spent outside the admission decision.

The quarter is the shape of the problem and not a fitted number. A deployment
would set it from its own history of how much of a declared ceiling gets used.
This repository has no such history and says so rather than implying one.

## The race is the term that grows with load

A budget shared across gateway processes lives in a store. Reading it and
writing it are two round trips, so a read, a decision and a write with nothing
between them is a window another request can land in.

```
  admitted_with_the_lock                    176 to    177, median    176 over 3 runs
  admitted_without_the_lock                 240 to    240, median    240 over 3 runs
```

Every request got in. Not some. All 240 of them, on all three repeats, against
a budget that fits about 177. Without the critical section two callers read the
same counter and each writes its own total over the other's, so the reservation
never accumulates and the quota stops refusing anything at all.

The settled spend moved by minus 752 tokens, which looks like the race costing
nothing. It is not. The extra traffic that got in was mostly attacks, and an
attack is blocked before it reaches the upstream, so it settles nothing. The
race admitted 63 requests that should have been refused and the bill for them
landed somewhere this corpus cannot show.

A spend figure is the wrong place to look for this. The admission count is the
right one.

## The cap on work in flight

Detection walks the whole payload, so its cost is linear in body size, and the
body limit is a megabyte. Without a cap the only thing bounding the work in
flight is how many sockets the host accepts, which is not a number anybody
chose.

The cap refuses rather than queues. A queue in front of a linear cost turns a
load spike into a latency spike every caller waits through. A refusal is a
thing a client can retry.

```
  limit   2  admitted   17  refused overloaded  223  peak in detection   2
  limit   4  admitted   19  refused overloaded  221  peak in detection   4
  limit   8  admitted   21  refused overloaded  219  peak in detection   8
  limit  16  admitted  176  refused overloaded    0  peak in detection  16
  limit  64  admitted  179  refused overloaded    0  peak in detection  16
```

## This sweep cannot choose the limit, and the reason is the harness

That table is a step and not a curve. Everything below 16 refuses about nine
requests in ten. Everything at 16 or above refuses none.

The generator is why. It is a closed loop with a fixed pool of 16 workers, each
sending its next request as soon as the last one answered. So the offered
concurrency is the worker count. A cap at or above it cannot bind, and a cap
below it refuses almost everything, because a refused worker comes straight
back with the next request.

The step sits at the worker count because the worker count put it there. A real
limit comes off an open loop generator with an arrival rate and a service that
is allowed to fall behind, and that is a different day.

What the sweep does show is the price of admitting more at once. These are wall
times on one machine and they are not in any manifest.

```
  limit-2                200  median    27.6 ms  p95    38.3 ms  worst     38.3 ms    17 samples
  limit-4                200  median    47.6 ms  p95    69.2 ms  worst     69.2 ms    19 samples
  limit-8                200  median    83.7 ms  p95   115.4 ms  worst    129.4 ms    21 samples
  limit-16               200  median   140.1 ms  p95   142.9 ms  worst    143.9 ms   176 samples
  limit-64               200  median   140.0 ms  p95   143.2 ms  worst    208.3 ms   179 samples
```

Served latency tracks the limit close to proportionally over this range, on two
cores. Doubling what is allowed in at once roughly doubles what each admitted
request waits. That is the trade the cap is making and it is the argument for
setting one.

Latency is reported per status and never pooled. A refused request answers in
single digit milliseconds. An arm that refuses nine in ten has a pooled median
that is the refusal path, which says nothing about the service.

## What the reference tokeniser can and cannot say

The gateway does not have the provider's tokeniser. `gateway/tokens.py` carries
a stand in that splits on orthographic boundaries and never merges.

A real subword vocabulary does merge, so it charges fewer tokens than four
characters each for exactly the long common words that merging is for. This
stand in is at least as dear as the character rule on every input, which a
check sweeps the whole corpus to confirm. So the estimate can only ever come
out short against it.

Every shortfall figure here is therefore a bound on what a real tokeniser would
disagree by and not a prediction of it. Choosing a constant that let the sign
go both ways would be fitting the reference to the answer.

## Rebuilding it

```
python scripts/quota_load.py --out measurements/quota-load.json
```

The run refuses to write a manifest unless five things held. The load really
exceeded the budget. The forecast policy really overspent on every repeat. The
upstream really served every request the guardrail did not block. The capped
arm really had two requests in detection at once. And no arm lost a request to
a transport failure.

An arm handed an input with nothing in it reports clean. A clean report off a
load test is the easiest thing here to publish by accident.
