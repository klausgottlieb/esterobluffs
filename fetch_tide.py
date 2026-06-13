#!/usr/bin/env python3
"""
Fetch tide predictions for Estero Bluffs from NOAA CO-OPS and write tide-data.json.

Station: Port San Luis, CA (9412110). It is the nearest OPEN-COAST harmonic
station, so it supports a continuous prediction curve (not just high/low) and is
a better physical analog for the open shoreline at Estero Bluffs than the
bay-mouth stations around Morro Bay, whose tides lag and distort.

Predictions are astronomical (computed years ahead), so this can run once a day
and never be stale. No real-time sensor dependency, unlike the weather feeds.

Writes:
  tide-data.json = {
    updated: ISO-UTC,
    station, name,
    hilo:  [{t:"YYYY-MM-DD HH:MM", type:"H"|"L", ft:float}, ...]   # ~8 days, local time
    curve: [{t:"YYYY-MM-DD HH:MM", ft:float}, ...]                 # 30-min, ~3 days, local time
  }
Times are LOCAL wall-clock at the station (NOAA time_zone=lst_ldt); the page
parses them as local and lines them up with the visitor's own clock.

Usage:  python3 fetch_tide.py [out_path]   (default tide-data.json)
"""
import sys, json, datetime, urllib.request, urllib.parse

API = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
STATION = "9412110"
NAME = "Port San Luis, CA"

def fetch(params):
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "esterobluffs.com tide fetcher"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

def common(begin, end, **extra):
    p = {"product": "predictions", "application": "esterobluffs.com",
         "begin_date": begin, "end_date": end, "datum": "MLLW",
         "station": STATION, "time_zone": "lst_ldt", "units": "english",
         "format": "json"}
    p.update(extra)
    return p

def main():
    out = next((a for a in sys.argv[1:] if not a.startswith("-")), "tide-data.json")
    # Start one day BEFORE today (UTC) so the viewer's local "today" is always covered,
    # whatever the timezone offset and whenever the daily job last ran.
    today = datetime.datetime.now(datetime.timezone.utc).date()
    # High/low list spans +/-3 weeks: the page ranks each daylight low against the
    # distribution of daytime lows over this window ("low for the season"), so it
    # needs a seasonal sample, not just the next few days.
    begin_hilo = (today - datetime.timedelta(days=21)).strftime("%Y%m%d")
    end_hilo = (today + datetime.timedelta(days=21)).strftime("%Y%m%d")
    # The 30-minute curve only feeds the chart and the first-light interpolation,
    # so it stays a tight ~3-day window to keep the file small.
    begin_curve = (today - datetime.timedelta(days=1)).strftime("%Y%m%d")
    end_curve = (today + datetime.timedelta(days=2)).strftime("%Y%m%d")

    data = {"updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "station": STATION, "name": NAME, "hilo": [], "curve": []}
    try:
        hilo = fetch(common(begin_hilo, end_hilo, interval="hilo"))
        if "predictions" not in hilo:
            print("hilo response had no 'predictions':", str(hilo)[:300], file=sys.stderr)
        for p in hilo.get("predictions", []):
            data["hilo"].append({"t": p["t"], "type": p["type"], "ft": round(float(p["v"]), 2)})
    except Exception as e:
        print("hilo fetch failed:", e, file=sys.stderr)
    try:
        curve = fetch(common(begin_curve, end_curve, interval="30"))   # 30-minute steps
        if "predictions" not in curve:
            print("curve response had no 'predictions':", str(curve)[:300], file=sys.stderr)
        for p in curve.get("predictions", []):
            data["curve"].append({"t": p["t"], "ft": round(float(p["v"]), 2)})
    except Exception as e:
        print("curve fetch failed:", e, file=sys.stderr)

    # If NOAA's high/low list came back empty but we have a curve, derive highs and
    # lows from the curve's own peaks and troughs so the file is always self-sufficient.
    if not data["hilo"] and len(data["curve"]) >= 3:
        c = data["curve"]
        for i in range(1, len(c) - 1):
            a, b, d = c[i-1]["ft"], c[i]["ft"], c[i+1]["ft"]
            if b >= a and b > d:
                data["hilo"].append({"t": c[i]["t"], "type": "H", "ft": b})
            elif b <= a and b < d:
                data["hilo"].append({"t": c[i]["t"], "type": "L", "ft": b})
        if data["hilo"]:
            print("derived", len(data["hilo"]), "hilo points from curve (NOAA hilo was empty)")

    if not data["hilo"] and not data["curve"]:
        print("No tide data retrieved; leaving previous file untouched.", file=sys.stderr)
        sys.exit(1)

    json.dump(data, open(out, "w"), indent=2)
    print("wrote", out, "-", len(data["hilo"]), "hilo,", len(data["curve"]), "curve points")

if __name__ == "__main__":
    main()
