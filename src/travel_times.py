"""Real door-to-door travel times instead of 'distance x constant': R5 (via r5py) on Kraków's public-transport timetable
(GTFS from ZTP Kraków: trams + buses) and the OpenStreetMap street network (Geofabrik extract, see src/fetch_osm_poi.py).
Origins: every populated 1 km census cell of the personas (city + suburban ring). Destinations: the same cells + the event
venues (viz/venues.json). Modes: public transport (walk + tram/bus, median over departures in the time window) and car
(free-flow on OSM roads, no congestion: ASSUMPTION, optimistic at rush hour; + 5 min parking/walking).
Output: out/travel_times.parquet  [from_cell, to_id, transit_min, car_min]   (to_id = cell id or 'v:<venue name>')
Needs Java 21 (r5py): JAVA_HOME is set below to Homebrew's openjdk@21 if present. Usage: python3 src/travel_times.py [--date 2026-10-09 --time 18:00]
"""
import datetime, json, os, sys, time
from pathlib import Path
import requests

J21 = "/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home"
if os.path.exists(J21): os.environ["JAVA_HOME"] = J21; os.environ["PATH"] = J21 + "/bin:" + os.environ["PATH"]
sys.argv += ["--max-memory", "10G"] if "--max-memory" not in sys.argv else []

SRC_PBF = "data/raw/osm/malopolskie-latest.osm.pbf"
PBF = "data/raw/osm/krakow-area.osm.pbf"   # Kraków + suburban ring: R5 on the whole voivodeship thrashed the Java heap
BBOX = "19.70,49.88,20.33,50.21"
GTFS = {"trams": "https://gtfs.ztp.krakow.pl/GTFS_KRK_T.zip", "buses": "https://gtfs.ztp.krakow.pl/GTFS_KRK_A.zip"}
OUT = "out/travel_times.parquet"
PARK_MIN = 5   # ASSUMPTION: parking + walking at both ends for car trips


def download():
    os.makedirs("data/raw/gtfs", exist_ok=True); paths = []
    for k, u in GTFS.items():
        f = f"data/raw/gtfs/{os.path.basename(u)}"
        if not os.path.exists(f):
            r = requests.get(u, headers={"User-Agent": "krakow-sim/0.1 (hackathon)"}, timeout=300); r.raise_for_status(); Path(f).write_bytes(r.content)
        paths.append(f)
    if not os.path.exists(PBF):
        if not os.path.exists(SRC_PBF): raise SystemExit("brak pliku OSM: uruchom najpierw python3 src/fetch_osm_poi.py")
        import subprocess   # needs osmium-tool (brew install osmium-tool)
        subprocess.run(["osmium", "extract", "-b", BBOX, "--set-bounds", "--overwrite", SRC_PBF, "-o", PBF], check=True)
    return paths


def main():
    a = sys.argv; opt = lambda k, d: a[a.index(k) + 1] if k in a else d
    dep = datetime.datetime.fromisoformat(f"{opt('--date', '2026-10-09')}T{opt('--time', '18:00')}")   # a Friday evening inside the GTFS validity
    gtfs = download()
    import pandas as pd, geopandas as gpd, r5py
    from shapely.geometry import Point
    P = pd.read_csv("out/personas.csv")
    cells = P.groupby("cell").agg(lat=("lat", "first"), lon=("lon", "first")).reset_index()
    venues = pd.DataFrame([{"cell": f"v:{v['name']}", "lat": v["latlon"][0], "lon": v["latlon"][1]} for v in json.load(open("viz/venues.json"))])
    pts = lambda D: gpd.GeoDataFrame({"id": D.cell.values}, geometry=[Point(lo, la) for la, lo in zip(D.lat, D.lon)], crs="EPSG:4326")
    O, Dst = pts(cells), pts(pd.concat([cells, venues], ignore_index=True))
    t0 = time.time(); net = r5py.TransportNetwork(PBF, gtfs); print(f"sieć: {time.time() - t0:.0f} s")
    common = dict(origins=O, destinations=Dst, departure=dep, max_time=datetime.timedelta(minutes=150))
    t0 = time.time()
    tr = r5py.TravelTimeMatrix(net, transport_modes=[r5py.TransportMode.TRANSIT, r5py.TransportMode.WALK],
                               departure_time_window=datetime.timedelta(minutes=30), **common)
    print(f"komunikacja: {time.time() - t0:.0f} s"); t0 = time.time()
    car = r5py.TravelTimeMatrix(net, transport_modes=[r5py.TransportMode.CAR], **common)
    print(f"auto: {time.time() - t0:.0f} s")
    M = tr.rename(columns={"travel_time": "transit_min"}).merge(car.rename(columns={"travel_time": "car_min"}), on=["from_id", "to_id"], how="outer")
    M["car_min"] = M.car_min + PARK_MIN
    M = M.rename(columns={"from_id": "from_cell"}); M.to_parquet(OUT, index=False)
    print(f"zapisano {OUT}: {len(M):,} par, brak dojazdu komunikacją w 150 min: {M.transit_min.isna().mean():.1%}")
    print(M.describe().round(1).to_string())


if __name__ == "__main__":
    main()
