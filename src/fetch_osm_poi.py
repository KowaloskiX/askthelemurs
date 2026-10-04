"""Supply side from OpenStreetMap: cafés, gyms, schools, stops, parks... inside Kraków.
Source: the Geofabrik extract for Małopolska (one ~200 MB .osm.pbf, updated daily), processed locally with pyosmium:
complete and reproducible, no public API limits. (The first version queried the Overpass API category by category;
the public instances timed out or returned empty answers, e.g. 0 cafés.)
Output: data/raw/osm_poi_krakow.json  {_source, _note, categories: {cat: description}, poi: [[cat, name, lat, lon], ...]}
Points = OSM nodes; buildings / parks drawn as areas = their centroid. OSM completeness varies by category (ASSUMPTION:
good for stops, schools, cafés; weaker for small services).
Usage: python3 src/fetch_osm_poi.py   (downloads data/raw/osm/malopolskie-latest.osm.pbf once, md5-checked; not committed)
"""
import hashlib, json, os, time
from datetime import date, datetime, timezone
import requests

PBF = "data/raw/osm/malopolskie-latest.osm.pbf"
URL = "https://download.geofabrik.de/europe/poland/malopolskie-latest.osm.pbf"
CATS = {   # category -> (OSM selectors, Polish description)
    "cafe": (['amenity=cafe'], "kawiarnie"), "restaurant": (['amenity=restaurant'], "restauracje"), "fast_food": (['amenity=fast_food'], "fast food"),
    "bar": (['amenity=bar', 'amenity=pub'], "bary i puby"), "nightclub": (['amenity=nightclub'], "kluby nocne"),
    "gym": (['leisure=fitness_centre'], "siłownie / fitness"), "swimming": (['leisure=sports_centre][sport=swimming', 'leisure=water_park'], "baseny"),
    "sports_centre": (['leisure=sports_centre'], "centra sportowe"), "playground": (['leisure=playground'], "place zabaw"), "park": (['leisure=park'], "parki"),
    "kindergarten": (['amenity=kindergarten'], "przedszkola"), "school": (['amenity=school'], "szkoły"), "university": (['amenity=university'], "uczelnie"),
    "library": (['amenity=library'], "biblioteki"), "cinema": (['amenity=cinema'], "kina"), "theatre": (['amenity=theatre'], "teatry"),
    "arts_centre": (['amenity=arts_centre'], "domy kultury / centra sztuki"), "museum": (['tourism=museum'], "muzea"),
    "supermarket": (['shop=supermarket'], "supermarkety"), "convenience": (['shop=convenience'], "sklepy osiedlowe"), "bakery": (['shop=bakery'], "piekarnie"),
    "pharmacy": (['amenity=pharmacy'], "apteki"), "doctors": (['amenity=doctors', 'amenity=clinic'], "przychodnie / lekarze"), "dentist": (['amenity=dentist'], "dentyści"),
    "hairdresser": (['shop=hairdresser', 'shop=beauty'], "fryzjer / kosmetyczka"), "coworking": (['amenity=coworking_space', 'office=coworking'], "coworkingi"),
    "tram_stop": (['railway=tram_stop'], "przystanki tramwajowe"), "bus_stop": (['highway=bus_stop'], "przystanki autobusowe"),
    "bike_rental": (['amenity=bicycle_rental'], "stacje roweru miejskiego"), "parking": (['amenity=parking][access!=private'], "parkingi publiczne"),
}


def classify(tags):
    out = []
    for cat, (sels, _) in CATS.items():
        for s in sels:
            conds = [c.split("=") if "=" in c else None for c in s.replace("][", "|").split("|")]
            ok = True
            for kv in conds:
                k, v = kv
                if k.endswith("!"): ok &= tags.get(k[:-1]) != v
                else: ok &= tags.get(k) == v
            if ok: out.append(cat); break
    return out



def download():
    if os.path.exists(PBF): return
    os.makedirs(os.path.dirname(PBF), exist_ok=True); ua = {"User-Agent": "krakow-sim/0.1 (hackathon)"}
    with requests.get(URL, headers=ua, stream=True, timeout=600) as r, open(PBF + ".part", "wb") as f:
        r.raise_for_status(); [f.write(c) for c in r.iter_content(1 << 20)]
    md5 = requests.get(URL + ".md5", headers=ua, timeout=60).text.split()[0]
    if hashlib.md5(open(PBF + ".part", "rb").read()).hexdigest() != md5: raise SystemExit("md5 mismatch, download again")
    os.replace(PBF + ".part", PBF)


def main():
    import osmium
    from shapely.geometry import Point, shape
    from shapely.prepared import prep
    download()
    city = prep(shape(next(f for f in json.load(open("data/geo/krakow_districts.geojson"))["features"] if f["properties"]["kind"] == "city")["geometry"]))
    keys = {s.split("=")[0].rstrip("!") for sels, _ in CATS.values() for sel in sels for s in sel.split("][")}
    poi = []

    def keep(tags, lat, lon):
        if not any(k in tags for k in keys): return
        cats = classify(tags)
        if cats and city.contains(Point(lon, lat)):
            for c in cats: poi.append([c, tags.get("name"), round(lat, 5), round(lon, 5)])

    class H(osmium.SimpleHandler):
        def node(self, n):
            if n.tags: keep({t.k: t.v for t in n.tags}, n.location.lat, n.location.lon)
        def area(self, a):   # closed ways and multipolygons (parks, schools, buildings): centroid of the outer ring
            tags = {t.k: t.v for t in a.tags}
            if not any(k in tags for k in keys): return
            try:
                ring = next(iter(a.outer_rings())); xs = [nd.lon for nd in ring]; ys = [nd.lat for nd in ring]
                keep(tags, sum(ys) / len(ys), sum(xs) / len(xs))
            except (StopIteration, osmium.InvalidLocationError): pass

    t0 = time.time(); H().apply_file(PBF, locations=True)
    stamp = datetime.fromtimestamp(os.path.getmtime(PBF), timezone.utc).date()
    json.dump({"_source": f"OpenStreetMap, Geofabrik extract malopolskie-latest.osm.pbf (downloaded {stamp}), © OpenStreetMap contributors (ODbL)",
               "_note": "points inside the Kraków city boundary (admin_level 8); areas as the centroid of their outer ring. Completeness varies by category.",
               "categories": {k: d for k, (_, d) in CATS.items()}, "poi": poi}, open("data/raw/osm_poi_krakow.json", "w"), ensure_ascii=False)
    from collections import Counter
    c = Counter(p[0] for p in poi); print(f"{len(poi)} obiektów w {time.time() - t0:.0f} s")
    for k, (_, d) in CATS.items(): print(f"  {k:14s} {c.get(k, 0):6d}  {d}")


if __name__ == "__main__":
    main()
