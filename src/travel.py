"""Door-to-door minutes from the R5 matrices (src/travel_times.py -> out/travel_times.parquet).
minutes(A, lat, lon): for every resident in A, car minutes if they own a car, else public transport (walk + tram/bus,
median over a 30-min departure window, Friday evening). A destination that is a venue uses its own column; any other
point uses the 1 km cell it falls into. Missing pairs (no matrix, or no transit within 150 min) fall back to the old
ASSUMPTION formula (car 5 + 2.5 min/km, tram/bus 10 + 3.5 min/km).
"""
import json, os
from functools import lru_cache
import numpy as np, pandas as pd

M_FILE = "out/travel_times.parquet"


def _km(lat, lon, la2, lo2):
    r = np.radians; h = np.sin(r(la2 - lat) / 2) ** 2 + np.cos(r(lat)) * np.cos(r(la2)) * np.sin(r(lo2 - lon) / 2) ** 2
    return 2 * 6371 * np.arcsin(np.sqrt(h))


def formula(A, lat, lon):
    d = _km(A.lat.values, A.lon.values, lat, lon); car = A.has_car.values.astype(bool)
    return np.where(car, 5 + d * 2.5, 10 + d * 3.5)


@lru_cache(maxsize=1)
def _matrix():
    if not os.path.exists(M_FILE): return None
    M = pd.read_parquet(M_FILE)
    return {k: g.set_index("from_cell")[["transit_min", "car_min"]] for k, g in M.groupby("to_id")}


@lru_cache(maxsize=1)
def _cells():
    P = pd.read_csv("out/personas.csv", usecols=["cell", "lat", "lon"])
    return P.groupby("cell")[["lat", "lon"]].first()


@lru_cache(maxsize=1)
def _venues():
    return {(round(v["latlon"][0], 4), round(v["latlon"][1], 4)): f"v:{v['name']}" for v in json.load(open("viz/venues.json"))}


def dest_id(lat, lon):
    v = _venues().get((round(lat, 4), round(lon, 4)))
    if v: return v
    C = _cells(); i = int(np.argmin(_km(C.lat.values, C.lon.values, lat, lon))); return C.index[i]


def minutes(A, lat, lon):
    M = _matrix(); base = formula(A, lat, lon)
    if M is None: return base
    d = dest_id(lat, lon); col = M.get(d)
    if col is None: return base
    if d.startswith("v:") and col.transit_min.notna().sum() == 0:   # venue point not reachable on foot in OSM (e.g. inside a fenced stadium)
        C = _cells(); cell = C.index[int(np.argmin(_km(C.lat.values, C.lon.values, lat, lon)))]
        col = col.assign(transit_min=M[cell].transit_min.reindex(col.index)) if cell in M else col
    car = A.has_car.values.astype(bool)
    t = np.where(car, A.cell.map(col.car_min).values, A.cell.map(col.transit_min).values).astype(float)
    return np.where(np.isnan(t), base, t)


def available(): return _matrix() is not None
