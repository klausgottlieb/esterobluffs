#!/usr/bin/env python3
"""Deterministic fetcher: SLO County Library, Cayucos branch (id 76).

Pulls the Cayucos branch event list, keeps family-friendly and special public
programming (drops routine adult/internal series), collapses a repeating weekly
series into a single recurring row, and writes library-events.json in the same
row shape harvest_events.py ingests. No LLM; the only network call is to the
library site.

Fail-safe: on any error, or if zero Cayucos events parse, the previous
library-events.json is left untouched, so the calendar never blanks or fills
with garbage.

Usage:
  python fetch_library.py           # dry run: print a report, write library-events.json.proposed
  python fetch_library.py --write   # write library-events.json
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import re
import sys
from collections import defaultdict
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

PACIFIC = ZoneInfo("America/Los_Angeles")
BASE = "https://sanluisobispo.librarycalendar.com"
LIST_URL = f"{BASE}/events/list?branches[76]=76"
OUT = "library-events.json"
HORIZON_DAYS = 90        # how far ahead to list one-off dated events
WEEKLY_MIN = 3           # this many same-title occurrences -> one recurring row
UA = {"User-Agent": "Mozilla/5.0 (compatible; esterobluffs-events/1.0)"}
ORGANIZER = "SLO County Library, Cayucos Branch"

# Age groups that make an event family-relevant for a vacation-rental audience.
FAMILY_AGES = {"children", "baby & toddler", "baby", "toddler", "preschool",
               "school age", "all ages", "family"}

# Routine adult/internal series that should never appear on a guest "things to
# do" page even though they are public. Matched against the lowercased title.
# Edit this list freely; it is the main tuning knob for the filter.
EXCLUDE_TITLE = (
    "chair yoga", "conversation group", "conversation club", "english conversation",
    "tmha", "outreach", "book club", "advisory board", "reader's theatre",
    "readers theatre", "knit", "crochet", "tech help", "tech tutor", "3d printer",
    "3-d printer", "board meeting", "friends of the library", "tax-aide", "tax aide",
    "resume", "job help", "esl class", "literacy", "staff", "library closed",
    "study room", "volunteer",
)

MONTHS = "January February March April May June July August September October November December".split()
DATE_RE = re.compile(r"(" + "|".join(MONTHS) + r")\s+(\d{1,2}),\s+(\d{4})")
TIME_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap])m", re.I)


def _ipt(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def fetch_html():
    sep = "&" if "?" in LIST_URL else "?"
    r = requests.get(f"{LIST_URL}{sep}_cb={dt.datetime.now().timestamp():.0f}",
                     headers=UA, timeout=30)
    r.raise_for_status()
    return r.text


def parse_cards(html):
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for link in soup.select("a.lc-event__link"):
        title = _ipt(link.get_text())
        if not title:
            continue
        href = link.get("href", "")
        card = link.find_parent("div", class_="lc-list-event-content") or link.parent
        date_el = card.select_one(".lc-list-event-info-item--date")
        loc_el = card.select_one(".lc-list-event-location")
        age_el = card.select_one(".lc-list-event-program-type")
        rows.append({
            "title": title,
            "url": (BASE + href) if href.startswith("/") else href,
            "aria": link.get("aria-label", ""),
            "date_text": _ipt(date_el.get_text()) if date_el else "",
            "location": _ipt(loc_el.get_text()) if loc_el else "",
            "age_text": _ipt(age_el.get_text()) if age_el else "",
        })
    return rows


def parse_date(row):
    src = row["date_text"] or row["aria"]
    m = DATE_RE.search(src)
    if not m:
        return None, None
    d = dt.date(int(m.group(3)), MONTHS.index(m.group(1)) + 1, int(m.group(2)))
    time = None
    if "all day" not in src.lower():
        tm = TIME_RE.search(row["date_text"]) or TIME_RE.search(row["aria"])
        if tm:
            h = int(tm.group(1)) % 12
            if tm.group(3).lower() == "p":
                h += 12
            time = (h, int(tm.group(2) or 0))
    return d, time


def ages_of(row):
    t = row["age_text"].lower().replace("age group:", " ")
    return {a.strip() for a in re.split(r"[,\n]", t) if a.strip()}


def is_family(ages):
    return bool(ages & FAMILY_AGES)


def keep(row):
    """family + special: drop routine adult/internal; keep everything else that
    is clearly a Cayucos event."""
    if any(k in row["title"].lower() for k in EXCLUDE_TITLE):
        return False
    loc = row["location"].lower()
    if loc and "cayucos" not in loc and "cayucos" not in row["aria"].lower():
        return False
    return True


def fmt_time(h, m):
    suffix = "am" if h < 12 else "pm"
    hh = h % 12 or 12
    return f"{hh}:{m:02d}{suffix}" if m else f"{hh}{suffix}"


def build(today):
    rows = parse_cards(fetch_html())
    dated = []
    for row in rows:
        if not keep(row):
            continue
        d, time = parse_date(row)
        if not d or d < today:
            continue
        dated.append({**row, "date": d, "time": time, "ages": ages_of(row)})

    by_title = defaultdict(list)
    for e in dated:
        by_title[e["title"].lower()].append(e)

    events = []
    horizon = today + dt.timedelta(days=HORIZON_DAYS)
    for group in by_title.values():
        group.sort(key=lambda e: e["date"])
        weekdays = {e["date"].weekday() for e in group}
        sample = group[0]
        venue = sample["location"] or "Cayucos Library"
        fam_all = is_family(set().union(*[e["ages"] for e in group]))

        if len(group) >= WEEKLY_MIN and len(weekdays) == 1:
            t = sample["time"]
            tstr = fmt_time(*t) if t else ""
            events.append({
                "title": sample["title"], "recurrence": "weekly",
                "window": f"Every {sample['date'].strftime('%A')}"
                          + (f", {tstr}" if tstr else "") + " at the Cayucos library",
                "chip_top": sample["date"].strftime("%a"),
                "chip_bottom": tstr or "Library",
                "season_end": group[-1]["date"].isoformat(),
                "venue": venue, "area": "cayucos", "category": "library",
                "organizer": ORGANIZER,
                "summary": ("Recurring family program at the Cayucos library branch."
                            if fam_all else "Recurring program at the Cayucos library branch."),
                "source_url": sample["url"], "confidence": 95,
            })
        else:
            for e in group:
                if e["date"] > horizon:
                    continue
                t = e["time"]
                start = (dt.datetime(e["date"].year, e["date"].month, e["date"].day,
                                     t[0], t[1], tzinfo=PACIFIC).isoformat()
                         if t else e["date"].isoformat())
                events.append({
                    "title": e["title"], "start": start,
                    "venue": e["location"] or "Cayucos Library", "area": "cayucos",
                    "category": "library", "organizer": ORGANIZER,
                    "summary": ("Family event at the Cayucos library branch."
                                if is_family(e["ages"]) else "Public event at the Cayucos library branch."),
                    "source_url": e["url"], "confidence": 90,
                })
    return events, len(rows), len(dated)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="write library-events.json")
    args = ap.parse_args()
    today = dt.datetime.now(PACIFIC).date()

    try:
        events, n_cards, n_kept = build(today)
    except Exception as e:  # never let the fetcher pollute or crash the pipeline
        print(f"library fetch failed ({e}); leaving {OUT} untouched", file=sys.stderr)
        return
    if not events:
        print(f"no Cayucos library events parsed; leaving {OUT} untouched", file=sys.stderr)
        return

    print(f"parsed {n_cards} event links, {n_kept} kept after filter, {len(events)} rows emitted\n")
    rec = [e for e in events if e.get("recurrence")]
    one = [e for e in events if not e.get("recurrence")]
    print(f"RECURRING ({len(rec)}):")
    for e in rec:
        print(f"  ~ {e['chip_top']:>3} {e['chip_bottom']:<8} {e['title']}  [through {e['season_end']}]")
    print(f"\nDATED ({len(one)}):")
    for e in sorted(one, key=lambda x: x["start"]):
        print(f"  + {e['start'][:16]}  {e['title']}")

    doc = {"harvested_at": dt.datetime.now(PACIFIC).isoformat(), "events": events}
    target = OUT if args.write else OUT + ".proposed"
    with open(target, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(f"\n{'WROTE ' + target if args.write else 'dry run -> ' + target}")


if __name__ == "__main__":
    main()
