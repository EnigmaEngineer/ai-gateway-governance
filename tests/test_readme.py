"""Every figure this repository publishes, re-derived from the manifests.

A number that is typed is a number that drifts. Each assertion here carries the
correct value in its message, so a failure says what the text should say rather
than only that it is wrong.

The measurement sections were split out of the README late in the build. These
checks read the README and the documents together, through the `published`
fixtures, because a check anchored to one filename is an argument for keeping
every number on that page. Three checks below still read `README.md` alone and
each says why, and they are about the front page rather than about a figure.
"""

import re

from gateway import policy


def _pairs(text):
    """Every `a / b` the README publishes, as integer pairs."""
    return set(
        (int(a), int(b))
        for a, b in re.findall(r"(\d+)\s*/\s*(\d+)", text)
    )




def test_the_published_documents_carry_figures_to_grade(published):
    assert _pairs(published), "no figures found, so this check graded nothing"


def test_every_rate_in_the_table_matches_its_manifest(published, manifests):
    pairs = _pairs(published)
    expected = []
    for stem, label in (("holdout-patterns", "held out slice"),
                        ("holdout-baseline", "crude floor")):
        m = manifests[stem]
        for section in ("phi", "injection"):
            for key in ("recall", "precision"):
                r = m[section][key]
                expected.append((label, section, key, r["numerator"], r["denominator"]))
    for label, section, key, num, den in expected:
        assert (num, den) in pairs, (
            "nothing published states the %s %s %s, which is %d / %d"
            % (label, section, key, num, den))


def test_the_row_count_in_the_table_is_the_row_count_measured(published, manifests):
    rows = manifests["holdout-patterns"]["rows"]
    assert "%d rows" % rows in published, (
        "the published table should say %d rows, which is what was scored" % rows)


def test_the_weakest_attack_class_is_named_with_its_real_figure(published, manifests):
    per_class = manifests["holdout-patterns"]["injection"]["per_class_recall"]
    worst = min(per_class.items(), key=lambda kv: kv[1]["caught"] / kv[1]["planted"])
    name, counts = worst
    assert name.replace("_", " ") in published.lower(), (
        "the weakest attack class is %s and nothing published names it" % name)
    assert "%d of %d" % (counts["caught"], counts["planted"]) in published, (
        "%s is %d of %d and the text says otherwise"
        % (name, counts["caught"], counts["planted"]))


def test_the_missed_identifier_count_is_the_measured_one(published, manifests):
    per_class = manifests["holdout-patterns"]["phi"]["per_class"]
    missed = {k: v["missed"] for k, v in per_class.items() if v["missed"]}
    total = sum(missed.values())
    assert len(missed) == 1, (
        "the limitations say every miss is one case and the misses are %r" % missed)
    assert "%d of the %d missed" % (total, total) in published, (
        "the limitations should say %d of the %d missed identifiers" % (total, total))


def test_the_evasion_counts_are_the_measured_ones(published, manifests):
    e = manifests["evasion"]
    attempts = e["injection"]["attempts"] + e["phi"]["attempts"]
    through = e["injection"]["got_through"] + e["phi"]["got_through"]
    text = published.lower()
    assert "22 of" in text or "twenty two" in text, (
        "nothing published states the attempt count, which is %d" % attempts)
    assert attempts == 22, "the manifest says %d attempts" % attempts
    assert "20 of 22 evasion attempts walked through" in text and through == 20, (
        "the front page should say 20 of 22 walked through and the manifest says %d"
        % through)


def test_the_attack_class_count_in_the_diagram_matches_the_detector(root):
    """README.md on purpose. The diagram is on the front page and nowhere else.

    The block gate cannot grade a drawing, so this is the only thing checking
    the two counts inside it.
    """
    from gateway import injection
    text = (root / "README.md").read_text(encoding="utf-8")
    assert "%d attack classes" % len(injection.CLASSES) in text


def test_the_class_counts_in_the_diagram_match_the_policy_module(root):
    """README.md on purpose, for the same reason as the check above."""
    text = re.sub(r"[ \t]*\n[ \t]*", " ", (root / "README.md").read_text(encoding="utf-8"))
    assert "%d enforced classes" % len(policy.ENFORCED) in text
    assert "%d declared omissions" % len(policy.DECLARED_OMISSIONS) in text
    assert "the eighteen classes" in text and len(policy.SAFE_HARBOR) == 18


def _band_line(key, band):
    """The spread line as the load run prints it, which is what the README shows."""
    return "%-38s %6d to %6d, median %6d over %d runs" % (
        key, band["low"], band["high"], band["median"], band["runs"])


def test_the_quota_spreads_are_the_measured_ones(published_raw, manifests):
    m = manifests["quota-load"]
    text = published_raw
    shown = 0
    for key in ("prompt_estimator_short_by_tokens", "forecast_short_by_tokens",
                "overshoot_under_forecast_tokens"):
        line = _band_line(key, m[key])
        assert line.strip() in text, "a published document should carry this line: %r" % line
        shown += 1
    assert shown == 3, "only %d spreads were graded" % shown


def test_the_budget_and_the_load_shape_are_the_measured_ones(published, manifests):
    m = manifests["quota-load"]
    text = published
    assert "{:,} tokens".format(m["budget_tokens"]) in text, (
        "the budget is %d tokens" % m["budget_tokens"])
    assert "%d held out bodies" % m["requests_graded"] in text, (
        "the run offered %d bodies" % m["requests_graded"])
    assert "%d concurrent" % m["workers"] in text
    assert "three repeats" in text and m["repeats"] == 3, (
        "the text says three repeats and the run did %d" % m["repeats"])


def test_the_race_claim_is_the_measured_one(published_raw, manifests):
    """The round trip sweep is published, so grade every row of it.

    An earlier version of this graded a sentence saying the lock was load
    bearing full stop. The sweep showed that claim was true only at a nonzero
    window, the sentence went, and this check went with it. A check written
    against a sentence dies when the sentence is wrong, which is the point.
    """
    m = manifests["quota-load"]
    raw = published_raw
    rows = m["store_round_trip_sweep"]
    assert len(rows) >= 3, "a sweep of %d widths is not a sweep" % len(rows)
    for row in rows:
        line = "  round trip %-8g admitted without the lock %4d  with it %4d" % (
            row["store_round_trip_setting"], row["admitted_without_the_lock"],
            row["admitted_with_the_lock"])
        assert line in raw, "a published document should carry this row: %r" % line


def test_nothing_published_claims_the_lock_matters_in_one_process(published, manifests):
    """The claim the sweep refuted must not come back by a different sentence."""
    m = manifests["quota-load"]
    narrowest = [r for r in m["store_round_trip_sweep"]
                 if r["store_round_trip_setting"] == 0][0]
    text = published.lower()
    assert narrowest["admitted_without_the_lock"] < m["requests_graded"], (
        "with no window the unlocked arm admitted everything, so the hedge is now "
        "the wrong way round")
    assert "free in one process" in text, (
        "the text has to say the race needs a window, because at nought the unlocked arm "
        "admitted %d against %d locked"
        % (narrowest["admitted_without_the_lock"], narrowest["admitted_with_the_lock"]))


def test_the_blocked_share_behind_the_slack_claim_is_the_measured_one(published, manifests):
    """A nought overshoot is explained with a blocked count, so grade it."""
    ceiling = [a for a in manifests["quota-load"]["arms"] if a["arm"] == "ceiling"]
    blocked = sorted(a["blocked_by_guardrail"] for a in ceiling)
    text = published
    assert "%d of the %d bodies are blocked" % (
        blocked[len(blocked) // 2], manifests["quota-load"]["requests_graded"]) in text, (
        "the ceiling arm blocked %r across its repeats" % blocked)


def test_the_admission_sweep_claims_are_the_measured_ones(published, manifests):
    m = manifests["quota-load"]
    text = published
    limits = [row["limit"] for row in m["admission_limit_sweep"]]
    assert "Caps of %s and %d at %d workers" % (
        ", ".join(str(x) for x in limits[:-2]), limits[-2], m["workers"]) in text, (
        "the sweep ran limits %r" % limits)
    assert "below %d refuses" % m["workers"] in text
    assert "generator with %d workers offers %d" % (m["workers"], m["workers"]) in text


STREAM_TABLE_HEADER = "holdback  lookbehind  leaked  diverged"


def _stream_table_rows(text):
    """The four column rows of every buffer table, wherever it is printed.

    The table moved to docs/streaming.md and that document carries two of
    them, a holdback sweep and a lookbehind sweep, so this walks every
    occurrence of the header rather than the first.
    """
    lines = text.splitlines()
    rows = []
    for i, line in enumerate(lines):
        if line.strip() != STREAM_TABLE_HEADER:
            continue
        for after in lines[i + 1:]:
            parts = after.split()
            if len(parts) != 4 or not all(p.isdigit() for p in parts):
                break
            rows.append(tuple(int(p) for p in parts))
    assert rows, "no buffer table found under the header, so this graded nothing"
    return rows


def test_the_buffer_table_is_the_measured_one(published_raw, manifests):
    """The only thing grading this table used to be a capture directory.

    That directory is not in the repository and it does not survive the run
    that wrote it, so on any later day the table was unverifiable. Comparing it
    to the committed manifest travels with the repository instead.
    """
    rows = _stream_table_rows(published_raw)
    assert len(rows) >= 4, "found %d rows under the table header, so this graded nothing" % len(rows)
    arms = (manifests["stream-buffers"]["by_holdback"]
            + manifests["stream-buffers"]["by_lookbehind"])
    measured = {(a["holdback_chars"], a["lookbehind_chars"],
                 a["leaked_spans"], a["bodies_diverged"]) for a in arms}
    for row in rows:
        assert row in measured, (
            "a document publishes holdback %d lookbehind %d as %d leaked and %d diverged, "
            "and no arm in the manifest reads that" % row)


def test_the_buffer_table_rows_are_arms_that_were_really_swept(published_raw, manifests):
    """A row matching an arm by coincidence is still a row nobody swept."""
    rows = _stream_table_rows(published_raw)
    swept = {(a["holdback_chars"], a["lookbehind_chars"])
             for a in manifests["stream-buffers"]["by_holdback"]}
    swept |= {(a["holdback_chars"], a["lookbehind_chars"])
              for a in manifests["stream-buffers"]["by_lookbehind"]}
    shown = {(h, lb) for h, lb, _, _ in rows}
    assert shown <= swept, (
        "these settings are published and in no sweep arm: %r" % sorted(shown - swept))
