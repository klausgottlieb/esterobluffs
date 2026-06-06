#!/usr/bin/env python3
"""
fetch_station.py -- writes station-data.json for the "Bluff House Station Cayucos"
(WeatherFlow Tempest). Standard library only.

Secrets come from the environment so the token NEVER reaches the browser:
  WEATHERFLOW_TOKEN   personal access token (tempestwx.com -> Data Authorizations)
  TEMPEST_STATION_ID  numeric station id (from the Stations service / app URL)

Run on a tight cadence (Tempest updates ~every minute) separately from the ocean job.

Usage:
  WEATHERFLOW_TOKEN=... TEMPEST_STATION_ID=12345 python3 fetch_station.py [station-data.json]
"""
import os, sys, json, datetime, urllib.request

TOKEN = os.environ.get("WEATHERFLOW_TOKEN", "")
STN   = os.environ.get("TEMPEST_STATION_ID", "")
BASE  = "https://swd.weatherflow.com/swd/rest/observations/station/"
UA    = {"User-Agent": "esterobluffs-station/1.0"}

C_F   = lambda c: round(c*9/5+32) if c is not None else None
MS_MPH= lambda v: round(v*2.23694) if v is not None else None
MM_IN = lambda v: round(v/25.4, 2) if v is not None else None
KM_MI = lambda v: round(v*0.621371, 1) if v is not None else None

def fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        return json.loads(r.read().decode("utf-8","replace"))

def build():
    if not (TOKEN and STN):
        return {"error":"set WEATHERFLOW_TOKEN and TEMPEST_STATION_ID"}
    d = fetch(BASE + STN + "?token=" + TOKEN)
    obs = (d.get("obs") or [{}])[0]            # latest federated station observation
    g = obs.get
    epoch = g("timestamp")
    t_iso = (datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).isoformat()
             if epoch else None)
    return {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "station": {"name": "Bluff House Station Cayucos",
                    "id": STN, "tz": d.get("timezone"),
                    "lat": d.get("latitude"), "lon": d.get("longitude")},
        "time": t_iso,
        "air_f":      C_F(g("air_temperature")),
        "feels_f":    C_F(g("feels_like")),
        "dew_f":      C_F(g("dew_point")),
        "humidity":   g("relative_humidity"),
        "wind_mph":   MS_MPH(g("wind_avg")),
        "gust_mph":   MS_MPH(g("wind_gust")),
        "lull_mph":   MS_MPH(g("wind_lull")),
        "wind_dir":   g("wind_direction"),
        "pressure_mb":g("sea_level_pressure") or g("station_pressure"),
        "pressure_trend": g("pressure_trend"),
        "uv":         g("uv"),
        "solar_wm2":  g("solar_radiation"),
        "rain_in_today": MM_IN(g("precip_accum_local_day")),
        "lightning_count": g("lightning_strike_count"),
        "lightning_mi":    KM_MI(g("lightning_strike_last_distance")),
    }

def main():
    out = next((a for a in sys.argv[1:] if not a.startswith("-")), "station-data.json")
    data = build()
    json.dump(data, open(out,"w"), indent=2)
    print("wrote", out); print(json.dumps(data, indent=2))

if __name__ == "__main__":
    main()
