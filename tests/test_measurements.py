import hashlib
import json
from pathlib import Path

WALL_TIME_WORDS = frozenset((
    "seconds", "second", "ms", "millis", "milliseconds", "elapsed", "duration",
    "wall", "latency", "latencies", "micros", "nanos", "took", "runtime",
))


def names_a_wall_time(key):
    """Whether a manifest key is claiming to hold a time.

    The parts of the name are compared whole. Matching the letters anywhere in
    the key refuses `arms` for carrying `ms`, and a gate that fires on an
    innocent name is a gate somebody switches off.
    """
    return any(part in WALL_TIME_WORDS for part in key.lower().replace("-", "_").split("_"))


def test_there_are_measurements_to_grade(manifests):
    assert len(manifests) >= 4, "expected the baseline, both pattern slices and the evasion pass"


def test_each_scored_manifest_names_a_corpus_that_still_hashes_the_same(manifests, root):
    """A figure measured against a corpus that has since changed is not a figure."""
    graded = 0
    for stem, m in manifests.items():
        if "corpus_sha256" not in m:
            continue
        graded += 1
        path = root / m["corpus_path"]
        assert path.exists(), "%s names a corpus that is not here: %s" % (stem, path)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == m["corpus_sha256"], (
            "%s was measured against a different %s, rerun the scorer"
            % (stem, m["corpus_path"]))
    assert graded >= 3, "only %d manifests carried a corpus hash" % graded


def test_no_manifest_carries_a_wall_time(manifests):
    """A count reproduces on another machine. A millisecond does not."""
    def walk(node, where):
        if isinstance(node, dict):
            for k, v in node.items():
                assert not names_a_wall_time(k), "%s carries a timing key %r" % (where, k)
                walk(v, where)
        elif isinstance(node, list):
            for v in node:
                walk(v, where)
    for stem, m in manifests.items():
        walk(m, stem)


def test_every_rate_is_published_as_two_counts(manifests):
    for stem, m in manifests.items():
        for section in ("phi", "injection"):
            block = m.get(section, {})
            for key in ("precision", "recall"):
                if key in block:
                    assert set(block[key]) == {"numerator", "denominator"}, (
                        "%s %s %s is not a pair of counts" % (stem, section, key))


def test_the_evasion_pass_controls_fired(manifests):
    """Every attempt getting through is also what a broken harness looks like."""
    controls = manifests["evasion"]["controls"]
    assert all(controls.values()), "a control did not fire, so the pass graded nothing: %r" % controls


def test_the_baseline_is_worse_than_the_patterns(manifests):
    """If it is not, the patterns are not doing anything the floor does not."""
    base = manifests["holdout-baseline"]["phi"]["recall"]
    real = manifests["holdout-patterns"]["phi"]["recall"]
    assert base["denominator"] == real["denominator"]
    assert real["numerator"] > base["numerator"]


def test_the_scored_slice_is_not_the_slice_the_patterns_were_written_against(manifests):
    dev = manifests["development-patterns"]["corpus_sha256"]
    hold = manifests["holdout-patterns"]["corpus_sha256"]
    assert dev != hold, "both slices hash the same, so nothing is held out"


def test_the_split_probe_controls_fired(manifests):
    """One cut the shipped setting must survive and one it must fail."""
    controls = manifests["split-probe"]["controls"]
    assert all(controls.values()), "a control did not fire, so the pass graded nothing: %r" % controls


def test_the_buffer_sweep_graded_streams_and_not_closes(manifests):
    m = manifests["stream-buffers"]
    assert m["oracle_spans_total"] > 0, "no identifiers in the bodies, so a zero leak count means nothing"
    assert m["bodies_not_graded_as_a_stream"] == 0, (
        "%d bodies never reached the emit point, so those arms graded the whole body redactor"
        % m["bodies_not_graded_as_a_stream"])


def test_a_wider_buffer_never_leaks_more(manifests):
    for key in ("by_holdback", "by_lookbehind"):
        arms = manifests["stream-buffers"][key]
        counts = [a["leaked_spans"] for a in arms]
        assert counts == sorted(counts, reverse=True), (
            "%s is not monotone, which means the sweep or the redactor is wrong: %r" % (key, counts))


def test_the_sweep_still_has_a_leaking_arm_to_compare_against(manifests):
    """A sweep where every arm is clean has stopped measuring anything."""
    arms = manifests["stream-buffers"]["by_holdback"] + manifests["stream-buffers"]["by_lookbehind"]
    assert any(a["leaked_spans"] > 0 for a in arms)


def test_the_load_run_offered_more_than_the_budget(manifests):
    """A budget the load never reaches reports perfect reconciliation."""
    m = manifests["quota-load"]
    assert m["offered_estimate_tokens"] > m["budget_tokens"]


def test_the_forecast_policy_reserves_less_than_the_ceiling_policy(manifests):
    """If it does not, the two arms are the same arm under two names."""
    m = manifests["quota-load"]
    assert m["forecast_short_by_tokens"]["low"] > m["prompt_estimator_short_by_tokens"]["high"]


def test_the_forecast_policy_overspent_on_every_repeat(manifests):
    """A spread whose low is nought is a figure that is sometimes nothing."""
    band = manifests["quota-load"]["overshoot_under_forecast_tokens"]
    assert band["runs"] >= 3, "one interleaving is not a spread"
    assert band["low"] > 0, (
        "the forecast policy overspent nothing on at least one repeat, so the published "
        "figure is not a property of the policy: %r" % band)


def test_dropping_the_lock_stopped_the_quota_refusing(manifests):
    m = manifests["quota-load"]
    assert m["admitted_without_the_lock"]["low"] > m["admitted_with_the_lock"]["high"], (
        "the unserialised arm did not admit more than the serialised one, so the race "
        "the lock exists to stop did not happen")
    assert m["admissions_the_lock_refused"] > 0


def test_every_load_arm_reached_the_upstream_over_the_socket(manifests):
    for arm in manifests["quota-load"]["arms"]:
        forwarded = arm["admitted"] - arm["blocked_by_guardrail"]
        assert arm["upstream_served"] == forwarded, (
            "%s forwarded %d and the upstream served %d"
            % (arm["arm"], forwarded, arm["upstream_served"]))


def test_no_load_arm_lost_a_request_in_transport(manifests):
    m = manifests["quota-load"]
    for row in m["arms"] + m["admission_limit_sweep"]:
        assert row["transport_failures"] == 0, row


def test_a_cap_below_the_offered_concurrency_admits_far_fewer(manifests):
    """The step, which is the claim. Not monotonicity, which is not.

    The first version of this asserted that admissions rise with the limit. It
    failed on a run where a cap of 2 admitted more than a cap of 4 did. Inside
    the binding region
    almost everything is refused and which requests win a slot is noise, so
    those three are one number measured three times. The ordered claim was a
    property the measurement does not have.
    """
    m = manifests["quota-load"]
    sweep = m["admission_limit_sweep"]
    assert len(sweep) >= 4, "a sweep of %d arms is not a sweep" % len(sweep)
    binding = [r for r in sweep if r["limit"] < m["workers"]]
    free = [r for r in sweep if r["limit"] >= m["workers"]]
    assert binding and free, "the sweep has no step in it: %r" % sweep
    assert min(r["admitted"] for r in free) > max(r["admitted"] for r in binding), (
        "the caps at or above %d did not admit more than the caps below it: %r"
        % (m["workers"], [(r["limit"], r["admitted"]) for r in sweep]))


def test_every_binding_cap_refused_and_no_free_cap_did(manifests):
    m = manifests["quota-load"]
    for row in m["admission_limit_sweep"]:
        if row["limit"] < m["workers"]:
            assert row["refused_overloaded"] > 0, (
                "a cap of %d is below %d offered and refused nothing" % (row["limit"], m["workers"]))
        else:
            assert row["refused_overloaded"] == 0, (
                "a cap of %d is at or above %d offered and refused %d"
                % (row["limit"], m["workers"], row["refused_overloaded"]))


def test_the_race_needs_a_window_and_the_sweep_says_so(manifests):
    """The arm at nought is the one that mattered and it reversed the finding.

    The race term is a function of a constant the load script sets. The sweep
    was written to check whether the finding rested on it and the answer is
    that it does. With no artificial window the unserialised arm admits about what
    the locked one admits, because the interpreter does not leave the read and
    the write far enough apart to matter. Open a window of two tenths of a
    millisecond and the quota stops refusing almost everything.

    So the honest claim is not that dropping the lock breaks the quota. It is
    that dropping the lock is free in one process and total the moment the
    counter is a network hop away, which is where a shared counter always is.
    """
    m = manifests["quota-load"]
    rows = m["store_round_trip_sweep"]
    assert len(rows) >= 3, "a sweep of %d widths is not a sweep" % len(rows)
    by_width = {r["store_round_trip_setting"]: r for r in rows}
    assert 0 in by_width, "no arm ran without an artificial window, so this graded nothing"
    for row in rows:
        assert row["transport_failures"] == 0, row
        assert row["admitted_with_the_lock"] < m["requests_graded"], (
            "the locked control at round trip %g admitted everything, so the unserialised arm "
            "at that width had nothing to get past" % row["store_round_trip_setting"])

    widest = by_width[max(by_width)]
    narrowest = by_width[0]
    assert widest["admitted_without_the_lock"] == m["requests_graded"], (
        "at the widest window the unserialised arm admitted %d of %d rather than all of them"
        % (widest["admitted_without_the_lock"], m["requests_graded"]))
    assert narrowest["admitted_without_the_lock"] < m["requests_graded"], (
        "with no window at all the unserialised arm still admitted every request, so the race "
        "does not need the window and this test has the story backwards")

    def gap(row):
        return row["admitted_without_the_lock"] - row["admitted_with_the_lock"]

    assert gap(narrowest) < gap(widest), (
        "the gap did not widen with the window, %d at nought against %d at %g"
        % (gap(narrowest), gap(widest), max(by_width)))


def test_the_admission_sweep_has_a_binding_arm_and_a_free_one(manifests):
    """A sweep where every cap binds, or none does, compares nothing."""
    sweep = manifests["quota-load"]["admission_limit_sweep"]
    assert any(row["refused_overloaded"] > 0 for row in sweep)
    assert any(row["refused_overloaded"] == 0 for row in sweep)


def test_the_load_reached_the_worker_count_in_detection(manifests):
    m = manifests["quota-load"]
    widest = m["admission_limit_sweep"][-1]
    assert widest["peak_in_detection"] >= m["workers"], (
        "the widest cap peaked at %d against %d workers, so a cap at the worker count was "
        "never tested" % (widest["peak_in_detection"], m["workers"]))


TIMING_KEYS_THAT_MUST_BE_REFUSED = (
    "latency_ms", "p95_ms", "median_ms", "elapsed_seconds", "duration",
    "wall_clock", "total_duration_ms", "request_latency", "took_ms",
    "p50-ms", "runtime_seconds",
)

ORDINARY_KEYS_THAT_MUST_BE_PERMITTED = (
    "arms", "terms", "items", "forms", "msgs_total", "admissions_refused",
    "overshoot_tokens", "spans", "store_reads", "peak_in_detection",
    "shortfall_tokens", "limit", "workers",
)


def test_the_timing_refusal_fires_on_a_timing_name():
    """The gate is a function over a key, so it gets graded on keys."""
    missed = [k for k in TIMING_KEYS_THAT_MUST_BE_REFUSED if not names_a_wall_time(k)]
    assert not missed, "these would reach a manifest unchallenged: %r" % missed


def test_the_timing_refusal_leaves_an_ordinary_name_alone():
    flagged = [k for k in ORDINARY_KEYS_THAT_MUST_BE_PERMITTED if names_a_wall_time(k)]
    assert not flagged, "the gate refuses these and none of them is a time: %r" % flagged
