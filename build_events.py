"""Build events-data.json for cayucos.html.

Layers, lowest to highest priority:
  1. events-seed.json  -- hand-maintained annual baseline, always present.
  2. live fetchers     -- library, rotary (more later), each isolated so one
                          failure cannot blank the calendar.

Each run loads the seed, runs every live fetcher in its own try/except, and on
failure reuses that source's events from the previous events-data.json (marked
'stale'). It writes a fresh events-data.json with per-source status so the page
can show freshness, the same discipline as the conditions feeds.

Run from the repo root (same place as the other *-data.json files):
    python build_events.py
"""
from __future__ import annotations
import datetime as dt
import json
import os
import sys
from zoneinfo import ZoneInfo

from events_ical import NotConfigured
import fetch_events_library
import fetch_events_rotary
import fetch_events_venues

PACIFIC = ZoneInfo("America/Los_Angeles")
SEED = "events-seed.json"
OUT = "events-data.json"

# Live sources that have a fetcher: (id, label, module).
LIVE = [
    ("library", "County Library (Cayucos)", fetch_events_library),
    ("rotary",  "Cayucos Rotary",           fetch_events_rotary),
    ("venues",  "Schooners / Sea Shanty / Tavern", fetch_events_venues),
]
# Planned sources with no fetcher yet -- shown as 'pending' for transparency.
PLANNED = [
    ("chamber", "Cayucos Chamber"),
    ("slocal",  "Visit SLO CAL"),
]


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def norm_key(ev):
    return (ev.get("title", "").strip().lower(), (ev.get("sort_date") or "")[:10])


def main():
    now = dt.datetime.now(PACIFIC)
    seed_doc = load_json(SEED) or {"events": []}
    prev_doc = load_json(OUT) or {}
    prev_sources = {s["id"]: s for s in prev_doc.get("sources", [])}
    prev_by_source = {}
    for ev in prev_doc.get("events", []):
        prev_by_source.setdefault(ev.get("source"), []).append(ev)

    seed_events = seed_doc.get("events", [])
    sources = [{
        "id": "seed", "label": "Annual baseline", "status": "ok",
        "last_success": now.isoformat(), "count": len(seed_events),
    }]
    events = list(seed_events)

    for sid, label, mod in LIVE:
        try:
            fetched = mod.fetch()
            events.extend(fetched)
            sources.append({"id": sid, "label": label, "status": "ok",
                            "last_success": now.isoformat(), "count": len(fetched)})
            print(f"{sid}: ok ({len(fetched)} events)")
        except NotConfigured as e:
            sources.append({"id": sid, "label": label, "status": "pending",
                            "last_success": None, "count": 0})
            print(f"{sid}: pending ({e})")
        except Exception as e:  # network / parse failure -> keep last-good
            kept = prev_by_source.get(sid, [])
            events.extend(kept)
            sources.append({"id": sid, "label": label, "status": "stale",
                            "last_success": prev_sources.get(sid, {}).get("last_success"),
                            "count": len(kept)})
            print(f"{sid}: STALE, kept {len(kept)} prior events ({e})", file=sys.stderr)

    for sid, label in PLANNED:
        sources.append({"id": sid, "label": label, "status": "pending",
                        "last_success": None, "count": 0})

    # Dedup: a live source confirming an event wins over its seed placeholder.
    live_keys = {norm_key(ev) for ev in events if ev.get("source") != "seed"}
    merged, seen = [], set()
    for ev in events:
        key = norm_key(ev)
        if ev.get("source") == "seed" and key in live_keys:
            continue
        if key in seen:
            continue
        seen.add(key)
        merged.append(ev)

    merged.sort(key=lambda e: (e.get("start") or e.get("sort_date") or "9999"))

    doc = {
        "generated_at": now.isoformat(),
        "timezone": "America/Los_Angeles",
        "sources": sources,
        "events": merged,
    }
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    os.replace(tmp, OUT)
    print(f"wrote {OUT}: {len(merged)} events across {len(sources)} sources")


if __name__ == "__main__":
    main()
