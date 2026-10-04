"""Build data/raw/krakow_grid_extra.json from the Eurostat Census 2021 1 km grid (GISCO, V3).
Poland's cells in it are supplied by GUS (NSP 2021, Reg. (EU) 2018/1799). Compared with
GRID_NSP2021_RES (fetch_nsp_grid.py) it adds: employed persons, place of birth, place of
usual residence one year before the census.

No full download: the ~540 MB zip is read with HTTP Range requests (zipfile over a seekable
remote file), and only ESTAT_Census_2021_V3.csv (~49 MB compressed) is streamed and filtered.
Nothing is written to disk except the output JSON.
Usage: python3 src/fetch_grid_extra.py [E_min E_max N_min N_max]   (km, EPSG:3035)
"""
import csv, io, json, re, sys, zipfile, requests

URL = "https://gisco-services.ec.europa.eu/census/2021/Eurostat_Census-GRID_2021_V3.zip"
MEMBER = "Eurostat_Census-GRID_2021_V3/ESTAT_Census_2021_V3.csv"
OUT = "data/raw/krakow_grid_extra.json"
e0, e1, n0, n1 = map(int, sys.argv[1:5]) if len(sys.argv) == 5 else (5018, 5052, 3029, 3053)

# source column -> output column (definitions quoted from the V3 read.me)
FIELDS = {
    "T": ("pop", "Total population"),
    "M": ("male", "Male Population"),
    "F": ("female", "Female Population"),
    "Y_LT15": ("age_0_14", "Age Under 15 years"),
    "Y_1564": ("age_15_64", "Age 15 to 64 years"),
    "Y_GE65": ("age_65p", "Age 65 years and over"),
    "EMP": ("employed", "Employed persons"),
    "NAT": ("born_pl", "Place of birth in reporting country"),
    "EU_OTH": ("born_eu_other", "Place of birth in other EU Member State"),
    "OTH": ("born_elsewhere", "Place of birth elsewhere"),
    "SAME": ("res1y_same", "Place of usual residence one year prior to the census unchanged"),
    "CHG_IN": ("res1y_moved_within_pl", "Place of usual residence one year prior to the census: move within reporting country"),
    "CHG_OUT": ("res1y_moved_from_abroad", "Place of usual residence one year prior to the census: move from outside of the reporting country"),
    "LAND_SURFACE": ("land_km2", "Land surface area of the cell, in km2. The value is within [0,1]."),
    "POPULATED": ("populated", "1 for populated cells, 0 for unpopulated cells."),
}


class RangeFile(io.RawIOBase):
    """Read-only seekable view of a remote file via HTTP Range."""
    def __init__(self, url):
        self.url, self.pos, self.s = url, 0, requests.Session()
        self.size = int(self.s.head(url, timeout=60).headers["content-length"])
    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.pos
    def seek(self, off, whence=0):
        self.pos = {0: off, 1: self.pos + off, 2: self.size + off}[whence]; return self.pos
    def readinto(self, b):
        if self.pos >= self.size or not len(b): return 0
        end = min(self.pos + len(b), self.size) - 1
        data = self.s.get(self.url, headers={"Range": f"bytes={self.pos}-{end}"}, timeout=120).content
        b[:len(data)] = data; self.pos += len(data); return len(data)


def num(s):
    v = float(s)
    return None if v in (-8888, -9999) else (int(v) if v.is_integer() else v)  # -8888 confidential, -9999 n/a


z = zipfile.ZipFile(io.BufferedReader(RangeFile(URL), 8 << 20))
rows, seen, flagged = [], 0, {k: 0 for k in FIELDS}
with z.open(MEMBER) as f:
    for r in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")):
        m = re.search(r"N(\d+)E(\d+)", r["GRD_ID"])
        if not m: continue                                    # e.g. "IT_unallocated"
        N, E = (int(x) // 1000 for x in m.groups())
        if not (e0 <= E <= e1 and n0 <= N <= n1): continue
        seen += 1
        if float(r["T"]) == 0 and r["POPULATED"] != "1": continue   # unpopulated
        for k in FIELDS:
            flagged[k] += float(r[k]) in (-8888, -9999)
        rows.append([E, N] + [num(r[k]) for k in FIELDS])
rows.sort(key=lambda r: -(r[2] or 0))

json.dump({
    "_source": f"Eurostat Census 2021 grid V3 ({URL}, member {MEMBER}); PL data supplied by GUS (NSP 2021, "
               f"ref. date 2021-03-31, residents). EPSG:3035 bbox E{e0}-{e1} N{n0}-{n1} km, populated cells only; "
               f"null = -8888 (confidential) or -9999 (not available). Copyright European Union.",
    "_fields": {v[0]: f"{k}: {v[1]}" for k, v in FIELDS.items()},
    "columns": ["E_km", "N_km"] + [v[0] for v in FIELDS.values()],
    "rows": rows,
}, open(OUT, "w"), ensure_ascii=False)

# ---- consistency check vs GRID_NSP2021_RES cut --------------------------------------
cols = ["E_km", "N_km"] + [v[0] for v in FIELDS.values()]
tot = {c: sum(r[i] or 0 for r in rows) for i, c in enumerate(cols) if i >= 2}
print(f"{seen} cells in bbox, {len(rows)} populated -> {OUT}")
for c, v in tot.items():
    print(f"  {c:26s} {v:>12,.0f}   null cells: {flagged[[k for k in FIELDS if FIELDS[k][0] == c][0]]}")
try:
    old = json.load(open("data/raw/krakow_grid_nsp2021.json"))
    oldmap = {(r[0], r[1]): r[2] for r in old["rows"]}
    newmap = {(r[0], r[1]): r[2] for r in rows}
    diff = sum(1 for k in oldmap.keys() | newmap.keys() if (oldmap.get(k) or 0) != (newmap.get(k) or 0))
    print(f"krakow_grid_nsp2021.json res total: {sum(oldmap.values()):,.0f} in {len(oldmap)} cells; "
          f"here pop: {tot['pop']:,.0f} in {len(rows)} cells; cells with different totals: {diff}")
except FileNotFoundError:
    pass
