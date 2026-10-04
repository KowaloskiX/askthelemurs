"""Bundle personas + grid + districts into a self-contained interactive page: viz/index.html
The in-page model is a JS port of utility() in src/simulate.py - keep them in sync."""
import json, pandas as pd
P = pd.read_csv("out/personas.csv"); G = pd.read_csv("out/grid_cells.csv"); DS = pd.read_csv("out/districts.csv")
S = json.load(open("scenarios/concert.json")); V = json.load(open("viz/venues.json"))
GJ = json.load(open("data/geo/krakow_districts.geojson"))["features"]
cells = G.reset_index()[["cell", "lat", "lon", "res"]]
cidx = {c: i for i, c in enumerate(cells.cell)}
OUTSIDE = "poza Krakowem"
dnames = sorted(n for n in DS.district if n != OUTSIDE) + [OUTSIDE]; didx = {n: i for i, n in enumerate(dnames)}
TAST = ["pop", "rock", "hip-hop", "elektronika", "klasyka/jazz", "disco polo"]; ST = ["uczeń", "student", "pracuje", "bezrobotny", "emeryt", "niepracujący"]
pers = [[cidx[r.cell], round(r.lat, 4), round(r.lon, 4), int(r.age), ST.index(r.status), int(r.net_income), int(bool(r.has_car)),
         TAST.index(r.fav_genre), didx[r.district], int(r.origin == "student_spoza")] for r in P.itertuples()]
cell_d = P.groupby("cell").district.agg(lambda s: s.mode()[0])  # majority district per cell, for tooltips
rings = lambda geom: [[[round(x, 4), round(y, 4)] for x, y in ring] for poly in
                      (geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]) for ring in poly[:1]]
geo = {f["properties"]["name"]: rings(f["geometry"]) for f in GJ}
ds = DS.set_index("district")
districts = [{"name": n, "lat": float(ds.lat[n]), "lon": float(ds.lon[n]), "people": int(ds.people[n]), "rings": geo.get(n, [])} for n in dnames]
data = {"cells": [[r.lat, r.lon, int(r.res), didx.get(cell_d.get(r.cell, OUTSIDE), didx[OUTSIDE])] for r in cells.round(5).itertuples()],
        "persons": pers, "tastes": TAST, "statuses": ST, "pop": int(ds.people.sum()), "scenario": S, "venues": V,
        "districts": districts, "city": geo.get("Kraków", [])}
html = open("viz/template.html").read().replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
open("viz/index.html", "w").write(html)
print(f"viz/index.html: {len(P)} personas, {len(cells)} cells, {len(dnames) - 1} districts, {len(html)//1024} KB")
