"""Deterministic document identifiers built from device and timestamp.

The identifier of a reading is the string `<device>|<epoch milliseconds>`, for
instance `b8:27:eb:bf:9d:51|1594512094385`. The same reading always receives the
same identifier, so loading a batch twice, or loading a row that the source
contains twice, cannot create a second document (ADR-0004 and ADR-0010).

This module is pure and does not import pymongo, so it is tested without a database.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

SEPARATOR = "|"


def epoch_ms(raw_ts):
    """Return the timestamp of the source in whole milliseconds, truncated.

    The raw text is converted with exact decimal arithmetic and not through a
    float, so that no rounding of the binary representation can move a reading
    to a neighbouring millisecond. The value is truncated and not rounded,
    because a BSON date truncates as well. Identifier and `ts` then always
    describe the same instant.
    """
    try:
        value = Decimal(raw_ts.strip())
    except (InvalidOperation, AttributeError) as error:
        raise ValueError(f"not a timestamp: {raw_ts!r}") from error
    if not value.is_finite() or value < 0:
        raise ValueError(f"not a timestamp after the epoch: {raw_ts!r}")
    return int(value.scaleb(3))


def instant(ms):
    """Return the moment of a millisecond count as an aware UTC datetime.

    Timedelta arithmetic is exact, whereas `fromtimestamp` divides by 1000 as a
    float and can land on the wrong microsecond.
    """
    return EPOCH + timedelta(milliseconds=ms)


def identifier(device, ms):
    """Return the identifier of a reading from its device and its milliseconds."""
    if not device or SEPARATOR in device:
        # The separator would make the identifier ambiguous, and the validator of
        # the database rejects such a device in the identifier.
        raise ValueError(f"not a usable device name: {device!r}")
    return f"{device}{SEPARATOR}{ms}"


def reading_id(device, raw_ts):
    """Return the identifier of a reading from its device and raw timestamp."""
    return identifier(device, epoch_ms(raw_ts))
