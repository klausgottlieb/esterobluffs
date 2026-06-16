#!/usr/bin/env python3
"""One-shot diagnostic for the SLO County Library Cayucos calendar.

This is NOT part of the events pipeline. It writes nothing the site reads.
Its only job is to run once in GitHub Actions (a runner can reach the library
domain; our other tooling cannot) and PRINT what the page actually exposes, so
the real fetcher can be written against verified facts instead of guesses.

It answers four questions:
  1. Does the page advertise a machine feed (iCal / RSS / JSON:API)?
  2. Do individual events carry an .ics / add-to-calendar link? (the durable path)
  3. Does server-side branch filtering (?branches[76]=76) actually work?
  4. What is the real HTML structure of one event row? (so selectors are correct)

Delete this file and its workflow once the real fetcher is in.
"""
from __future__ import annotations
import re
import sys

import requests
from bs4 import BeautifulSoup

BASE = "https://sanluisobispo.librarycalendar.com"
CAYUCOS_BRANCH_ID = 76
UA = {"User-Agent": "Mozilla/5.0 (compatible; esterobluffs-probe/1.0)"}

# Try the documented Cayucos list URL with the branch filter, plus an unfiltered
# control, so we can tell whether the server honors the filter or returns all 15
# branches (in which case the real fetcher must filter client-side on the branch
# name).
TARGETS = {
    "filtered_list": f"{BASE}/events/list?branches[{CAYUCOS_BRANCH_ID}]={CAYUCOS_BRANCH_ID}",
    "filtered_upcoming": f"{BASE}/events/upcoming?branches[{CAYUCOS_BRANCH_ID}]={CAYUCOS_BRANCH_ID}",
    "unfiltered_control": f"{BASE}/events/upcoming",
}

BRANCHES = [
    "Arroyo Grande", "Atascadero", "Cambria", "Cayucos", "Creston", "Los Osos",
    "Morro Bay", "Nipomo", "Oceano", "San Luis Obispo", "San Miguel",
    "Santa Margarita", "Shandon", "Shell Beach",
]


def fetch(url):
    # cache-bust so we never read a stale edge copy
    sep = "&" if "?" in url else "?"
    r = requests.get(f"{url}{sep}_cb={hash(url) & 0xffffff}", headers=UA, timeout=30)
    return r


def report_feeds(html, label):
    print(f"\n[{label}] FEED / EXPORT DISCOVERY")
    # <link rel="alternate"> feed declarations in the head
    links = re.findall(r'<link[^>]+rel=["\']alternate["\'][^>]*>', html, re.I)
    for ln in links[:10]:
        print("  link alternate:", ln.strip())
    # any href that looks machine-readable
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, re.I)
    machine = sorted({h for h in hrefs
                      if re.search(r'(\.ics|/ical|/feed|jsonapi|\.json|/rss|format=ical|format=json|outlook\.office|calendar\.google|/export)', h, re.I)})
    if machine:
        for h in machine[:40]:
            print("  machine href:", h)
    else:
        print("  (no .ics / ical / feed / jsonapi / rss hrefs found in markup)")


def report_branch_filter(html, label):
    counts = {b: len(re.findall(re.escape(b) + r"\s+Library", html)) for b in BRANCHES}
    nonzero = {b: c for b, c in counts.items() if c}
    print(f"\n[{label}] BRANCH DISTRIBUTION (rough text counts)")
    print("  ", nonzero if nonzero else "(no branch names matched)")
    only_cayucos = set(nonzero) <= {"Cayucos"} and nonzero.get("Cayucos", 0) > 0
    print("  -> server-side filter effective:", only_cayucos)


def report_event_sample(html, label):
    print(f"\n[{label}] RAW HTML SAMPLE AROUND FIRST EVENT")
    # Anchor on the first per-event link or the first "Library Branch:" label.
    m = re.search(r'href=["\'](/event/[^"\']+)["\']', html, re.I)
    if m:
        idx = m.start()
        print("  first /event/ href:", m.group(1))
    else:
        idx = html.find("Library Branch")
    if idx == -1:
        print("  (could not locate an event anchor in markup)")
        return
    excerpt = html[max(0, idx - 400): idx + 1600]
    print("  ---- begin excerpt ----")
    print(excerpt)
    print("  ---- end excerpt ----")


def main():
    for label, url in TARGETS.items():
        print("=" * 70)
        print(f"TARGET {label}: {url}")
        try:
            r = fetch(url)
        except Exception as e:
            print("  FETCH FAILED:", e)
            continue
        html = r.text
        print(f"  HTTP {r.status_code}  final_url={r.url}  bytes={len(html)}")
        print(f"  'Cayucos' occurrences: {html.count('Cayucos')}")
        report_feeds(html, label)
        report_branch_filter(html, label)
        # only dump the heavy raw sample once, from the filtered list
        if label == "filtered_list":
            report_event_sample(html, label)
    print("\nDONE. Paste this entire log back so the real fetcher can be finalized.")


if __name__ == "__main__":
    sys.exit(main())
