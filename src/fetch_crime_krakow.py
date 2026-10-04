"""Przestępczość w Krakowie poniżej poziomu miasta: komisariaty KMP Kraków (I–VIII) + ankieta poczucia
bezpieczeństwa wg dzielnic.

Uruchom z roota repo:  python src/fetch_crime_krakow.py  [--offline]
  --offline  nie pobieraj, parsuj tylko pliki już zapisane w data/raw/crime_krakow/

Co istnieje (stan: 2026-10):
  * Przestępstwa stwierdzone wg komisariatu (8 obszarów) 2004–2020: tabela „Przestępstwa stwierdzone na
    terenie poszczególnych Komisariatów Policji” w rozdziale „Bezpieczeństwo publiczne” Raportu o stanie
    Miasta (UMK, dane KWP/KMP). Raport o stanie Miasta skończył się na 2020; Raporty o stanie Gminy
    2021–2025 podają tylko sumę dla miasta (+ mapę bez liczb). Nowszych liczb wg komisariatu nie ma
    w publicznych dokumentach.
  * Obszary komisariatów = sumy całych dzielnic (strona KMP „Jednostki”), więc mapowanie
    komisariat→dzielnice jest dokładne. Podział liczby z komisariatu wielodzielnicowego na dzielnice
    już NIE jest (patrz _note).
  * Ankieta UMK „Badanie poczucia bezpieczeństwa mieszkańców Krakowa” (~100 wywiadów na dzielnicę):
    odsetki wg dzielnicy zamieszkania (to opinie i deklaracje, nie statystyka policyjna).

Pliki wynikowe:
  data/raw/crime_krakow/*.pdf|*.html   oryginały (niemodyfikowane)
  data/raw/crime_krakow.json           tabele
Wymaga `pdftotext` (poppler) albo działa na pypdf (gorsze rozpoznanie układu kolumn).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import requests

RAW = Path("data/raw/crime_krakow")
OUT = Path("data/raw/crime_krakow.json")
UA = "krakow-sim/0.1 (hackathon)"
THROTTLE_S = 1.3
BIP = "https://www.bip.krakow.pl/zalaczniki/dokumenty"

# Rozdział „Bezpieczeństwo publiczne” Raportu o stanie Miasta (lata 2018–2020: cały raport).
# Linki z https://krakow.pl/biznes/1164,artykul,raporty_o_stanie_miasta.html -> strony BIP poszczególnych lat.
RSM = {
    2004: "https://bip.krakow.pl/zalaczniki/dokumenty/2248",
    2005: f"{BIP}/n/53783", 2006: f"{BIP}/n/56549", 2007: f"{BIP}/n/59699",
    2008: f"{BIP}/n/65699", 2009: f"{BIP}/n/72781", 2010: f"{BIP}/n/81431",
    2011: f"{BIP}/n/94044", 2012: f"{BIP}/n/108709", 2013: f"{BIP}/n/125142",
    2014: f"{BIP}/n/145220", 2015: f"{BIP}/n/166307", 2016: f"{BIP}/n/193069",
    2017: f"{BIP}/n/239915", 2018: f"{BIP}/n/253681", 2019: f"{BIP}/n/287199",
    2020: f"{BIP}/n/318038",
}
# Raport o stanie Gminy (tylko suma dla miasta). Lista: https://www.bip.krakow.pl/?dok_id=111327
RSG = {2021: f"{BIP}/n/337263", 2024: f"{BIP}/n/581632", 2025: f"{BIP}/n/684238"}
# Ankieta poczucia bezpieczeństwa. Lista: https://www.bip.krakow.pl/?dok_id=104391
SURVEYS = {
    "2025-11": f"{BIP}/n/641776",                  # 2. fala 2025, BBS Obserwator, N=1801
    "2022-10": "https://krakow.pl/zalacznik/436819",  # październik 2022, CBM Indicator, N=1800
}
UNITS_URL = "https://krakow.policja.gov.pl/kr1/jednostki/6595,Jednostki.html"
RADIO_URL = "https://www.radiokrakow.pl/aktualnosci/krakow/najwiecej-kradziezy-rozbojow-bojek-w-centrum-krakowa"

ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII"]
DISTRICTS = [
    "Stare Miasto", "Grzegórzki", "Prądnik Czerwony", "Prądnik Biały", "Krowodrza", "Bronowice",
    "Zwierzyniec", "Dębniki", "Łagiewniki-Borek Fałęcki", "Swoszowice", "Podgórze Duchackie",
    "Bieżanów-Prokocim", "Podgórze", "Czyżyny", "Mistrzejowice", "Bieńczyce", "Wzgórza Krzesławickie",
    "Nowa Huta",
]
# Fallback, gdy strona KMP niedostępna; przy pobraniu jest weryfikowany ze stroną.
KP_DISTRICTS_FALLBACK = {
    "I": ["Stare Miasto"], "II": ["Grzegórzki"], "III": ["Prądnik Czerwony", "Prądnik Biały"],
    "IV": ["Krowodrza", "Bronowice", "Zwierzyniec"],
    "V": ["Dębniki", "Łagiewniki-Borek Fałęcki", "Podgórze"],
    "VI": ["Swoszowice", "Podgórze Duchackie", "Bieżanów-Prokocim"],
    "VII": ["Mistrzejowice", "Bieńczyce", "Wzgórza Krzesławickie"],
    "VIII": ["Czyżyny", "Nowa Huta"],
}

_last = [0.0]


def fetch(url: str, dest: Path, offline: bool) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    if offline:
        raise FileNotFoundError(dest)
    wait = THROTTLE_S - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    r = requests.get(url, headers={"User-Agent": UA}, timeout=120)
    _last[0] = time.time()
    if r.status_code in (403, 429):
        sys.exit(f"STOP: HTTP {r.status_code} for {url} (nie obchodzimy limitów)")
    r.raise_for_status()
    dest.write_bytes(r.content)
    print(f"  pobrano {dest.name} ({len(r.content)/1e6:.2f} MB)")
    return dest


def pdf_text(path: Path) -> str:
    cache = path.with_suffix(".txt")
    if cache.exists() and cache.stat().st_mtime >= path.stat().st_mtime:
        return cache.read_text(encoding="utf-8")
    if shutil.which("pdftotext"):
        subprocess.run(["pdftotext", "-layout", str(path), str(cache)], check=True,
                       stderr=subprocess.DEVNULL)
        return cache.read_text(encoding="utf-8")
    from pypdf import PdfReader  # fallback
    txt = "\n".join(p.extract_text(extraction_mode="layout") or "" for p in PdfReader(path).pages)
    cache.write_text(txt, encoding="utf-8")
    return txt


NUM = re.compile(r"(?<![\d,])(\d{1,2} \d{3}|\d{4,5})(?![\d,])")
ROW = re.compile(r"(?:^\s*|\s{2,})(?:KP\s+|Komisariat Policji\s+)?(VIII|VII|VI|IV|V|III|II|I)(?:\s{2,}|,\s)(.*)$")


def nums(s: str) -> list[int]:
    return [int(x.replace(" ", "")) for x in NUM.findall(s)]


def label_value(lines: list[str], i: int) -> list[int]:
    """Liczby z wiersza etykiety; jeśli brak (etykieta złamana), z 2 kolejnych wierszy."""
    def clean(l: str) -> str:  # przypis doklejony do liczby: "24 0351" -> "24 035"
        return re.sub(r"\b(\d{1,2} \d{3})\d\b", r"\1", l)
    v = nums(clean(lines[i].split("Ogółem")[-1] if "Ogółem" in lines[i] else lines[i]))
    j = i + 1
    while not v and j <= i + 2 and j < len(lines):
        v = nums(clean(lines[j])); j += 1
    return v


def parse_kp_table(txt: str, year: int) -> dict:
    lines = txt.splitlines()
    cap = re.compile(r"poszczeg[oó]lnych\s+komisariat|podległych\s+poszczególnym\s+komisariatom", re.I)
    start = next(i for i in range(len(lines) - 1)
                 if cap.search(lines[i] + " " + lines[i + 1].strip()))
    start = max(0, start - 3)  # 2011: podpis tabeli na marginesie, obok pierwszych wierszy
    rows, prev, i = {}, {}, start
    while i < min(start + 70, len(lines)) and len(rows) < 8:
        m = ROW.search(lines[i])
        if m and m.group(1) == ROMAN[len(rows)]:
            v = nums(m.group(2))
            if v:
                rows[m.group(1)] = v[-1]
                if len(v) >= 2:
                    prev[m.group(1)] = v[-2]
        i += 1
    if len(rows) != 8:
        raise ValueError(f"{year}: znaleziono {len(rows)} komisariatów")
    res = {"by_kp": rows, "kp_sum": sum(rows.values())}
    if len(prev) == 8:
        res["prev_year_by_kp"] = prev
    # Wiersze podsumowań (do 25 linii po VIII)
    for k in range(i, min(i + 25, len(lines))):
        l = lines[k]
        if re.search(r"Komenda Miejska Policji", l) and "kmp_handled" not in res and "Źródło" not in l \
                and "ŹRÓDŁO" not in l:
            v = label_value(lines, k)
            if v:
                res["kmp_handled"] = v[-1]
                if len(v) >= 2:
                    res["prev_kmp_handled"] = v[-2]
        elif re.match(r"\s*Ogółem", l):
            v = label_value(lines, k)
            if not v:
                continue
            if re.search(r"komisariat", l, re.I) or "reported_kp_total" not in res and "kmp_handled" not in res:
                res.setdefault("reported_kp_total", v[-1])
            else:
                res["reported_city_total"] = v[-1]
                if len(v) >= 2:
                    res["prev_city_total"] = v[-2]
                break
    return res


def parse_units(html: str) -> dict:
    import html as _h
    t = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    t = re.sub(r"\s+", " ", _h.unescape(re.sub(r"<[^>]+>", " ", t)).replace("\xa0", " "))
    t = t.replace("Łagiewniki - Borek Fałęcki", "Łagiewniki-Borek Fałęcki")
    t = t[t.find("Komisariat Policji I w Krakowie"):]
    out = {}
    for m in re.finditer(r"Komisariat Policji (VIII|VII|VI|IV|V|III|II|I) w Krakowie(.*?)(?=Komisariat Policji|Ocena:)", t):
        out[m.group(1)] = re.findall(r"Dzielnica [IVXL]+ (.+?)(?= Dzielnica| *$)", m.group(2).strip())
    return out


SURVEY_NAMES = {d: d for d in DISTRICTS}
SURVEY_NAMES.update({"Łagiewniki-Borek Fałęcki": r"Łagiewniki\s*-\s*Borek Fałęcki",
                     "Bieżanów-Prokocim": r"Prokocim\s*-\s*Bieżanów"})
PCT = re.compile(r"(\d{1,3},\d)%")


def survey_chart(txt: str, caption: str) -> dict:
    """Wartości z wykresu słupkowego wg dzielnic (tekst pdftotext -layout).
    Dla każdej dzielnicy: lista % w kolejności pionowej (wiersz nad nazwą, wiersz nazwy, wiersze pod)."""
    lines = txt.splitlines()
    end = next(i for i, l in enumerate(lines) if l.strip().startswith(caption))
    begin = max(i for i, l in enumerate(lines[:end]) if re.match(r"\s*Wykres \d+\.", l) or "BADANIE" in l and i < end - 5)
    seg = lines[begin:end]
    name_idx = {}
    for d, pat in SURVEY_NAMES.items():
        for j, l in enumerate(seg):
            if re.match(rf"\s*{pat}(\s{{2,}}[\d,]+%.*)?\s*$", l):
                name_idx[d] = j
                break
    if len(name_idx) != 18:
        raise ValueError(f"{caption}: znaleziono {len(name_idx)} dzielnic")
    order = sorted(name_idx, key=name_idx.get)
    out = {}
    for n, d in enumerate(order):
        j = name_idx[d]
        above = j - 1
        while above >= 0 and not PCT.search(seg[above]):
            above -= 1
        stop = name_idx[order[n + 1]] - 1 if n + 1 < len(order) else len(seg)
        if n + 1 < len(order):  # wiersz nad następną dzielnicą należy do niej
            k = stop
            while k > j and not PCT.search(seg[k]):
                k -= 1
            stop = k
        vals = [float(x.replace(",", ".")) for l in seg[above:stop] for x in PCT.findall(l)]
        out[d] = vals
    return out


def main():
    offline = "--offline" in sys.argv
    RAW.mkdir(parents=True, exist_ok=True)

    # 1) Mapowanie komisariat -> dzielnice (strona KMP)
    try:
        units_html = fetch(UNITS_URL, RAW / "kmp_jednostki.html", offline).read_text(encoding="utf-8", errors="ignore")
        kp_districts = parse_units(units_html)
        assert sorted(sum(kp_districts.values(), [])) == sorted(DISTRICTS), kp_districts
        assert kp_districts == KP_DISTRICTS_FALLBACK, "mapowanie KMP zmieniło się względem fallbacku"
        mapping_src = UNITS_URL
    except (requests.RequestException, FileNotFoundError, AssertionError) as e:
        print(f"  UWAGA: mapowanie z fallbacku ({e})")
        kp_districts, mapping_src = KP_DISTRICTS_FALLBACK, "fallback w skrypcie (strona KMP niedostępna)"

    # 2) Tabele komisariatów 2004–2020
    by_year, problems = {}, []
    for y, url in RSM.items():
        p = fetch(url, RAW / f"raport_o_stanie_miasta_{y}_bezpieczenstwo.pdf", offline)
        t = parse_kp_table(pdf_text(p), y)
        if "reported_kp_total" in t and t["reported_kp_total"] != t["kp_sum"]:
            problems.append(f"{y}: suma KP {t['kp_sum']} != podana {t['reported_kp_total']}")
        by_year[y] = t
    for y, t in by_year.items():  # spójność: kolumna „rok poprzedni” == tabela z poprzedniego raportu
        if "prev_year_by_kp" in t and y - 1 in by_year and t["prev_year_by_kp"] != by_year[y - 1]["by_kp"]:
            problems.append(f"{y}: kolumna {y-1} różni się od raportu {y-1}")

    for y, t in by_year.items():  # uzupełnij KMP/miasto z kolumny „rok poprzedni” następnego raportu (2014)
        nxt = by_year.get(y + 1, {})
        if "kmp_handled" not in t and "prev_kmp_handled" in nxt:
            t["kmp_handled"] = nxt["prev_kmp_handled"]
        if "reported_city_total" not in t and "prev_city_total" in nxt:
            t["reported_city_total"] = nxt["prev_city_total"]
        if t.get("kmp_handled") and t.get("reported_city_total") and \
                t["kp_sum"] + t["kmp_handled"] != t["reported_city_total"]:
            problems.append(f"{y}: kp_sum + kmp != miasto")

    # 3) Suma dla miasta 2021–2025 (Raport o stanie Gminy)
    city_recent = {}
    t21 = pdf_text(fetch(RSG[2021], RAW / "raport_o_stanie_gminy_2021.pdf", offline))
    m = re.search(r"W 2021 roku łączna liczba wszystkich przestępstw stwierdzonych.*?wyniosła\s+(\d{1,2} \d{3})", t21, re.S)
    city_recent[2021] = int(m.group(1).replace(" ", ""))
    t24 = pdf_text(fetch(RSG[2024], RAW / "raport_o_stanie_gminy_2024.pdf", offline))
    m = re.search(r"Łączna liczba wszystkich przestępstw stwierdzonych\s+(\d{1,2} \d{3})\s+(\d{1,2} \d{3})\s+(\d{1,2} \d{3})", t24)
    for y, v in zip((2022, 2023, 2024), m.groups()):
        city_recent[y] = int(v.replace(" ", ""))
    t25 = pdf_text(fetch(RSG[2025], RAW / "raport_o_stanie_gminy_2025.pdf", offline))
    m = re.search(r"W 2025 roku stwierdzono\s+(\d{1,2} \d{3})\s+przestępstw", t25)
    city_recent[2025] = int(m.group(1).replace(" ", ""))

    # 4) Ankieta wg dzielnic
    survey = {}
    for key, url in SURVEYS.items():
        txt = pdf_text(fetch(url, RAW / f"ankieta_bezpieczenstwo_{key}.pdf", offline))
        rec = {"_url": url}
        try:
            ch = survey_chart(txt, "Wykres 11. Poczucie bezpieczeństwa w dzielnicy zamieszkania")
            for d, v in ch.items():
                if len(v) != 3 or not 98 <= sum(v) <= 101.5:
                    raise ValueError(f"W11 {d}: {v}")
            rec["district_is_safe_pct"] = {d: v[0] for d, v in ch.items()}
        except (ValueError, StopIteration) as e:
            problems.append(f"ankieta {key} W11: {e}")
        if key == "2025-11":
            try:
                ch = survey_chart(txt, "Wykres 13. Poczucie bezpieczeństwa w okolicy miejsca zamieszkania po zmroku")
                for d, v in ch.items():
                    if len(v) != 3 or not 90 <= sum(v) <= 101.5:
                        raise ValueError(f"W13 {d}: {v}")
                rec["safe_walking_after_dark_pct"] = {d: v[0] for d, v in ch.items()}
                ch = survey_chart(txt, "Wykres 18. Doświadczenie bycia ofiarą przestępstwa")
                for d, v in ch.items():
                    if len(v) != 4 or not 90 <= sum(v) <= 101.5:
                        raise ValueError(f"W18 {d}: {v}")
                rec["victim_last_12m_pct"] = {d: v[0] for d, v in ch.items()}
                rec["victim_never_pct"] = {d: v[-1] for d, v in ch.items()}
            except (ValueError, StopIteration) as e:
                problems.append(f"ankieta {key}: {e}")
        survey[key] = rec

    # 5) Pomocnicze: udziały komisariatów (średnia 2016–2020) i wskaźnik do „ogółem miasto”
    recent = [y for y in range(2016, 2021)]
    share = {kp: round(sum(by_year[y]["by_kp"][kp] / by_year[y]["kp_sum"] for y in recent) / len(recent), 4)
             for kp in ROMAN}

    out = {
        "_source": {
            "komisariat_tables": {str(y): u for y, u in RSM.items()},
            "komisariat_tables_index": "https://krakow.pl/biznes/1164,artykul,raporty_o_stanie_miasta.html",
            "komisariat_areas": mapping_src,
            "city_totals_2021_2025": {str(y): u for y, u in RSG.items()},
            "surveys": SURVEYS,
            "surveys_index": "https://www.bip.krakow.pl/?dok_id=104391",
            "press_2022": RADIO_URL,
            "fetched": date.today().isoformat(),
        },
        "_note": (
            "Przestępstwa stwierdzone (KK + ustawy szczególne) wg terenu komisariatu KMP Kraków, czyli wg MIEJSCA "
            "ZDARZENIA, nie zamieszkania sprawcy/ofiary. KP I (Stare Miasto) jest zawyżony przez turystów, "
            "dojeżdżających i życie nocne, więc nie traktuj go jako ryzyka dla mieszkańców. Lata 2004–2012: 'Ogółem' = "
            "suma KP = całe miasto. Od 2013 tabele liczą tylko sprawy prowadzone przez komisariaty; przestępstwa z ich "
            "terenu prowadzone przez KMP/KWP (poważniejsze) są osobno ('kmp_handled', bez podziału na obszary), "
            "dlatego kp_sum < city_total. Skoki w jednym roku (np. KP III 2011: 8210, 2019: +43%) to zwykle "
            "wieloczynowe sprawy oszustw, a nie zmiana bezpieczeństwa. Od 2021 brak publicznych danych wg "
            "komisariatu (Raport o stanie Gminy daje tylko sumę miasta). Obszary komisariatów = sumy całych "
            "dzielnic (strona KMP), więc agregacja dzielnic do KP jest dokładna; rozbicie KP III–VIII na pojedyncze "
            "dzielnice wymaga założenia (np. proporcjonalnie do ludności) i jest ASSUMPTION. "
            "ASSUMPTION: obszary KP stałe 2004–2020 (zmieniały się adresy siedzib: VI Bieżanowska->Ćwiklińskiej "
            "2009, V przeniesiony 2025; nie znaleziono informacji o zmianie granic). Ankieta: deklaracje ~100 "
            "dorosłych mieszkańców na dzielnicę (ważone), błąd próby ±~10 pp na dzielnicę; odczytane z etykiet "
            "wykresów w PDF. victim_last_12m_pct to 0-8 osób na dzielnicę (miasto: 1,5%, 27 osób), czyli szum; "
            "używaj raczej district_is_safe_pct / safe_walking_after_dark_pct / victim_never_pct. Krajowa Mapa Zagrożeń Bezpieczeństwa: brak udokumentowanego zbioru do pobrania "
            "(tylko aplikacja mapowa geoportalu); to i tak zgłoszenia obywateli, nie przestępstwa."
        ),
        "komisariat_to_districts": kp_districts,
        "district_to_komisariat": {d: kp for kp, ds in kp_districts.items() for d in ds},
        "crimes_by_komisariat": {str(y): {
            **{f"KP {kp}": t["by_kp"][kp] for kp in ROMAN},
            "kp_sum": t["kp_sum"],
            "kmp_handled": t.get("kmp_handled"),
            "city_total": t.get("reported_city_total", t["kp_sum"] if y <= 2012 else None),
        } for y, t in by_year.items()},
        "kp_share_mean_2016_2020": share,
        "city_total_2021_2025": {str(y): v for y, v in sorted(city_recent.items())},
        "press_2022_partial": {
            "_note": "Dane KMP dla PAP (Radio Kraków 2023-03-15), 9 kategorii, niepełne: tylko liczby podane w tekście. "
                     "'theft' = kradzież mienia (cudzej rzeczy). Nie łącz z tabelami wyżej (inna definicja).",
            "KP I": {"theft": 1030, "vehicle_theft": 7, "burglary": 57, "robbery": 38, "bodily_injury": 64,
                     "fight_assault": 27, "sexual": 7, "homicide": 0},
            "KP II": {"theft": 256, "vehicle_theft": 3, "burglary": 26, "sexual": 2, "fight_assault": 4},
            "KP III": {"vehicle_theft": 19, "sexual": 8, "homicide": 1},
            "KP VI": {"homicide": 1},
            "KP VII": {"theft": 377, "sexual": 1, "homicide": 1},
            "KP VIII": {"theft": 404, "sexual": 2},
            "city_9_categories_total": {"2021": 3899, "2022": 4486},
        },
        "perceived_safety_by_district": survey,
        "_checks": problems,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"zapisano {OUT}")
    print("rok   " + " ".join(f"{'KP '+k:>7}" for k in ROMAN) + "   kp_sum  kmp  miasto")
    for y, r in out["crimes_by_komisariat"].items():
        print(y, " ".join(f"{r['KP '+k]:>7}" for k in ROMAN), f"{r['kp_sum']:>8} {r['kmp_handled'] or '-':>5} {r['city_total'] or '-':>6}")
    print("miasto 2021–2025:", out["city_total_2021_2025"])
    print("problemy:", problems or "brak")


if __name__ == "__main__":
    main()
