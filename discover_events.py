#!/usr/bin/env python3
"""Headless discovery for the Cayucos events harvest.

Calls the Anthropic API with the web_search tool to find and verify upcoming
public Cayucos events, then writes harvest-raw.json. harvest_events.py turns
that into events-data.json. This runs unattended in GitHub Actions (see
.github/workflows/events-update.yml) and needs ANTHROPIC_API_KEY.

Fail-safe: if discovery fails for any reason, the previous harvest-raw.json is
left untouched, so the merge still runs on the last-good data and the calendar
never blanks.
"""
from __future__ import annotations
import datetime as dt
import json
import os
import re
import sys
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
RAW = "harvest-raw.json"
MODEL = "claude-sonnet-4-6"


def build_prompt(today):
    month = today.strftime("%B %Y")
    nxt = (today.replace(day=1) + dt.timedelta(days=32)).strftime("%B %Y")
    return f"""You are refreshing the Cayucos, California events list for a vacation-rental website. Today is {today.isoformat()}.

Use web_search to find REAL, UPCOMING, PUBLIC events in or right around Cayucos. Search queries such as:
- "Cayucos events {month}", "Cayucos events {nxt}", "things to do Cayucos {month}"
- "Cayucos live music {month}", "Cayucos festival OR fundraiser OR market {month}"
- "Cayucos pier event {month}"
Also look at these maintained sources and read their result content for current dates:
- Cayucos Chamber event pages (carshow, antique-faire, christmas-in-cayucos)
- Visit SLO CAL (slocal.com) Cayucos listings
- County library Cayucos branch programming (sanluisobispo.librarycalendar.com)
- Cayucos Lioness and Rotary calendars; Cayucos Land Conservancy walks/cleanups

Emit an event ONLY if ALL are true:
- it has a SPECIFIC date that is {today.isoformat()} or later (confirm from the content; do not trust a stale snippet);
- it is in Cayucos, OR clearly starts/finishes in Cayucos (use area "nearby" for those, e.g. the Rock to Pier run);
- it is NOT on hold, cancelled, or postponed.
Do NOT emit these three (added separately): Fourth of July, Polar Bear Dip, Cayucos Turkey Trot.
For a clearly recurring in-season series (weekly market or weekly music night), emit ONE recurring row, not many dated rows.
Paraphrase summaries in your own words, max 160 chars. Never copy event text verbatim.

Respond with ONLY a JSON object, no prose and no markdown fences, exactly this shape:
{{
  "events": [
    {{"title":"...","start":"YYYY-MM-DDTHH:MM:SS-07:00","venue":"...","area":"cayucos","category":"festival|market|music|race|community|library|arts|civic","organizer":"...","source_url":"https://...","summary":"...","confidence":0-100}},
    {{"title":"...","recurrence":"weekly","window":"Every Friday, 10am to 12:30pm, through Labor Day","chip_top":"Fri","chip_bottom":"10am","season_end":"YYYY-MM-DD","venue":"...","area":"cayucos","category":"market","organizer":"...","source_url":"https://...","summary":"...","confidence":0-100}}
  ]
}}"""


def extract_json(text):
    text = re.sub(r"^```(json)?", "", text.strip()).strip()
    text = re.sub(r"```$", "", text).strip()
    i, j = text.find("{"), text.rfind("}")
    if i == -1 or j == -1:
        raise ValueError("no JSON object in model response")
    return json.loads(text[i:j + 1])


def main():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print(f"ANTHROPIC_API_KEY not set; leaving {RAW} untouched", file=sys.stderr)
        return
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=key)
        today = dt.datetime.now(PACIFIC).date()
        resp = client.messages.create(
            model=MODEL,
            max_tokens=8000,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 10}],
            messages=[{"role": "user", "content": build_prompt(today)}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        data = extract_json(text)
        events = data.get("events", [])
        if not isinstance(events, list):
            raise ValueError("'events' is not a list")
        out = {"harvested_at": dt.datetime.now(PACIFIC).isoformat(), "events": events}
        with open(RAW, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"wrote {RAW}: {len(events)} candidate events")
    except Exception as e:  # never let discovery crash the pipeline
        print(f"discovery failed ({e}); leaving {RAW} untouched", file=sys.stderr)


if __name__ == "__main__":
    main()
