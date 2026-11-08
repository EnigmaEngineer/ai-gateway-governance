# Provider failover, and why one number for it is the wrong answer

The gateway tries providers in a fixed order and stops at the first one that
answers. `gateway/failover.py` holds the pool, a breaker per provider, and the
counters. A pool of one provider is the same object and the same code path, so
the ordinary request and the failover request are not two implementations.

```
python3 scripts/failover_sweep.py --out measurements/failover.json
```

## The question the blueprint line asked, and what it turned into

The line asked for a provider failover time measured. The first version of
this measured one: point the primary at a port with nothing listening, send
some traffic, and read the clock. That gives a figure of under a millisecond
and the conclusion that failover is free.

It is also the answer to a question nobody asked. A provider whose listener is
gone is the easy case. The case that costs money is a provider still accepting
connections and no longer answering them, and the two differ by nearly three
orders of magnitude on the same machine.

The thing that separates them is the attempt timeout. It is a constant someone
picks, and a cost that is really just the constant you picked is not a
measurement of anything. So it is swept across four widths, in both failure
shapes, with the breaker on and off at every width. Sixteen arms plus one.

## What came back

Counts are in `measurements/failover.json`, forty requests per arm.

```
mode      width  breaker  answered  fo-asked  fo-past  lost  prim-att  prim-to
refused    0.05      off        40        40        0     0        40        0
refused    0.05       on        40         3       37     0         3        0
stalled    0.05      off        40        40        0     0        40       40
stalled    0.05       on        40         3       37     0         3        3
stalled     0.5      off        40        40        0     0        40       40
stalled     0.5       on        40         3       37     0         3        3
```

Every arm answered every request, because the secondary was healthy in all of
them. The column that moves is how often the primary was asked. With no
breaker it is asked on all forty. With a breaker at three consecutive failures
it is asked three times and the other thirty seven requests go straight past.

The wall times are not in that file. A committed figure has to re-derive on
another machine and a millisecond does not. These come from
`logs/bench-1107/failover.txt`. The machine is an AMD Ryzen 7 7735HS with two
cores visible to the VM. Python 3.10.12 on Linux 6.8. Loopback, with no TLS on
the connection.

- **Refusing primary.** Median per request ran from 0.00067 to 0.00071 seconds
  across all four widths, with lows around 0.00059 and occasional highs near
  0.0018. The attempt timeout does not appear in the number anywhere. Setting
  it to half a second rather than fifty milliseconds changed nothing that is
  visible above the noise.
- **Stalling primary, no breaker.** Median per request came back at 0.0516 and
  0.1018 and 0.2519 and 0.5020 seconds, against timeouts of 0.05 and 0.1 and
  0.25 and 0.5. The figure is the setting plus about two milliseconds. There is
  no sense in which this measured the gateway.
- **Stalling primary, breaker at three.** Median fell to between 0.00059 and
  0.00070 seconds at every width. The high in each of those arms sits at the
  timeout, because three requests pay it before the breaker opens.

So the honest statement is not a failover time. It is this. Failover past a
refusing provider costs about seven tenths of a millisecond and the timeout is
dead configuration. Failover past a stalling provider costs exactly the
timeout, on every request, and the breaker is the thing that turns a
per-request cost into a cost paid three times.

## Two counters rather than one

A failover is a request the first provider did not serve. The breaker-on arms
make it clear that this covers two different events, so the pool counts them
apart.

`failovers_after_an_attempt` is a failure the pool found out about by asking.
`failovers_past_an_open_breaker` is a request routed past a provider it did not
ask at all. Adding them together and calling the total detection reads the
breaker's own saving as extra detection work, and the breaker-on arms would
then show forty detections from three attempts.

## What stops an arm reporting the wrong thing

An arm whose stalling primary was never reached reports the refusing case's
cost under the stalling label, and the request counts are identical either way.
So the stall server counts its accepts and each arm carries the count it
contributed.

`scripts/failover_sweep.py` refuses to write the manifest unless every arm
attempted its primary and was answered by its secondary and failed over on
every request. The stalling arms must also have reached a socket that
accepted. Three injected defects were checked against that gate. Zeroing one
arm's accepts, zeroing a primary attempt count, or zeroing the lost count on
the both-down arm each produce a named failure rather than a published file.

`requests_lost` would otherwise be zero in every arm, which is not evidence
that the pool can lose a request. One further arm points both providers at
dead ports. It loses all forty and that is what makes the column real.

## The breaker's own race, swept rather than argued

The first version of the breaker had no critical section. Half open was a read
of `opened_at` followed by a write to it, so every thread arriving after the
cooldown saw an elapsed breaker and went through. The comment above it said
one request goes through and decides, and that sentence was false under a
threaded server.

The fix is a lock. What is worth recording is how nearly the test of the fix
proved nothing. Sixteen threads released from a barrier, counting how many
pass the half open gate, where exactly one is the correct answer.

```
gap        unguarded     locked
0.0                1           1
0.0002            16           1
0.001             16           1
0.005             16           1
```

At no gap the unguarded version is also correct. The race does not open. So a
concurrency test written the obvious way passes with the critical section
deleted, and it would have gone in as coverage. From two tenths of a
millisecond upward every one of the sixteen gets through.

`tests/test_failover.py` therefore runs both arms at every width and asserts
two things. The locked arm admits exactly one at every width, and at least one
width shows the unguarded arm above one. The second assertion is what stops
the first becoming a description.

The pool's own counters are inside the same lock and that is a correctness
choice rather than a measured one. Twelve trials at eight threads against a
pool whose lock was replaced by a no-op lost no increments, so nothing here
shows that lock doing work. The test says so instead of implying otherwise.

## What this does not answer

- **Nothing here is a measurement of a provider.** Both upstreams are the
  deterministic mock in `gateway/upstream.py` on loopback. These figures are a
  property of this gateway's failover logic and of this machine. A sentence
  claiming a provider failover time would be a different claim with no evidence
  behind it.
- **Stalling and refusing are not the only two shapes.** A provider can return
  429 or 503 quickly. Its TLS handshake can hang. It can answer slowly and
  successfully, which is the hardest of the three, because no timeout
  distinguishes that from a long completion.
- **A cloud load balancer changes the stalling case.** It may reset a
  connection to an unhealthy backend rather than letting it hang, which turns
  the expensive shape into the cheap one. That is worth knowing before
  choosing a timeout from these numbers.
- **The breaker's cooldown is untested at the boundary in traffic.** The unit
  tests drive a fake clock through it. No arm here runs long enough to reach a
  half open state, so the recovery path is graded by unit test and not by the
  sweep.
- **Two cores.** A concurrency figure measured on this VM is not one. These
  arms are sequential for that reason, so nothing here says what failover
  costs under load.

## Re-measured, and which figures held

The whole sweep ran again on the last day of the build. All 261 leaf values in
`failover.json` came back identical, because the manifest carries counts.

The wall times are the interesting half and they live here rather than in the
manifest. Against a stalling primary the medians per request were these, in
seconds, with the first run of the sweep beside them.

```
  timeout   this run   first run
     0.05     0.0522      0.0516
     0.1      0.1022      0.1018
     0.25     0.2522      0.2519
     0.5      0.5024      0.5020
```

The figure is the setting plus about two milliseconds on both runs. It
reproduces because it is mostly the setting.

Against a refusing primary the median went from about 0.0007 to about 0.0012.
That arm is purely the machine, it has no setting in it, and it moved by most
of a factor of two in a day on the same hardware. Both are published and
neither is in the manifest. One of them is worth quoting and the other is
worth quoting only with the machine named and the spread shown, which is the
whole reason the rule exists.
