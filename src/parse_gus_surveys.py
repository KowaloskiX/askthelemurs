"""Parse downloaded GUS survey tables into data/raw/gus_leisure_rates.json.

Run from repo root after src/fetch_gus_surveys.py:
    python src/parse_gus_surveys.py

Every number in the output is read from a file in data/raw/gus_surveys/ (nothing hard-coded).
Percentages are converted to fractions 0-1. PLN amounts stay in PLN, time in minutes.

GUS cell conventions handled by num():
  '.'      -> None  (unreliable: <20 sample cases, or not available)
  '-'/'–'  -> 0.0   (magnitude zero)
  '12,3v'  -> 12.3 + flagged (culture survey: 20-49 sample cases -> low precision)
  '7,4u'   -> 7.4  + flagged (household budget survey: low precision)
Flagged values are listed per variable under "_low_precision" as "<breakdown>/<key>".
"""
import json
import os
import re
import unicodedata

import openpyxl

RAW = "data/raw/gus_surveys"
OUT = "data/raw/gus_leisure_rates.json"
BASE = "https://stat.gov.pl/download/gfx/portalinformacyjny/pl/defaultaktualnosci"

F_CULT = f"{RAW}/uczestnictwo_ludnosci_w_kulturze_2024_tablice.xlsx"
F_SPORT = f"{RAW}/uczestnictwo_w_sporcie_i_rekreacji_ruchowej_w_2025_r._tablice.xlsx"
F_BGD = f"{RAW}/budzety_gospodarstw_domowych_w_2025_r_tablice/1 - Budżety gospodarstw domowych w 2025 r Tablice 1-74.xlsx"
F_TIME = f"{RAW}/budzet_czasu_ludnosci_2023_aneks_tabelaryczny_korekta.xlsx"


# ---------------------------------------------------------------- helpers
def num(v):
    """Return (value, flagged). value None if not usable."""
    if v is None:
        return None, False
    if isinstance(v, (int, float)):
        return float(v), False
    s = str(v).strip().replace("\xa0", "")
    if s in ("", "."):
        return None, False
    if s in ("-", "–", "—"):
        return 0.0, False
    if s in ("x",):
        return None, False
    flag = False
    if s[-1] in "vu":
        flag, s = True, s[:-1]
    s = s.replace(",", ".")
    try:
        return float(s), flag
    except ValueError:
        return None, False


def pct(v):
    x, f = num(v)
    return (None if x is None else round(x / 100.0, 4)), f


def pl(s):
    """Polish part of a bilingual GUS label (text before 2+ spaces / newline)."""
    if s is None:
        return ""
    s = str(s).replace("\xa0", " ").strip()
    s = re.split(r"\n|\s{2,}", s)[0]
    return s.strip()


def slug(s):
    s = pl(s).lower().replace("ł", "l")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def age_key(s):
    s = pl(s).replace("–", "-").replace(" ", "")
    m = re.match(r"(\d+)-(\d+)", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.match(r"(\d+)lat", s)
    if m and ("więcej" in s or "wiecej" in s or "lubwięcej" in s):
        return f"{m.group(1)}+"
    return s


def residence_key(label):
    s = pl(label).lower().replace("–", "-")
    if s.startswith("miasta"):
        return "miasta_razem"
    if s.startswith("wieś") or s.startswith("wies"):
        return "wies"
    if "500" in s:
        return "500k+"
    if "200-499" in s:
        return "200-499k"
    if "100-199" in s:
        return "100-199k"
    if "100-499" in s:
        return "100-499k"
    if "20-99" in s:
        return "20-99k"
    if "poniżej 20" in s:
        return "<20k"
    return slug(label)


EDU_MAP = [
    ("wyższe", "wyzsze"),
    ("policealne, średnie", "srednie_policealne"),
    ("średnie i policealne", "srednie_policealne"),
    ("zasadnicze", "zasadnicze_zawodowe"),
    ("gimnazjalne, podstawowe", "podstawowe_gimnazjalne_brak"),
    ("gimnazjalne", "gimnazjalne"),
    ("podstawowe", "podstawowe_brak"),
]


def edu_key(label):
    s = pl(label).lower()
    for k, v in EDU_MAP:
        if s.startswith(k):
            return v
    return slug(label)


class Rec:
    """Collects one variable's breakdowns + low-precision flags."""

    def __init__(self):
        self.d = {}
        self.low = []

    def put(self, path, value_flag):
        v, f = value_flag
        node = self.d
        for p in path[:-1]:
            node = node.setdefault(p, {})
        node[path[-1]] = v
        if f:
            self.low.append("/".join(path))

    def out(self, **extra):
        o = dict(extra)
        o.update(self.d)
        if self.low:
            o["_low_precision"] = self.low
        return o


# ---------------------------------------------------------------- culture 2024
def culture_rows(ws):
    """Yield (path, row) with path like ('by_age_sex','M','15-24')."""
    rows = list(ws.iter_rows(values_only=True))
    section, sub = "top", None
    for r in rows:
        lab = r[0]
        if lab is None or not str(lab).strip():
            continue
        p = pl(lab)
        low = p.lower()
        data = [c for c in r[1:-1] if c not in (None, "")]
        if p.startswith("OGÓŁEM"):
            yield ("total",), r
            continue
        if low.startswith("według makroregionów"):
            section, sub = "macroregion", None; continue
        if low.startswith("według klasy miejscowości"):
            section, sub = "residence", None; continue
        if low.startswith("według typu gospodarstwa"):
            section, sub = "household_type", None; continue
        if low.startswith("według wykształcenia"):
            section, sub = "education", None; continue
        if low.startswith("według grup wieku"):
            section, sub = "age", None; continue
        if low.startswith("w miastach"):
            section, sub = "urban_age", None; continue
        if low.startswith("na wsi"):
            section, sub = "rural_age", None; continue
        if not data:
            if low == "mężczyźni":
                sub = "M"
            elif low == "kobiety":
                sub = "K"
            continue
        if section == "top":
            if low == "mężczyźni":
                yield ("by_sex", "M"), r
            elif low == "kobiety":
                yield ("by_sex", "K"), r
            continue
        if section == "macroregion":
            key = "mazowieckie" if "mazowieckie" in low else slug(p)
            yield (("by_macroregion",) if sub is None else ("by_macroregion_sex", sub)) + (key,), r
        elif section == "residence":
            yield (("by_residence",) if sub is None else ("by_residence_sex", sub)) + (residence_key(p),), r
        elif section == "household_type":
            yield (("by_household_type",) if sub is None else ("by_household_type_sex", sub)) + (slug(p),), r
        elif section == "education":
            yield (("by_education",) if sub is None else ("by_education_sex", sub)) + (edu_key(p),), r
        elif section in ("age", "urban_age", "rural_age"):
            base = {"age": "by_age", "urban_age": "urban_by_age", "rural_age": "rural_by_age"}[section]
            yield ((base,) if sub is None else (base + "_sex", sub)) + (age_key(p),), r


def header_text(ws, col, upto=12):
    """Composite header of a column: own header cells plus (forward-filled from the left)
    group headers in rows above the column's first own header cell."""
    def cell(r, c):
        v = ws.cell(r, c + 1).value
        if v in (None, "") or isinstance(v, (int, float)) or "Powrót" in str(v):
            return None
        return re.sub(r"\s+", " ", str(v)).strip()
    own_rows = [r for r in range(1, upto + 1) if cell(r, col)]
    first_own = own_rows[0] if own_rows else upto + 1
    parts = []
    for r in range(4, upto + 1):
        v = cell(r, col)
        if v is None and r < first_own:
            for c in range(col - 1, 0, -1):
                if cell(r, c):
                    v = cell(r, c)
                    break
        if v and not v.startswith("WYSZCZEG") and v != "SPECIFICATION" and (not parts or parts[-1] != v):
            parts.append(v)
    return " > ".join(parts)


# (key, table, column index (0-based, col 0 = label), header substring check, description)
CULTURE_VARS = [
    ("tv_any", "Tabl. 10", 3, "oglądajacy", "watches TV (any frequency)"),
    ("tv_daily", "Tabl. 10", 6, "codziennie", "watches TV every day"),
    ("internet_any", "Tabl. 14", 3, "Korzystający", "uses the Internet"),
    ("internet_daily", "Tabl. 14", 6, "codziennie", "uses the Internet daily"),
    ("cinema_any", "Tabl. 13", 39, "chodzący", "went to the cinema (incl. outdoor/cultural-centre screenings) in the year"),
    ("cinema_1_3_per_year", "Tabl. 13", 40, "1–3 razy", "cinema 1-3 times a year"),
    ("cinema_every_2_3_months", "Tabl. 13", 41, "2–3 miesiące", "cinema once every 2-3 months"),
    ("cinema_monthly", "Tabl. 13", 42, "w miesiącu", "cinema once or several times a month"),
    ("cinema_weekly", "Tabl. 13", 43, "w tygodniu", "cinema weekly or more often"),
    ("paid_streaming_films", "Tabl. 13", 33, "odpłatnego", "uses paid online film access (VOD)"),
    ("books_read_any", "Tabl. 16", 3, "Czytający razem", "read/listened to >=1 book in the year"),
    ("books_print", "Tabl. 16", 4, "drukowanej", "read printed books"),
    ("books_digital", "Tabl. 16", 6, "cyfrowej", "read digital books / audiobooks"),
    ("library_any", "Tabl. 20", 3, "korzystający", "used a library or reading room"),
    ("author_meetings", "Tabl. 21", 3, "uczęszczający", "attended a meeting with a writer/poet"),
    ("music_listening_any", "Tabl. 22", 3, "słuchający", "listens to music"),
    ("music_listening_daily", "Tabl. 22", 6, "codziennie", "listens to music daily"),
    ("opera_operetta", "Tabl. 23", 3, "operowe", "attended opera/operetta"),
    ("ballet_dance_show", "Tabl. 23", 7, "baletowe", "attended ballet/dance/musical/folk dance show"),
    ("concert_classical", "Tabl. 23", 11, "filharmonii", "attended philharmonic / classical concert"),
    ("concert_other", "Tabl. 23", 15, "koncerty (inne", "attended concerts other than classical (pop/rock/jazz etc.)"),
    ("concert_other_1_4_per_year", "Tabl. 23", 16, "1–4", "non-classical concerts 1-4 times a year"),
    ("concert_other_more_than_4", "Tabl. 23", 17, "częściej", "non-classical concerts >4 times a year"),
    ("theatre", "Tabl. 23", 19, "teatru", "attended theatre performance"),
    ("theatre_more_than_4", "Tabl. 23", 21, "częściej", "theatre >4 times a year"),
    ("online_performance", "Tabl. 23", 23, "Internetu", "watched a performance/concert online"),
    ("artist_meetings", "Tabl. 23", 27, "artystą", "attended meeting with an artist"),
    ("stage_cabaret", "Tabl. 24", 3, "estradowe", "attended stage show / cabaret"),
    ("amusement_park", "Tabl. 24", 7, "wesołych", "visited amusement park"),
    ("disco_club_dancing", "Tabl. 24", 9, "dyskotek", "went to disco/club/dancing to dance"),
    ("disco_club_more_than_4", "Tabl. 24", 11, "częściej", "disco/club >4 times a year"),
    ("festival", "Tabl. 24", 13, "festiwalach", "participated in festivals"),
    ("monuments_domestic", "Tabl. 25", 3, "w kraju", "visited historical monuments in Poland"),
    ("monuments_abroad", "Tabl. 25", 7, "za granicą", "visited historical monuments abroad"),
    ("museum_domestic", "Tabl. 26", 3, "muzeów", "visited museums in Poland"),
    ("museum_abroad", "Tabl. 26", 7, "za granicą", "visited museums abroad"),
    ("gallery_domestic", "Tabl. 26", 11, "galerii", "visited art galleries in Poland"),
    ("gallery_abroad", "Tabl. 26", 15, "za granicą", "visited art galleries abroad"),
    ("zoo_botanic_planetarium", "Tabl. 27", 3, "zoologicznych", "visited zoo/botanical garden/planetarium"),
    ("local_culture_centre", "Tabl. 28", 3, "korzystający", "used offer of local cultural centre (dom kultury etc.)"),
    ("hobby_photography", "Tabl. 31", 2, "fotografia", "hobby: amateur photography"),
    ("hobby_film", "Tabl. 31", 3, "film", "hobby: amateur filmmaking"),
    ("hobby_computer_graphics", "Tabl. 31", 4, "grafika", "hobby: computer graphics"),
    ("hobby_writing", "Tabl. 31", 5, "pisanie", "hobby: writing (diary, blog, vlog, articles, poems)"),
    ("hobby_visual_arts", "Tabl. 31", 6, "plastyka", "hobby: painting, sculpture, weaving"),
    ("hobby_music_making", "Tabl. 31", 7, "muzyka", "hobby: playing instrument / singing"),
    ("hobby_social_media_creator", "Tabl. 31", 8, "profilu", "hobby: running artistic/cultural social media profile"),
    ("hobby_dance", "Tabl. 31", 9, "taniec", "hobby: dancing"),
    ("hobby_amateur_theatre_folk", "Tabl. 31", 10, "teatralnym", "hobby: amateur theatre / folk group"),
    ("hobby_historical_reenactment", "Tabl. 31", 11, "rekonstrukcją", "hobby: historical reenactment"),
    ("hobby_collecting", "Tabl. 31", 12, "kolekcjonerstwo", "hobby: collecting"),
    ("hobby_diy_tinkering", "Tabl. 31", 13, "majsterkowanie", "hobby: DIY, model building, mechanics"),
    ("hobby_other", "Tabl. 31", 14, "inna", "hobby: other"),
    ("hobby_none", "Tabl. 31", 15, "brak zainteresowania", "no interest in any hobby form"),
    ("culture_association_member", "Tabl. 32", 3, "uczestniczący", "active in associations promoting culture"),
]


def parse_culture():
    wb = openpyxl.load_workbook(F_CULT, data_only=True)
    out = {}
    for key, tab, col, check, desc in CULTURE_VARS:
        ws = wb[tab]
        h = header_text(ws, col)
        assert check.lower() in h.lower(), (key, tab, col, h)
        rec = Rec()
        for path, row in culture_rows(ws):
            rec.put(path, pct(row[col]))
        out[key] = rec.out(description=desc, table=f"{tab} – {pl(ws.cell(1, 1).value)}", column=h)
    return out


# ---------------------------------------------------------------- sport 2025
def sport_participation_any(wb):
    """Tabl. 11: any participation, regular/often, occasional; by group x sex."""
    ws = wb["tablica_11"]
    rows = list(ws.iter_rows(values_only=True))
    cols = {"any": (1, 3, 6), "regular": (2, 4, 7), "occasional": (None, 5, 8)}
    recs = {k: Rec() for k in cols}
    section = None
    sec_map = {
        "wiek": "by_age", "stan cywilny": "by_marital_status", "poziom najwyższego": "by_education",
        "główne źródło": "by_income_source", "klasa wielkości": "by_residence", "sprawność fizyczna": "by_disability",
        "gospodarstwa domowe": "by_household_type", "charakter wykonywanej": "by_work_type",
        "subiektywna ocena": "by_self_rated_fitness",
    }
    for r in rows[8:]:
        lab = r[0]
        if lab is None:
            continue
        p = pl(lab)
        low = p.lower()
        if isinstance(lab, (int, float)) or p.startswith("OGÓŁEM"):
            year = int(lab) if isinstance(lab, (int, float)) else int(re.search(r"(20\d\d)", str(lab)).group(1))
            for k, (ct, cm, ck) in cols.items():
                if ct is not None:
                    recs[k].put(("total_by_year", str(year)), pct(r[ct]))
                recs[k].put(("by_sex_by_year", str(year), "M"), pct(r[cm]))
                recs[k].put(("by_sex_by_year", str(year), "K"), pct(r[ck]))
            continue
        hit = [v for k, v in sec_map.items() if low.startswith(k)]
        if hit and r[1] is None:
            section = hit[0]
            continue
        if r[1] is None or section is None:
            continue
        if section == "by_age":
            key = age_key(p)
        elif section == "by_residence":
            key = residence_key(p)
        elif section == "by_education":
            key = edu_key(p)
        else:
            key = slug(p)
        for k, (ct, cm, ck) in cols.items():
            if ct is not None:
                recs[k].put((section, key), pct(r[ct]))
            recs[k].put((section + "_sex", "M", key), pct(r[cm]))
            recs[k].put((section + "_sex", "K", key), pct(r[ck]))
    desc = {
        "any": "took part in sport or physical recreation (1.10.2024-30.09.2025), % of persons aged 5+",
        "regular": "regularly/often (at least once a week)",
        "occasional": "occasionally",
    }
    return {k: recs[k].out(description=desc[k], table="tablica_11 – Uczestnictwo w zajęciach sportowych lub rekreacji ruchowej (w odsetkach ogółu danej grupy)")
            for k in recs}


def sport_types_table(wb, sheet, colkey_fn):
    """Generic: rows = activity types in blocks (OGÓŁEM / MĘŻCZYŹNI / KOBIETY / MIASTO / WIEŚ),
    columns = group (age, residence class, education, quintile). Returns {activity: {block: {col: frac}}}."""
    ws = wb[sheet]
    rows = list(ws.iter_rows(values_only=True))
    hdr_row = rows[4]
    top_hdr = rows[3]
    colkeys = {}
    for c in range(1, len(hdr_row)):
        v = hdr_row[c] if hdr_row[c] not in (None, "") else (top_hdr[c] if c == 1 else None)
        if c == 1:
            colkeys[c] = "all"
        elif v not in (None, ""):
            colkeys[c] = colkey_fn(v)
    block = "all"
    blocks = {"MĘŻCZYŹNI": "M", "KOBIETY": "K", "MIASTO": "urban", "WIEŚ": "rural"}
    res = {}
    low = {}
    for r in rows[6:]:
        lab = r[0]
        if lab is None:
            continue
        p = pl(lab)
        first = p.split()[0] if p.split() else ""
        if first in blocks and r[1] is None:
            block = blocks[first]
            continue
        if p.startswith("OGÓŁEM") or r[1] is None:
            continue
        act = slug(p)
        for c, ck in colkeys.items():
            v, f = pct(r[c])
            res.setdefault(act, {}).setdefault(block, {})[ck] = v
            if f:
                low.setdefault(act, []).append(f"{block}/{ck}")
    return res, {pl(r[0]): slug(r[0]) for r in rows[6:] if r[0] and r[1] not in (None, "") and not pl(r[0]).startswith("OGÓŁEM")}


def parse_sport():
    wb = openpyxl.load_workbook(F_SPORT, data_only=True)
    any_ = sport_participation_any(wb)
    by_age, labels = sport_types_table(wb, "tablica_18", age_key)
    by_res, _ = sport_types_table(wb, "tablica_14", residence_key)
    by_edu, _ = sport_types_table(wb, "tablica_20", edu_key)
    by_q, _ = sport_types_table(wb, "tablica_22", lambda v: "Q" + str({"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}[pl(v)]))
    types = {}
    for act in by_age:
        t = {}
        a = by_age[act]
        t["total"] = a["all"]["all"]
        t["by_sex"] = {s: a[s]["all"] for s in ("M", "K") if s in a}
        t["by_age"] = {k: v for k, v in a["all"].items() if k != "all"}
        t["by_age_sex"] = {s: {k: v for k, v in a[s].items() if k != "all"} for s in ("M", "K") if s in a}
        t["urban_by_age"] = {k: v for k, v in a.get("urban", {}).items() if k != "all"}
        t["rural_by_age"] = {k: v for k, v in a.get("rural", {}).items() if k != "all"}
        if act in by_res:
            r = by_res[act]
            t["by_residence"] = {k: v for k, v in r["all"].items() if k != "all"}
            t["by_residence_sex"] = {s: {k: v for k, v in r[s].items() if k != "all"} for s in ("M", "K") if s in r}
        if act in by_edu:
            e = by_edu[act]
            t["by_education"] = {k: v for k, v in e["all"].items() if k != "all"}
            t["by_education_sex"] = {s: {k: v for k, v in e[s].items() if k != "all"} for s in ("M", "K") if s in e}
        if act in by_q:
            t["by_income_quintile"] = {k: v for k, v in by_q[act]["all"].items() if k != "all"}
        types[act] = t
    return {
        "sport_any": any_["any"],
        "sport_regular": any_["regular"],
        "sport_occasional": any_["occasional"],
        "sport_types": types,
        "sport_type_labels_pl": {v: k for k, v in labels.items()},
    }


# ---------------------------------------------------------------- household budgets 2025
def bgd_table(ws, colnames, first_data_row):
    """Rows: Polish label lines (English lines have all-None data and are skipped by
    taking only rows whose label is followed by numeric data)."""
    out = {}
    low = []
    for r in ws.iter_rows(min_row=first_data_row, values_only=True):
        lab = r[0]
        if lab is None or all(c in (None, "") for c in r[1:1 + len(colnames)]):
            continue
        key = slug(lab)
        for i, cn in enumerate(colnames):
            v, f = num(r[1 + i])
            out.setdefault(key, {})[cn] = v
            if f:
                low.append(f"{key}/{cn}")
    return out, low


def parse_budgets():
    wb = openpyxl.load_workbook(F_BGD, data_only=True)
    Q = ["total", "Q1", "Q2", "Q3", "Q4", "Q5"]
    LOC = ["total", "miasta_razem", "<20k", "20-99k", "100-499k", "500k+", "wies_razem",
           "wies_aglomeracyjne", "wies_aglo_duza_gestosc", "wies_aglo_mala_gestosc",
           "wies_pozaaglomeracyjne", "wies_pozaaglo_duza_gestosc", "wies_pozaaglo_mala_gestosc"]
    BIO = ["total", "malzenstwo_bez_dzieci", "malzenstwo_1_dziecko", "malzenstwo_2_dzieci",
           "malzenstwo_3plus_dzieci", "rodzic_samotny_z_dziecmi"]

    # sanity checks on headers
    def hdr(ws, r, c):
        return pl(ws.cell(r, c).value)
    assert hdr(wb["Tablica 30"], 5, 3) == "I" and hdr(wb["Tablica 30"], 5, 7) == "V"
    assert "500" in str(wb["Tablica 34"].cell(6, 7).value)
    assert "dzieci" in str(wb["Tablica 31"].cell(5, 3).value)

    exp_q, l1 = bgd_table(wb["Tablica 30"], Q, 7)
    exp_loc, l2 = bgd_table(wb["Tablica 34"], LOC, 9)
    exp_bio, l3 = bgd_table(wb["Tablica 31"], BIO, 8)
    eq_q, l4 = bgd_table(wb["Tablica 52"], Q, 7)
    eq_loc, l5 = bgd_table(wb["Tablica 56"], LOC, 9)
    info_q, _ = bgd_table(wb["Tablica 2"], Q, 6)

    def share(d, cat):
        return {k: (round(d[cat][k] / d["towary_i_uslugi_konsumpcyjne"][k], 4)
                    if d[cat].get(k) and d["towary_i_uslugi_konsumpcyjne"].get(k) else None) for k in d[cat]}

    def frac(d):
        return {item: {k: (None if v is None else round(v / 100, 4)) for k, v in cols.items()} for item, cols in d.items()}

    pick = ["rekreacja_i_kultura", "restauracje_i_hotele", "towary_i_uslugi_konsumpcyjne", "wydatki",
            "rozchody_netto", "transport", "edukacja", "odziez_i_obuwie", "zywnosc_i_napoje_bezalkoholowe",
            "napoje_alkoholowe_wyroby_tytoniowe_i_narkotyki", "napoje_alkoholowe_wyroby_tytoniowe_i_narkotyki_a"]

    def sub(d):
        return {k: v for k, v in d.items() if k in pick or k.startswith("napoje_alkoholowe")}

    return {
        "description": "Household Budget Survey 2025: average MONTHLY outgoings per person (PLN) and household equipment (% of households)",
        "income_quintile_upper_limits_pln_per_capita_monthly": {k: v for k, v in exp_q.get("gorna_granica_grupy_kwintylowej_a", {}).items() if k != "total"},
        "quintile_note": "Quintiles of monthly per-capita disposable income (Tabl. 30 footnote a). Q5 upper limit 'x' = none. Sport tablica_22 uses GUS quintile groups from the same household budget sample (2025), labelled Q1-Q5 here.",
        "quintile_household_info": info_q,
        "expenditure_per_capita_by_quintile": sub(exp_q),
        "expenditure_per_capita_by_residence": sub(exp_loc),
        "expenditure_per_capita_by_household_type": sub(exp_bio),
        "share_of_consumption_rekreacja_kultura": {
            "by_quintile": share(exp_q, "rekreacja_i_kultura"),
            "by_residence": share(exp_loc, "rekreacja_i_kultura"),
        },
        "share_of_consumption_restauracje_hotele": {
            "by_quintile": share(exp_q, "restauracje_i_hotele"),
            "by_residence": share(exp_loc, "restauracje_i_hotele"),
        },
        "equipment_by_quintile": frac(eq_q),
        "equipment_by_residence": frac(eq_loc),
        "_low_precision": l1 + l2 + l3 + l4 + l5,
        "tables": {
            "expenditure_per_capita_by_quintile": "Tabl. 30",
            "expenditure_per_capita_by_residence": "Tabl. 34",
            "expenditure_per_capita_by_household_type": "Tabl. 31 (małżeństwa incl. cohabiting couples; rodzic samotny = matka lub ojciec z dziećmi na utrzymaniu)",
            "equipment_by_quintile": "Tabl. 52",
            "equipment_by_residence": "Tabl. 56",
            "quintile_household_info": "Tabl. 2",
        },
    }


# ---------------------------------------------------------------- time use 2023
def hmm_to_min(v):
    x, _ = num(v)
    if x is None:
        return None
    h = int(x)
    m = round((x - h) * 100)
    return h * 60 + m


TIME_ROWS = {
    "zycie_towarzyskie_uczestnictw": "social_life_and_entertainment_total",
    "zycie_towarzyskie": "social_life",
    "uczestnictwo_w_rozrywce_i_kult": "entertainment_and_culture",
    "odpoczynek_bierny": "passive_rest",
    "uczestnictwo_w_sporcie_lub_rek": "sport_and_outdoor_total",
    "cwiczenia_fizyczne": "physical_exercise",
    "w_tym_spacery_i_wycieczki_pies": "of_which_walking_hiking",
    "zamilowania_osobiste_korzysta": "hobbies_computing_games_total",
    "zamilowania_artystyczne_hobby": "arts_and_hobbies",
    "korzystanie_z_komputera_smart": "computing_smartphone",
    "gry_i_zabawy": "games",
    "korzystanie_ze_srodkow_masoweg": "mass_media_total",
    "czytanie_ksiazek_sluchanie_au": "reading_books_audiobooks",
    "ogladanie_telewizji_i_filmow": "tv_and_films",
    "sluchanie_muzyki_i_radia": "music_and_radio",
    "praca_zawodowa_glowna_i_dodat": "paid_work_total",
    "prace_domowe_lub_opieka_nad_cz": "household_and_family_care_total",
    "sen": "sleep",
}


def time_table(ws, colnames, ncols):
    rows = list(ws.iter_rows(values_only=True))
    block = None
    out = {}
    for r in rows:
        c1 = r[1] if len(r) > 1 else None
        txt = " ".join(str(c) for c in r[:4] if isinstance(c, str))
        if "PRZECIĘTNY CZAS TRWANIA" in txt:
            block = "mean_minutes_all_persons"
        elif "PRZECIĘTNY CZAS WYKONYWANIA" in txt:
            block = "mean_minutes_participants"
        elif "ODSETEK OSÓB" in txt:
            block = "share_doing"
        lab = r[0]
        if block is None or lab is None or not isinstance(lab, str):
            continue
        key = slug(lab)[:30]
        key = key.rstrip("_")
        name = None
        for k, v in TIME_ROWS.items():
            if key == k or key.startswith(k):
                name = v
                break
        if name is None:
            continue
        for i, cn in enumerate(colnames):
            raw = r[1 + i] if 1 + i < len(r) else None
            if block == "share_doing":
                val = pct(raw)[0]
            else:
                val = hmm_to_min(raw)
            out.setdefault(block, {}).setdefault(name, {})[cn] = val
    return out


def parse_time():
    wb = openpyxl.load_workbook(F_TIME, data_only=True)
    # Tabl.1: blocks are column groups (Ogółem/Kobiety/Mężczyźni x 3 measures)
    ws = wb["Tabl.1"]
    assert "Kobiety" in str(ws.cell(4, 3).value) and "Mężczyźni" in str(ws.cell(4, 4).value)
    by_sex = {}
    for r in ws.iter_rows(min_row=5, values_only=True):
        if not isinstance(r[0], str):
            continue
        key = slug(r[0])[:30].rstrip("_")
        name = next((v for k, v in TIME_ROWS.items() if key == k or key.startswith(k)), None)
        if not name:
            continue
        for bi, block in enumerate(["mean_minutes_all_persons", "mean_minutes_participants", "share_doing"]):
            for si, s in enumerate(["total", "K", "M"]):
                raw = r[1 + bi * 3 + si]
                val = pct(raw)[0] if block == "share_doing" else hmm_to_min(raw)
                by_sex.setdefault(block, {}).setdefault(name, {})[s] = val
    ws2 = wb["Tabl.2"]
    ages = ["total"] + [age_key(ws2.cell(4, c).value) for c in range(3, 12)]
    by_age = time_table(ws2, ages, len(ages))
    LOC8 = ["total", "miasta_razem", "500k+", "100-499k", "20-99k", "<20k", "wies_razem",
            "wies_aglomeracyjne", "wies_pozaaglomeracyjne"]
    assert "500" in str(wb["Tabl.8"].cell(5, 4).value)
    by_loc = time_table(wb["Tabl.8"], LOC8, len(LOC8))
    return {
        "description": "Time Use Survey 2023: average daily minutes (all persons incl. non-doers; participants only) and share doing the activity on a given day",
        "population": "persons aged 15+ (Tabl.1, Tabl.8); persons aged 10+ (Tabl.2)",
        "by_sex": by_sex,
        "by_age": by_age,
        "by_residence": by_loc,
        "tables": {"by_sex": "Tabl.1", "by_age": "Tabl.2", "by_residence": "Tabl.8"},
    }


# ---------------------------------------------------------------- main
def main():
    culture = parse_culture()
    sport = parse_sport()
    budgets = parse_budgets()
    time_use = parse_time()
    out = {
        "_sources": {
            "culture": {
                "publication": "Uczestnictwo ludności w kulturze w 2024 r. (GUS, 18.12.2025)",
                "page": "https://stat.gov.pl/obszary-tematyczne/kultura-turystyka-sport/kultura/uczestnictwo-ludnosci-w-kulturze-w-2024-r-,6,4.html",
                "file": f"{BASE}/5493/6/4/1/uczestnictwo_ludnosci_w_kulturze_2024_tablice.xlsx",
                "population": "persons aged 15+ in households (survey module of Household Budget Survey, 2024)",
                "reference_period": "activity in the past year ('w ciągu roku')",
            },
            "sport": {
                "publication": "Uczestnictwo w sporcie i rekreacji ruchowej w 2025 r. (GUS, full publication with tables)",
                "page": "https://stat.gov.pl/obszary-tematyczne/kultura-turystyka-sport/sport/uczestnictwo-w-sporcie-i-rekreacji-ruchowej-w-2025-r-,4,5.html",
                "file": f"{BASE}/5495/4/5/1/uczestnictwo_w_sporcie_i_rekreacji_ruchowej_w_2025_r._tablice.xlsx",
                "population": "household members aged 5+; school/academic compulsory PE and rehabilitation excluded",
                "reference_period": "1.10.2024-30.09.2025",
                "note": "sport_types values = % of ALL persons in the group (tablica_18/14/20/22), not % of participants",
            },
            "budgets": {
                "publication": "Budżety gospodarstw domowych w 2025 r. (GUS)",
                "page": "https://stat.gov.pl/obszary-tematyczne/warunki-zycia/dochody-wydatki-i-warunki-zycia-ludnosci/budzety-gospodarstw-domowych-w-2025-r-,9,24.html",
                "file": f"{BASE}/5486/9/24/1/budzety_gospodarstw_domowych_w_2025_r_tablice.zip",
            },
            "time_use": {
                "publication": "Budżet czasu ludności w 2023 r. (GUS, 26.06.2025)",
                "page": "https://stat.gov.pl/obszary-tematyczne/warunki-zycia/dochody-wydatki-i-warunki-zycia-ludnosci/budzet-czasu-ludnosci-w-2023-r-,36,1.html",
                "file": f"{BASE}/5486/36/1/1/budzet_czasu_ludnosci_2023_aneks_tabelaryczny_korekta.xlsx",
            },
        },
        "_meta": {
            "units": "rates are fractions 0-1; money in PLN per person per month; time in minutes per day",
            "null": "GUS '.' = fewer than 20 sample cases / not available",
            "low_precision": "values listed in _low_precision had GUS 'v'/'u' marker (20-49 sample cases / high error)",
            "age_groups": {
                "culture": "15-24, 25-34, 35-49, 50-64, 65+",
                "sport": "5-9, 10-14, 15-19, 20-29, 30-39, 40-49, 50-59, 60+",
                "time_use_by_age": "10-14, 15-19, 20-24, 25-34, 35-44, 45-54, 55-59, 60-64, 65+",
            },
            "residence_keys": "miasta_razem, 500k+ (Kraków's class), 200-499k, 100-199k, 20-99k, <20k, wies",
            "education_keys": "wyzsze, srednie_policealne, zasadnicze_zawodowe, podstawowe_gimnazjalne_brak (culture); sport splits gimnazjalne / podstawowe_brak",
            "culture_breakdowns": "total, by_sex, by_age, by_age_sex, urban_by_age(_sex), rural_by_age(_sex), by_residence(_sex), by_education(_sex), by_household_type(_sex), by_macroregion(_sex) [Kraków: 'poludniowy']",
            "generated_by": "src/parse_gus_surveys.py",
        },
        "culture": culture,
        **sport,
        "spending": budgets,
        "time_use": time_use,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"wrote {OUT} ({os.path.getsize(OUT)} B)")


if __name__ == "__main__":
    main()
