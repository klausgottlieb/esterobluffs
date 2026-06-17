#!/usr/bin/env python3
"""Deterministic fetcher: SLO County Library branches.

Pulls each configured branch's event list, keeps family-friendly and special
public programming (drops routine adult/internal series), collapses a repeating
weekly series into a single recurring row, and writes library-events.json in the
same row shape harvest_events.py ingests. No LLM; the only network calls are to
the library site.

Cayucos (branch 76) is the in-town source and is verified. Morro Bay and Cambria
are "nearby" day-trip towns: fill their numeric branch ids into BRANCHES below
(run library-probe and read the BRANCH IDS section in the log). A branch whose id
is None is skipped, so this file is safe to deploy before the ids are known -- it
simply runs Cayucos-only until you fill them in.

Fail-safe: a branch that errors is skipped, not fatal. If NO branch yields any
event, the previous library-events.json is left untouched, so the calendar never
blanks or fills with garbage.

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
OUT = "library-events.json"
HORIZON_DAYS = 90        # how far ahead to list one-off dated events
WEEKLY_MIN = 3           # this many same-title occurrences -> one recurring row
UA = {"User-Agent": "Mozilla/5.0 (compatible; esterobluffs-events/1.0)"}

# Branches to pull. `area` and `town` are stamped onto every row from that branch
# so the page can split events into In town / Morro Bay / Cambria. `loc_match` is
# a lowercase sanity check against the event location text. Fill the two None ids
# from the probe, then this fetcher covers all three towns.
BRANCHES = [
    {"id": 76,   "name": "Cayucos",   "area": "cayucos", "town": None,        "loc_match": "cayucos"},
    {"id": 81,   "name": "Morro Bay", "area": "nearby",  "town": "Morro Bay", "loc_match": "morro bay"},
    {"id": 78,   "name": "Cambria",   "area": "nearby",  "town": "Cambria",   "loc_match": "cambria"},
]

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
    "study room", "volunteer", "booked for lunch",
)

MONTHS = "January February March April May June July August September October November December".split()
DATE_RE = re.compile(r"(" + "|".join(MONTHS) + r")\s+(\d{1,2}),\s+(\d{4})")
TIME_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap])m", re.I)


def _ipt(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def list_url(branch_id):
    return f"{BASE}/events/list?branches[{branch_id}]={branch_id}"


def fetch_html(branch_id):
    url = list_url(branch_id)
    sep = "&" if "?" in url else "?"
    r = requests.get(f"{url}{sep}_cb={dt.datetime.now().timestamp():.0f}",
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


def keep(row, loc_match):
    """family + special: drop routine adult/internal; keep everything else that
    is clearly an event at this branch's town."""
    if any(k in row["title"].lower() for k in EXCLUDE_TITLE):
        return False
    loc = row["location"].lower()
    if loc and loc_match not in loc and loc_match not in row["aria"].lower():
        return False
    return True


def fmt_time(h, m):
    suffix = "am" if h < 12 else "pm"
    hh = h % 12 or 12
    return f"{hh}:{m:02d}{suffix}" if m else f"{hh}{suffix}"


def build_branch(branch, today):
    """Fetch and shape one branch's events, stamped with its area and town."""
    name, area, town = branch["name"], branch["area"], branch["town"]
    organizer = f"SLO County Library, {name} Branch"
    venue_default = f"{name} Library"

    rows = parse_cards(fetch_html(branch["id"]))
    dated = []
    for row in rows:
        if not keep(row, branch["loc_match"]):
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
        venue = sample["location"] or venue_default
        fam_all = is_family(set().union(*[e["ages"] for e in group]))

        if len(group) >= WEEKLY_MIN and len(weekdays) == 1:
            t = sample["time"]
            tstr = fmt_time(*t) if t else ""
            events.append({
                "title": sample["title"], "recurrence": "weekly",
                "window": f"Every {sample['date'].strftime('%A')}"
                          + (f", {tstr}" if tstr else "") + f" at the {name} library",
                "chip_top": sample["date"].strftime("%a"),
                "chip_bottom": tstr or "Library",
                "season_end": group[-1]["date"].isoformat(),
                "venue": venue, "area": area, "town": town, "category": "library",
                "organizer": organizer,
                "summary": (f"Recurring family program at the {name} library branch."
                            if fam_all else f"Recurring program at the {name} library branch."),
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
                    "venue": e["location"] or venue_default,
                    "area": area, "town": town,
                    "category": "library", "organizer": organizer,
                    "summary": (f"Family event at the {name} library branch."
                                if is_family(e["ages"]) else f"Public event at the {name} library branch."),
                    "source_url": e["url"], "confidence": 90,
                })
    return events, len(rows), len(dated)


def discover_branches():
    """Best-effort: read the branch filter and return {id: label} so the Morro
    Bay and Cambria ids can be read out of the probe log. Never raises."""
    try:
        url = f"{BASE}/events/list"
        sep = "&" if "?" in url else "?"
        r = requests.get(f"{url}{sep}_cb={dt.datetime.now().timestamp():.0f}",
                         headers=UA, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        found = {}
        for el in soup.find_all(attrs={"name": re.compile(r"branches\[\d+\]")}):
            m = re.search(r"branches\[(\d+)\]", el.get("name", ""))
            if not m:
                continue
            bid, label, eid = m.group(1), "", el.get("id")
            if eid:
                lab = soup.find("label", attrs={"for": eid})
                if lab:
                    label = _ipt(lab.get_text())
            if not label and el.parent:
                label = _ipt(el.parent.get_text())[:60]
            found.setdefault(bid, label)
        if not found:  # fallback: links carrying the branch filter in the href
            for a in soup.find_all("a", href=re.compile(r"branches\[\d+\]")):
                m = re.search(r"branches\[(\d+)\]", a.get("href", ""))
                if m:
                    found.setdefault(m.group(1), _ipt(a.get_text())[:60])
        return found
    except Exception as e:
        print(f"branch discovery failed ({e})", file=sys.stderr)
        return {}


def build(today):
    events, stats = [], []
    for b in BRANCHES:
        if b["id"] is None:
            continue
        try:
            evs, n_cards, n_kept = build_branch(b, today)
        except Exception as e:  # one bad branch must not drop the others
            print(f"{b['name']} branch fetch failed ({e}); skipping", file=sys.stderr)
            continue
        events.extend(evs)
        stats.append((b["name"], n_cards, n_kept, len(evs)))
    return events, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="write library-events.json")
    args = ap.parse_args()
    today = dt.datetime.now(PACIFIC).date()

    try:
        events, stats = build(today)
    except Exception as e:  # never let the fetcher pollute or crash the pipeline
        print(f"library fetch failed ({e}); leaving {OUT} untouched", file=sys.stderr)
        return
    if not events:
        print(f"no library events parsed; leaving {OUT} untouched", file=sys.stderr)
        return

    for (name, n_cards, n_kept, n_emit) in stats:
        print(f"{name}: parsed {n_cards} links, {n_kept} kept after filter, {n_emit} rows")
    rec = [e for e in events if e.get("recurrence")]
    one = [e for e in events if not e.get("recurrence")]
    print(f"\nRECURRING ({len(rec)}):")
    for e in rec:
        print(f"  ~ {e['chip_top']:>3} {e['chip_bottom']:<8} [{e.get('town') or 'Cayucos'}] "
              f"{e['title']}  [through {e['season_end']}]")
    print(f"\nDATED ({len(one)}):")
    for e in sorted(one, key=lambda x: x["start"]):
        print(f"  + {e['start'][:16]}  [{e.get('town') or 'Cayucos'}] {e['title']}")

    missing = [b["name"] for b in BRANCHES if b["id"] is None]
    if missing:
        print(f"\nBRANCH IDS  (still needed for: {', '.join(missing)})")
        found = discover_branches()
        if found:
            for bid, label in sorted(found.items(), key=lambda kv: int(kv[0])):
                print(f"  branches[{bid}] = {label or '(no label found)'}")
            print("  -> copy the Morro Bay and Cambria ids into BRANCHES at the top of this file.")
        else:
            print(f"  could not auto-detect; open {BASE}/events/list and read the branch filter.")

    doc = {"harvested_at": dt.datetime.now(PACIFIC).isoformat(), "events": events}
    target = OUT if args.write else OUT + ".proposed"
    with open(target, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(f"\n{'WROTE ' + target if args.write else 'dry run -> ' + target}")


if __name__ == "__main__":
    main()
