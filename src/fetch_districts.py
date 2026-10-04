"""Kraków's 18 districts (dzielnice, OSM admin_level=9) + city boundary -> data/geo/krakow_districts.geojson
Source: OpenStreetMap via Overpass API (ODbL). One request; run only when refreshing data.
"""
import json, requests
from shapely.geometry import LineString, mapping
from shapely.ops import polygonize, unary_union

Q = """[out:json][timeout:90];
(rel(50.0,19.78,50.13,20.22)["boundary"="administrative"]["admin_level"="9"];
 rel(2768922););
out geom;"""
r = requests.post("https://overpass-api.de/api/interpreter", data={"data": Q},
                  headers={"User-Agent": "krakow-sim/0.1 (hackathon)"}, timeout=180)
r.raise_for_status()
feats = []
for e in r.json()["elements"]:
    lines = [LineString([(p["lon"], p["lat"]) for p in m["geometry"]]) for m in e["members"]
             if m["type"] == "way" and m.get("role") in ("outer", "") and len(m.get("geometry", [])) > 1]
    poly = unary_union(list(polygonize(unary_union(lines)))).simplify(0.0003, preserve_topology=True)  # ~25 m
    lvl = e["tags"]["admin_level"]
    feats.append({"type": "Feature", "properties": {"name": e["tags"]["name"], "kind": "city" if lvl == "8" else "district",
                  "ref": e["tags"].get("ref")}, "geometry": mapping(poly)})
    print(f'{e["tags"]["name"]:<32} {poly.geom_type:<12} area≈{poly.area * 111.3 * 71.5:.1f} km²')
feats.sort(key=lambda f: (f["properties"]["kind"] != "city", f["properties"]["name"]))
json.dump({"type": "FeatureCollection", "_source": "OpenStreetMap contributors (ODbL), Overpass API", "features": feats},
          open("data/geo/krakow_districts.geojson", "w"), ensure_ascii=False)
print(f"{len(feats)} features -> data/geo/krakow_districts.geojson")
