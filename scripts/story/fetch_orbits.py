"""Re-fetch current GP elements (OMM JSON + TLE) for the ISS (EMIT host) and Tanager-1 from CelesTrak,
then estimate where Tanager-1 was at each 2026 scene time.

Requests: 4 (Tanager search by name as JSON, ISS JSON, one TLE per satellite). CelesTrak asks users not
to re-download the same GP data more than once per update cycle (~2 h) — run this rarely.
Writes orbits_tkm.json.  Run: python3.12 -I fetch_orbits.py   (add --offline to recompute from the saved
elements in orbits_tkm.json without contacting CelesTrak)

HONESTY NOTE — why the mean anomaly is NOT back-propagated:
A current TLE propagated back months (Tanager-1) or years (ISS: reboosts, drag) cannot place the satellite
along its track: along-track error grows to hundreds or thousands of km. Two things survive:
  1. Carbon Mapper's own per-scene view geometry (scenes/annotated: `azimuth`, `off_nadir`). With an
     altitude this gives the sub-satellite point relative to the target directly.  -> METHOD A (preferred)
  2. A sun-synchronous orbit keeps its plane fixed relative to the Sun, so the current plane, moved back
     with J2 nodal drift (altitude held; a decaying-orbit variant is reported as sensitivity), gives the
     ground track at the scene time. Only the across-track position is meaningful.        -> METHOD B (cross-check)
Method B is used only to check that Method A's side-of-track and pass direction are plausible.
Both are approximations; draw the result as simulated (purple, dashed), never as a measured position.
"""
import datetime as dt
import json
import math
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.join(ROOT, "data", "derived", "story")   # samples the story page is built from (scripts/build_story_data.py)
LAT, LON = 39.48457, 53.72005
ORIGIN = "http://127.0.0.1:4180"
UA = "MethaneClause-story/1.0"
BASE = "https://celestrak.org/NORAD/elements/gp.php"
RE = 6378.137          # WGS84 equatorial radius, km (altitude reference for mean elements)
RM = 6371.0088         # mean Earth radius, km (spherical geometry)
MU = 398600.4418       # km^3/s^2
J2 = 1.08262668e-3

# Carbon Mapper /catalog/scenes/annotated, fetched 2026-10-17 (see carbonmapper_plume_tkm.json).
# `azimuth` is read here as the direction from the scene toward the satellite; Method B supports this
# reading for 06-11 and 08-10 (satellite east of the target in both). Carbon Mapper does not document it.
TANAGER_SCENES = [
    {"name": "tan20260611t083132c23s4001", "t": "2026-06-11T08:31:32Z", "offNadir": 12.500, "azimuth": 90.898,
     "bounds": [53.3642926, 39.1931502, 54.6771963, 39.5488788]},
    {"name": "tan20260620t083638c93s4001", "t": "2026-06-20T08:36:38Z", "offNadir": 20.482, "azimuth": 199.709,
     "bounds": [53.3818688, 39.3254862, 54.7029597, 39.7044897]},
    {"name": "tan20260810t083543c12s4001", "t": "2026-08-10T08:35:43Z", "offNadir": 12.324, "azimuth": 99.137,
     "bounds": [53.5941768, 39.2177537, 53.9585849, 39.6968333]},
]


def fetch(q):
    req = urllib.request.Request(f"{BASE}?{q}", headers={"Origin": ORIGIN, "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        hdrs = {k: v for k, v in r.headers.items() if k.lower().startswith("access-control") or k.lower() == "content-type"}
        return hdrs, r.read().decode()


def jd(t):
    return t.timestamp() / 86400.0 + 2440587.5


def gmst_rad(t):
    return math.radians((280.46061837 + 360.98564736629 * (jd(t) - 2451545.0)) % 360.0)


def sun_ra_rad(t):
    n = jd(t) - 2451545.0
    L = math.radians((280.460 + 0.9856474 * n) % 360)
    g = math.radians((357.528 + 0.9856003 * n) % 360)
    lam = L + math.radians(1.915) * math.sin(g) + math.radians(0.020) * math.sin(2 * g)
    eps = math.radians(23.439 - 0.0000004 * n)
    return math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam)) % (2 * math.pi)


def hav_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * RM * math.asin(math.sqrt(min(1.0, a)))


def bearing(lat1, lon1, lat2, lon2):
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360


def destination(lat, lon, brg_deg, dist_km):
    p1, l1, b, d = math.radians(lat), math.radians(lon), math.radians(brg_deg), dist_km / RM
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


def offnadir_to_ground_km(theta_deg, h_km):
    """Ground (arc) distance target->sub-satellite point for off-nadir theta from altitude h (spherical Earth)."""
    th = math.radians(theta_deg)
    s = (RM + h_km) / RM * math.sin(th)
    return None if s >= 1 else (math.asin(s) - th) * RM


def subpoint(raan, inc, u, t):
    """Sub-satellite lat/lon for a circular orbit at argument of latitude u (rad), time t (geocentric lat)."""
    x = math.cos(raan) * math.cos(u) - math.sin(raan) * math.sin(u) * math.cos(inc)
    y = math.sin(raan) * math.cos(u) + math.cos(raan) * math.sin(u) * math.cos(inc)
    z = math.sin(u) * math.sin(inc)
    lon = (math.degrees(math.atan2(y, x) - gmst_rad(t)) + 180) % 360 - 180
    return math.degrees(math.asin(z)), lon


def describe(omm):
    n = float(omm["MEAN_MOTION"]) * 2 * math.pi / 86400.0
    a = (MU / n ** 2) ** (1 / 3)
    e = float(omm["ECCENTRICITY"])
    return {"norad": omm["NORAD_CAT_ID"], "intl": omm["OBJECT_ID"], "epoch": omm["EPOCH"],
            "inclination_deg": float(omm["INCLINATION"]), "mean_motion_rev_day": float(omm["MEAN_MOTION"]),
            "period_min": round(1440 / float(omm["MEAN_MOTION"]), 2),
            "perigee_km": round(a * (1 - e) - RE, 1), "apogee_km": round(a * (1 + e) - RE, 1),
            "mean_alt_km": round(a - RE, 1), "omm": omm}


def main():
    out = {"source": "CelesTrak GP data (celestrak.org/NORAD/elements/gp.php)", "requests": {}}
    if "--offline" in sys.argv:  # recompute from the elements saved by an earlier online run
        prev = json.load(open(os.path.join(HERE, "orbits_tkm.json")))
        for k in ("requests", "tanager_search", "tle"):
            out[k] = prev[k]
        iss = prev["satellites"]["ISS (ZARYA) - EMIT host"]["omm"]
        tan1 = prev["satellites"]["Tanager-1"]["omm"]
    else:
        h, txt = fetch("NAME=TANAGER&FORMAT=JSON")
        out["requests"]["NAME=TANAGER&FORMAT=JSON"] = h
        tan = json.loads(txt)
        h, txt = fetch("CATNR=25544&FORMAT=JSON")
        out["requests"]["CATNR=25544&FORMAT=JSON"] = h
        iss = json.loads(txt)[0]
        tan1 = next(s for s in tan if s["OBJECT_NAME"].upper() == "TANAGER-1")
        out["tanager_search"] = [{k: s[k] for k in ("OBJECT_NAME", "OBJECT_ID", "NORAD_CAT_ID", "EPOCH")} for s in tan]
        out["tle"] = {}
        for cat in (25544, tan1["NORAD_CAT_ID"]):  # gp.php takes one CATNR per call
            h, txt = fetch(f"CATNR={cat}&FORMAT=TLE")
            out["requests"][f"CATNR={cat}&FORMAT=TLE"] = h
            out["tle"][str(cat)] = txt.strip().splitlines()

    sats = {"ISS (ZARYA) - EMIT host": describe(iss), "Tanager-1": describe(tan1)}
    out["satellites"] = sats

    s = sats["Tanager-1"]
    inc = math.radians(s["inclination_deg"])
    ep = dt.datetime.fromisoformat(tan1["EPOCH"]).replace(tzinfo=dt.timezone.utc)
    n0 = float(tan1["MEAN_MOTION"])                      # rev/day
    ndot = 2 * float(tan1["MEAN_MOTION_DOT"])            # rev/day^2 (TLE field is ndot/2)
    raan0 = math.radians(float(tan1["RA_OF_ASC_NODE"]))
    s["ltan_hours_at_epoch"] = round(((raan0 - sun_ra_rad(ep)) / (2 * math.pi) * 24 + 12) % 24, 2)

    def state(t, decay=True):
        """Mean motion, altitude and integrated RAAN at time t (decay: roll n back linearly with ndot)."""
        days = (t - ep).total_seconds() / 86400.0
        steps = max(1, int(abs(days)))
        raan, dd = raan0, days / steps
        for i in range(steps):
            mid = (i + 0.5) * dd
            n = (n0 + (ndot * mid if decay else 0.0)) * 2 * math.pi / 86400.0
            a = (MU / n ** 2) ** (1 / 3)
            raan += -1.5 * n * J2 * (RE / a) ** 2 * math.cos(inc) * dd * 86400.0
        n = (n0 + (ndot * days if decay else 0.0)) * 2 * math.pi / 86400.0
        a = (MU / n ** 2) ** (1 / 3)
        return a - RE, raan

    est = []
    for sc in TANAGER_SCENES:
        t = dt.datetime.strptime(sc["t"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        # Primary: altitude held (Tanager-1 has propulsion; third-party Aug-2026 elements show ~433-436 km,
        # same as today). Sensitivity: roll the mean motion back with the TLE decay term instead.
        alt, raan = state(t, decay=False)
        alt_decay, raan_decay = state(t, decay=True)
        b = sc["bounds"]
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2

        # METHOD A: Carbon Mapper view geometry -> sub-satellite point (look line ends at the facility)
        g = offnadir_to_ground_km(sc["offNadir"], alt)
        a_lat, a_lon = destination(LAT, LON, sc["azimuth"], g)
        elev = 90 - sc["offNadir"] - math.degrees(g / RM)
        slant = math.sqrt(RM ** 2 + (RM + alt) ** 2 - 2 * RM * (RM + alt) * math.cos(g / RM))

        # METHOD B: plane at scene time; closest ground-track point to the facility (phase solved, not propagated)
        def closest(raan_t):
            best = None
            for k in range(36000):
                u = math.radians(k * 0.01)
                la, lo = subpoint(raan_t, inc, u, t)
                d = hav_km(LAT, LON, la, lo)
                if best is None or d < best[0]:
                    best = (d, la, lo, u)
            return best
        d, la, lo, u = closest(raan)
        la2, lo2 = subpoint(raan, inc, u + math.radians(0.05), t)
        heading = bearing(la, lo, la2, lo2)
        side_fac = "right" if math.sin(math.radians(bearing(la, lo, LAT, LON) - heading)) > 0 else "left"
        d_dc, la_dc, lo_dc, _ = closest(raan_decay)
        # cross-track distance of Method-A point from Method-B track (signed: + = right of flight direction)
        xa = math.asin(math.sin(hav_km(la, lo, a_lat, a_lon) / RM) *
                       math.sin(math.radians(bearing(la, lo, a_lat, a_lon) - heading))) * RM
        est.append({
            "scene": sc["name"], "time_utc": sc["t"],
            "cm_off_nadir_deg": sc["offNadir"], "cm_azimuth_deg": sc["azimuth"],
            "alt_km_assumed": round(alt, 1),
            "A_subsat_point_lonlat": [round(a_lon, 4), round(a_lat, 4)],
            "A_ground_distance_facility_to_subsat_km": round(g, 1),
            "A_slant_range_km": round(slant, 1),
            "A_elevation_of_satellite_seen_from_facility_deg": round(elev, 1),
            "B_track_heading_deg": round(heading, 1),
            "B_pass": "descending" if 90 < heading < 270 else "ascending",
            "B_closest_track_point_lonlat": [round(lo, 3), round(la, 3)],
            "B_cross_track_facility_km": round(d, 1),
            "B_facility_side": side_fac + " of flight direction",
            "B_sensitivity_if_decaying_orbit": {"alt_km": round(alt_decay, 1),
                                                "cross_track_facility_km": round(d_dc, 1),
                                                "closest_track_point_lonlat": [round(lo_dc, 3), round(la_dc, 3)]},
            "A_vs_B_cross_track_km": round(xa, 1),
            "footprint_centre_lonlat": [round(cx, 4), round(cy, 4)],
            "facility_to_footprint_centre_km": round(hav_km(LAT, LON, cy, cx), 1),
        })
    out["tanager_pass_estimates"] = est
    out["altitude_note"] = ("Tanager-1 altitude assumed = today's CelesTrak mean altitude (epoch 2026-10-16), i.e. orbit "
                            "maintained. Sensitivity case rolls mean motion back with the TLE decay term "
                            "(MEAN_MOTION_DOT) and gives ~440-446 km. Published figures differ (pre-launch 405 km; "
                            "~500-522 km after launch; lowered to ~430 km), so treat altitude as +/- ~15 km.")
    out["method"] = __doc__.split("HONESTY NOTE")[1].strip()
    with open(os.path.join(HERE, "orbits_tkm.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    for k, v in sats.items():
        print(k, {x: y for x, y in v.items() if x != "omm"})
    print(json.dumps({"requests": out["requests"], "tanager_search": out["tanager_search"], "tle": out["tle"],
                      "est": est}, indent=1))


if __name__ == "__main__":
    main()
