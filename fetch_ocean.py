#!/usr/bin/env python3
"""
fetch_ocean.py -- writes ocean-data.json for the Estero Bluffs page.

Wave source, in order of preference:
  1. CDIP MOP nearshore point (the offshore wave field transformed to this coast:
     refraction, shoaling, island/headland blocking). Read from CDIP's THREDDS
     server over OPeNDAP's plain-text ASCII service, so NO netCDF library is
     needed -- just stdlib HTTP + text parsing.
  2. Fallback: NDBC buoy 46215 (Diablo Canyon) .txt + .spec -- the raw offshore buoy.
Wind: NDBC PSLC1 (Port San Luis) with 46011 (Santa Maria) as backup.

MOP is OFF until you set MOP_ID below to the point off the bluffs:
  - Open CDIP's MOP map (https://cdip.ucsd.edu/mops/), find Estero Bluffs / north
    Cayucos, click the nearshore alongshore point, read its 5-char id. San Luis
    Obispo county points start with "SL" (e.g. SL567), numbered south -> north.
  - Set MOP_ID below, then confirm it before relying on it:
        python3 fetch_ocean.py --probe SL567
    which prints that point's latest modeled wave height, period, and direction.
If MOP is unset or fails for any reason, the script silently falls back to buoy
46215 and labels the data "raw offshore". It can never break the page.

Usage:
  python3 fetch_ocean.py [ocean-data.json]
  python3 fetch_ocean.py --probe [SLxxx]   # check a MOP point's latest values
"""
import json, sys, os, datetime, urllib.request, re

WAVE_BUOY = "46215"
MOP_ID    = "SL405"   # CDIP MOP alongshore point directly off Bluff House (by transect); empty = buoy fallback
MOP_BASE  = "https://thredds.cdip.ucsd.edu/thredds/dodsC/cdip/model/MOP_alongshore/"
NDBC      = "https://www.ndbc.noaa.gov/data/realtime2/"
UA        = {"User-Agent": "esterobluffs-ocean/1.0"}

MISSING = {"MM","999","999.0","9999.0","99.0","N/A",""}
M_TO_FT, MS_TO_KT = 3.28084, 1.94384
COMPASS = {"N":0,"NNE":22.5,"NE":45,"ENE":67.5,"E":90,"ESE":112.5,"SE":135,"SSE":157.5,
           "S":180,"SSW":202.5,"SW":225,"WSW":247.5,"W":270,"WNW":292.5,"NW":315,"NNW":337.5}

def fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
        return r.read().decode("utf-8","replace")

def num(t):
    if t is None or t in MISSING: return None
    try: return float(t)
    except ValueError: return None

def tdir(t):
    if t is None or t in MISSING: return None
    return COMPASS.get(t.upper())

def m_ft(v): return round(v*M_TO_FT,2) if v is not None else None
def ms_kt(v): return round(v*MS_TO_KT,1) if v is not None else None

def rows(text):
    return [ln.split() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]

def iso(r):
    try:
        return datetime.datetime(int(r[0]),int(r[1]),int(r[2]),int(r[3]),int(r[4]),
                                 tzinfo=datetime.timezone.utc).isoformat()
    except Exception: return None

# ---- NDBC buoy (fallback, tested format) ----
def buoy_std(text):
    rr = rows(text)
    if not rr: return {}
    r = rr[0]; g=lambda i: r[i] if len(r)>i else None
    return {"time":iso(r),"wvht":num(g(8)),"dpd":num(g(9)),"apd":num(g(10)),"mwd":num(g(11)),
            "wdir":num(g(5)),"wspd":num(g(6)),"gst":num(g(7))}

def buoy_spec(text):
    rr = rows(text)
    if not rr: return {}
    r = rr[0]; g=lambda i: r[i] if len(r)>i else None
    return {"swh":num(g(6)),"swp":num(g(7)),"wwh":num(g(8)),"wwp":num(g(9)),
            "swd":tdir(g(10)),"wwd":tdir(g(11)),"steep":(g(12) if g(12) not in MISSING else None)}

# ---- CDIP MOP (preferred): read the nearshore point from THREDDS via OPeNDAP's
# ---- plain-text ASCII service. Stdlib only, no netCDF library. ----
def mop_url(suffix):
    return "%s%s_nowcast.nc%s" % (MOP_BASE, MOP_ID, suffix)

def _ascii_value(body, var):
    """Pull the numeric value that follows 'var[...]' in an OPeNDAP .ascii body."""
    m = re.search(re.escape(var) + r"\[[^\]]*\]\s*([-\d.][\d.eE+\-]*)", body)
    if not m: return None
    try: return float(m.group(1))
    except ValueError: return None

def mop_latest():
    """Latest modeled Hs (m), Tp (s), Dp (deg) for MOP_ID. Returns None on any
    problem so the caller falls back to the offshore buoy. Two small requests:
    the .dds gives the time-dimension length; the .ascii gives the last sample."""
    if not MOP_ID: return None
    dds = fetch(mop_url(".dds"))
    m = re.search(r"waveTime\s*=\s*(\d+)", dds)
    if not m: return None
    i = int(m.group(1)) - 1
    if i < 0: return None
    q = ",".join("%s[%d:1:%d]" % (v, i, i) for v in ("waveHs","waveTp","waveDp","waveTime"))
    body = fetch(mop_url(".ascii?" + q))
    # use only the data section (after the dashed separator) when present
    parts = re.split(r"-{10,}", body)
    body = parts[-1] if len(parts) > 1 else body
    hs = _ascii_value(body, "waveHs")
    if hs is None: return None
    t = _ascii_value(body, "waveTime")
    iso = None
    if t is not None:
        try: iso = datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat()
        except Exception: iso = None
    return {"Hs": hs, "Tp": _ascii_value(body,"waveTp"), "Dp": _ascii_value(body,"waveDp"), "time": iso}

def build_mop():
    if not MOP_ID: return None
    try:
        p = mop_latest()
        if not p or p.get("Hs") is None: return None
        hs = p["Hs"]
        return {"provenance":"CDIP MOP %s (modeled nearshore)"%MOP_ID,
                "time":p.get("time"),
                "wvht_m":round(hs,2),"wvht_ft":m_ft(hs),
                "dpd_s":p.get("Tp"),"apd_s":None,"mwd_deg":p.get("Dp"),
                "partition_measured":False}
    except Exception:
        return None

def build_buoy():
    std = spec = {}
    try: std = buoy_std(fetch(NDBC+WAVE_BUOY+".txt"))
    except Exception: pass
    try: spec = buoy_spec(fetch(NDBC+WAVE_BUOY+".spec"))
    except Exception: pass
    if not std: return None
    have = all(spec.get(k) is not None for k in ("swh","wwh","swp","wwp"))
    hs = std.get("wvht")
    return {"provenance":"NDBC buoy %s (raw offshore)"%WAVE_BUOY,
            "time":std.get("time"),
            "wvht_m":hs,"wvht_ft":m_ft(hs),
            "dpd_s":std.get("dpd"),"apd_s":std.get("apd"),"mwd_deg":std.get("mwd"),
            "partition_measured":have,
            "swh_m":spec.get("swh"),"swh_ft":m_ft(spec.get("swh")),"swp_s":spec.get("swp"),"swd_deg":spec.get("swd"),
            "wwh_m":spec.get("wwh"),"wwh_ft":m_ft(spec.get("wwh")),"wwp_s":spec.get("wwp"),"wwd_deg":spec.get("wwd"),
            "steepness":spec.get("steep")}

# Ordered wind sources: nearest first. PSLC1 (6 nm, most representative) is a
# tide station whose anemometer drops out often; 46011 (open-ocean Santa Maria)
# reports wind reliably and backstops the gap. Whichever supplies a reading is
# the one named on the page.
WIND_SOURCES = [("PSLC1","Port San Luis, CA"), ("46011","Santa Maria, CA (open ocean)")]

def build_wind():
    last = {}
    for stn, name in WIND_SOURCES:
        try: w = buoy_std(fetch(NDBC+stn+".txt"))
        except Exception: continue
        rec = {"station":stn,"name":name,"time":w.get("time"),
               "wspd_kt":ms_kt(w.get("wspd")),"gst_kt":ms_kt(w.get("gst")),
               "wdir_deg":w.get("wdir")}
        if rec["wspd_kt"] is not None: return rec   # got a real wind, use it
        last = rec                                   # remember, keep trying
    return last                                      # all blank: honest dash, nearest label

def build():
    mop  = build_mop()    # modeled nearshore, at the bluff (~2 h behind: hourly model + swell travel time)
    buoy = build_buoy()   # measured, raw offshore, ~hourly and fresher
    wave = mop or buoy or {"provenance":"unavailable","partition_measured":False}
    out = {"updated":datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "wave":{"name":"Estero Bluffs nearshore","lat":35.4492,"lon":-120.9160, **wave},
           "wind":build_wind()}
    # When MOP is the primary, also carry the measured offshore buoy as a secondary
    # reading: the same swell, measured and fresher, before refraction and shoaling
    # bring it onto the bluff. Omitted when the buoy is already the primary (MOP down),
    # so the page never shows the same reading twice.
    if mop and buoy:
        out["wave_offshore"] = buoy
    return out

# ============================================================================
# 12-HOUR WAVE-TREND HISTORY
# Appends one trend point per NEW wave observation (keyed on the wave's own
# 'time', not the fetch time), so ocean-history.json changes -- and therefore
# commits -- only when the buoy/model actually advances. Trims to 12 hours.
# Stored P and S use the same formulas the web page uses, so they can't drift.
# ============================================================================
HISTORY_PATH  = "ocean-history.json"
HISTORY_HOURS = 12

def _energy_kw(hs_m, tp_s):
    # Sea Force: wave power, P = 0.49 * H^2 * T  (kW per metre of crest).
    if hs_m is None or not tp_s:
        return None
    return round(0.49 * hs_m * hs_m * tp_s, 2)

def _steepness(hs_m, tp_s):
    # Steepness: S = H / L, deep-water wavelength L = 1.56 * T^2.
    if hs_m is None or not tp_s:
        return None
    return round(hs_m / (1.56 * tp_s * tp_s), 5)

def update_history(data, path=HISTORY_PATH, hours=HISTORY_HOURS):
    wave  = data.get("wave") or {}
    wtime = wave.get("time")
    hs    = wave.get("wvht_m")
    tp    = wave.get("dpd_s")
    if not wtime or hs is None or not tp:
        return  # nothing usable this run; leave history untouched

    point = {"t": wtime,
             "P": _energy_kw(hs, tp),
             "S": _steepness(hs, tp),
             "hs_ft": wave.get("wvht_ft"),
             "tp_s": tp}

    hist = []
    if os.path.exists(path):
        try:
            with open(path) as f:
                hist = json.load(f)
            if not isinstance(hist, list):
                hist = []
        except Exception:
            hist = []

    # dedupe on observation time: replace if same obs re-fetched, else append
    if hist and hist[-1].get("t") == wtime:
        hist[-1] = point
    else:
        hist.append(point)

    # trim to the rolling window
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
    def _keep(p):
        try:
            return datetime.datetime.fromisoformat(p["t"]) >= cutoff
        except Exception:
            return True
    hist = [p for p in hist if _keep(p)]

    with open(path, "w") as f:
        json.dump(hist, f, indent=2)

def main():
    global MOP_ID
    if "--probe" in sys.argv:
        cand = next((a for a in sys.argv[1:] if a.upper().startswith("SL") or (a[:1].isalpha() and a[-1:].isdigit() and not a.startswith("-"))), None)
        if cand: MOP_ID = cand
        if not MOP_ID:
            print("Give a point id, e.g.:  python3 fetch_ocean.py --probe SL567"); return
        print("Probing CDIP MOP point %s ..." % MOP_ID)
        try:
            p = mop_latest()
        except Exception as e:
            print("FAILED to read MOP point:", e); return
        if not p:
            print("No data parsed. Check the id is correct (5 chars, e.g. SL567) and that the point exists."); return
        print("latest modeled values for %s:" % MOP_ID)
        print("  wave height: %.2f m (%.1f ft)" % (p["Hs"], p["Hs"]*M_TO_FT))
        print("  peak period: %s s" % p.get("Tp"))
        print("  direction:   %s deg" % p.get("Dp"))
        print("  time (UTC):  %s" % p.get("time"))
        print("If those look like your spot, set MOP_ID = \"%s\" at the top and deploy." % MOP_ID)
        return
    out_path = next((a for a in sys.argv[1:] if not a.startswith("-")), "ocean-data.json")
    data = build()
    update_history(data)
    json.dump(data, open(out_path,"w"), indent=2)
    print("wrote", out_path); print(json.dumps(data, indent=2))

if __name__ == "__main__":
    main()
