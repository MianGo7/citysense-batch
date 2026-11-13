"""Plausibility rules that flag implausible readings instead of dropping them.

A flagged reading stays stored. The flags go into `quality.flags`, and summaries
and alerts select the readings with an empty list (ADR-0010). Two kinds of rule
exist, a range rule per metric and a jump rule for the sudden change between
consecutive readings of one device, with the limits and the reasons in ADR-0011.

The jump rule compares a reading with the last unflagged reading of the device,
the reference, and not with the previous raw reading. A single spike then flags
only itself, because the normal reading after it is compared with the reference
again, whereas a comparison with the previous raw reading would flag the
spike and the normal reading that follows it.

The state that this needs is one `DeviceState` per device. To continue after a
restart it is rebuilt by passing the last valid reading of the device and every
reading after it, in order, through `flag_readings` from an empty state. The flags
then come out as they did the first time, because the rules are deterministic.

This module is pure and does not import pymongo, so it is tested without a database.
"""

import math
from dataclasses import dataclass

# The range rule per metric, bounds included. The gases only have a lower bound,
# because the source does not explain the unit "ppm (%)", so no upper bound could
# be justified. The humidity has no lower bound of 10 percent: the readings below
# it are caught by the jump rule, and a fixed 10 percent has no physical basis.
RANGES = {
    "co": (0.0, math.inf),
    "humidity": (0.0, 100.0),
    "lpg": (0.0, math.inf),
    "smoke": (0.0, math.inf),
    "temp": (-40.0, 60.0),
}

# The largest plausible change between a reading and its reference. The limits
# lie below the smallest regular spike of each metric, see ADR-0011.
JUMP_LIMITS = {"humidity": 10.0, "temp": 3.0}

# A reference older than this says little about the present, so no comparison is
# made. The largest gap in the sample data is 40.2 seconds.
MAX_GAP_MS = 300_000

# After this many consecutive readings flagged by the jump rule, the next one is
# accepted as the new reference. Otherwise a real change of level would lock a
# device out for good, because every following reading would be compared with
# the old level.
MAX_CONSECUTIVE_JUMP_FLAGS = 2


@dataclass(frozen=True)
class DeviceState:
    """What the rules remember about one device between readings."""

    reference: object = None
    jump_streak: int = 0
    last_id: str = None
    last_flags: tuple = ()


@dataclass(frozen=True)
class CheckResult:
    flags: tuple
    state: DeviceState
    # The reading was accepted as the new reference after a run of jump flags.
    rebaselined: bool = False
    # The reading repeats the previous one of its device, so nothing was checked.
    repeated: bool = False


def _range_flags(reading):
    flags = []
    for metric, (low, high) in RANGES.items():
        value = reading.metrics.get(metric)
        if value is None:
            continue
        if not math.isfinite(value) or not low <= value <= high:
            flags.append(f"{metric}_range")
    return flags


def _jump_flags(reading, reference):
    if reference is None or reading.ms - reference.ms > MAX_GAP_MS:
        return []
    flags = []
    for metric, limit in JUMP_LIMITS.items():
        value = reading.metrics.get(metric)
        before = reference.metrics.get(metric)
        if value is not None and before is not None and abs(value - before) > limit:
            flags.append(f"{metric}_jump")
    return flags


def check_reading(reading, state=DeviceState()):
    """Return the flags of one reading and the state for the next one.

    A reading with the identifier of the previous reading of its device is a
    repeat, as the source contains such rows. Only the first one is stored, so it
    receives the flags of the first one and leaves the state as it was.
    """
    if reading.id == state.last_id:
        return CheckResult(state.last_flags, state, repeated=True)

    range_flags = _range_flags(reading)
    jump_flags = _jump_flags(reading, state.reference)

    accepted = bool(jump_flags) and state.jump_streak >= MAX_CONSECUTIVE_JUMP_FLAGS
    if accepted:
        jump_flags = []

    flags = tuple(sorted(range_flags + jump_flags))
    if not flags:
        reference, streak = reading, 0
    else:
        reference = state.reference
        streak = state.jump_streak + 1 if jump_flags else state.jump_streak

    new_state = DeviceState(reference, streak, reading.id, flags)
    return CheckResult(flags, new_state, rebaselined=accepted and not flags)


def flag_readings(readings, states=None):
    """Return the flags of every reading, in order, and the states afterwards.

    `states` maps a device to its `DeviceState`. A device without a state starts
    without a reference, so its first reading is not compared with anything.
    """
    states = dict(states or {})
    all_flags = []
    for reading in readings:
        result = check_reading(reading, states.get(reading.device, DeviceState()))
        states[reading.device] = result.state
        all_flags.append(result.flags)
    return all_flags, states
