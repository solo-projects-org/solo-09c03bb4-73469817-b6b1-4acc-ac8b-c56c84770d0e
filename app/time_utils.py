"""UTC whole-hour helpers shared by the API layer and the service layer."""

from datetime import datetime, timezone


class HourValidationError(ValueError):
    """Raised when the supplied hour is naive or not an exact UTC hour."""


def normalize_utc_hour(value: datetime) -> datetime:
    """Validate *value* and return a timezone-aware UTC whole-hour datetime.

    Rules:
      * naive datetimes are rejected (the hour must state its timezone);
      * after conversion to UTC, minutes/seconds/microseconds must all be zero.
    """
    if not isinstance(value, datetime):
        raise HourValidationError("hour must be a datetime value")
    if value.tzinfo is None or value.utcoffset() is None:
        raise HourValidationError(
            "hour must be timezone-aware, e.g. 2026-11-15T08:00:00Z"
        )
    utc_value = value.astimezone(timezone.utc)
    if (
        utc_value.minute != 0
        or utc_value.second != 0
        or utc_value.microsecond != 0
    ):
        raise HourValidationError("hour must be an exact UTC whole hour (HH:00:00)")
    return utc_value


def canonical_hour(value: datetime) -> str:
    """Stable string form used as the storage key, e.g. 2026-11-15T08:00:00Z."""
    utc_value = normalize_utc_hour(value)
    return utc_value.strftime("%Y-%m-%dT%H:00:00Z")
