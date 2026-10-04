"""Wyniki wyborów w Krakowie na poziomie obwodów głosowania (PKW) + geokodowanie siedzib.

Uruchom z roota repo:  python src/fetch_pkw_krakow.py  [--download-only]

Źródła (oficjalne arkusze PKW, "Dane w arkuszach"):
  - Sejm 2023:            https://sejmsenat2023.pkw.gov.pl/sejmsenat2023/pl/dane_w_arkuszach
  - Prezydent RP 2025:    https://prezydent2025.pkw.gov.pl/prezydent2025/pl/dane_w_arkuszach
  - Samorząd 2024 (WBP):  https://samorzad2024.pkw.gov.pl/samorzad2024/pl/dane_w_arkuszach
Geokodowanie: OSM Nominatim (<=1 req/s, cache w data/raw/pkw_krakow/geocode_cache.json).

Wyniki:
  data/raw/pkw_krakow/*.zip            oryginalne paczki PKW (nie modyfikowane)
  data/raw/pkw_krakow/geocode_cache.json
  data/raw/pkw_krakow.json             obwody + agregaty dzielnicowe
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import sys
import time
import zipfile
from collections import defaultdict
from pathlib import Path

import requests
from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

RAW = Path("data/raw/pkw_krakow")
OUT = Path("data/raw/pkw_krakow.json")
CACHE = RAW / "geocode_cache.json"
DISTRICTS = Path("data/geo/krakow_districts.geojson")
GRID = Path("out/grid_cells.csv")
UA = "krakow-sim/0.1 (synthetic population research project; non-commercial)"
TERYT_KRAKOW = "126101"  # gmina miejska Kraków (TERYT 6-cyfrowy jak w plikach PKW)
THROTTLE_S = 1.2

S23 = "https://sejmsenat2023.pkw.gov.pl/sejmsenat2023/data/csv"
P25 = "https://prezydent2025.pkw.gov.pl/prezydent2025/data/csv"
P25_TS = "1758650309"  # znacznik wersji plików z strony PKW (linki mają postać *_csv.<ts>.zip)
L24 = "https://samorzad2024.pkw.gov.pl/samorzad2024/data/csv"

FILES = {
    "sejm2023_wyniki_obwody.zip": f"{S23}/wyniki_gl_na_listy_po_obwodach_sejm_csv.zip",
    "sejm2023_obwody_glosowania.zip": f"{S23}/obwody_glosowania_csv.zip",
    "sejm2023_wyniki_gminy.zip": f"{S23}/wyniki_gl_na_listy_po_gminach_sejm_csv.zip",
    "prez2025_r1_protokoly_obwody.zip": f"{P25}/protokoly_po_obwodach_csv.{P25_TS}.zip",
    "prez2025_r2_protokoly_obwody.zip": f"{P25}/protokoly_po_obwodach_w_drugiej_turze_csv.{P25_TS}.zip",
    "prez2025_r1_obwody_glosowania.zip": f"{P25}/obwody_glosowania_csv.{P25_TS}.zip",
    "prez2025_r1_wyniki_gminy.zip": f"{P25}/wyniki_gl_na_kandydatow_po_gminach_csv.{P25_TS}.zip",
    "prez2025_r2_wyniki_gminy.zip": f"{P25}/wyniki_gl_na_kandydatow_po_gminach_w_drugiej_turze_csv.{P25_TS}.zip",
    "local2024_wbp_r1_protokoly_obwody.zip": f"{L24}/protokoly_po_obwodach_wbp_csv.zip",
    "local2024_wbp_r2_protokoly_obwody.zip": f"{L24}/protokoly_po_obwodach_wbp2_csv.zip",
    "local2024_obwody_glosowania.zip": f"{L24}/obwody_glosowania_csv.zip",
}

SESSION = requests.Session()
SESSION.headers["User-Agent"] = UA


# ---------------------------------------------------------------- download
def download_all() -> dict[str, str]:
    RAW.mkdir(parents=True, exist_ok=True)
    status = {}
    for name, url in FILES.items():
        p = RAW / name
        if p.exists() and zipfile.is_zipfile(p):
            status[name] = "cached"
            continue
        ok = False
        for attempt in range(4):
            try:
                r = SESSION.get(url, timeout=(15, 180))
                time.sleep(THROTTLE_S)
                if r.status_code == 200 and r.content[:2] == b"PK":
                    p.write_bytes(r.content)
                    ok = True
                    break
                print(f"  {name}: HTTP {r.status_code}, {r.headers.get('content-type')} (próba {attempt+1})")
            except requests.RequestException as e:
                print(f"  {name}: {type(e).__name__} (próba {attempt+1})")
                time.sleep(5 * (attempt + 1))
        status[name] = "downloaded" if ok else "FAILED"
        print(f"{name}: {status[name]}")
    return status


def read_csv_from_zip(name: str, member_hint: str = "") -> list[dict]:
    with zipfile.ZipFile(RAW / name) as z:
        members = [m for m in z.namelist() if m.endswith(".csv")]
        # pliki PKW bywają w dwóch kodowaniach; preferuj utf8
        members.sort(key=lambda m: ("utf8" not in m, member_hint not in m))
        raw = z.read(members[0])
    for enc in ("utf-8-sig", "cp1250"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    return list(csv.DictReader(io.StringIO(text), delimiter=";"))


def num(x) -> int:
    if x is None:
        return 0
    x = str(x).strip().replace(" ", "").replace(",", ".")
    if x in ("", "-", "XXX"):
        return 0
    return int(float(x))


def teryt6(x) -> str:
    """PKW zapisuje TERYT gminy bez wiodącego zera / czasem 7-cyfrowo; normalizuj do 6."""
    s = re.sub(r"\D", "", str(x or ""))
    return s[:6].zfill(6) if len(s) <= 6 else s[:6]


def col(row: dict, *cands: str) -> str | None:
    """Pierwsza kolumna, której nazwa zaczyna się od któregoś z kandydatów (case-insensitive)."""
    low = {k.replace("\xa0", " ").lower().strip(): k for k in row}
    for c in cands:
        for lk, k in low.items():
            if lk == c.lower():
                return k
    for c in cands:
        for lk, k in low.items():
            if lk.startswith(c.lower()):
                return k
    return None


# ---------------------------------------------------------------- elections
# Skróty komitetów Sejm 2023 (pełne nazwy zostają w pliku źródłowym)
SEJM_SHORT = {
    "KOALICYJNY KOMITET WYBORCZY KOALICJA OBYWATELSKA PO .N IPL ZIELONI": "KO",
    "KOMITET WYBORCZY PRAWO I SPRAWIEDLIWOŚĆ": "PiS",
    "KOALICYJNY KOMITET WYBORCZY TRZECIA DROGA POLSKA 2050 SZYMONA HOŁOWNI - POLSKIE STRONNICTWO LUDOWE": "Trzecia Droga",
    "KOMITET WYBORCZY NOWA LEWICA": "Lewica",
    "KOMITET WYBORCZY KONFEDERACJA WOLNOŚĆ I NIEPODLEGŁOŚĆ": "Konfederacja",
    "KOMITET WYBORCZY BEZPARTYJNI SAMORZĄDOWCY": "Bezpartyjni Samorządowcy",
    "KOMITET WYBORCZY POLSKA JEST JEDNA": "Polska Jest Jedna",
    "KOMITET WYBORCZY WYBORCÓW RUCHU DOBROBYTU I POKOJU": "RDiP",
    "KOMITET WYBORCZY NORMALNY KRAJ": "Normalny Kraj",
    "KOMITET WYBORCZY ANTYPARTIA": "Antypartia",
    "KOMITET WYBORCZY RUCH NAPRAWY POLSKI": "Ruch Naprawy Polski",
    "KOMITET WYBORCZY WYBORCÓW MNIEJSZOŚĆ NIEMIECKA": "Mniejszość Niemiecka",
}

ELECTIONS = {
    "sejm2023": dict(results="sejm2023_wyniki_obwody.zip", meta="sejm2023_obwody_glosowania.zip",
                     official="sejm2023_wyniki_gminy.zip", label="Sejm RP 2023-10-15 (listy)"),
    "prezydent2025_r1": dict(results="prez2025_r1_protokoly_obwody.zip", meta="prez2025_r1_obwody_glosowania.zip",
                             official="prez2025_r1_wyniki_gminy.zip", label="Prezydent RP 2025-05-18, I tura"),
    # ASSUMPTION: numeracja/siedziby obwodów w II turze = I tura (meta z I tury; adres i tak bierzemy z protokołu)
    "prezydent2025_r2": dict(results="prez2025_r2_protokoly_obwody.zip", meta="prez2025_r1_obwody_glosowania.zip",
                             official="prez2025_r2_wyniki_gminy.zip", label="Prezydent RP 2025-06-01, II tura"),
    "prezydent_krakowa2024_r1": dict(results="local2024_wbp_r1_protokoly_obwody.zip", member="małopolskie",
                                     meta="local2024_obwody_glosowania.zip", official=None,
                                     label="Prezydent Miasta Krakowa 2024-04-07, I tura"),
    "prezydent_krakowa2024_r2": dict(results="local2024_wbp_r2_protokoly_obwody.zip",
                                     meta="local2024_obwody_glosowania.zip", official=None,
                                     label="Prezydent Miasta Krakowa 2024-04-21, II tura"),
}

SPECIAL_TYPES = {"zakład leczniczy", "dom pomocy społecznej", "areszt śledczy", "zakład karny", "dom studencki"}
SPECIAL_RE = re.compile(r"szpital|areszt|zakład karny|dom pomocy społecznej|\bDPS\b|dom student|akademik|"
                        r"zakład opiekuńczo|hospicjum", re.I)


def short_candidate(c: str, election: str) -> str:
    if election.startswith("sejm"):
        return SEJM_SHORT.get(c, c)
    m = re.match(r"Głosy na kandydata nr \d+ - (.+?) zarejestrowanego przez (.+)", c)
    if m:
        return f"{m.group(1)} ({m.group(2)})"
    return c


def parse_protocols(cfg: dict, election: str) -> tuple[list[dict], list[str]]:
    rows = read_csv_from_zip(cfg["results"], cfg.get("member", ""))
    tc = col(rows[0], "TERYT Gminy")
    kr = [r for r in rows if teryt6(r[tc]) == TERYT_KRAKOW]
    cols = list(rows[0].keys())
    c_valid = col(rows[0], "Liczba głosów ważnych oddanych łącznie")
    cand_cols = cols[cols.index(c_valid) + 1:]
    # pliki samorządowe mają unię kolumn wszystkich gmin: zostaw kandydatów z głosami w Krakowie
    cand_cols = [c for c in cand_cols if any(str(r.get(c) or "").strip() not in ("", "-") for r in kr)]
    c_elig = col(rows[0], "Liczba wyborców uprawnionych do głosowania")
    c_issued = col(rows[0], "Liczba wyborców, którym wydano karty do głosowania w lokalu wyborczym oraz w")
    c_cards_valid = col(rows[0], "Liczba kart ważnych")
    out = []
    for r in kr:
        elig, issued = num(r[c_elig]), num(r[c_issued])
        out.append(dict(
            obwod_no=num(r[col(r, "Nr komisji")]),
            address=(r.get(col(r, "Siedziba")) or "").strip(),
            typ_obwodu=(r.get(col(r, "Typ obwodu") or "", "") or "").strip() or None,
            voters_eligible=elig, ballots_issued=issued,
            ballots_valid=num(r[c_cards_valid]), votes_valid=num(r[c_valid]),
            turnout=round(issued / elig, 4) if elig else None,
            results={short_candidate(c, election): num(r[c]) for c in cand_cols},
        ))
    return out, [short_candidate(c, election) for c in cand_cols]


def official_total(cfg: dict, election: str) -> dict | None:
    if not cfg.get("official"):
        return None
    rows = read_csv_from_zip(cfg["official"])
    tc = col(rows[0], "TERYT Gminy")
    r = next(r for r in rows if teryt6(r[tc]) == TERYT_KRAKOW)
    cols = list(r.keys())
    c_valid = col(r, "Liczba głosów ważnych oddanych łącznie")
    return dict(
        voters_eligible=num(r[col(r, "Liczba wyborców uprawnionych")]),
        ballots_issued=num(r[col(r, "Liczba wyborców, którym wydano karty do głosowania w lokalu wyborczym oraz w")]),
        votes_valid=num(r[c_valid]),
        results={short_candidate(c, election): num(r[c]) for c in cols[cols.index(c_valid) + 1:] if num(r[c])},
    )


def load_meta(name: str) -> dict[int, dict]:
    rows = read_csv_from_zip(name)
    return {num(r["Numer"]): r for r in rows if teryt6(r["TERYT gminy"]) == TERYT_KRAKOW}


# ---------------------------------------------------------------- geocoding
def addr_parts(meta: dict | None, siedziba: str) -> dict:
    if meta:
        street = (meta.get("Ulica") or "").strip()
        no = (meta.get("Numer posesji") or "").strip()
        return dict(name=(meta.get("Siedziba") or "").strip(), street=street, no=no,
                    postcode=(meta.get("Kod pocztowy") or "").strip())
    # fallback: "Nazwa, ul. Ulica 7, 31-057 Kraków"
    parts = [p.strip() for p in siedziba.split(",")]
    pc = next((re.match(r"(\d\d-\d\d\d)", p).group(1) for p in parts if re.match(r"\d\d-\d\d\d", p)), "")
    st = next((p for p in parts[1:] if re.match(r"(ul|al|os|pl|rondo|Rynek)\.?\s", p, re.I)), "")
    m = re.match(r"(.+?)\s+(\d+[A-Za-z]?(?:[/-]\d+[A-Za-z]?)?)$", st)
    return dict(name=parts[0], street=m.group(1) if m else st, no=m.group(2) if m else "", postcode=pc)


def addr_key(a: dict) -> str:
    return f"{a['street']} {a['no']}, {a['postcode']} Kraków | {a['name']}".strip()


class Geocoder:
    URL = "https://nominatim.openstreetmap.org/search"

    def __init__(self, city_poly):
        self.cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
        self.city = city_poly.buffer(0.005)  # ~500 m tolerancji na granicy
        self.last = 0.0
        self.calls = 0
        # Bezpiecznik: po 429/403 nie wysyłamy już nic w tym uruchomieniu (polityka Nominatim);
        # brakujące adresy dociągnie kolejne uruchomienie (cache).  --no-geocode = tylko cache.
        self.blocked = "--no-geocode" in sys.argv

    def _get(self, params: dict):
        if self.blocked:
            return None
        wait = 1.5 - (time.time() - self.last)  # Nominatim: max 1 req/s (z zapasem)
        if wait > 0:
            time.sleep(wait)
        params = {**params, "format": "jsonv2", "limit": 3, "countrycodes": "pl"}
        try:
            r = SESSION.get(self.URL, params=params, timeout=30)
        except requests.RequestException:
            self.last = time.time()
            return None
        self.last = time.time()
        self.calls += 1
        if r.status_code in (403, 429):
            print(f"  nominatim HTTP {r.status_code}: przerywam geokodowanie w tym uruchomieniu")
            self.blocked = True
            return None
        if r.status_code != 200:
            print("  nominatim HTTP", r.status_code)
            return None
        for hit in r.json():
            lat, lon = float(hit["lat"]), float(hit["lon"])
            if self.city.contains(Point(lon, lat)):
                return lat, lon, hit.get("display_name", ""), hit.get("addresstype") or hit.get("type")
        return []

    def geocode(self, a: dict) -> dict:
        key = addr_key(a)
        if key in self.cache:
            return self.cache[key]
        street = re.sub(r"^(ul\.|al\.)\s*", lambda m: "Aleja " if m.group(1) == "al." else "", a["street"]).strip()
        street = re.sub(r"^Aleja Aleja ", "Aleja ", street)
        street = re.sub(r"^(os\.|pl\.)\s*(?=Osiedle|Plac)", "", street)
        a = {**a, "no": re.split(r"\s*[-/,]\s*", a["no"])[0]}  # "60 - 62" -> "60"
        strategies = []
        if street and a["no"]:
            strategies.append(("structured", {"street": f"{a['no']} {street}", "city": "Kraków",
                                              **({"postalcode": a["postcode"]} if a["postcode"] else {})}))
            strategies.append(("street_no", {"q": f"{street} {a['no']}, Kraków"}))
        if a["name"]:
            strategies.append(("name", {"q": f"{a['name']}, Kraków"}))
        if street:
            strategies.append(("street_only", {"street": street, "city": "Kraków"}))
        res = dict(ok=False, method=None)
        for method, params in strategies:
            hit = self._get(params)
            if hit is None:  # błąd sieci / blokada: nie zapisuj do cache, spróbujemy następnym razem
                return dict(ok=False, method="not_attempted" if self.blocked else "error")
            if hit:
                lat, lon, disp, typ = hit
                res = dict(ok=True, lat=round(lat, 6), lon=round(lon, 6), method=method,
                           precision="street" if method == "street_only" else "address_or_poi",
                           display_name=disp, osm_type=typ)
                break
        self.cache[key] = res
        self.save()  # zapis po każdym nowym adresie: przerwanie nie gubi wyników
        return res

    def save(self):
        CACHE.write_text(json.dumps(self.cache, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------- spatial
def load_spatial():
    g = json.loads(DISTRICTS.read_text())
    dists = [(f["properties"]["name"], shape(f["geometry"])) for f in g["features"]
             if f["properties"].get("kind") == "district"]
    city = next((shape(f["geometry"]) for f in g["features"] if f["properties"].get("kind") == "city"), None)
    if city is None:
        from shapely.ops import unary_union
        city = unary_union([p for _, p in dists])
    cells = {}
    if GRID.exists():
        with GRID.open() as fh:
            for r in csv.DictReader(fh):
                e, n = map(int, r["cell"].split("_"))
                cells[(e, n)] = r["cell"]
    return dists, city, cells


TO_3035 = Transformer.from_crs("EPSG:4326", "EPSG:3035", always_xy=True)


def district_of(lat, lon, dists):
    p = Point(lon, lat)
    for name, poly in dists:
        if poly.contains(p):
            return name
    # punkt tuż za granicą dzielnicy (np. na granicznej ulicy): najbliższa dzielnica do 300 m
    best = min(dists, key=lambda d: d[1].distance(p))
    return best[0] if best[1].distance(p) < 0.004 else None


def grid_of(lat, lon, cells):
    x, y = TO_3035.transform(lon, lat)
    e, n = int(x // 1000), int(y // 1000)
    if (e, n) in cells:
        return cells[(e, n)], True
    if not cells:
        return f"{e}_{n}", False
    # komórka bez ludności w NSP (np. park/przemysł): najbliższa zamieszkana po środkach komórek
    best = min(cells, key=lambda k: (k[0] + .5 - x / 1000) ** 2 + (k[1] + .5 - y / 1000) ** 2)
    return cells[best], False


# ---------------------------------------------------------------- main
def aggregate(precs: list[dict], cands: list[str], key) -> dict:
    agg = defaultdict(lambda: dict(n_precincts=0, voters_eligible=0, ballots_issued=0, votes_valid=0,
                                   results=defaultdict(int)))
    for p in precs:
        k = key(p)
        if k is None:
            continue
        a = agg[k]
        a["n_precincts"] += 1
        for f in ("voters_eligible", "ballots_issued", "votes_valid"):
            a[f] += p[f]
        for c, v in p["results"].items():
            a["results"][c] += v
    out = {}
    for k, a in sorted(agg.items()):
        out[k] = dict(n_precincts=a["n_precincts"], voters_eligible=a["voters_eligible"],
                      ballots_issued=a["ballots_issued"], votes_valid=a["votes_valid"],
                      turnout=round(a["ballots_issued"] / a["voters_eligible"], 4) if a["voters_eligible"] else None,
                      shares={c: round(a["results"][c] / a["votes_valid"], 4) if a["votes_valid"] else None
                              for c in cands},
                      votes={c: a["results"][c] for c in cands})
    return out


def main():
    status = download_all()
    if "--download-only" in sys.argv:
        return
    failed = [k for k, v in status.items() if v == "FAILED"]
    dists, city, cells = load_spatial()
    geo = Geocoder(city)
    precincts, checks, aggregates, cand_lists = [], {}, {}, {}
    try:
        for el, cfg in ELECTIONS.items():
            if cfg["results"] in failed:
                print(f"{el}: brak pliku, pomijam")
                continue
            precs, cands = parse_protocols(cfg, el)
            meta = load_meta(cfg["meta"]) if cfg["meta"] not in failed else {}
            print(f"{el}: {len(precs)} obwodów, {len(cands)} komitetów/kandydatów")
            for p in precs:
                m = meta.get(p["obwod_no"])
                # meta z innego głosowania może mieć inną siedzibę: używaj jej tylko, gdy ulica pasuje do protokołu
                if m and (m.get("Ulica") or "").replace("ul. ", "") not in p["address"]:
                    m = None
                a = addr_parts(m, p["address"])
                typ = p.pop("typ_obwodu") or ((m or {}).get("Typ obwodu") or "").strip() or None
                special = bool((typ and typ != "stały") or SPECIAL_RE.search(p["address"]))
                g = geo.geocode(a)
                rec = dict(election=el, obwod_no=p["obwod_no"], address=p["address"],
                           lat=g.get("lat"), lon=g.get("lon"), geocode_ok=bool(g.get("ok")),
                           geocode_method=g.get("method"), special=special, typ_obwodu=typ,
                           district=None, grid_cell=None, grid_cell_exact=None)
                if rec["geocode_ok"]:
                    rec["district"] = district_of(rec["lat"], rec["lon"], dists)
                    rec["grid_cell"], rec["grid_cell_exact"] = grid_of(rec["lat"], rec["lon"], cells)
                if m:
                    rec["opis_granic"] = (m.get("Opis granic") or "").strip() or None
                    rec["mieszkancy"] = num(m.get("Mieszkańcy"))
                rec.update({k: p[k] for k in ("voters_eligible", "ballots_issued", "ballots_valid",
                                              "votes_valid", "turnout", "results")})
                precincts.append(rec)
            mine = [r for r in precincts if r["election"] == el]
            cand_lists[el] = cands
            off = official_total(cfg, el)
            summed = dict(voters_eligible=sum(r["voters_eligible"] for r in mine),
                          ballots_issued=sum(r["ballots_issued"] for r in mine),
                          votes_valid=sum(r["votes_valid"] for r in mine),
                          results={c: sum(r["results"][c] for r in mine) for c in cands})
            internal = sum(1 for r in mine if sum(r["results"].values()) != r["votes_valid"])
            checks[el] = dict(label=cfg["label"], precinct_sum=summed, official_city=off,
                              precincts_where_candidate_sum_ne_valid=internal,
                              match=None if off is None else all(
                                  summed[k] == off[k] for k in ("voters_eligible", "ballots_issued", "votes_valid"))
                              and all(summed["results"].get(c, 0) == v for c, v in off["results"].items()))
            aggregates[el] = dict(
                by_district=aggregate(mine, cands, lambda r: r["district"] if not r["special"] else None),
                city_all_precincts=aggregate(mine, cands, lambda r: "Kraków")["Kraków"],
            )
            n_ok = sum(r["geocode_ok"] for r in mine)
            print(f"  geokodowanie {n_ok}/{len(mine)}; zgodność z sumą oficjalną: {checks[el]['match']}")
    finally:
        geo.save()

    uniq = {}
    for r in precincts:
        uniq[r["address"]] = r["geocode_ok"]
    out = dict(
        _source=("PKW, arkusze 'Dane w arkuszach' (protokoły/wyniki po obwodach + obwody głosowania): "
                 + "; ".join(sorted(set(FILES.values())))
                 + ". Geokodowanie: OpenStreetMap Nominatim (© OpenStreetMap contributors, ODbL)."),
        _note=[
            "Obwody Krakowa: TERYT gminy 126101. Liczby z protokołów PKW, bez zmian.",
            "turnout = karty wydane (lokal + korespondencyjnie) / uprawnieni, tak jak liczy PKW.",
            "Lokalizacja = geokodowana SIEDZIBA komisji (lokal wyborczy), nie środek obszaru obwodu. "
            "Obwód obejmuje kilka-kilkanaście ulic wokół; dokładne granice są w polu opis_granic (tekst).",
            "special=True: obwody odrębne (szpitale, DPS, areszty, domy studenckie) lub siedziba wskazująca na taką "
            "placówkę. Ich wyborcy nie są mieszkańcami okolicy; agregaty dzielnicowe je pomijają (city_all_precincts nie).",
            "Agregaty by_district pomijają też obwody bez geokodu (geocode_ok=False).",
            "grid_cell = komórka 1 km (EPSG:3035, kod E_N w km) zawierająca siedzibę; gdy niezamieszkana w NSP 2021, "
            "najbliższa zamieszkana (grid_cell_exact=False).",
            "Dzielnica = point-in-polygon siedziby; siedziba może leżeć tuż za granicą dzielnicy obsługiwanego obszaru.",
            "Wybory samorządowe 2024: brak osobnego pliku gminnego w paczce PKW, więc suma obwodów nie jest "
            "porównana z niezależną sumą miejską (tylko kontrola wewnętrzna: suma głosów na kandydatów = głosy ważne).",
            "Głosowanie za granicą i na statkach nie jest przypisane do Krakowa (nie wchodzi do sum).",
        ],
        elections={k: v["label"] for k, v in ELECTIONS.items() if k in checks},
        candidates=cand_lists,
        checks_vs_official=checks,
        geocoding=dict(unique_addresses=len(uniq), unique_ok=sum(uniq.values()),
                       precinct_rows=len(precincts), precinct_rows_ok=sum(r["geocode_ok"] for r in precincts),
                       nominatim_calls_this_run=geo.calls),
        district_aggregates=aggregates,
        precincts=precincts,
    )
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"zapisano {OUT} ({len(precincts)} rekordów); unikalne adresy geokod OK: "
          f"{sum(uniq.values())}/{len(uniq)}")


if __name__ == "__main__":
    main()
