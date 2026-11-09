"""Time helpers. Timestamps are America/Los_Angeles ISO 8601 with offset, as in
financial-research-hub; snapshot dates are the Los Angeles calendar date."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("America/Los_Angeles")


def now_iso() -> str:
    """e.g. 2026-11-09T12:06:16-08:00"""
    return dt.datetime.now(LOCAL_TZ).replace(microsecond=0).isoformat()


def today() -> str:
    """Today's date in Los Angeles, YYYY-MM-DD."""
    return dt.datetime.now(LOCAL_TZ).date().isoformat()
