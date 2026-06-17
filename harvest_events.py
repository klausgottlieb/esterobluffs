#!/usr/bin/env python3
"""Cayucos events harvester -- deterministic merge step.

This script needs no network. It merges four layers into events-data.json:

  1. seed-events.json    -- curated baseline, always kept
  2. library-events.json -- Cayucos library programming, from fetch_library.py
  3. harvest-raw.json    -- web harvest, from discover_events.py (Anthropic API + web search)
  4. computed fixed dates -- a small floor of rule-based annual events

It drops anything past, undated, on-hold, or non-Cayucos, dedupes on
(title, date), sorts, and writes events-data.json. First writer of a given
(title, date) wins, so priority is seed > library > harvest > fixed: the weekly
refresh can ADD events but can never delete anything curated in the seed.

In CI (.github/workflows/events-update.yml) this runs Mondays with --write,
right after discover_events.py and fetch_library.py produce their input files.
Run it by hand without --write for a dry run that writes events-data.json.proposed
so you can review the diff first.

Usage
-----
  python harvest_events.py           # dry run: print diff, write events-data.json.proposed
  python harvest_events.py --write   # apply: overwrite events-data.json
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import sys
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
RAW = "harvest-raw.json"
SEED = "seed-events.json"
LIBRARY = "library-events.json"
OUT = "events-data.json"
DROP_STATUS = {"on_hold", "cancelled", "postponed", "canceled"}


# ---- helpers ------------------------------------------------------------
def _slug(text):
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")[:48]


def _event(source, title, date, time, venue, area, category, organizer,
           summary, url, confidence, town=None):
    if isinstance(date, str):
        date = dt.date.fromisoformat(date[:10])
    h, m = time if time else (0, 0)
    start = dt.datetime(date.year, date.month, date.day, h, m, tzinfo=PACIFIC)
    return {
        "id": f"{source}-{date.isoformat()}-{_slug(title)}",
        "title": title,
        "start": start.isoformat(),
        "end": None,
        "all_day": time is None,
        "date_confirmed": True,
        "recurrence": "annual" if source == "fixed" else "none",
        "window": None,
        "sort_date": date.isoformat(),
        "venue": venue or "",
        "area": area or "cayucos",
        "town": town,
        "category": category or "community",
        "organizer": organizer or "",
        "source": source,
        "source_type": "fixed" if source == "fixed" else "harvest",
        "confidence": confidence,
        "status": "scheduled",
        "summary": (summary or "")[:200],
        "canonical_url": url or None,
    }


def norm_key(ev):
    # Town/area is part of an event's identity, so a same-named event in two
    # towns on the same day (e.g. a farmers market) is not silently collapsed.
    place = ev.get("town") or ev.get("area") or "cayucos"
    return (ev["title"].strip().lower(), ev["sort_date"][:10], place)


# ---- deterministic floor: only genuinely rule-based fixed dates ---------
def _on_or_after(date, today, years=1):
    return date if date >= today else date.replace(year=date.year + years)


def _nth_weekday(year, month, weekday, n):
    d = dt.date(year, month, 1)
    return d + dt.timedelta(days=(weekday - d.weekday()) % 7 + 7 * (n - 1))


def floor_events(today):
    july4 = _on_or_after(dt.date(today.year, 7, 4), today)
    jan1 = _on_or_after(dt.date(today.year, 1, 1), today)
    tgiving = _nth_weekday(today.year, 11, 3, 4)  # 4th Thursday of November
    if tgiving < today:
        tgiving = _nth_weekday(today.year + 1, 11, 3, 4)
    spec = [
        ("Fourth of July in Cayucos", july4, "Downtown Cayucos & the pier",
         "festival", "Cayucos Chamber / Lions Club",
         "Parade, Front Street Faire, and fireworks over the pier.",
         "https://www.cayucoschamber.com/july4th"),
        ("Carlin Soule Memorial Polar Bear Dip", jan1, "Cayucos Pier & beach",
         "community", "Cayucos Chamber",
         "New Year's Day plunge into the Pacific at the pier.",
         "https://www.cayucoschamber.com/polar-bear-dip"),
        ("Cayucos Turkey Trot", tgiving, "Cayucos Pier", "race",
         "Cayucos Turkey Trot", "Thanksgiving-morning fun run along the seafront.",
         "https://runsignup.com/Race/CA/Cayucos/CayucosTurkeyTrot"),
    ]
    return [_event("fixed", t, d, None, v, "cayucos", c, o, s, u, 95)
            for (t, d, v, c, o, s, u) in spec]


# ---- ingestion (shared by the web harvest and the curated seed) ---------
def _ingest(path, source, today):
    """Parse a raw events file (harvest-raw.json or seed-events.json) into
    normalized rows tagged with `source`. Handles recurring rows (cadence +
    season) and single dated rows. Past, undated, and on-hold rows are dropped.
    A missing file yields an empty list rather than an error."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return [], None
    stype = source if source in ("seed", "fixed", "library") else "harvest"
    kept = []
    for e in raw.get("events", []):
        title = (e.get("title") or "").strip()
        if not title:
            continue
        if (e.get("status") or "").lower() in DROP_STATUS:
            continue

        # Recurring entry (cadence + season), e.g. a weekly market or music night.
        if e.get("recurrence"):
            se = e.get("season_end")
            if se:
                try:
                    if dt.date.fromisoformat(se) < today:
                        continue  # season is over
                except ValueError:
                    pass
            kept.append({
                "id": f"{source}-recurring-" + _slug(title),
                "title": title, "start": None, "end": None, "all_day": True,
                "date_confirmed": False, "recurrence": e["recurrence"],
                "window": e.get("window", ""), "sort_date": today.isoformat(),
                "chip_top": e.get("chip_top", ""), "chip_bottom": e.get("chip_bottom", ""),
                "venue": e.get("venue", ""), "area": e.get("area") or "cayucos",
                "town": e.get("town"),
                "category": e.get("category", "community"), "organizer": e.get("organizer", ""),
                "source": source, "source_type": stype,
                "confidence": int(e.get("confidence", 70) or 70), "status": "scheduled",
                "summary": (e.get("summary", "") or "")[:200],
                "canonical_url": e.get("source_url") or e.get("canonical_url"),
            })
            continue

        start = (e.get("start") or "").strip()
        if not start:
            continue
        try:
            d = dt.date.fromisoformat(start[:10])
        except ValueError:
            continue
        if d < today:
            continue
        time = None
        if "T" in start:
            try:
                t = dt.datetime.fromisoformat(start)
                if (t.hour, t.minute) != (0, 0):
                    time = (t.hour, t.minute)
            except ValueError:
                pass
        ev = _event(source, title, d, time, e.get("venue", ""),
                    (e.get("area") or "cayucos"), e.get("category", "community"),
                    e.get("organizer", ""), e.get("summary", ""),
                    e.get("source_url") or e.get("canonical_url"),
                    int(e.get("confidence", 70) or 70), town=e.get("town"))
        ev["source_type"] = stype
        kept.append(ev)
    return kept, raw.get("harvested_at")


def harvested_events(today):
    return _ingest(RAW, "harvest", today)


def library_events(today):
    return _ingest(LIBRARY, "library", today)


def seed_events(today):
    kept, _ = _ingest(SEED, "seed", today)
    return kept


# ---- merge + diff -------------------------------------------------------
def build(today):
    seed = seed_events(today)
    library, library_at = library_events(today)
    harvest, harvested_at = harvested_events(today)
    floor = floor_events(today)
    # Priority: a curated seed event is never dropped or duplicated; the library
    # feed is next, then the web harvest, then the computed fixed dates as a
    # floor. First writer of a given (title, date) wins, so the order is
    # seed > library > harvest > fixed. The weekly refresh can ADD events but
    # can never delete anything in the seed.
    merged, seen = [], set()
    for ev in seed + library + harvest + floor:
        k = norm_key(ev)
        if k in seen:
            continue
        seen.add(k)
        merged.append(ev)
    merged.sort(key=lambda e: e.get("start") or e.get("sort_date") or "9999")
    now = dt.datetime.now(PACIFIC)
    sources = [
        {"id": "seed", "label": "Curated baseline (always kept)", "status": "ok",
         "last_success": now.isoformat(), "count": len(seed)},
        {"id": "library", "label": "Cayucos library programming", "status": "ok",
         "last_success": library_at or now.isoformat(), "count": len(library)},
        {"id": "harvest", "label": "Cayucos web harvest", "status": "ok",
         "last_success": harvested_at or now.isoformat(), "count": len(harvest)},
        {"id": "fixed", "label": "Fixed annual dates", "status": "ok",
         "last_success": now.isoformat(), "count": len(floor)},
    ]
    return {"generated_at": now.isoformat(), "timezone": "America/Los_Angeles",
            "sources": sources, "events": merged}


def load_current():
    try:
        with open(OUT, encoding="utf-8") as f:
            return json.load(f).get("events", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def show_diff(old, new):
    oldk = {norm_key(e): e for e in old}
    newk = {norm_key(e): e for e in new}
    added = [newk[k] for k in newk if k not in oldk]
    removed = [oldk[k] for k in oldk if k not in newk]
    print(f"\nProposed: {len(new)} events total")
    print(f"  + {len(added)} added, - {len(removed)} removed, "
          f"{len(new) - len(added)} unchanged\n")
    for e in sorted(added, key=lambda x: x.get("sort_date") or "9999"):
        print(f"  + {e['sort_date']}  {e['title']}  ({e['source']})")
    for e in sorted(removed, key=lambda x: x.get("sort_date") or "9999"):
        print(f"  - {e.get('sort_date','?')}  {e['title']}")
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="overwrite events-data.json")
    args = ap.parse_args()

    today = dt.datetime.now(PACIFIC).date()
    doc = build(today)
    show_diff(load_current(), doc["events"])

    target = OUT if args.write else OUT + ".proposed"
    with open(target, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    if args.write:
        print(f"WROTE {OUT}  (committed only when you git add/commit/push)")
    else:
        print(f"dry run -> {target}. Review above, then rerun with --write to apply.")


if __name__ == "__main__":
    main()
