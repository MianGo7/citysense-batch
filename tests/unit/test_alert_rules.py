"""Unit tests of the alert rules, which need no database."""

from datetime import UTC, datetime, timedelta
from types import MappingProxyType

import pytest

from citysense.alert_rules import (
    ACKNOWLEDGED,
    BATCH_FAILED,
    HOUR,
    OPEN,
    RESOLVED,
    SENSOR_SILENT,
    THRESHOLD,
    ThresholdError,
    Thresholds,
    alert_id,
    batch_failed_alert,
    batch_failed_id,
    device_episodes,
    last_complete_hour,
    number,
    reconcile,
    resolved_alert,
    silent_episodes,
    threshold_episodes,
    to_alert,
)

DEVICE = "1c:bf:ce:15:ec:4d"
OTHER = "b8:27:eb:bf:9d:51"
START = datetime(2020, 7, 12, tzinfo=UTC)
LIMITS = {"temp": 28.0, "humidity": 85.0, "co": 0.0075, "lpg": 0.010, "smoke": 0.030}


def hour(number_):
    return START + number_ * HOUR


def thresholds(cap=10, coverage=0.5, **limits):
    return Thresholds.from_mapping(
        {
            "limits": limits or LIMITS,
            "silence": {"coverage": coverage},
            "cap": {"per_source_and_day": cap},
        }
    )


def row(number_, temp=None, coverage=1.0, expected=100.0):
    """The hourly summary of one hour, with only the fields that the rules read."""
    metrics = {"temp": {"mean": temp}} if temp is not None else {}
    return {
        "period_start": hour(number_),
        "reading_count": 0 if coverage is None else round(100 * coverage),
        "coverage": coverage,
        "expected_count": expected,
        "metrics": metrics,
    }


def temps(first, *values):
    """Rows of consecutive hours from `first`, one per mean."""
    return [row(first + step, temp=value) for step, value in enumerate(values)]


def episodes_of(rows, last=48, limit=28.0):
    return threshold_episodes(DEVICE, "temp", rows, limit, hour(last))


# The limits.


def test_the_limits_are_read_from_a_mapping_and_cannot_be_changed_afterwards():
    result = thresholds()
    assert dict(result.limits) == LIMITS
    assert (result.coverage, result.cap) == (0.5, 10)
    assert isinstance(result.limits, MappingProxyType)
    with pytest.raises(TypeError):
        result.limits["temp"] = 99.0


def test_a_metric_without_a_limit_is_allowed_and_means_that_it_is_not_judged():
    assert dict(thresholds(temp=28.0).limits) == {"temp": 28.0}


@pytest.mark.parametrize(
    "mapping",
    [
        {"silence": {"coverage": 0.5}, "cap": {"per_source_and_day": 10}},
        {"limits": {}, "silence": {"coverage": 0.5}},
        {"limits": {}, "silence": {"coverage": 0.5}, "cap": {"per_source_and_day": 10}, "x": {}},
        {"limits": {"noise": 1.0}, "silence": {"coverage": 0.5}, "cap": {"per_source_and_day": 10}},
        {"limits": {"temp": True}, "silence": {"coverage": 0.5}, "cap": {"per_source_and_day": 10}},
        {"limits": {"temp": "28"}, "silence": {"coverage": 0.5}, "cap": {"per_source_and_day": 10}},
        {
            "limits": {"temp": float("nan")},
            "silence": {"coverage": 0.5},
            "cap": {"per_source_and_day": 10},
        },
        {"limits": {}, "silence": {"coverage": 0}, "cap": {"per_source_and_day": 10}},
        {"limits": {}, "silence": {"coverage": 1.5}, "cap": {"per_source_and_day": 10}},
        {"limits": {}, "silence": {"share": 0.5}, "cap": {"per_source_and_day": 10}},
        {"limits": {}, "silence": {"coverage": 0.5}, "cap": {"per_source_and_day": 0}},
        {"limits": {}, "silence": {"coverage": 0.5}, "cap": {"per_source_and_day": True}},
        {"limits": {}, "silence": {"coverage": 0.5}, "cap": {"per_source_and_day": 2.5}},
        {"limits": {}, "silence": {"coverage": 0.5}, "cap": {}},
    ],
)
def test_a_value_that_cannot_be_used_is_refused(mapping):
    with pytest.raises(ThresholdError):
        Thresholds.from_mapping(mapping)


# The horizon.


def test_the_last_complete_hour_is_the_one_before_the_hour_of_the_newest_reading():
    horizon = datetime(2020, 7, 20, 0, 3, 37, 264000, tzinfo=UTC)
    assert last_complete_hour(horizon) == datetime(2020, 7, 19, 23, tzinfo=UTC)


def test_an_hour_that_ends_exactly_at_the_horizon_is_complete():
    assert last_complete_hour(datetime(2020, 7, 20, 1, tzinfo=UTC)) == datetime(
        2020, 7, 20, tzinfo=UTC
    )


def test_an_hour_after_the_horizon_is_not_judged_however_high_its_mean():
    rows = temps(10, 20.0, 20.0) + temps(12, 40.0)
    assert episodes_of(rows, last=11) == []


def test_the_open_hour_of_the_horizon_does_not_make_a_device_silent():
    rows = [row(number_) for number_ in range(10)]
    found = silent_episodes(DEVICE, rows, 0.5, hour(1), hour(9), baseline=True)
    assert found == []
    # The same series judged one hour further would find the missing hour.
    assert len(silent_episodes(DEVICE, rows, 0.5, hour(1), hour(10), baseline=True)) == 1


# Threshold episodes, deduplication and the clear hour.


def test_consecutive_hours_above_the_limit_are_one_episode_not_one_alert_per_hour():
    (episode,) = episodes_of(temps(10, 29.0, 31.0, 30.0))
    assert (episode.first, episode.last, episode.occurrences) == (hour(10), hour(12), 3)


def test_the_value_and_the_basis_of_an_episode_are_those_of_its_highest_hour():
    (episode,) = episodes_of(temps(10, 29.0, 31.0, 30.0))
    assert (episode.value, episode.basis) == (31.0, hour(11))


def test_a_mean_equal_to_the_limit_does_not_breach():
    assert episodes_of(temps(10, 28.0)) == []


def test_an_hour_without_a_mean_does_not_breach():
    assert episodes_of([row(10)]) == []


def test_one_clear_hour_ends_an_episode_and_a_later_breach_is_a_new_one():
    first, second = episodes_of(temps(10, 29.0, 27.0, 29.0))
    assert (first.first, first.last) == (hour(10), hour(10))
    assert (second.first, second.last) == (hour(12), hour(12))


def test_an_hour_that_is_missing_between_two_breaches_also_separates_them():
    rows = [row(10, temp=29.0), row(12, temp=29.0)]
    assert len(episodes_of(rows)) == 2


def test_an_episode_is_open_while_the_next_hour_is_not_complete_and_resolved_after_it():
    (episode,) = episodes_of(temps(10, 29.0))
    assert to_alert(episode, hour(10))["status"] == OPEN
    resolved = to_alert(episode, hour(11))
    assert resolved["status"] == RESOLVED
    # The clear hour is the hour 11, so the alert is resolved at the end of it.
    assert resolved["resolved_at"] == hour(12)


def test_an_open_alert_has_no_time_of_resolution():
    (episode,) = episodes_of(temps(10, 29.0))
    assert "resolved_at" not in to_alert(episode, hour(10))


# Sensor silent.


def test_an_hour_with_no_summary_at_all_counts_as_a_coverage_of_zero():
    rows = [row(10), row(12)]
    (episode,) = silent_episodes(DEVICE, rows, 0.5, hour(10), hour(12), baseline=False)
    assert (episode.first, episode.last, episode.value, episode.basis) == (
        hour(11),
        hour(11),
        0.0,
        None,
    )


def test_a_coverage_below_the_limit_is_silent_and_one_at_the_limit_is_not():
    rows = [row(10, coverage=0.49), row(11, coverage=0.5), row(12, coverage=0.51)]
    (episode,) = silent_episodes(DEVICE, rows, 0.5, hour(10), hour(12), baseline=True)
    assert (episode.first, episode.last, episode.value) == (hour(10), hour(10), 0.49)
    assert episode.basis == hour(10)


def test_a_silent_run_is_one_episode_with_its_lowest_coverage():
    rows = [row(10, coverage=0.3), row(12, coverage=0.1)]
    (episode,) = silent_episodes(DEVICE, rows, 0.5, hour(10), hour(12), baseline=True)
    assert (episode.first, episode.last, episode.occurrences) == (hour(10), hour(12), 3)
    assert episode.value == 0.0


def test_the_lowest_coverage_of_a_run_that_has_a_summary_names_that_summary():
    rows = [row(10, coverage=0.4), row(11, coverage=0.2)]
    (episode,) = silent_episodes(DEVICE, rows, 0.5, hour(10), hour(11), baseline=True)
    assert (episode.value, episode.basis) == (0.2, hour(11))


def test_without_a_baseline_a_missing_hour_is_unknown_and_not_silent():
    assert silent_episodes(DEVICE, [], 0.5, hour(10), hour(12), baseline=False) == []


def test_a_baseline_from_an_earlier_row_makes_a_later_missing_hour_silent():
    rows = [row(10)]
    found = silent_episodes(DEVICE, rows, 0.5, hour(10), hour(12), baseline=False)
    assert [(episode.first, episode.last) for episode in found] == [(hour(11), hour(12))]


def test_a_row_with_an_unknown_coverage_is_not_silent():
    rows = [row(10, coverage=None, expected=None)]
    assert silent_episodes(DEVICE, rows, 0.5, hour(10), hour(10), baseline=False) == []


def test_the_first_hour_of_a_device_is_not_judged_for_silence():
    rows = [row(10, coverage=0.1), row(11)]
    found = device_episodes(DEVICE, rows, thresholds(), hour(10), hour(10), hour(11), False)
    assert found == []


def test_device_episodes_collects_the_threshold_and_the_silent_episodes_of_a_device():
    rows = [row(10), row(11, temp=29.0), row(12, coverage=0.2)]
    found = device_episodes(DEVICE, rows, thresholds(), hour(10), hour(10), hour(12), True)
    assert sorted((episode.kind, episode.metric) for episode in found) == [
        (SENSOR_SILENT, None),
        (THRESHOLD, "temp"),
    ]


# The alert documents.


def test_the_identifier_has_the_type_the_source_the_metric_and_the_first_hour():
    assert alert_id(THRESHOLD, DEVICE, "temp", hour(10)) == (
        f"threshold|{DEVICE}|temp|2020-07-12T10:00:00Z"
    )
    assert alert_id(SENSOR_SILENT, DEVICE, None, hour(10)) == (
        f"sensor_silent|{DEVICE}|-|2020-07-12T10:00:00Z"
    )
    assert alert_id(BATCH_FAILED, "loader", None, None) == "batch_failed|loader|-|-"


def test_a_threshold_alert_carries_everything_that_the_alert_model_asks_for():
    (episode,) = episodes_of(temps(10, 28.4, 28.1))
    alert = to_alert(episode, hour(11))
    assert alert == {
        "_id": f"threshold|{DEVICE}|temp|2020-07-12T10:00:00Z",
        "type": "threshold",
        "severity": "warning",
        "status": "open",
        "source": DEVICE,
        "device": DEVICE,
        "metric": "temp",
        "value": 28.4,
        "threshold": 28.0,
        "basis": {
            "collection": "summaries_hourly",
            "_id": {"device": DEVICE, "period_start": hour(10)},
        },
        "message": f"Hourly mean temperature 28.4 above limit 28.0 at {DEVICE}",
        "recommended_action": "Check the location and inform the affected district.",
        "first_seen": hour(10),
        "last_seen": hour(11),
        "occurrences": 2,
    }


def test_a_silent_alert_says_that_the_values_are_unknown_and_has_no_metric():
    rows = [row(10, coverage=0.25)]
    (episode,) = silent_episodes(DEVICE, rows, 0.5, hour(10), hour(10), baseline=True)
    alert = to_alert(episode, hour(10))
    assert alert["_id"] == f"sensor_silent|{DEVICE}|-|2020-07-12T10:00:00Z"
    assert "metric" not in alert
    assert alert["message"] == f"Hourly coverage 0.25 below limit 0.5 at {DEVICE}"
    assert "unknown, not as normal" in alert["recommended_action"]
    assert (alert["severity"], alert["value"], alert["threshold"]) == ("warning", 0.25, 0.5)


def test_the_basis_of_an_hour_without_a_summary_is_null():
    (episode,) = silent_episodes(DEVICE, [row(10)], 0.5, hour(11), hour(11), baseline=True)
    assert to_alert(episode, hour(11))["basis"] is None


def test_the_documents_of_the_alerts_hold_no_wall_clock_time():
    (episode,) = episodes_of(temps(10, 29.0))
    alert = to_alert(episode, hour(30))
    times = {key: value for key, value in alert.items() if isinstance(value, datetime)}
    assert set(times) == {"first_seen", "last_seen", "resolved_at"}
    assert all(value.year == 2020 for value in times.values())


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (28.0, "28.0"),
        (28.4, "28.4"),
        (28.41237, "28.41"),
        (0.0075, "0.0075"),
        (0.5, "0.5"),
        (0, "0.0"),
    ],
)
def test_numbers_in_a_message_keep_a_decimal_point_and_four_significant_digits(value, text):
    assert number(value) == text


# Reconciling with the stored alerts.


def candidate(first, temp=29.0, device=DEVICE, metric_hours=1, last=48):
    rows = temps(first, *([temp] * metric_hours))
    (episode,) = threshold_episodes(device, "temp", rows, 28.0, hour(last))
    return to_alert(episode, hour(last))


def test_a_new_episode_is_inserted_and_counted_as_opened():
    plan = reconcile([candidate(10)], [], 10, hour(48))
    assert [alert["_id"] for alert in plan.inserts] == [candidate(10)["_id"]]
    assert (plan.opened, plan.resolved, plan.suppressed, plan.replacements) == (1, 1, 0, [])


def test_an_episode_that_is_stored_already_is_not_raised_again_and_not_rewritten():
    stored = candidate(10)
    plan = reconcile([candidate(10)], [stored], 10, hour(48))
    assert (plan.inserts, plan.replacements, plan.opened) == ([], [], 0)


def test_a_condition_that_persists_updates_the_open_alert_instead_of_raising_a_second_one():
    old = candidate(10, metric_hours=2, last=11)
    new = candidate(10, metric_hours=5, last=14)
    plan = reconcile([new], [old], 10, hour(14))
    assert plan.inserts == []
    ((before, after),) = plan.replacements
    assert before == old
    assert (after["occurrences"], after["last_seen"]) == (5, hour(14))
    assert after["status"] == OPEN


def test_an_alert_that_has_cleared_is_resolved_and_counted_once():
    old = candidate(10, last=10)
    new = candidate(10, last=12)
    plan = reconcile([new], [old], 10, hour(12))
    assert plan.resolved == 1
    assert plan.replacements[0][1]["status"] == RESOLVED
    # Evaluating again changes nothing, which is what makes a rerun safe.
    again = reconcile([new], [plan.replacements[0][1]], 10, hour(12))
    assert (again.inserts, again.replacements, again.resolved) == ([], [], 0)


def test_a_resolved_alert_is_final_whatever_a_later_evaluation_finds():
    stored = {**candidate(10, last=12), "value": 99.0}
    plan = reconcile([candidate(10, last=12)], [stored], 10, hour(12))
    assert (plan.inserts, plan.replacements) == ([], [])


def test_an_acknowledged_alert_stays_acknowledged_while_the_condition_persists():
    acknowledged_at = hour(11)
    stored = {
        **candidate(10, last=10),
        "status": ACKNOWLEDGED,
        "acknowledged_at": acknowledged_at,
    }
    new = candidate(10, metric_hours=3, last=12)
    ((_, after),) = reconcile([new], [stored], 10, hour(12)).replacements
    assert (after["status"], after["acknowledged_at"], after["occurrences"]) == (
        ACKNOWLEDGED,
        acknowledged_at,
        3,
    )


def test_an_acknowledged_alert_is_resolved_when_the_condition_clears_and_keeps_its_time():
    stored = {**candidate(10, last=10), "status": ACKNOWLEDGED, "acknowledged_at": hour(11)}
    ((_, after),) = reconcile([candidate(10, last=12)], [stored], 10, hour(12)).replacements
    assert (after["status"], after["acknowledged_at"]) == (RESOLVED, hour(11))


def test_an_unresolved_alert_without_an_episode_is_resolved_at_the_end_of_the_last_hour():
    stored = candidate(10, last=10)
    plan = reconcile([], [stored], 10, hour(20))
    ((before, after),) = plan.replacements
    assert before == stored
    assert (after["status"], after["resolved_at"]) == (RESOLVED, hour(21))
    assert plan.resolved == 1


def test_a_resolved_alert_without_an_episode_is_left_alone():
    stored = candidate(10, last=20)
    assert stored["status"] == RESOLVED
    assert reconcile([], [stored], 10, hour(20)).replacements == []


def test_the_alert_of_a_failed_batch_is_not_resolved_by_an_evaluation_of_the_summaries():
    stored = batch_failed_alert(None, START.date(), RuntimeError("down"), hour(5))
    assert reconcile([], [stored], 10, hour(20)).replacements == []


def many(count, device=DEVICE, day=0):
    """Episodes on different hours of one day, each separated by a clear hour."""
    rows = [row(day * 24 + step * 2, temp=29.0) for step in range(count)]
    return [
        to_alert(episode, hour(day * 24 + 2 * count + 2))
        for episode in threshold_episodes(
            device, "temp", rows, 28.0, hour(day * 24 + 2 * count + 2)
        )
    ]


def test_the_cap_keeps_the_earliest_alerts_of_a_source_and_counts_the_rest_as_suppressed():
    candidates = many(12)
    plan = reconcile(list(reversed(candidates)), [], 10, hour(48))
    assert len(plan.inserts) == 10
    assert plan.opened == 10
    assert plan.suppressed == 2
    assert {alert["first_seen"] for alert in plan.inserts} == {
        alert["first_seen"] for alert in candidates[:10]
    }


def test_alerts_that_are_stored_count_towards_the_cap_of_their_day():
    stored = many(10)
    extra = candidate(23)
    plan = reconcile([*stored, extra], stored, 10, hour(48))
    assert (plan.inserts, plan.suppressed) == ([], 1)


def test_the_cap_does_not_stop_an_alert_that_is_stored_from_being_updated():
    stored = [
        {**{key: value for key, value in alert.items() if key != "resolved_at"}, "status": OPEN}
        for alert in many(10)
    ]
    updated = [{**stored[0], "occurrences": 7}, *stored[1:]]
    plan = reconcile(updated, stored, 10, hour(48))
    assert (plan.suppressed, len(plan.replacements)) == (0, 1)


def test_the_cap_counts_per_source_and_per_day():
    other_source = many(10, device=OTHER)
    next_day = many(10, day=1)
    plan = reconcile([*many(10), *other_source, *next_day], [], 10, hour(96))
    assert (plan.opened, plan.suppressed) == (30, 0)


def test_a_cap_of_one_lets_the_first_alert_of_the_day_through():
    plan = reconcile(many(3), [], 1, hour(48))
    assert (plan.opened, plan.suppressed) == (1, 2)


# Alerts about the loader.

NOW = datetime(2026, 11, 14, 9, 30, tzinfo=UTC)
DAY = START.date()


def test_a_failed_batch_raises_a_critical_alert_with_the_source_loader():
    alert = batch_failed_alert(None, DAY, RuntimeError("the primary was lost"), NOW)
    assert alert == {
        "_id": "batch_failed|loader|-|2020-07-12T00:00:00Z",
        "type": "batch_failed",
        "severity": "critical",
        "status": "open",
        "source": "loader",
        "basis": {"collection": "batch_runs", "_id": "2020-07-12"},
        "message": "Batch 2020-07-12 failed: the primary was lost",
        "recommended_action": "Inspect the loader log and rerun the batch.",
        "first_seen": NOW,
        "last_seen": NOW,
        "occurrences": 1,
    }
    assert alert["_id"] == batch_failed_id(DAY)


def test_an_unreadable_source_has_no_batch_day_and_no_basis():
    alert = batch_failed_alert(None, None, ValueError("bad header"), NOW)
    assert alert["_id"] == "batch_failed|loader|-|-" == batch_failed_id(None)
    assert alert["basis"] is None
    assert alert["message"] == "The source file cannot be read: bad header"


def test_a_second_failure_of_the_same_batch_updates_the_alert_and_counts_an_occurrence():
    first = batch_failed_alert(None, DAY, RuntimeError("one"), NOW)
    later = NOW + timedelta(hours=1)
    second = batch_failed_alert(first, DAY, RuntimeError("two"), later)
    assert (second["occurrences"], second["first_seen"], second["last_seen"]) == (2, NOW, later)
    assert second["message"] == "Batch 2020-07-12 failed: two"


def test_a_failure_after_the_resolution_reopens_the_alert_and_keeps_its_history():
    resolved = resolved_alert(batch_failed_alert(None, DAY, RuntimeError("one"), NOW), NOW)
    reopened = batch_failed_alert(resolved, DAY, RuntimeError("two"), NOW)
    assert (reopened["status"], reopened["occurrences"]) == (OPEN, 2)
    assert "resolved_at" not in reopened


def test_an_acknowledged_failure_stays_acknowledged_when_it_happens_again():
    first = {**batch_failed_alert(None, DAY, RuntimeError("one"), NOW), "status": ACKNOWLEDGED}
    assert batch_failed_alert(first, DAY, RuntimeError("two"), NOW)["status"] == ACKNOWLEDGED


def test_the_error_in_the_message_is_cut_to_a_short_text():
    alert = batch_failed_alert(None, DAY, RuntimeError("x" * 5000), NOW)
    assert len(alert["message"]) < 300


def test_resolving_sets_the_time_and_does_nothing_to_an_alert_that_is_resolved():
    alert = batch_failed_alert(None, DAY, RuntimeError("one"), NOW)
    resolved = resolved_alert(alert, NOW + HOUR)
    assert (resolved["status"], resolved["resolved_at"]) == (RESOLVED, NOW + HOUR)
    assert resolved_alert(resolved, NOW + 2 * HOUR) is None
    assert resolved_alert(None, NOW) is None
