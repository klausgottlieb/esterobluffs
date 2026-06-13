#!/usr/bin/env python3
"""
Fetch the NWS hourly forecast for Bluff House (Cayucos) and write forecast-data.json.

Purpose: the page matches this forecast to upcoming DAYLIGHT low tides so a
tide-pooling window can be reported as likely sunny, partly cloudy, or overcast.
Weather is shown as guidance only; it never downgrades a good tide.

Source: National Weather Service api.weather.gov (US government, free, no key).
NWS requires a descriptive User-Agent on every request.

Two-step resolution (NWS design):
  1) /points/{lat},{lon}     -> forecastHourly URL + forecastGridData URL + office
  2) forecastHourly          -> hourly periods (short text, rain %, temperature)
     forecastGridData        -> numeric skyCover % time series (best-effort)

skyCover is the honest numeric cloud figure. If the gridpoint call fails, we
derive an approximate sky % from NWS's own worded shortForecast instead, so the
file is always usable.

Writes forecast-data.json = {
  updated: ISO-UTC, source, office,
  hours: [{t: ISO-with-offset, sky: int 0-100, pop: int 0-100, tempF: int, short: str}, ...]
}
Times are kept in NWS's own offset form; the page parses them as real instants.

Usage:  python3 fetch_forecast.py [out_path]   (default forecast-data.json)
"""
import sys, json, re, datetime, urllib.request, urllib.parse

LAT, LON = 35.449, -120.916          # Bluff House, Cayucos (matches the page)
UA = "esterobluffs.com forecast fetcher (contact: webmaster@esterobluffs.com)"
HOURS_AHEAD = 60                     # keep the file small; covers ~2.5 days of tides

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/geo+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

# NWS shortForecast wording -> representative sky-cover %, used only if the
# numeric gridpoint skyCover is unavailable.
def sky_from_text(short):
    s = (short or "").lower()
    if "overcast" in s or s.strip() in ("cloudy",):            return 95
    if "mostly cloudy" in s:                                   return 80
    if "partly sunny" in s or "partly cloudy" in s:            return 50
    if "mostly sunny" in s or "mostly clear" in s:             return 25
    if "sunny" in s or "clear" in s or "fair" in s:            return 5
    if "fog" in s or "haze" in s:                              return 90
    if "rain" in s or "showers" in s or "drizzle" in s:        return 90
    return 50  # unknown wording -> call it a coin flip, neither claim sun nor doom

# Parse NWS validTime "2026-06-13T14:00:00+00:00/PT6H" into (start_epoch_s, span_hours).
_DUR = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?")
def expand_intervals(values):
    out = {}  # hour-floor epoch (s) -> value
    for v in values or []:
        vt = v.get("validTime", "")
        if "/" not in vt:
            continue
        start_s, dur = vt.split("/", 1)
        try:
            start = datetime.datetime.fromisoformat(start_s)
        except ValueError:
            continue
        m = _DUR.fullmatch(dur)
        if not m:
            continue
        days, hrs, mins = (int(x) if x else 0 for x in m.groups())
        span_h = max(1, days * 24 + hrs + (1 if mins else 0))
        base = int(start.timestamp()) // 3600 * 3600
        for k in range(span_h):
            out[base + k * 3600] = v.get("value")
    return out

def main():
    out = next((a for a in sys.argv[1:] if not a.startswith("-")), "forecast-data.json")
    data = {"updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "source": "NWS api.weather.gov", "office": None, "hours": []}

    try:
        pts = get(f"https://api.weather.gov/points/{LAT:.4f},{LON:.4f}")
        props = pts.get("properties", {})
        data["office"] = props.get("cwa") or props.get("gridId")
        hourly_url = props["forecastHourly"]
        grid_url = props.get("forecastGridData")
    except Exception as e:
        print("points lookup failed:", e, file=sys.stderr)
        sys.exit(1)

    # numeric sky cover (best-effort; tolerate failure)
    sky_by_hour = {}
    try:
        grid = get(grid_url)
        sky_by_hour = expand_intervals(grid.get("properties", {})
                                       .get("skyCover", {}).get("values"))
    except Exception as e:
        print("gridpoint skyCover unavailable, will derive from text:", e, file=sys.stderr)

    try:
        fc = get(hourly_url)
        periods = fc.get("properties", {}).get("periods", [])
    except Exception as e:
        print("hourly forecast fetch failed:", e, file=sys.stderr)
        sys.exit(1)

    for p in periods[:HOURS_AHEAD]:
        try:
            start = datetime.datetime.fromisoformat(p["startTime"])
        except (ValueError, KeyError):
            continue
        hkey = int(start.timestamp()) // 3600 * 3600
        sky = sky_by_hour.get(hkey)
        if sky is None:
            sky = sky_from_text(p.get("shortForecast"))
        pop = (p.get("probabilityOfPrecipitation") or {}).get("value")
        data["hours"].append({
            "t": p["startTime"],
            "sky": int(round(sky)) if sky is not None else None,
            "pop": int(pop) if pop is not None else 0,
            "tempF": p.get("temperature"),
            "short": p.get("shortForecast"),
        })

    if not data["hours"]:
        print("No forecast hours parsed; leaving previous file untouched.", file=sys.stderr)
        sys.exit(1)

    json.dump(data, open(out, "w"), indent=2)
    print("wrote", out, "-", len(data["hours"]), "hours,",
          "numeric sky" if sky_by_hour else "text-derived sky")

if __name__ == "__main__":
    main()
