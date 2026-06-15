"""Cayucos venue live-music fetcher -- computed, not scraped.

The venue sites publish stable recurring schedules in prose and load their
dated gigs via JavaScript, so scraping them is fragile and goes stale. This
computes the known recurring shows forward from their stated cadence, so the
output is current by construction and needs no network. One-off touring shows
are not captured -- those live only on each venue's dynamic page.

Cadences (from each venue's site / local press):
  Schooners (171 N Ocean Ave): Open Mic every Wednesday; Kam's Jams last
    Tuesday monthly; Song-A-Thon last Thursday monthly; Beachside Live summer
    concert series on summer Sundays.
  Sea Shanty: live music some Sunday afternoons in season (informal).
  Old Cayucos Tavern: live music Friday & Saturday nights (informal).
"""
from __future__ import annotations
import datetime as dt
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
HORIZON_DAYS = 56
SCHOONERS_URL = "https://www.schoonerscayucos.com/live/"


def _ev(eid, title, date, hour, venue, summary, url, confidence=70):
    start = dt.datetime(date.year, date.month, date.day, hour, 0, tzinfo=PACIFIC)
    return {
        "id": eid, "title": title, "start": start.isoformat(), "end": None,
        "all_day": False, "date_confirmed": True, "recurrence": "weekly",
        "window": None, "sort_date": date.isoformat(), "venue": venue,
        "area": "cayucos", "category": "music", "organizer": venue,
        "source": "venues", "source_type": "venue", "confidence": confidence,
        "status": "scheduled", "summary": summary, "canonical_url": url,
    }


def _weekdays(start, end, weekday, limit=None):
    d, n = start, 0
    while d <= end and (limit is None or n < limit):
        if d.weekday() == weekday:
            yield d
            n += 1
        d += dt.timedelta(days=1)


def _last_weekday(year, month, weekday):
    nxt = dt.date(year + 1, 1, 1) if month == 12 else dt.date(year, month + 1, 1)
    d = nxt - dt.timedelta(days=1)
    while d.weekday() != weekday:
        d -= dt.timedelta(days=1)
    return d


def fetch():
    today = dt.datetime.now(PACIFIC).date()
    end = today + dt.timedelta(days=HORIZON_DAYS)
    out = []

    # Schooners Open Mic -- every Wednesday (next 3)
    for d in _weekdays(today, end, 2, limit=3):
        out.append(_ev(f"venues-schooners-openmic-{d.isoformat()}",
                        "Open Mic Night at Schooners", d, 18,
                        "Schooners, 171 N Ocean Ave",
                        "Weekly open mic, free and open to the public.", SCHOONERS_URL))

    # Schooners Beachside Live -- summer Sundays Jun-Aug (next 3)
    for d in _weekdays(today, end, 6, limit=3):
        if 6 <= d.month <= 8:
            out.append(_ev(f"venues-schooners-beachside-{d.isoformat()}",
                            "Beachside Live Summer Concert", d, 14,
                            "Schooners, 171 N Ocean Ave",
                            "Free summer concert series on the beach.", SCHOONERS_URL,
                            confidence=65))

    # Schooners monthly shows -- last Tue (Kam's Jams), last Thu (Song-A-Thon)
    months = [(today.year, today.month)]
    nm = (today.replace(day=1) + dt.timedelta(days=32))
    months.append((nm.year, nm.month))
    for yy, mm in months:
        kt = _last_weekday(yy, mm, 1)
        if today <= kt <= end:
            out.append(_ev(f"venues-schooners-kams-{kt.isoformat()}",
                            "Kam's Jams at Schooners", kt, 18,
                            "Schooners, 171 N Ocean Ave",
                            "Monthly jam, last Tuesday of the month.", SCHOONERS_URL))
        st = _last_weekday(yy, mm, 3)
        if today <= st <= end:
            out.append(_ev(f"venues-schooners-songathon-{st.isoformat()}",
                            "Song-A-Thon Playoffs at Schooners", st, 18,
                            "Schooners, 171 N Ocean Ave",
                            "Monthly songwriter contest, last Thursday.", SCHOONERS_URL))

    # Sea Shanty -- some Sunday afternoons in season (informal, not dated)
    out.append({
        "id": "venues-seashanty-sundays", "title": "Live music at the Sea Shanty",
        "start": None, "end": None, "all_day": True, "date_confirmed": False,
        "recurrence": "weekly", "window": "Some Sunday afternoons, in season",
        "sort_date": today.isoformat(), "chip_top": "Some", "chip_bottom": "Sun",
        "venue": "Sea Shanty, Cayucos", "area": "cayucos", "category": "music",
        "organizer": "Sea Shanty", "source": "venues", "source_type": "venue",
        "confidence": 40, "status": "tentative",
        "summary": "Back-lot live music on select Sunday afternoons in season.",
        "canonical_url": None,
    })

    # Old Cayucos Tavern -- Friday & Saturday nights (informal, not dated)
    out.append({
        "id": "venues-tavern-weekends", "title": "Live music at Old Cayucos Tavern",
        "start": None, "end": None, "all_day": True, "date_confirmed": False,
        "recurrence": "weekly", "window": "Friday & Saturday nights",
        "sort_date": today.isoformat(), "chip_top": "Fri", "chip_bottom": "Sat",
        "venue": "Old Cayucos Tavern, Cayucos", "area": "cayucos", "category": "music",
        "organizer": "Old Cayucos Tavern", "source": "venues", "source_type": "venue",
        "confidence": 45, "status": "tentative",
        "summary": "Weekend live music in the historic tavern.",
        "canonical_url": "https://www.oldcayucostavern.com/events",
    })

    return out
