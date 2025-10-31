#!/usr/bin/env python3
"""
Rolling 7-day online status utilities.

This module mirrors the behavior of the current online status tracking in
miner_online_simple (which aggregates into per-day docs with hourly slots),
but rolls those day-level aggregates into a 7-day window.

Usage:
    from rolling_days import update_rolling_7_days
    update_rolling_7_days(miner_key)

The function is idempotent and safe to call on every interval; it only
appends a finished day (yesterday) when a new day has begun. It keeps only
the latest 7 days.
"""

import datetime as dt
import json
import os
from typing import Optional, Dict, Any, List

# Reuse helpers and UTC definition from the main module
from miner_online_simple import (
    UTC,
    data_dir,
    cache_path_for,
    read_local_cache,
    atomic_write_json,
    now_utc,
    day_bucket,
    day_iso,
)
from cache_integrity import compute_cache_signature


def _rolling7_path() -> str:
    """Path to the rolling 7-day summary file in the data dir."""
    return os.path.join(data_dir(), "status-rolling7.json")


def _compute_day_stats(day_dt: dt.datetime) -> Optional[Dict[str, Any]]:
    """Compute day-level stats from the per-day cache file.

    Returns a dict: {date, onlineCountDay, totalSlotsDay, onlinePercentDay}
    or None if the file is missing or invalid.
    """
    # Normalize to UTC midnight for the given day
    d0 = day_bucket(day_dt)
    path = cache_path_for(d0)
    doc = read_local_cache(path)
    if not isinstance(doc, dict) or not doc:
        return None

    # Prefer precomputed fields if available
    date_str = doc.get("date") or day_iso(d0)
    online_count = doc.get("onlineCountDay")
    total_slots = doc.get("totalSlotsDay")
    online_percent = doc.get("onlinePercentDay")

    # If any are missing, reconstruct from hours (best-effort)
    if not isinstance(online_count, int) or not isinstance(total_slots, int) or not isinstance(online_percent, (int, float)):
        hours = doc.get("hours") if isinstance(doc, dict) else None
        if not isinstance(hours, dict):
            return None
        day_online = 0
        day_total = 0
        for h in range(24):
            hdoc = hours.get(str(h))
            if isinstance(hdoc, dict):
                slots = hdoc.get("slots") or []
                if isinstance(slots, list) and slots:
                    day_total += len(slots)
                    day_online += sum(1 for s in slots if s == "online")
        if day_total <= 0:
            return None
        online_count = day_online
        total_slots = day_total
        online_percent = round(100.0 * day_online / day_total, 1)

    return {
        "date": str(date_str),
        "onlineCountDay": int(online_count),
        "totalSlotsDay": int(total_slots),
        "onlinePercentDay": float(online_percent),
    }


def _load_rolling_doc() -> Dict[str, Any]:
    path = _rolling7_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        if isinstance(doc, dict):
            return doc
    except Exception:
        pass
    return {}


def _save_rolling_doc(doc: Dict[str, Any]) -> None:
    atomic_write_json(_rolling7_path(), doc)


def update_rolling_7_days(miner_key: str, ts: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """Update the rolling 7-day status file using finished days.

    - Looks for the previous day (yesterday) and, if not yet recorded, reads
      the per-day cache and appends that day's aggregate into a rolling list.
    - Keeps only the most recent 7 day entries (by date).
    - Returns the updated rolling document.

    Call this once per interval after writing the current slot. It is safe to
    call more frequently; it writes only when a new day boundary has passed.
    """
    now = ts or now_utc()
    # Yesterday (completed day) in UTC
    yesterday = day_bucket(now) - dt.timedelta(days=1)
    y_key = day_iso(yesterday)

    rolling = _load_rolling_doc()
    days: List[Dict[str, Any]] = []
    if isinstance(rolling.get("days"), list):
        # Ensure proper typing
        for d in rolling["days"]:
            if isinstance(d, dict) and "date" in d:
                days.append(d)

    # Already present?
    already = any((isinstance(d.get("date"), str) and d.get("date") == y_key) for d in days)
    if not already:
        stats = _compute_day_stats(yesterday)
        if stats:
            # Merge if date exists; else append
            merged = False
            for i, d in enumerate(days):
                if d.get("date") == stats["date"]:
                    days[i] = stats
                    merged = True
                    break
            if not merged:
                days.append(stats)

    # Keep only last 7 by date
    def _date_key(d: Dict[str, Any]) -> str:
        try:
            return str(d.get("date") or "")
        except Exception:
            return ""

    days = sorted([d for d in days if isinstance(d, dict) and d.get("date")], key=_date_key)
    if len(days) > 7:
        days = days[-7:]

    # Shape and write
    out = {
        "miner_key": miner_key,
        "days": days,
        "last_day_recorded": (days[-1]["date"] if days else None),
        "lastUpdated": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        sig = compute_cache_signature(out, os.environ.get('LOCAL_SIGNING_KEY_HEX',''))
        if isinstance(sig, str):
            out["sig"] = sig
    except Exception:
        pass
    _save_rolling_doc(out)
    return out


__all__ = ["update_rolling_7_days"]
