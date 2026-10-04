"""End-to-end test set for the app (through the running API, `make app`): questions a user would ask, graded against
official data computed at run time (PKW, GUS BDL, OpenStreetMap, the personas), not against numbers typed in here.
  routing   the router picks the right mode
  facts     the data agent's answer contains the true number (within tolerance) and no unverified numbers
  events    sanity: a large concert gets a large venue, a small club night a small one
Usage: python3 src/eval_app.py [--only routing|facts|events]   (cost: ~$0.5; answers cached by the app where possible)
"""
import json, re, sys, time
import requests

API = "http://localhost:8000"
sys.path.insert(0, "src")


def truth():
    import datastore as ds
    PK = json.load(open("data/raw/pkw_krakow.json"))["district_aggregates"]
    T = ds.tables()
    r2 = PK["prezydent2025_r2"]["by_district"]["Krowodrza"]["shares"]
    trz = next(v for k, v in r2.items() if k.startswith("TRZASKOWSKI"))
    cafes_so = sum(1 for _ in ds.poi().query("cat == 'cafe' and district == 'Stare Miasto'").itertuples())
    seniors = ds.query([{"field": "district", "op": "=", "value": "Bieńczyce"}, {"field": "age", "op": ">=", "value": "65"}])["rows"][0]["people"]
    ed = T["city.education.edu_by_sex_13plus"]["value"]["total"]
    return [
        ("Jaki wynik miał Rafał Trzaskowski w Krowodrzy w 2. turze wyborów prezydenckich 2025?", 100 * trz, 1.0, "pct", "PKW"),
        ("Jaka była frekwencja w Nowej Hucie w wyborach do Sejmu 2023?", 100 * PK["sejm2023"]["by_district"]["Nowa Huta"]["turnout"], 1.0, "pct", "PKW"),
        ("Ile kin jest w Krakowie według GUS?", T["city.culture.cinemas"]["value"]["cinemas"], 0.01, "num", "GUS BDL"),
        ("Ile samochodów osobowych zarejestrowano w Krakowie w 2025 roku?", T["city.transport.cars_by_fuel"]["value"]["total"], 0.01, "num", "GUS BDL"),
        ("Ilu studentów jest w Krakowie według GUS?", T["city.students.he_students_graduates"]["value"]["students_total"], 0.01, "num", "GUS BDL"),
        ("Ile kawiarni jest na Starym Mieście według OpenStreetMap?", cafes_so, 0.05, "num", "OSM"),
        ("Ilu seniorów 65+ mieszka w Bieńczycach?", seniors, 0.05, "num", "persony (GUS)"),
        ("Jaki odsetek mieszkańców Krakowa (13+) ma wyższe wykształcenie według spisu 2021?", 100 * ed["higher"] / (ed["total"] - ed["unknown"]), 1.5, "pct", "NSP 2021"),
    ]


ROUTING = [("Koncert jazzowy w sobotę wieczorem, 80 zł, dla 300 osób", "event"), ("Gdzie zrobić piknik rodzinny w czerwcu?", "event"),
           ("Miasto chce zlikwidować linię tramwajową 18. Co na to mieszkańcy?", "policy"), ("Czy mieszkańcy poprą zakaz sprzedaży alkoholu po 22?", "policy"),
           ("Ilu emerytów mieszka w Nowej Hucie?", "agent"), ("Chcę otworzyć przedszkole, gdzie jest największa luka?", "agent")]
EVENTS = [("Duży koncert popowej gwiazdy, sobota wieczór, 250 zł, ok. 15 000 osób, w hali", "large", lambda v: (v.get("capacity") or 0) >= 8000),
          ("Kameralny wieczór poezji w czwartek, 20 zł, dla 60 osób, w środku", "small", lambda v: v["kind"] == "district" or (v.get("capacity") or 1e9) <= 600)]


def nums(text):
    out = []
    for m in re.finditer(r"(\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)\s*(%)?", text):
        try: out.append((float(re.sub(r"[   ]", "", m[1]).replace(",", ".")), bool(m[2])))
        except ValueError: pass
    return out


def run_chat(text, slots=None):
    r = requests.post(f"{API}/api/chat", json={"text": text, "slots": slots or {}}, timeout=300).json(); s = r["slots"]
    while r.get("missing"):   # answer follow-ups with the first option (or 0 zł)
        m = r["missing"][0]; v = m["options"][0]["value"] if m.get("options") else 0
        if m.get("free"): break
        r = requests.post(f"{API}/api/chat", json={"slots": s, "slot": m["slot"], "value": v}, timeout=300).json(); s = r["slots"]
    return r, s


def run_sim(s):
    with requests.get(f"{API}/api/run", params={"n": 1, "slots": json.dumps(s)}, stream=True, timeout=900) as res:
        for line in res.iter_lines():
            if line.startswith(b"data: "):
                e = json.loads(line[6:])
                if e["type"] in ("result", "error"): return e


def main():
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None; score = {}
    if only in (None, "routing"):
        ok = 0
        for q, want in ROUTING:
            r, s = run_chat(q); got = s.get("mode") or ("agent" if r.get("message") else None)
            ok += got == want; print(f"[routing] {'OK ' if got == want else 'ZLE'} {want:6s} <- {got}: {q}")
        score["routing"] = f"{ok}/{len(ROUTING)}"
    if only in (None, "facts"):
        ok = 0
        for q, val, tol, kind, src in truth():
            t0 = time.time(); r, s = run_chat(q); e = run_sim(s); d = e.get("data", {})
            ans = d.get("answer", ""); N = nums(ans)
            hit = any(abs(x - val) <= (tol if kind == "pct" else max(tol * val, 0.5)) for x, _ in N)
            clean = not d.get("flagged")
            ok += hit and clean
            print(f"[facts] {'OK ' if hit and clean else 'ZLE'} prawda {val:,.1f} ({src}) | {time.time() - t0:.0f}s | niesprawdzone {d.get('flagged')} | {q}\n        → {ans[:220].replace(chr(10), ' ')}")
        score["facts"] = f"{ok}/{len(truth())}"
    if only in (None, "events"):
        ok = 0
        for q, label, check in EVENTS:
            r, s = run_chat(q); e = run_sim(s); d = e.get("data", {})
            v0 = (d.get("venues") or [{}])[0]; good = bool(v0) and check(v0)
            ok += good; print(f"[events] {'OK ' if good else 'ZLE'} {label}: najlepsze = {v0.get('name')} (pojemność {v0.get('capacity')}, {v0.get('kind')}) | {q}")
        score["events"] = f"{ok}/{len(EVENTS)}"
    print("\nWYNIK:", score)


if __name__ == "__main__":
    main()
