"""Unit tests of the pipelines and of the expected counts, without a database."""

from datetime import UTC, datetime, timedelta

import pytest

from citysense.aggregation import (
    DAILY,
    HOURLY,
    LATEST,
    expected_count,
    expected_counts,
    expected_daily_count,
    hour_counts_pipeline,
    latest_pipeline,
    summary_pipeline,
)

START = datetime(2020, 7, 13, tzinfo=UTC)
END = START + timedelta(days=1)
DEVICES = ["b", "a"]


# The expected count of an hour.


def test_the_first_hour_of_a_device_is_dropped_from_its_baseline():
    assert expected_count([10, 580, 590, 600], []) == 590.0


def test_the_baseline_is_the_median_and_not_the_mean():
    # An outage of ten hours lowers the mean to 527 and leaves the median alone,
    # so the mean would let a healthy hour look better than it is and hide the outage.
    counts = [1] + [580] * 100 + [0] * 10
    assert expected_count(counts, []) == 580.0
    assert sum(counts[1:]) / len(counts[1:]) < 530


def test_an_even_number_of_hours_gives_a_median_between_the_two_middle_values():
    assert expected_count([1, 570, 580], []) == 575.0


def test_a_device_without_earlier_hours_takes_the_baseline_from_its_own_full_hours():
    own = [(40, True), (580, True), (600, True), (590, True), (20, False)]
    # The first hour is partial because the device starts at an arbitrary moment,
    # and the last one ends after the newest reading of the batch.
    assert expected_count([], own) == 590.0


def test_a_device_with_one_earlier_hour_does_not_drop_the_first_of_its_own_hours():
    assert expected_count([40], [(580, True), (600, True)]) == 590.0


def test_there_is_no_baseline_without_a_full_hour():
    assert expected_count([], [(40, True), (20, False)]) is None
    assert expected_count([], []) is None
    assert expected_count([5], [(20, False)]) is None


def test_the_baseline_does_not_depend_on_the_hours_of_the_batch_when_there_are_earlier_ones():
    earlier = [1, 580, 590]
    assert expected_count(earlier, [(1, True), (1, True)]) == expected_count(earlier, [])


# The expected count of a day.


def test_the_daily_baseline_is_the_median_of_the_earlier_days_without_the_first():
    assert expected_daily_count([100, 13900, 14000, 13800], 580.0) == 13900.0


def test_the_daily_baseline_falls_back_on_24_hours_of_the_hourly_baseline():
    assert expected_daily_count([13000], 580.0) == 13920.0
    assert expected_daily_count([], 580.0) == 13920.0
    assert expected_daily_count([], None) is None


# The baselines of a whole batch.


def row(device, hour, count, last):
    start = START + timedelta(hours=hour)
    return {
        "_id": {"device": device, "period_start": start},
        "reading_count": count,
        "last_ts": START + timedelta(hours=last),
    }


def test_the_horizon_is_the_newest_reading_of_the_batch_over_all_devices():
    # Device a reports until hour 2 and device b until hour 4, so the horizon is
    # that of b, and the hour 3 of b is complete while the hour 4 is not.
    rows = [
        row("a", 0, 40, 2),
        row("a", 1, 580, 2),
        row("a", 2, 580, 2),
        row("b", 0, 40, 4),
        row("b", 1, 970, 4),
        row("b", 2, 975, 4),
        row("b", 3, 980, 4),
        row("b", 4, 12, 4),
    ]
    hourly, daily = expected_counts(["a", "b"], rows, {"a": [], "b": []}, {"a": [], "b": []})
    assert hourly == {"a": 580.0, "b": 975.0}
    assert daily == {"a": 580.0 * 24, "b": 975.0 * 24}


def test_a_device_that_the_day_does_not_cover_with_a_full_hour_has_no_baseline():
    rows = [row("a", 0, 12, 0)]
    assert expected_counts(["a"], rows, {"a": []}, {"a": []}) == ({"a": None}, {"a": None})


# The pipelines.


def test_every_pipeline_selects_by_device_and_time_first_so_that_an_index_serves_it():
    for pipeline in (
        hour_counts_pipeline(DEVICES, START, END),
        summary_pipeline("hour", DEVICES, START, END, {}, HOURLY),
        latest_pipeline(DEVICES, START, END),
    ):
        assert pipeline[0]["$match"]["device"] == {"$in": ["a", "b"]}
        assert pipeline[0]["$match"]["ts"] == {"$gte": START, "$lt": END}


def test_the_summaries_are_sorted_before_they_are_grouped_for_a_stable_sum():
    stages = [
        next(iter(stage)) for stage in summary_pipeline("day", DEVICES, START, END, {}, DAILY)
    ]
    assert stages[:3] == ["$match", "$sort", "$group"]
    assert stages[-1] == "$merge"


def test_the_summaries_are_merged_into_the_collection_of_their_unit_and_replace_the_old_ones():
    pipeline = summary_pipeline("hour", DEVICES, START, END, {}, HOURLY)
    assert pipeline[-1]["$merge"] == {
        "into": HOURLY,
        "on": "_id",
        "whenMatched": "replace",
        "whenNotMatched": "insert",
    }
    group = next(stage["$group"] for stage in pipeline if "$group" in stage)
    assert group["_id"]["period_start"] == {"$dateTrunc": {"date": "$ts", "unit": "hour"}}


def test_the_statistics_cover_valid_readings_only():
    pipeline = summary_pipeline("hour", DEVICES, START, END, {}, HOURLY)
    group = next(stage["$group"] for stage in pipeline if "$group" in stage)
    valid = {"$eq": [{"$size": "$quality.flags"}, 0]}
    assert group["temp_mean"] == {"$avg": {"$cond": [valid, "$temp", None]}}
    assert group["reading_count"] == {"$sum": {"$cond": [valid, 1, 0]}}
    assert group["flagged_count"] == {"$sum": {"$cond": [valid, 0, 1]}}
    assert set(group) >= {"light_true", "motion_true", "co_min", "smoke_max", "lpg_mean"}


def test_the_expected_count_of_a_device_without_a_baseline_is_left_out_of_the_switch():
    pipeline = summary_pipeline("hour", DEVICES, START, END, {"a": 580.0, "b": None}, HOURLY)
    fields = next(
        stage["$addFields"] for stage in pipeline if "expected_count" in stage.get("$addFields", {})
    )
    branches = fields["expected_count"]["$switch"]["branches"]
    assert [branch["then"] for branch in branches] == [580.0]
    assert fields["expected_count"]["$switch"]["default"] is None


def test_no_baseline_at_all_gives_a_null_expected_count():
    pipeline = summary_pipeline("hour", DEVICES, START, END, {"a": None}, HOURLY)
    fields = next(
        stage["$addFields"] for stage in pipeline if "expected_count" in stage.get("$addFields", {})
    )
    assert fields == {"expected_count": None}


def test_a_device_name_is_never_read_as_a_field_path():
    pipeline = summary_pipeline("hour", ["$odd"], START, END, {"$odd": 1.0}, HOURLY)
    fields = next(
        stage["$addFields"] for stage in pipeline if "expected_count" in stage.get("$addFields", {})
    )
    case = fields["expected_count"]["$switch"]["branches"][0]["case"]
    assert case == {"$eq": ["$device", {"$literal": "$odd"}]}


def test_the_latest_value_is_replaced_only_by_a_reading_that_is_not_older():
    pipeline = latest_pipeline(DEVICES, START, END)
    merge = pipeline[-1]["$merge"]
    assert merge["into"] == LATEST
    (stage,) = merge["whenMatched"]
    assert stage == {"$replaceWith": {"$cond": [{"$gte": ["$$new.ts", "$ts"]}, "$$new", "$$ROOT"]}}
    assert pipeline[0]["$match"]["quality.flags"] == {"$size": 0}


@pytest.mark.parametrize("unit", ["hour", "day"])
def test_the_period_of_a_summary_is_truncated_to_the_unit_in_utc(unit):
    pipeline = summary_pipeline(unit, DEVICES, START, END, {}, HOURLY)
    group = next(stage["$group"] for stage in pipeline if "$group" in stage)
    assert group["_id"]["period_start"]["$dateTrunc"]["unit"] == unit
