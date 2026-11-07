"""The pool, the breaker, and whether the published sweep graded its own arms.

The breaker tests drive a fake clock. A breaker tested with time.monotonic
would need sleeps to reach its cooldown, and a suite that sleeps is a suite
somebody stops running.
"""

import threading
import time

import pytest

from gateway import failover


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_a_pool_with_no_providers_is_refused():
    with pytest.raises(ValueError):
        failover.Pool([], attempt_timeout=1.0)


def test_a_breaker_at_zero_never_opens():
    """The control arm. The sweep needs this to be genuinely off."""
    b = failover.Breaker(0, 30.0)
    assert not b.enabled
    for _ in range(100):
        b.failed(0.0)
    assert b.allows(0.0)
    assert b.opens == 0


def test_a_breaker_opens_on_its_threshold_and_not_before():
    b = failover.Breaker(3, 30.0)
    b.failed(0.0)
    b.failed(0.0)
    assert b.allows(0.0), "opened on the second of three"
    b.failed(0.0)
    assert not b.allows(0.0)
    assert b.opens == 1


def test_a_success_clears_the_run_of_failures():
    b = failover.Breaker(3, 30.0)
    b.failed(0.0)
    b.failed(0.0)
    b.succeeded()
    b.failed(0.0)
    b.failed(0.0)
    assert b.allows(0.0), "two failures after a success should not open it"


def test_an_open_breaker_lets_one_request_through_after_the_cooldown():
    clock = Clock()
    b = failover.Breaker(2, 30.0)
    b.failed(clock())
    b.failed(clock())
    assert not b.allows(clock())
    clock.now = 29.9
    assert not b.allows(clock())
    clock.now = 30.0
    assert b.allows(clock()), "the cooldown elapsed and it stayed shut"


def test_the_breaker_does_not_count_its_opens_twice_while_already_open():
    b = failover.Breaker(2, 30.0)
    for _ in range(10):
        b.failed(0.0)
    assert b.opens == 1


def test_a_request_past_an_open_breaker_is_not_counted_as_detection():
    """Summing the two halves and calling it detection reads the saving as work."""
    pool = failover.Pool(
        [failover.Provider("dead", "http://127.0.0.1:1/"),
         failover.Provider("dead-too", "http://127.0.0.1:2/")],
        attempt_timeout=0.05, breaker_threshold=1, breaker_cooldown=3600.0)
    for _ in range(4):
        pool.send("hello", 16)
    counts = pool.counters()
    assert counts["requests_lost"] == 4
    assert counts["breaker_short_circuits"] > 0
    # Nothing answered, so neither failover column may have moved.
    assert counts["failovers_after_an_attempt"] == 0
    assert counts["failovers_past_an_open_breaker"] == 0


def test_a_refused_connection_is_reported_as_refused_and_not_as_an_error():
    """urllib wraps a refusal and a timeout in one class, so this is the unwrap."""
    pool = failover.Pool([failover.Provider("dead", "http://127.0.0.1:1/")],
                         attempt_timeout=0.05)
    pool.send("hello", 16)
    counts = pool.counters()["per_provider"]["dead"]
    assert counts["refused"] == 1
    assert counts["errored"] == 0
    assert counts["timed_out"] == 0


def test_the_published_sweep_swept_more_than_one_width(manifests):
    sweep = manifests["failover"]
    assert len(sweep["widths_swept"]) >= 3
    widths = {row["attempt_timeout"] for row in sweep["arms"]}
    assert widths == set(sweep["widths_swept"])


def test_every_width_has_a_breaker_control(manifests):
    """A width with no control cannot say whether the breaker did anything."""
    sweep = manifests["failover"]
    for mode in ("refused", "stalled"):
        for width in sweep["widths_swept"]:
            settings = {row["breaker"] for row in sweep["arms"]
                        if row["primary_failure"] == mode
                        and row["attempt_timeout"] == width}
            assert settings == {"on", "off"}, (
                "%s at %s has arms %s" % (mode, width, sorted(settings)))


def test_both_failure_shapes_were_measured(manifests):
    """One shape is nearly free and the other costs the timeout. Reporting one
    of them as the failover cost is the thing this sweep exists to stop."""
    modes = {row["primary_failure"] for row in manifests["failover"]["arms"]}
    assert modes == {"refused", "stalled"}


def test_every_stalled_arm_reached_a_socket_that_accepted(manifests):
    """An arm that never connected reports the refusing cost under this name."""
    graded = 0
    for row in manifests["failover"]["arms"]:
        if row["primary_failure"] != "stalled":
            continue
        graded += 1
        assert row["stall_accepts_during_arm"] > 0, row
        assert row["primary_timed_out"] == row["primary_attempts"], row
    assert graded >= 4, "only %d stalled arms, so the sweep narrowed" % graded


def test_no_refusing_arm_timed_out(manifests):
    for row in manifests["failover"]["arms"]:
        if row["primary_failure"] == "refused":
            assert row["primary_timed_out"] == 0, row


def test_every_arm_failed_over_on_every_request(manifests):
    sweep = manifests["failover"]
    for row in sweep["arms"]:
        total = (row["failovers_after_an_attempt"]
                 + row["failovers_past_an_open_breaker"])
        assert total == row["requests"], row
        assert row["answered"] == row["requests"], row
        assert row["requests_lost"] == 0, row


def test_the_breaker_stops_the_primary_being_asked(manifests):
    """The whole claim for the breaker, read off the counts rather than a time."""
    sweep = manifests["failover"]
    for width in sweep["widths_swept"]:
        off = [r for r in sweep["arms"] if r["primary_failure"] == "stalled"
               and r["attempt_timeout"] == width and r["breaker"] == "off"][0]
        on = [r for r in sweep["arms"] if r["primary_failure"] == "stalled"
              and r["attempt_timeout"] == width and r["breaker"] == "on"][0]
        assert off["primary_attempts"] == off["requests"]
        assert on["primary_attempts"] == sweep["breaker_threshold"]
        assert on["primary_attempts"] < off["primary_attempts"]


def test_requests_lost_is_a_column_the_sweep_produced(manifests):
    """A counter that is zero in every arm is not evidence the pool can lose one."""
    down = manifests["failover"]["both_providers_down"]
    sweep = manifests["failover"]
    assert down["requests_lost"] == sweep["requests_per_arm"]
    assert down["answered"] == 0


def test_the_sweep_went_over_a_socket(manifests):
    """An in process shortcut would measure a different service."""
    assert manifests["failover"]["upstream_served"] > 0


# Widths for the half open race. The arm at nought is the one that matters,
# because it is the one that shows the defect is invisible without a gap.
RACE_WIDTHS = (0.0, 0.0002, 0.001, 0.005)
RACERS = 16


def _half_open_arm(locked, gap, workers=RACERS):
    """How many threads get through a half open breaker, with and without the lock.

    The unguarded body is the version this module shipped first, reproduced
    here rather than kept in the module, because a control arm that has to
    stay in the production code is a second implementation to maintain.
    """
    b = failover.Breaker(1, 0.0)
    b.failed(0.0)
    verdicts = []
    gate = threading.Barrier(workers)

    def unguarded():
        if b.opened_at is None:
            return True
        if b.probing:
            return False
        elapsed = 1.0 - b.opened_at >= b.cooldown
        if gap:
            time.sleep(gap)
        if elapsed:
            b.probing = True
            return True
        return False

    def ask():
        gate.wait()
        verdicts.append(b.allows(1.0) if locked else unguarded())

    threads = [threading.Thread(target=ask) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return sum(1 for v in verdicts if v)


def test_half_open_lets_exactly_one_request_through_at_every_width():
    """The whole point of half open is that one request finds out."""
    for gap in RACE_WIDTHS:
        through = _half_open_arm(locked=True, gap=gap)
        assert through == 1, "%d of %d got through at a gap of %s" % (
            through, RACERS, gap)


def test_the_half_open_race_is_real_at_some_width():
    """Otherwise the test above passes with the critical section deleted.

    Measured: the unguarded version lets one thread through at a gap of
    nought and all sixteen from two tenths of a millisecond upward. So a
    concurrency test written without a widened window grades nothing, which
    is what the first version of this file did.
    """
    at_zero = _half_open_arm(locked=False, gap=0.0)
    widened = [_half_open_arm(locked=False, gap=gap)
               for gap in RACE_WIDTHS if gap > 0]
    assert at_zero == 1, (
        "the race opened with no gap at all, so the arm at nought is no "
        "longer the control it was")
    assert max(widened) > 1, (
        "no width exercised the race, so the locked arms prove nothing: %s"
        % widened)


def test_a_failed_probe_restarts_the_cooldown_rather_than_reopening():
    clock = Clock()
    b = failover.Breaker(2, 10.0)
    b.failed(clock())
    b.failed(clock())
    assert b.opens == 1
    clock.now = 10.0
    assert b.allows(clock()), "the cooldown elapsed"
    b.failed(clock())
    assert b.opens == 1, "a failed probe is not a second open"
    assert not b.allows(clock()), "it should be shut again"
    clock.now = 19.9
    assert not b.allows(clock()), "the cooldown restarted at the probe"
    clock.now = 20.0
    assert b.allows(clock())


def test_the_pool_counters_add_up_to_the_traffic_under_threads():
    """What this grades is the arithmetic, and that is worth saying.

    The counters are incremented inside the pool's lock, because an increment
    is a read and a write and nothing in the language promises otherwise. This
    test does NOT prove the lock is load bearing. Run against a pool whose
    lock is replaced by a no-op, twelve trials at eight threads lost no
    increments at all, so the window is too narrow at this size to show up.
    The half open sweep above is the one that discriminates, and it only does
    so because it widens the gap on purpose.
    """
    pool = failover.Pool(
        [failover.Provider("dead", "http://127.0.0.1:1/"),
         failover.Provider("dead-too", "http://127.0.0.1:2/")],
        attempt_timeout=0.05)
    workers, each = 8, 12

    def drive():
        for _ in range(each):
            pool.send("hello", 16)

    threads = [threading.Thread(target=drive) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    counts = pool.counters()
    assert counts["requests_lost"] == workers * each
    assert counts["attempts"] == workers * each * 2
    assert counts["per_provider"]["dead"]["attempts"] == workers * each
    assert counts["per_provider"]["dead"]["refused"] == workers * each
