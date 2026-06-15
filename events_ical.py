"""Shared iCal fetch + normalize helper for the Cayucos events pipeline.

Both the library and Rotary fetchers read a public iCal (.ics) subscription
feed and map its VEVENTs into the events-data.json schema. Network or parse
failures raise; build_events.py turns a raised exception into a graceful
'stale' (keep last-good) or 'pending' source state so the calendar never blanks.
"""
from __future__ import annotations
import datetime as dt
import re
from zoneinfo import ZoneInfo

import requests
from icalendar import Calendar

PACIFIC = ZoneInfo("America/Los_Angeles")
UA = "EsteroBluffsEvents/1.0 (+https://esterobluffs.com)"
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


class NotConfigured(Exception):
    """Raised when a feed URL has not been set yet (source stays 'pending')."""


def _clean(text: str, limit: int = 160) -> str:
    text = _WS.sub(" ", _TAGS.sub(" ", text or "")).strip()
    return (text[: limit - 3].rstrip() + "...") if len(text) > limit else text


def _to_iso(value):
    """Return (iso_string_or_None, all_day_bool) from an iCal date/datetime."""
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=PACIFIC)
        return value.astimezone(PACIFIC).isoformat(), False
    if isinstance(value, dt.date):  # all-day VEVENT
        return dt.datetime(value.year, value.month, value.day, tzinfo=PACIFIC).isoformat(), True
    return None, False


def fetch_ical(url, *, source, source_type, organizer, area, category,
               confidence, default_venue=None, default_url=None, horizon_days=180):
    """Fetch an iCal feed and return a list of events in the data-file schema."""
    if not url:
        raise NotConfigured(f"{source}: feed URL not set")
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    resp.raise_for_status()
    cal = Calendar.from_ical(resp.content)

    now = dt.datetime.now(PACIFIC)
    horizon = now + dt.timedelta(days=horizon_days)
    out = []
    for comp in cal.walk("VEVENT"):
        dtstart = comp.get("DTSTART")
        if dtstart is None:
            continue
        start_iso, all_day = _to_iso(dtstart.dt)
        if not start_iso:
            continue
        start_dt = dt.datetime.fromisoformat(start_iso)
        if start_dt < now - dt.timedelta(hours=12) or start_dt > horizon:
            continue  # skip past events and anything past the horizon

        dtend = comp.get("DTEND")
        end_iso = _to_iso(dtend.dt)[0] if dtend is not None else None
        title = (str(comp.get("SUMMARY", "")).strip() or "Untitled event")
        venue = str(comp.get("LOCATION", "")).strip() or (default_venue or "")
        url_prop = str(comp.get("URL", "")).strip()
        uid = str(comp.get("UID", "")).strip()

        out.append({
            "id": f"{source}-{uid or start_iso[:10] + '-' + re.sub(r'[^a-z0-9]+', '-', title.lower())[:40]}",
            "title": title,
            "start": start_iso,
            "end": end_iso,
            "all_day": all_day,
            "date_confirmed": True,
            "recurrence": "none",
            "window": None,
            "sort_date": start_iso[:10],
            "venue": venue,
            "area": area,
            "category": category,
            "organizer": organizer,
            "source": source,
            "source_type": source_type,
            "confidence": confidence,
            "status": "scheduled",
            "summary": _clean(str(comp.get("DESCRIPTION", ""))),
            "canonical_url": url_prop or default_url,
        })
    return out
