"""Budżet Obywatelski Miasta Krakowa (BO, civic participatory budget): voting results 2019-2025 -> data/raw/bo_krakow.json

Ground truth for validating how residents vote on city proposals.

Sources (official, public, no login):
  * BIP Kraków, one page per edition (https://www.bip.krakow.pl/?dok_id=73900 lists all editions). Attachments:
      - "Wyniki głosowania"  : points per project (city-wide + district PDFs)
      - "Informacja nt. liczby osób głosujących": number of PEOPLE per project, split by points given (3/2/1)
        and channel (paper / electronic). Published from 2020 on.
  * budzet.krakow.pl/projektyYYYY/{miasto,dzielnica/N}: list of projects put to the vote with short description,
    theme icon and the "Przyjęty do realizacji" badge (= selected for funding).
  * Summary presentations (plikimpi.krakow.pl) for turnout by age, transcribed by hand (see TURNOUT).

Raw files are kept in data/raw/bo_krakow/<year>/ (cache: re-running does not re-download).
Requires `pdftotext` (poppler) on PATH.
Run from repo root:  python3 src/fetch_bo_krakow.py
"""
import datetime, html, json, re, subprocess, sys, time
from pathlib import Path
import requests

RAW = Path("data/raw/bo_krakow")
OUT = Path("data/raw/bo_krakow.json")
UA = "krakow-sim/0.1 (research: synthetic population validation; contact via repo owner) python-requests"
THROTTLE_S = 1.2
BIP = "https://www.bip.krakow.pl"
BO = "https://budzet.krakow.pl"
_last = [0.0]

DISTRICTS = {  # official number -> normalized name (official: "Dzielnica <roman> <name>")
    1: "Stare Miasto", 2: "Grzegórzki", 3: "Prądnik Czerwony", 4: "Prądnik Biały", 5: "Krowodrza", 6: "Bronowice",
    7: "Zwierzyniec", 8: "Dębniki", 9: "Łagiewniki-Borek Fałęcki", 10: "Swoszowice", 11: "Podgórze Duchackie",
    12: "Bieżanów-Prokocim", 13: "Podgórze", 14: "Czyżyny", 15: "Mistrzejowice", 16: "Bieńczyce",
    17: "Wzgórza Krzesławickie", 18: "Nowa Huta"}
ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "XIV", "XV", "XVI",
         "XVII", "XVIII"]

# BIP attachment ids (zid), read off each edition's BIP page (section "Wyniki głosowania" / "Informacja nt. liczby
# osób głosujących"). 2022 district points: the BIP copy is image-only, the identical krakow.pl copy has text.
EDITIONS = {
    2019: dict(bip=107044, pts_city=254843, pts_dist=254842),
    2020: dict(bip=122645, pts_city=287991, pts_dist=287990, vot_city=289264, vot_dist=289263),
    2021: dict(bip=137874, pts_city=317868, pts_dist=317864, vot_city=324377, vot_dist=324376),
    2022: dict(bip=149146, pts_city=360270, pts_dist="https://plikimpi.krakow.pl/zalacznik/431713",
               vot_city=362334, vot_dist=362319),
    2023: dict(bip=163251, pts_city=429612, pts_dist=429822, vot_city=440070, vot_dist=440061),
    2024: dict(bip=185139, pts_city=521593, pts_dist=521596, vot_city=521599, vot_dist=521602),
    2025: dict(bip=212383, pts_city=621055, pts_dist=621049, vot_city=621775, vot_dist=621781),
}

# Theme: the site shows only an icon per project (CSS class "ifa-*"), no text label.
# ASSUMPTION: icon -> theme mapping inferred from the category names used in the city's 2024 summary presentation
# ("Zieleń i ochrona środowiska", "Infrastruktura", "Kultura", "Sport i infrastruktura sportowa", "Zdrowie",
# "Społeczeństwo", ...). Raw icon is kept in `category_icon`.
ICON_THEME = {"tree": "Zieleń i ochrona środowiska", "road": "Infrastruktura", "bank": "Kultura",
              "trophy": "Sport", "users": "Społeczeństwo", "ambulance": "Zdrowie", "book": "Edukacja",
              "shield": "Bezpieczeństwo", "bicycle": "Transport rowerowy"}

# ASSUMPTION: transcribed by hand from official summary charts/news (numbers are printed on the charts; the PDFs are
# charts, not tables). Age groups are VALID ballots by age of voter. Not every figure exists for every year.
TURNOUT = {
    2019: dict(ballots_cast=53647, valid_ballots=50004, invalid_ballots=3643,
               src="2020 summary PDF (BIP zid 287993), table 'głosy ważne / nieważne / wszystkie'"),
    2020: dict(ballots_cast=44800, valid_ballots=42281, invalid_ballots=2519, paper_valid=11987,
               electronic_valid=30294, turnout_share_of_residents=0.0596, women_share=0.55, men_share=0.45,
               voters_share_by_age={"0-15": 0.08, "16-25": 0.11, "26-35": 0.31, "36-45": 0.26, "46-55": 0.10,
                                    "56-65": 0.07, "66+": 0.07},
               src="2020 summary PDF (BIP zid 287993)"),
    # 2022 chart labels 51229 'głosy ważne', but the same chart's 2019/2020 values equal ALL ballots (incl. invalid)
    2021: dict(ballots_cast_or_valid=51229, src="2022 summary PDF, chart 'Głosowanie w poprzednich latach' "
               "(label ambiguous: valid vs all ballots)"),
    2022: dict(ballots_cast=46095, valid_ballots=42965, invalid_ballots=3130, paper_valid=17059,
               electronic_valid=25906, women_share=0.57, men_share=0.43,
               valid_by_age={"0-15": 4539, "16-25": 3637, "26-35": 10038, "36-45": 11799, "46-55": 4842,
                             "56-65": 3241, "65+": 4869},
               src="https://plikimpi.krakow.pl/zalacznik/431630 (Podsumowanie głosowania 2022)"),
    2023: dict(ballots_cast=69490, src="2024 summary presentation https://plikimpi.krakow.pl/zalacznik/493341"),
    2024: dict(ballots_cast=72942, valid_ballots=65101, invalid_ballots=7841, women_valid=36693, men_valid=28408,
               valid_by_age={"0-15": 8602, "16-25": 6171, "26-35": 13271, "36-45": 18116, "46-55": 7774,
                             "56-65": 4326, "65+": 6841},
               turnout_share_of_residents=0.10,
               src="https://plikimpi.krakow.pl/zalacznik/493341 (Prezentacja podsumowująca 2024)"),
    2025: dict(ballots_cast=84842, turnout_share_of_residents=0.12,
               valid_by_age_partial={"26-35": 15051, "36-45": 22025},
               src="budzet.krakow.pl / Radio Kraków results news, Oct 2025 (only the two largest age groups given)"),
}
EXTRA_DOCS = {2020: {"summary.pdf": f"{BIP}/plik.php?zid=287993&wer=0&new=t&mode=shw"},
              2022: {"summary.pdf": "https://plikimpi.krakow.pl/zalacznik/431630"},
              2024: {"summary.pdf": "https://plikimpi.krakow.pl/zalacznik/493341"}}


def bip_file(zid):
    return zid if isinstance(zid, str) else f"{BIP}/plik.php?zid={zid}&wer=0&new=t&mode=shw"


def get(url, path: Path, binary_pdf=False):
    """Download once into path (cache). Throttled; resumes truncated PDFs with Range requests (BIP drops
    connections on larger files)."""
    def ok(p):
        return p.exists() and p.stat().st_size > 0 and (not binary_pdf or b"%%EOF" in p.read_bytes()[-2048:])
    if ok(path):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + ".part")
    for attempt in range(8):
        wait = THROTTLE_S - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        have = part.stat().st_size if part.exists() else 0
        hdr = {"User-Agent": UA}
        if have and binary_pdf:
            hdr["Range"] = f"bytes={have}-"
        try:
            with requests.get(url, headers=hdr, timeout=(20, 120), stream=True) as r:
                r.raise_for_status()
                mode = "ab" if (have and r.status_code == 206) else "wb"
                with open(part, mode) as f:
                    for chunk in r.iter_content(65536):
                        f.write(chunk)
        except requests.RequestException as e:
            print(f"  retry {attempt + 1} {url}: {type(e).__name__}", file=sys.stderr)
        finally:
            _last[0] = time.time()
        if part.exists() and (not binary_pdf or b"%%EOF" in part.read_bytes()[-2048:]):
            part.rename(path)
            return path
    raise RuntimeError(f"could not download {url}")


def pdf_text(path):
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True,
                          check=True).stdout


def num(s):
    return int(re.sub(r"\s", "", s))


def money(s):
    return float(re.sub(r"\s", "", s).replace(",", "."))


ID_RE = re.compile(r"BO\.(OM|D(\d{1,2}))\.(\d+)/(\d\d)")


def parse_points(text):
    """Rows: BO.OM.13/22  Title  [district]  points  cost zł. Also pool budgets from section header lines."""
    rows, pools, cur = {}, {}, None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = ID_RE.search(line)
        if not m:
            h = re.match(r"\s*(DZIELNICA\s+([IVX]+)\b.*?|PROJEKTY OGÓLNOMIEJSKIE|OGÓLNOMIEJSKIE)\s{2,}([\d ]+(?:,\d\d)?)\s*zł",
                         line)
            if h:
                cur = ("district", ROMAN.index(h.group(2))) if h.group(2) else ("city", None)
                pools[cur] = money(h.group(3))
            continue
        key = ("city", None, int(m.group(3))) if m.group(1) == "OM" else ("district", int(m.group(2)), int(m.group(3)))
        rest = line[m.end():]
        c = re.search(r"(\d{1,3}(?: \d{3})*,\d\d)\s*zł\s*$", rest)
        if not c:  # long title wrapped: points/cost spill onto the next lines (seen in 2025)
            nxt = []
            for l2 in lines[i + 1:i + 3]:
                if ID_RE.search(l2):
                    break
                nxt.append(l2)
            joined = " ".join([rest] + nxt)
            cs = list(re.finditer(r"\d{1,3}(?: \d{3})*,\d\d", joined))
            ps = cs and re.findall(r"(?<![\d ])(\d{1,6})(?![\d,])|(?<= )(\d{1,6})(?= )", joined[:cs[-1].start()])
            if not cs or not ps:
                continue
            pts_s = [a or b for a, b in ps][-1]
            rows[key] = dict(code=m.group(0), title_pdf=re.split(r"\d", joined)[0].strip(), points=int(pts_s),
                             cost_pln=money(cs[-1].group(0)), parse_note="wrapped row, points/cost recovered")
            continue
        body = rest[:c.start()].rstrip()
        cols = re.split(r"\s{2,}", body.strip())
        if not re.fullmatch(r"\d{1,3}(?: \d{3})*|\d+", cols[-1]):
            continue
        pts = num(cols[-1])
        title = cols[0] if len(cols) >= 2 else ""
        prev = lines[i - 1].strip() if i else ""
        if prev and not ID_RE.search(prev) and "zł" not in prev and not prev.isupper():
            title = f"{prev} {title}"  # wrapped title: first part on the line above (2023)
        rows[key] = dict(code=m.group(0), title_pdf=title.strip(), points=pts, cost_pln=money(c.group(1)))
    return rows, pools


def parse_voters(text):
    """Rows with code then 8 numbers: points, six counts (by channel x points), persons total.
    Column order differs between years; resolved by checking 3a+2b+c == points."""
    hdr = text[:3000]
    paper_first = hdr.find("PAPIER") < hdr.find("ELEKTR")
    rows, bad = {}, 0
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = ID_RE.search(line)
        if not m:
            continue
        rest = line[m.end():].strip()
        if not rest:  # 2025: the numbers of a wrapped row sit on the line above the code
            for j in range(i - 1, max(i - 3, -1), -1):
                if re.fullmatch(r"[\d\s-]+", lines[j]) and lines[j].strip():
                    rest = lines[j].strip()
                    break
        rest = re.sub(r"(?<!\S)-(?!\S)", "0", rest)  # '-' = nobody
        rest = re.sub(r"^(OM|DZ|D)\s+", "", rest)  # 2022 has a TYP_PROJEKTU column
        cols = re.split(r"\s{2,}", rest)
        try:
            vals = [num(c) for c in cols if re.fullmatch(r"[\d ]+", c)]
        except ValueError:
            vals = []
        if len(vals) != 8:  # fallback: plain whitespace split (no thousands separators in this row)
            vals = [int(x) for x in rest.split() if x.isdigit()]
        key = ("city", None, int(m.group(3))) if m.group(1) == "OM" else ("district", int(m.group(2)), int(m.group(3)))
        if len(vals) != 8:
            bad += 1
            continue
        found = None
        for pts, c, persons in ((vals[0], vals[1:7], vals[7]), (vals[6], vals[0:6], vals[7])):  # 2023: points after counts
            for w in ((3, 2, 1), (1, 2, 3)):
                if sum(x * y for x, y in zip(c, w * 2)) == pts:
                    found = w
                    break
            if found:
                break
        if not found:
            bad += 1
            continue
        g1, g2 = dict(zip(found, c[:3])), dict(zip(found, c[3:]))
        paper, elec = (g1, g2) if paper_first else (g2, g1)
        rows[key] = dict(points=pts, voters=persons,
                         voters_by_points={str(k): paper[k] + elec[k] for k in (3, 2, 1)},
                         voters_paper=sum(paper.values()), voters_electronic=sum(elec.values()))
        if sum(c) != persons:  # source typo/inconsistency: published persons total != sum of the six counts
            rows[key]["voters_note"] = f"published total {persons} != sum of counts {sum(c)}"
    return rows, bad


CARD_RE = re.compile(r'<div id="project(\d+)" class="projekty columnsbo project ifa-([a-z-]+)">(.*?)class="projView"',
                     re.S)


def parse_listing(page):
    out = {}
    for pid, icon, body in CARD_RE.findall(page):
        nr = re.search(r'<span id="mx\d+">\s*(\d+)\s*</span>', body)
        tit = re.search(r'class="projTit"[^>]*>(.*?)</div>', body, re.S)
        sh = re.search(r'class="projShort">(.*?)</div>', body, re.S)
        clean = lambda s: " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())
        out[int(nr.group(1)) if nr else -int(pid)] = dict(
            site_id=int(pid), title=clean(tit.group(1)) if tit else None,
            short_description=clean(sh.group(1)) if sh else None,
            category_icon=icon, selected_badge='class="projaccepted"' in body)
    return out


def main():
    today = datetime.date.today().isoformat()
    projects, editions, problems, urls = [], {}, [], set()
    for year, cfg in EDITIONS.items():
        d = RAW / str(year)
        print(f"== {year}")
        urls.add(f"{BIP}/?dok_id={cfg['bip']}")
        get(f"{BIP}/?dok_id={cfg['bip']}", d / "bip_edition.html")
        for name, url in EXTRA_DOCS.get(year, {}).items():
            get(url, d / name, binary_pdf=True)
            urls.add(url)
        pts, pools, vot, vot_levels = {}, {}, {}, set()
        for lvl in ("city", "dist"):
            f = get(bip_file(cfg[f"pts_{lvl}"]), d / f"points_{lvl}.pdf", binary_pdf=True)
            urls.add(bip_file(cfg[f"pts_{lvl}"]))
            r, p = parse_points(pdf_text(f))
            pts.update(r)
            pools.update(p)
            if f"vot_{lvl}" in cfg:
                f = get(bip_file(cfg[f"vot_{lvl}"]), d / f"voters_{lvl}.pdf", binary_pdf=True)
                urls.add(bip_file(cfg[f"vot_{lvl}"]))
                txt = pdf_text(f)
                r, bad = parse_voters(txt)
                if not txt.strip():
                    problems.append(f"{year} voters_{lvl}.pdf has no text layer (scan) - voter counts missing")
                elif bad:
                    problems.append(f"{year} voters_{lvl}.pdf: {bad} rows failed parse/consistency check")
                vot.update(r)
                if r:
                    vot_levels.add(lvl)
        # project listings (title as on site, short description, icon, selected badge)
        listing = {}
        pages = [("city", None, f"{BO}/projekty{year}/miasto")] + \
                [("district", k, f"{BO}/projekty{year}/dzielnica/{k}") for k in DISTRICTS]
        for lvl, k, url in pages:
            f = get(url, d / f"listing_{'city' if k is None else f'd{k:02d}'}.html")
            for nr, rec in parse_listing(f.read_text(encoding="utf-8", errors="replace")).items():
                listing[(lvl, k, nr)] = rec
        urls.add(f"{BO}/projekty{year}/miasto")
        urls.add(f"{BO}/projekty{year}/dzielnica/<1..18>")

        keys = set(pts) | set(listing)
        recs = []
        for key in keys:
            lvl, k, nr = key
            p, L, v = pts.get(key), listing.get(key), vot.get(key)
            if v is None and p and p["points"] == 0 and vot_levels & {("city" if lvl == "city" else "dist")}:
                v = dict(points=0, voters=0, voters_by_points={"3": 0, "2": 0, "1": 0}, voters_paper=0,
                         voters_electronic=0)  # 0-point projects are omitted from the voters PDF
            if p is None:
                problems.append(f"{year} {key}: on site listing but not in results PDF ({L and L['title']})")
            if L is None:
                problems.append(f"{year} {key}: in results PDF but not on site listing ({p and p['title_pdf']})")
            if p and v and p["points"] != v["points"]:
                problems.append(f"{year} {key}: points differ between PDFs ({p['points']} vs {v['points']})")
            rec = dict(edition=year, id=p["code"] if p else f"BO.{'OM' if k is None else f'D{k}'}.{nr}/{year % 100:02d}",
                       ballot_number=nr, site_id=L and L["site_id"],
                       title=(L and L["title"]) or (p and p["title_pdf"]),
                       short_description=L and L["short_description"], level=lvl,
                       district=None if k is None else f"Dzielnica {ROMAN[k]} {DISTRICTS[k]}",
                       district_norm=None if k is None else DISTRICTS[k], district_number=k,
                       category=L and ICON_THEME.get(L["category_icon"]), category_icon=L and L["category_icon"],
                       cost_pln=p and p["cost_pln"], votes=p and p["points"], votes_unit="points",
                       voters=v and v["voters"], voters_by_points=v and v["voters_by_points"],
                       voters_paper=v and v["voters_paper"], voters_electronic=v and v["voters_electronic"],
                       voters_note=v and v.get("voters_note"),
                       won=bool(L and L["selected_badge"]), rank=None, pool_size=None,
                       parse_note=p and p.get("parse_note"))
            recs.append(rec)
        # rank within pool (city, or one district), by points desc; ties share the best rank
        for pool in {(r["level"], r["district_number"]) for r in recs}:
            grp = sorted([r for r in recs if (r["level"], r["district_number"]) == pool and r["votes"] is not None],
                         key=lambda r: -r["votes"])
            for r in grp:
                r["rank"] = 1 + sum(1 for o in grp if o["votes"] > r["votes"])
                r["pool_size"] = len(grp)
            # ASSUMPTION: Kraków rule = take projects by points; skip one that exceeds the money left and continue
            # with the next that fits. Reconstructed independently of the site badge as a cross-check.
            budget = pools.get(pool if pool[0] == "district" else ("city", None))
            left = budget
            for r in grp:
                fits = budget is not None and r["votes"] > 0 and r["cost_pln"] is not None and r["cost_pln"] <= left + 0.01
                r["won_by_budget_rule"] = bool(fits) if budget is not None else None
                if fits:
                    left -= r["cost_pln"]
        recs.sort(key=lambda r: (r["level"] != "city", r["district_number"] or 0, r["rank"] or 9999))
        projects += recs
        editions[year] = dict(
            n_projects=len(recs), n_city=sum(r["level"] == "city" for r in recs),
            n_district=sum(r["level"] == "district" for r in recs), n_won=sum(r["won"] for r in recs),
            n_with_voters=sum(r["voters"] is not None for r in recs),
            n_won_by_budget_rule=sum(bool(r.get("won_by_budget_rule")) for r in recs),
            n_won_disagree=sum(bool(r.get("won_by_budget_rule")) != r["won"] for r in recs),
            pool_budget_pln={("city" if lv == "city" else DISTRICTS[k]): b for (lv, k), b in sorted(
                pools.items(), key=lambda x: (x[0][0] != "city", x[0][1] or 0))},
            turnout=TURNOUT.get(year))
        e = editions[year]
        print(f"   projects={e['n_projects']} (city {e['n_city']}, district {e['n_district']}) won={e['n_won']} "
              f"with_voters={e['n_with_voters']} won_by_rule={e['n_won_by_budget_rule']} disagree={e['n_won_disagree']}")

    out = {
        "_source": {
            "fetched": today,
            "publisher": "Urząd Miasta Krakowa (Wydział Dialogu, Konsultacji i Kontaktu Obywatelskiego; earlier "
                         "Wydział Polityki Społecznej i Zdrowia)",
            "editions_index": f"{BIP}/?dok_id=73900",
            "urls": sorted(urls),
            "script": "src/fetch_bo_krakow.py",
        },
        "_note": (
            "Kraków civic budget (BO) projects put to the public vote, editions 2019-2025 (edition = voting year; "
            "funded projects are implemented the following year(s)). 2026 (XIII) voting closed 2026-09-28 but "
            "results were not yet published at fetch time. Projects rejected at formal verification are NOT included. "
            "VOTING RULE (all included years): each voter picks up to 3 city-wide and up to 3 district projects (own "
            "district) and ranks them: 1st choice = 3 points, 2nd = 2, 3rd = 1. `votes` = sum of points as published "
            "(votes_unit='points'), not number of voters. `voters` = number of people who gave the project any "
            "points (from the separate 'liczba osób głosujących' PDFs, 2020+); `voters_by_points` splits them by "
            "3/2/1 points; paper vs electronic also given. `won` = project marked 'Przyjęty do realizacji' on "
            "budzet.krakow.pl (selected for funding; this badge reflects the city's CURRENT status, so it can differ from the count announced right after the vote, e.g. 2023: 139 badges vs 144 announced). `won_by_budget_rule` = selection reconstructed from points, costs and pool budgets in the results PDFs (matches the announced totals 2022, 2024, 2025 exactly); prefer it when validating vote predictions. Selection is by points within each pool (city-wide or one "
            "district) until the pool budget runs out, so a project that does not fit the remaining money is "
            "skipped and a lower-ranked cheaper one can win: rank 1..k is not always = won. `rank` = position by "
            "points within its pool (ties share rank). `category` is inferred from the site's theme icon "
            "(ASSUMPTION, see ICON_THEME); the raw icon is `category_icon`. `title`/`short_description` are as "
            "published on budzet.krakow.pl (Polish). cost_pln = estimated cost from the results PDF. "
            "Per-project votes by voter district or age are NOT published. Turnout (editions.*.turnout) is "
            "transcribed by hand from summary charts/news and is incomplete (age groups only 2022, 2024; partial "
            "2025); ballots_cast includes invalid ballots."),
        "_problems": problems,
        "editions": editions,
        "projects": projects,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"{len(projects)} projects -> {OUT}; {len(problems)} problems")
    for p in problems[:40]:
        print("  !", p)


if __name__ == "__main__":
    main()
