"""Rebuild data/raw/krakow_grid_nsp2021.json from the GUS NSP 2021 1 km grid.
Downloads GRID_NSP2021_RES.zip (~23.5 MB, no login, INSPIRE ATOM), reads only the .dbf
(cell code carries EPSG:3035 coords, so no geometry parsing needed) and keeps the bbox.
Usage: python3 src/fetch_nsp_grid.py [E_min E_max N_min N_max]   (km, EPSG:3035)
Default bbox = Kraków + some suburbs: E 5018-5052, N 3029-3053.
"""
import io, json, re, struct, sys, zipfile, requests
URL = "https://geo.stat.gov.pl/atom-web/download/?fileId=2795b7ed9098f86ab761352519ba1d85&name=GRID_NSP2021_RES.zip"
e0, e1, n0, n1 = map(int, sys.argv[1:5]) if len(sys.argv) == 5 else (5018, 5052, 3029, 3053)
z = zipfile.ZipFile(io.BytesIO(requests.get(URL, timeout=120).content))
dbf = z.read(next(n for n in z.namelist() if n.endswith(".dbf")))
nrec, hlen, rlen = struct.unpack("<IHH", dbf[4:12])
fields, o = [], 32
while dbf[o] != 0x0D:
    fields.append((dbf[o:o + 11].split(b"\0")[0].decode(), dbf[o + 16])); o += 32
def num(s):
    s = s.strip(); return float(s) if s else None    # empty = suppressed by GUS (small counts)
rows = []
for i in range(nrec):
    rec, pos, r = dbf[hlen + i * rlen + 1: hlen + (i + 1) * rlen], 0, {}
    for name, ln in fields: r[name] = rec[pos:pos + ln].decode("utf-8", "replace"); pos += ln
    N, E = (int(x) // 1000 for x in re.search(r"N(\d+)E(\d+)", r["CODE"]).groups())
    if e0 <= E <= e1 and n0 <= N <= n1 and (num(r["RES"]) or 0) > 0:
        rows.append([E, N] + [num(r[k]) for k in ("RES", "RES_MALE", "RES_FEM", "RES_0_14", "RES_15_64", "RES_65_")])
json.dump({"_source": f"GUS NSP 2021 GRID_NSP2021_RES, EPSG:3035 bbox E{e0}-{e1} N{n0}-{n1} km, RES>0; null = suppressed",
           "columns": ["E_km", "N_km", "res", "male", "female", "age_0_14", "age_15_64", "age_65p"], "rows": rows},
          open("data/raw/krakow_grid_nsp2021.json", "w"))
print(len(rows), "cells,", int(sum(r[2] for r in rows)), "residents")
