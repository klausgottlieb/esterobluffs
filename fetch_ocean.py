#!/usr/bin/env python3
"""
fetch_ocean.py -- writes ocean-data.json for the Estero Bluffs page.

Wave source, in order of preference:
  1. CDIP MOP nearshore point (transformed to the coast: refraction + shoaling).
     Accessed via the mop_data_access.cdip CGI (plain text, no netCDF needed).
  2. Fallback: NDBC buoy 46215 (Diablo Canyon) .txt + .spec  -- the raw offshore buoy.
Wind: NDBC PSLC1 (Port San Luis), the nearest reporting anemometer.

MOP is OFF until you set MOP_ID below to the confirmed point off Cayucos:
  - Open the CDIP MOP map, click the alongshore point nearest the bluff, read its
    5-char ID (San Luis Obispo county -> "SL" prefix, numbered south->north).
  - Set MOP_ID, then run:  python3 fetch_ocean.py --probe
    to print the raw CGI response so the parser can be confirmed against reality.
Until then the script runs on buoy 46215 and labels the data accordingly.

Usage:
  python3 fetch_ocean.py [ocean-data.json]
  python3 fetch_ocean.py --probe        # dump raw MOP response for the set MOP_ID
"""
import json, sys, datetime, urllib.request

WAVE_BUOY = "46215"
MOP_ID    = ""        # <-- set to confirmed Cayucos MOP id, e.g. "SL063"; empty = buoy fallback
MOP_CGI   = "https://cdip.ucsd.edu/data_access/mop_data_access.cdip"
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

# ---- CDIP MOP (preferred). Tolerant parser: handles XML or whitespace-column text. ----
def mop_fetch_raw(tag):
    # tag: 'mp' bulk params, 'ss' sea/swell 2-band params. '1' = last 1 day.
    return fetch("%s?%s+%s+1" % (MOP_CGI, MOP_ID, tag))

def _floats(s):
    out=[]
    for tok in s.replace(",", " ").split():
        try: out.append(float(tok))
        except ValueError: pass
    return out

def mop_parse_latest(raw_mp, raw_ss):
    """Best-effort: return dict with hs_m,tp_s,dp_deg and swell/sea split.
    NOTE: written to the documented CGI interface but UNVERIFIED against a live
    response from the build sandbox. Run --probe and adjust column indices if needed."""
    import xml.etree.ElementTree as ET
    res = {}
    # Try XML first
    def try_xml(raw, keys):
        try:
            root = ET.fromstring(raw)
            last = list(root.iter())[-1] if len(list(root.iter())) else None
            # collect attrib floats from the last record-like element
            for el in root.iter():
                a = el.attrib
                for k in keys:
                    if k in a and num(a[k]) is not None:
                        res.setdefault(k, num(a[k]))
            return bool(res)
        except Exception:
            return False
    used_xml = try_xml(raw_mp, ["Hs","Tp","Dp"])
    if not used_xml:
        # whitespace fallback: take last non-empty data line, last numbers as Hs Tp Dp (order varies!)
        data_lines=[ln for ln in raw_mp.splitlines() if _floats(ln)]
        if data_lines:
            vals=_floats(data_lines[-1])
            # heuristic: time columns first; the trailing values carry params.
            # CONFIRM order with --probe; placeholder assumes ... Hs Tp Dp at the end.
            if len(vals)>=3:
                res["Hs"], res["Tp"], res["Dp"] = vals[-3], vals[-2], vals[-1]
    return res or None

def build_mop():
    if not MOP_ID: return None
    try:
        mp = mop_fetch_raw("mp"); ss = mop_fetch_raw("ss")
        p = mop_parse_latest(mp, ss)
        if not p or p.get("Hs") is None: return None
        hs = p["Hs"]
        return {"provenance":"CDIP MOP %s (transformed nearshore)"%MOP_ID,
                "wvht_m":hs,"wvht_ft":m_ft(hs),
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
    wave = build_mop() or build_buoy() or {"provenance":"unavailable","partition_measured":False}
    return {"updated":datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "wave":{"name":"Estero Bluffs nearshore","lat":35.4492,"lon":-120.9160, **wave},
            "wind":build_wind()}

def main():
    if "--probe" in sys.argv:
        if not MOP_ID: print("MOP_ID not set."); return
        print("=== mp ===\n"+mop_fetch_raw("mp")[:1500])
        print("\n=== ss ===\n"+mop_fetch_raw("ss")[:1500]); return
    out_path = next((a for a in sys.argv[1:] if not a.startswith("-")), "ocean-data.json")
    data = build()
    json.dump(data, open(out_path,"w"), indent=2)
    print("wrote", out_path); print(json.dumps(data, indent=2))

if __name__ == "__main__":
    main()
