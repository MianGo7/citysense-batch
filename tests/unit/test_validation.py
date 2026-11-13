"""Unit tests of the plausibility rules, on synthetic readings."""

import math

import pytest

from citysense.batches import Reading
from citysense.validation import (
    JUMP_LIMITS,
    MAX_CONSECUTIVE_JUMP_FLAGS,
    MAX_GAP_MS,
    DeviceState,
    check_reading,
    flag_readings,
)

STEP_MS = 5000
NORMAL = {
    "co": 0.005,
    "humidity": 50.0,
    "lpg": 0.008,
    "smoke": 0.02,
    "temp": 20.0,
    "light": False,
    "motion": False,
}


def reading(number=0, device="d1", **metrics):
    """The `number`th reading of a device, five seconds apart, normal unless changed."""
    ms = 1_594_512_000_000 + number * STEP_MS
    return Reading(f"{device}|{ms}", device, ms, {**NORMAL, **metrics})


def flags_of(*readings):
    flags, _ = flag_readings(readings)
    return [list(each) for each in flags]


# The range rule.


@pytest.mark.parametrize(
    ("metric", "value"),
    [
        ("temp", -40.0),
        ("temp", 60.0),
        ("humidity", 0.0),
        ("humidity", 100.0),
        ("co", 0.0),
        ("lpg", 0.0),
        ("smoke", 0.0),
    ],
)
def test_the_bounds_themselves_are_plausible(metric, value):
    assert check_reading(reading(**{metric: value})).flags == ()


@pytest.mark.parametrize(
    ("metric", "value"),
    [
        ("temp", -40.1),
        ("temp", 60.1),
        ("humidity", -0.1),
        ("humidity", 100.1),
        ("co", -0.0001),
        ("lpg", -0.0001),
        ("smoke", -0.0001),
    ],
)
def test_a_value_outside_the_range_is_flagged_with_the_name_of_the_metric(metric, value):
    assert check_reading(reading(**{metric: value})).flags == (f"{metric}_range",)


@pytest.mark.parametrize("metric", ["co", "lpg", "smoke"])
def test_the_gases_have_no_upper_bound(metric):
    # The unit "ppm (%)" is not explained by the source, so no ceiling is justified.
    assert check_reading(reading(**{metric: 1e9})).flags == ()


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("metric", ["temp", "humidity", "co"])
def test_a_value_that_is_not_finite_is_flagged(metric, value):
    assert f"{metric}_range" in check_reading(reading(**{metric: value})).flags


def test_a_low_humidity_is_not_a_range_violation():
    # The readings below 10 percent are caught by the jump rule, see ADR-0011.
    assert check_reading(reading(humidity=1.1)).flags == ()


def test_several_violations_are_all_reported_in_a_fixed_order():
    result = check_reading(reading(temp=-50.0, humidity=120.0, co=-1.0))
    assert result.flags == ("co_range", "humidity_range", "temp_range")


def test_booleans_and_unknown_metrics_have_no_rule():
    assert check_reading(reading(light=True, motion=True, co2=99999.0, noise_db=-5)).flags == ()


# The jump rule.


def test_the_first_reading_of_a_device_is_compared_with_nothing():
    assert check_reading(reading(0, temp=-30.0, humidity=1.1)).flags == ()


@pytest.mark.parametrize(("metric", "limit"), sorted(JUMP_LIMITS.items()))
def test_a_change_of_exactly_the_limit_is_plausible_and_above_it_is_not(metric, limit):
    assert flags_of(reading(0), reading(1, **{metric: NORMAL[metric] + limit})) == [[], []]
    assert flags_of(reading(0), reading(1, **{metric: NORMAL[metric] + limit + 0.1})) == [
        [],
        [f"{metric}_jump"],
    ]


def test_a_drop_is_flagged_as_well_as_a_rise():
    assert flags_of(reading(0), reading(1, temp=0.0)) == [[], ["temp_jump"]]


def test_the_limits_are_the_approved_ones():
    assert JUMP_LIMITS == {"humidity": 10.0, "temp": 3.0}
    assert MAX_GAP_MS == 300_000
    assert MAX_CONSECUTIVE_JUMP_FLAGS == 2


def test_a_spike_flags_itself_and_not_the_normal_reading_after_it():
    # The reference is the last unflagged reading, and not the previous one.
    assert flags_of(reading(0), reading(1, temp=0.0), reading(2)) == [[], ["temp_jump"], []]


def test_a_flagged_reading_does_not_become_the_reference():
    result = flag_readings([reading(0, temp=20.0), reading(1, temp=10.0), reading(2, temp=11.0)])
    assert [list(each) for each in result[0]] == [[], ["temp_jump"], ["temp_jump"]]


def test_a_spike_in_both_metrics_carries_both_flags():
    assert flags_of(reading(0), reading(1, temp=7.6, humidity=1.1)) == [
        [],
        ["humidity_jump", "temp_jump"],
    ]


def test_the_devices_are_judged_independently():
    flags, _ = flag_readings([reading(0, "d1"), reading(0, "d2", temp=0.0), reading(1, "d1")])
    assert [list(each) for each in flags] == [[], [], []]


def test_a_gap_of_exactly_the_maximum_is_still_compared():
    old = reading(0)
    late = Reading("d1|late", "d1", old.ms + MAX_GAP_MS, {**NORMAL, "temp": 0.0})
    assert check_reading(late, check_reading(old).state).flags == ("temp_jump",)


def test_a_reference_older_than_the_maximum_gap_is_not_compared():
    old = reading(0)
    late = Reading("d1|late", "d1", old.ms + MAX_GAP_MS + 1, {**NORMAL, "temp": 0.0})
    result = check_reading(late, check_reading(old).state)
    assert result.flags == ()
    assert result.state.reference is late


# The re-baseline.


def test_after_two_flagged_readings_the_third_is_accepted_as_the_new_reference():
    # A real fast change, such as a fire: the first two readings are flagged, and
    # the third becomes the reference. At the sample rate that is about ten seconds.
    readings = [reading(0), reading(1, temp=30.0), reading(2, temp=31.0), reading(3, temp=32.0)]
    flags, states = flag_readings(readings)
    assert [list(each) for each in flags] == [[], ["temp_jump"], ["temp_jump"], []]
    assert states["d1"].reference is readings[3]


def test_the_reading_after_the_accepted_one_is_compared_with_the_new_level():
    readings = [reading(i, temp=t) for i, t in enumerate([20, 30, 31, 32, 32.5, 33])]
    assert flags_of(*readings) == [[], ["temp_jump"], ["temp_jump"], [], [], []]


def test_the_result_reports_the_rebaseline():
    state = DeviceState()
    results = []
    for i, temp in enumerate([20.0, 30.0, 31.0, 32.0]):
        results.append(check_reading(reading(i, temp=temp), state))
        state = results[-1].state
    assert [result.rebaselined for result in results] == [False, False, False, True]


def test_a_valid_reading_in_between_resets_the_run():
    readings = [
        reading(0),
        reading(1, temp=30.0),  # flagged, run of one
        reading(2),  # valid again, run is reset
        reading(3, temp=30.0),  # flagged, run of one
        reading(4, temp=30.0),  # flagged, run of two
        reading(5, temp=30.0),  # accepted
    ]
    assert flags_of(*readings) == [[], ["temp_jump"], [], ["temp_jump"], ["temp_jump"], []]


def test_an_accepted_reading_with_a_range_violation_stays_flagged():
    readings = [reading(0), reading(1, temp=30.0), reading(2, temp=31.0), reading(3, temp=70.0)]
    flags, states = flag_readings(readings)
    assert list(flags[3]) == ["temp_range"]
    assert states["d1"].reference is readings[0]


def test_a_range_violation_alone_does_not_count_towards_the_run():
    readings = [reading(0), reading(1, co=-1.0), reading(2, co=-1.0), reading(3, temp=30.0)]
    assert flags_of(*readings)[3] == ["temp_jump"]


# Repeated readings and the state.


def test_a_repeated_reading_receives_the_flags_of_the_first_and_leaves_the_state():
    first = check_reading(reading(0), check_reading(reading(0)).state)
    spike = reading(1, temp=0.0)
    flagged = check_reading(spike, first.state)
    again = check_reading(spike, flagged.state)
    assert flagged.flags == again.flags == ("temp_jump",)
    assert again.repeated and not flagged.repeated
    assert again.state is flagged.state


def test_a_repeated_spike_does_not_shorten_the_run_before_acceptance():
    # Two identical rows are one stored reading, so they count as one flag.
    spike = reading(1, temp=30.0)
    readings = [reading(0), spike, spike, reading(2, temp=31.0), reading(3, temp=32.0)]
    assert flags_of(*readings) == [[], ["temp_jump"], ["temp_jump"], ["temp_jump"], []]


def test_the_input_states_are_not_changed():
    states = {"d1": DeviceState()}
    flag_readings([reading(0)], states)
    assert states == {"d1": DeviceState()}


def test_continuing_from_a_rebuilt_state_gives_the_flags_of_the_uninterrupted_run():
    # What the loader does after a restart: it passes the last valid reading and
    # everything after it through the rules, then carries on with the next batch.
    temps = [20, 30, 31, 32, 20, 20, 0, 20, 21, 30, 30, 20, 20.5, 20.2]
    readings = [reading(i, temp=float(t)) for i, t in enumerate(temps)]
    whole, _ = flag_readings(readings)

    for cut in range(1, len(readings)):
        before, after = readings[:cut], readings[cut:]
        flags_before, _ = flag_readings(before)
        last_valid = max(i for i, each in enumerate(flags_before) if not each)
        _, rebuilt = flag_readings(before[last_valid:])
        flags_after, _ = flag_readings(after, rebuilt)
        assert list(flags_before) + list(flags_after) == list(whole), cut
