"""Jev (TypeSafe System One model) as the decision model: every persona aged 15+ is asked separately.
state = persona (from GUS-based personas.csv) + scenario + personal facts computed in code
        (ticket cost as % of income, travel time, whether the SCT bans their car, fare change in PLN/month).
questions = typed Noul / Score / Choice -> calibrated probabilities, no parsing, no Likert mapping.
Jev guidance followed: arithmetic in code, English text, literal instructions, small focused state.

Usage: python3 src/jev_sim.py SCENARIO.json [--n 500] [--dry-run] [--calibrate] [--concurrency 40]
  --calibrate   ask the GUS culture-survey questions ("attended a concert in the last 12 months?") with the
                persona's leisure traits REMOVED, and compare Jev's rates with GUS by age x sex
Output: out/jev/<name>_<tag>.jsonl (cache, one line per persona), <name>_by_district.csv, <name>_personas.csv
Key: TYPESAFE_API_KEY in env or .env. Price: $0.042 per million input tokens, output free.
"""
import asyncio, hashlib, json, os, sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema import load_scenario
import bio

PRICE_PER_MTOK = 0.042
JEV_CONCURRENCY = int(os.environ.get("KS_JEV_CONC", "40"))   # parallel Jev calls in the app (20 was the safe default; timeouts seen at high load)
RYNEK = (50.0617, 19.9373)
OUTSIDE = "poza Krakowem"

EN = {  # persona vocabulary -> English (Jev's best language)
    "status": {"uczeń": "secondary school pupil", "student": "university student", "pracuje": "employed", "bezrobotny": "unemployed (registered)",
               "emeryt": "retired / on pension", "niepracujący": "not working (economically inactive)"},
    "education": {"wyższe": "university degree", "średnie": "secondary school", "zasadnicze zawodowe": "basic vocational", "podstawowe": "primary school"},
    "household": {"mieszka sam(a)": "lives alone", "z partnerem/partnerką": "lives with partner", "z partnerem i dziećmi": "lives with partner and children",
                  "samotny rodzic z dziećmi": "single parent living with children", "z rodzicami": "lives with parents",
                  "ze współlokatorami / w akademiku": "lives with flatmates or in a dorm", "z dalszą rodziną (wielopokoleniowo)": "lives with extended family",
                  "z dorosłymi dziećmi (bez partnera)": "lives with adult children, no partner"},
    "fuel": {"benzyna": "petrol", "diesel": "diesel", "LPG": "petrol + LPG", "hybryda/elektryk/inne": "hybrid or electric"},
}
LEISURE_EN = {"koncerty (pop, rock, inne)": "goes to pop/rock concerts", "filharmonia, koncerty klasyczne": "goes to classical concerts",
              "teatr": "goes to the theatre", "kino co miesiąc": "goes to the cinema monthly", "kluby, dyskoteki": "goes clubbing",
              "potańcówki, dancingi": "goes to dances", "festiwale": "goes to festivals", "muzea": "visits museums", "kabaret, stand-up": "watches cabaret / stand-up",
              "czyta książki": "reads books", "fotografia": "photography", "gra na instrumencie / śpiewa": "plays music / sings", "taniec": "dancing",
              "majsterkowanie": "DIY", "kolekcjonowanie": "collecting", "rower": "cycling", "pływanie": "swimming", "fitness": "fitness classes",
              "siłownia": "gym", "bieganie / nordic walking": "running / nordic walking", "piłka nożna": "football", "joga": "yoga",
              "narty / snowboard": "skiing / snowboarding", "siatkówka": "volleyball", "tenis / badminton / squash": "racket sports",
              "wspinaczka": "climbing", "sporty walki": "martial arts", "wędkarstwo": "fishing", "rolki / deskorolka": "rollerblading / skateboarding"}
DAY = {"Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday", "Fri": "Friday", "Sat": "Saturday", "Sun": "Sunday"}


def pop_version():
    """Short hash of the synthetic population file: part of every per-persona cache key (persona ids are reused when the
    population is regenerated, so old answers would land on different people)."""
    import hashlib as _h
    st = os.stat("out/personas.csv"); return _h.sha1(f"{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:6]


def load_env():
    if Path(".env").exists():
        for line in Path(".env").read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip().strip('"'))
    # docker --env-file and some hosting dashboards keep the quotes from .env as part of the value
    for k in ("OPENAI_API_KEY", "TYPESAFE_API_KEY", "BDL_KEY"):
        if os.environ.get(k, "").strip().startswith(('"', "'")): os.environ[k] = os.environ[k].strip().strip("\"'")


def km(lat, lon, la2, lo2):
    r = np.radians; h = np.sin(r(la2 - lat) / 2) ** 2 + np.cos(r(lat)) * np.cos(r(la2)) * np.sin(r(lo2 - lon) / 2) ** 2
    return 2 * 6371 * np.arcsin(np.sqrt(h))


def nn(v):  # NaN -> None
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else v


def no_income_text(r):
    hh = str(nn(r.household) or "")
    return ("no own income; lives on their partner's income" if "partner" in hh else
            "no own income; supported by parents" if r.age < 26 or "rodzic" in hh else "no own income; supported by family")


def person_state(r, leisure="all"):
    """Persona as a small English JSON object. Only facts; no numbers the model has to compare."""
    p = {"age": int(r.age), "gender": "male" if r.sex == "M" else "female",
         "home": "suburb just outside Kraków" if r.district == OUTSIDE else f"Kraków, {r.district} district",
         "occupation": EN["status"][r.status] + (" and also studying" if nn(r.studying) and r.status == "pracuje" else ""),
         "monthly_net_income_pln": int(r.net_income) if r.net_income > 0 else no_income_text(r)}
    if r.origin == "student_spoza": p["note"] = "student from another town; lives in Kraków during the academic year, family home elsewhere"
    if nn(r.born_abroad): p["born"] = "abroad"
    if nn(r.education): p["education"] = EN["education"].get(r.education, r.education)
    if nn(r.household): p["household"] = EN["household"].get(r.household, r.household) + (f", {int(r.children)} child(ren)" if r.children else "")
    if r.has_car and nn(r.car_age) is not None:
        age = "brand-new" if r.car_age < 1 else f"{int(r.car_age)}-year-old"
        p["car"] = f"owns a {age} {EN['fuel'].get(r.car_fuel, r.car_fuel)} car"
    else: p["car"] = "no car"
    cycles = isinstance(r.hobbies, str) and "rower" in r.hobbies.split(";")
    p["gets_around_by"] = ("mostly car" if r.has_car else "public transport (tram/bus)") + (", also cycles" if cycles else "")
    if leisure == "all":   # only events need leisure & taste; extra detail hurts other questions (Jev guidance)
        acts = [LEISURE_EN.get(x, x) for x in (r.hobbies.split(";") if isinstance(r.hobbies, str) and r.hobbies else [])]
        p["free_time"] = acts or ["nothing in particular: home, TV, internet"]
        p["favourite_music"] = {"klasyka/jazz": "classical/jazz", "elektronika": "electronic"}.get(r.fav_genre, r.fav_genre)
    return p


# ---------------- per-scenario personal facts computed in code + questions
CAT_FLAG = {"koncert": ("c_concert_other", "concert_other", "concerts like this"), "festiwal": ("c_festival", "festival", "festivals"),
            "teatr": ("c_theatre", "theatre", "the theatre"), "sport": ("sport", None, "sport")}


def habit(r, S):
    """(field, value) of the person's habit for this event category, e.g. goes_to_live_concerts: several times a year.
    Put inside the person: as a separate fact Jev mostly ignored it (src/jev_experiment.py, variant F2)."""
    cat = CAT_FLAG.get(S.category)
    if not cat: return None
    flag, _, what = cat
    freq = getattr(r, "c_concert_freq", False) if flag == "c_concert_other" else False
    v = "several times a year" if freq else "about once or twice a year" if getattr(r, flag, False) else "rarely or never"
    return ("goes_to_" + ("live_concerts" if flag == "c_concert_other" else what.replace("the ", "").replace(" ", "_")), v)


def fit_facts(r, S):
    """How well the event fits this person, computed in code (Jev: no multi-hop inference from raw hobby lists)."""
    f = {}
    if S.genre:
        adj = (S.adjacent or {}).get(r.fav_genre, [])
        f["music_fit"] = "this is their favourite kind of music" if r.fav_genre == S.genre else "close to their taste" if S.genre in adj else "not their kind of music"
    return f


def travel_minutes(r, lat, lon):   # ASSUMPTION: evening door-to-door minutes
    d = km(r.lat, r.lon, lat, lon); return 5 + d * 2.5 if r.has_car else 10 + d * 3.5


TRAVEL_HALFLIFE = 45   # ASSUMPTION: every 45 min of one-way travel beyond 15 min halves the chance of going


def facts_event(r, S, fit=True):
    mins = travel_minutes(r, *S.venue_latlon)
    share = S.price / r.net_income if r.net_income > 0 else None
    cost = ("paid from the household budget" if share is None else
            "trivial for them" if share < .02 else "affordable" if share < .05 else "noticeable" if share < .10 else "expensive for them" if share < .20 else "very expensive for them")
    out = {"travel_time_one_way": f"about {int(round(mins / 5) * 5)} minutes {'by car' if r.has_car else 'by tram/bus'}",
           "ticket_cost_for_this_person": cost + (f" ({share:.0%} of their monthly income)" if share is not None else "")}
    return {**out, **fit_facts(r, S)} if fit else out


def facts_policy(r, S):
    banned = bool(r.has_car) and nn(r.car_age) is not None and (
        (r.car_fuel in ("benzyna", "LPG") and r.car_age > 27) or (r.car_fuel == "diesel" and r.car_age > 17))
    return {"effect_on_their_car": ("their current car would be banned from the city; they would have to replace it" if banned else
                                    "their current car is allowed" if r.has_car else "they have no car")}


def facts_transit(r, S):
    pct = (S.fare_change_pct or 0) / 100
    if r.age >= 70: extra = 0
    elif r.status in ("student", "uczeń") or nn(r.studying) or r.age >= 65: extra = 149 * pct * 0.5   # 50% discount
    else: extra = 149 * pct
    uses = not r.has_car
    return {"uses_public_transport": "daily, it is their main way of getting around" if uses else "occasionally (mostly drives)",
            "extra_cost_per_month_if_monthly_pass": "none (rides free at 70+)" if extra == 0 else
            f"about {int(round(extra, -1))} PLN ({extra / r.net_income:.1%} of monthly income)" if r.net_income > 0 else f"about {int(round(extra, -1))} PLN (no own income)"}


def travel(r, lat, lon):
    d = km(r.lat, r.lon, lat, lon); mins = 5 + d * 2.5 if r.has_car else 10 + d * 3.5   # ASSUMPTION (as for events)
    return f"about {int(round(mins / 5) * 5)} minutes {'by car' if r.has_car else 'by tram/bus'}"


def cost_text(price, inc):
    share = price / inc if inc > 0 else None
    if share is None: return "no own income, someone would have to pay for them"
    w = "trivial for them" if share < .02 else "affordable" if share < .05 else "noticeable" if share < .10 else "expensive for them" if share < .20 else "very expensive for them"
    return f"{w} ({share:.0%} of their monthly income)"


def facts_custom(r, S):
    f = {}
    if S.price_pln: f["cost_for_this_person"] = cost_text(S.price_pln, r.net_income)
    if S.places:
        if len(S.places) == 1: f["travel_time_one_way"] = travel(r, S.places[0].lat, S.places[0].lon)
        else: f["travel_time_one_way_to"] = {pl.name: travel(r, pl.lat, pl.lon) for pl in S.places}
    return f


REASONS = {"price": "money: cost or price", "distance_time": "travel distance or travel time", "schedule": "work, family duties or lack of time",
           "taste": "personal interests and taste", "age_health": "age or health", "car": "owning or using a car",
           "public_transport": "depending on public transport", "environment": "air quality, environment, health of the city", "indifferent": "it doesn't really matter to them"}


def to_typesafe(q):
    from typesafe_sdk import Choice, Noul, Score
    if q.type == "noul":
        return Noul(instructions=q.instructions, criteria={"true": q.yes, "false": q.no}) if q.yes and q.no else Noul(instructions=q.instructions)
    if q.type == "score": return Score(instructions=q.instructions, criteria=q.levels)
    return Choice(instructions=q.instructions, criteria={o.key: o.description for o in q.options})


def questions(S):
    from typesafe_sdk import Choice, Noul, Score
    if S.questions:
        qs = {q.id: to_typesafe(q) for q in S.questions}
        qs.setdefault("reason", Choice(instructions="Which single factor matters most for this person's answers about the scenario?", criteria=REASONS))
        return qs
    reason = Choice(instructions="Which single factor matters most for this person's decision about the scenario?", criteria=REASONS)
    if S.kind == "event":
        # composite scoring (Jev docs; src/jev_experiment.py variant F2): attend = want * afford * free, computed in code.
        # Spread after anchoring: 25-34 vs 65+ 3.3x (GUS ~2.5-3x), concert-goers vs not 2.9x (single question: 1.4-1.6x).
        return {"want": Noul(instructions="This person would genuinely enjoy this specific event: the artist, the music and the setting.",
                             criteria={"true": "they would enjoy it", "false": "it does not appeal to them"}),
                "afford": Noul(instructions="Paying for the ticket is comfortable for this person's budget (their own income or their household's).",
                               criteria={"true": "the ticket fits their budget comfortably", "false": "the ticket would be a strain on their budget"}),
                "free": Noul(instructions="This person is free and has the energy to go out to this venue at this time, given work, family duties and travel time.",
                             criteria={"true": "they can realistically go that evening", "false": "work, family, health or travel makes it hard"}),
                "interest": Score(instructions="How interested is this person in this event, regardless of cost?",
                                  criteria=["not interested at all", "slightly curious", "interested", "very keen"]),
                "reason": reason}
    if S.kind == "policy":
        return {"support": Noul(instructions="This person supports introducing this policy.",
                                criteria={"true": "supports it (somewhat or strongly)", "false": "opposes it or does not care"}),
                "stance": Score(instructions="What is this person's attitude to this policy?",
                                criteria=["strongly against", "rather against", "indifferent", "rather in favour", "strongly in favour"]),
                "reason": reason}
    return {"use_less": Noul(instructions="After this change this person will use Kraków public transport less often than today.",
                             criteria={"true": "they ride less often", "false": "they ride about as often as before"}),
            "impact": Score(instructions="How strongly does this change affect this person's daily life?",
                            criteria=["not at all", "a little", "noticeably", "a lot"]),
            "reason": reason}


GUS_CAL = {"concert_other": "In the last 12 months this person attended at least one live concert other than classical music (pop, rock, jazz, hip-hop...).",
           "theatre": "In the last 12 months this person went to the theatre at least once.",
           "cinema_any": "In the last 12 months this person went to the cinema at least once.",
           "disco_club_dancing": "In the last 12 months this person went out to a club, disco or dance to dance."}


def build(P, S, calibrate, fit=True, leisure=None):
    """Returns list of (id, state) and the questions."""
    if calibrate:
        from typesafe_sdk import Noul
        qs = {k: Noul(instructions=v) for k, v in GUS_CAL.items()}
        return [(int(r.id), {"person": person_state(r, leisure="none"), "context": "a resident of Kraków, Poland, in 2025"}) for r in P.itertuples()], qs
    facts = {"event": facts_event, "policy": facts_policy, "transit": facts_transit, "custom": facts_custom}[S.kind]
    sc = {"title": S.name_en or S.name, "description": S.description_en or S.description}
    if S.kind == "event": sc["day"] = f"{DAY[S.weekday]} evening"
    # events: habit + music fit computed in code replace the raw hobby list (smaller state, better spread; variant F2)
    lv = leisure or ("none" if S.kind == "event" and fit else "all" if S.kind == "event" or getattr(S, "leisure_relevant", False) else "none")
    fx = (lambda r: facts(r, S, fit)) if S.kind == "event" else (lambda r: facts(r, S))
    def person(r):
        p = person_state(r, lv)
        if S.kind == "event" and fit and (h := habit(r, S)): p[h[0]] = h[1]
        return p
    return [(int(r.id), {"person": person(r), "scenario": sc, "personal_situation": fx(r)}) for r in P.itertuples()], questions(S)


async def run(items, qs, cache, conc):
    from typesafe_sdk import AsyncTypeSafeClient, TypeSafeError
    sem = asyncio.Semaphore(conc); done = 0; tokens = 0; errors = {}
    async with AsyncTypeSafeClient(timeout=30.0) as client:
        with cache.open("a") as f:
            async def one(pid, state):
                nonlocal done, tokens
                async with sem:
                    try: r = await client.system_one(state=state, questions=qs)
                    except TypeSafeError as e:
                        errors[type(e).__name__] = errors.get(type(e).__name__, 0) + 1
                        if sum(errors.values()) <= 3: print("  !", pid, type(e).__name__, str(e)[:120])
                        return
                row = {"id": pid, **{k: a.noul for k, a in r.nouls.items()}, **{k: a.score for k, a in r.scores.items()},
                       **{k: a.choice for k, a in r.choices.items()}, **{f"{k}_conf": a.confidence for k, a in r.choices.items()}}
                f.write(json.dumps(row) + "\n"); done += 1; tokens += r.usage.input_tokens if r.usage else 0
                if done % 500 == 0: print(f"  {done}/{len(items)}"); f.flush()
            await asyncio.gather(*(one(pid, s) for pid, s in items))
    print(f"gotowe {done}; input tokens {tokens:,} ≈ {tokens * PRICE_PER_MTOK / 1e6:.3f} $"
          + (f"; błędy {errors} (uruchom ponownie, cache dociągnie brakujące)" if errors else ""))


def main():
    load_env(); args = sys.argv[1:]
    opt = lambda k, d: args[args.index(k) + 1] if k in args else d
    path = args[0]; n = opt("--n", None); conc = int(opt("--concurrency", 20)); calibrate = "--calibrate" in args
    S = load_scenario(path)
    P = pd.read_csv("out/personas.csv"); DS = pd.read_csv("out/districts.csv", index_col="district")
    scale = DS.people.sum() / DS.personas.sum()
    Q = P[P.age >= 15]
    seed = int(opt("--seed", 7))
    if n:   # a sample stands for all personas 15+: scale up the weight
        scale *= len(Q) / int(n); Q = Q.sample(int(n), random_state=seed)
    items, qs = build(Q, S, calibrate)
    name = ("gus_calibration" if calibrate else Path(path).stem) + (f"_s{seed}" if seed != 7 else "")
    qjson = json.dumps({k: v.model_dump(exclude_none=True) for k, v in qs.items()}, sort_keys=True)
    # cache key: questions + the full state of a fixed persona (state shape, wording or scenario changes -> new cache)
    ref = min(items, key=lambda x: x[0])[1]
    tag = hashlib.sha1((qjson + json.dumps(ref, sort_keys=True)).encode()).hexdigest()[:8]
    out = Path("out/jev"); out.mkdir(parents=True, exist_ok=True); cache = out / f"{name}_{tag}.jsonl"
    done = {json.loads(l)["id"] for l in cache.read_text().splitlines()} if cache.exists() else set()
    todo = [(i, s) for i, s in items if i not in done]
    est_tok = np.mean([len(json.dumps(s)) + len(qjson) for _, s in items[:200]]) / 3.5
    print(f"{'kalibracja GUS' if calibrate else S.kind + ': ' + (S.name_en or S.name)}\nOsoby 15+: {len(items)} (każda osobno), w cache {len(done)}, do zapytania {len(todo)}"
          f"\nSzacunek: ~{est_tok:.0f} tokenów na osobę → ~{est_tok * len(todo) / 1e6:.2f} M tokenów ≈ {est_tok * len(todo) * PRICE_PER_MTOK / 1e6:.3f} $")
    if "--dry-run" in args or (todo and not os.environ.get("TYPESAFE_API_KEY")):
        print("\n--- przykładowe zapytanie (POST /v1/systemone) ---")
        print(json.dumps({"model": "jev-latest", "state": items[0][1], "questions": json.loads(qjson)}, indent=1, ensure_ascii=False)[:3500])
        if not os.environ.get("TYPESAFE_API_KEY"): print("\nBrak TYPESAFE_API_KEY w .env; to był dry-run.")
        return
    if todo: asyncio.run(run(todo, qs, cache, conc))

    R = pd.read_json(cache, lines=True).drop_duplicates("id", keep="last")
    A = Q.merge(R, on="id")
    if {"want", "afford", "free"} <= set(A.columns):   # ASSUMPTION: independent factors; travel decay in code (see schema)
        hl = getattr(S, "travel_halflife_min", None) or TRAVEL_HALFLIFE
        mins = np.array([travel_minutes(r, *S.where()) for r in A.itertuples()]) if S.where() else np.zeros(len(A))
        A["travel_min"] = mins.round()
        A["attend"] = A.want * A.afford * A.free * 0.5 ** (np.maximum(0, mins - 15) / hl)
    print(f"\nOdpowiedzi: {len(A)}/{len(Q)} osób")
    if calibrate:
        # expected Kraków rates from GUS tables (same combination as src/bio.py) vs Jev, by age x sex
        A["band"] = A.age.map(bio.band5); rows = []
        for k in GUS_CAL:
            A[f"gus_{k}"] = [bio.culture_rate(bio.CU[k], a, s) for a, s in zip(A.age, A.sex)]
            for (b, s), g in A.groupby(["band", "sex"]):
                rows.append(dict(question=k, age=b, sex=s, n=len(g), jev=g[k].mean(), gus=g[f"gus_{k}"].mean()))
        C = pd.DataFrame(rows); C.round(3).to_csv(out / "gus_calibration_table.csv", index=False)
        print("\nJev vs GUS (Kraków, oczekiwane z tabel GUS), średnio:")
        for k in GUS_CAL:
            g = C[C.question == k]; w = g.n / g.n.sum()
            print(f"  {k:20s} Jev {np.sum(w * g.jev):.3f}  GUS {np.sum(w * g.gus):.3f}  korelacja po grupach wiek×płeć {g.jev.corr(g.gus):.2f}")
        # logit recalibration per question: logit(gus) ≈ a + b·logit(jev), weighted by group size.
        # b > 1 means Jev compresses towards 0.5. Saved for reuse; only valid for these survey-style questions.
        lg = lambda p: np.log(np.clip(p, .01, .99) / (1 - np.clip(p, .01, .99))); cal = {}
        old = json.load(open(out / "calibration.json")) if (out / "calibration.json").exists() else {}
        if old:   # out-of-sample check: coefficients fitted on a previous sample, applied to this one
            print("\nTest poza próbką (współczynniki z poprzedniej kalibracji, n={}):".format(next(iter(old.values()))["n"]))
            for k, g in C.groupby("question"):
                if k not in old: continue
                fit = 1 / (1 + np.exp(-(old[k]["a"] + old[k]["b"] * lg(g.jev.values))))
                print(f"  {k:20s} błąd surowy {np.average(abs(g.jev - g.gus), weights=g.n):.1%} → po korekcie {np.average(abs(fit - g.gus), weights=g.n):.1%}")
        for k, g in C.groupby("question"):
            x, y, w = lg(g.jev.values), lg(g.gus.values), np.sqrt(g.n.values)
            a, b = np.linalg.lstsq(np.c_[np.ones_like(x), x] * w[:, None], y * w, rcond=None)[0]
            fit = 1 / (1 + np.exp(-(a + b * x)))
            cal[k] = dict(a=round(a, 3), b=round(b, 3), mae_before=round(float(np.average(abs(g.jev - g.gus), weights=g.n)), 4),
                          mae_after=round(float(np.average(abs(fit - g.gus), weights=g.n)), 4), corr=round(g.jev.corr(g.gus), 3), n=int(g.n.sum()))
        json.dump(cal, open(out / "calibration.json", "w"), indent=1)
        print("\nKorekta logit (a, b) i średni błąd w grupach przed → po:")
        for k, c in cal.items(): print(f"  {k:20s} a={c['a']:5.2f} b={c['b']:4.2f}  {c['mae_before']:.1%} → {c['mae_after']:.1%}")
        print("\nSzczegóły → out/jev/gus_calibration_table.csv, out/jev/calibration.json"); return

    if S.questions:
        key = S.main_question or next((q.id for q in S.questions if q.type == "noul"), S.questions[0].id)
        print(f"\nPytania ({len(S.questions)}):")
        for q in S.questions:
            if q.type == "choice":
                print(f"  {q.id} (choice): " + ", ".join(f"{k} {v:.0%}" for k, v in A[q.id].value_counts(normalize=True).items()))
            else: print(f"  {q.id} ({q.type}): średnio {A[q.id].mean():.2f}" + (f" (skala 0–{len(q.levels) - 1})" if q.type == "score" else ""))
        if next(q for q in S.questions if q.id == key).type == "choice":   # map the share of the most popular option
            top = A[key].value_counts().index[0]; A[f"{key}={top}"] = (A[key] == top).astype(float); key = f"{key}={top}"
    else:
        key = {"event": "attend", "policy": "support", "transit": "use_less"}[S.kind]
    print(f"\nŚrednie P({key}) = {A[key].mean():.3f} → ok. {int(A[key].sum() * scale // 100 * 100):,} osób (15+, aglomeracja), poziom surowy Jev")
    anchor = int(opt("--anchor", 0)) or getattr(S, "anchor_total", None)
    if anchor and A[key].between(0, 1).all():
        lg = np.log(A[key].clip(1e-4, 1 - 1e-4) / (1 - A[key].clip(1e-4, 1 - 1e-4))); lo, hi = -15.0, 15.0
        for _ in range(60):   # one constant c so that sum(sigmoid(logit p + c)) * scale = anchor
            c = (lo + hi) / 2; tot = (1 / (1 + np.exp(-(lg + c)))).sum() * scale; lo, hi = (c, hi) if tot < anchor else (lo, c)
        A[key + "_raw"] = A[key]; A[key] = 1 / (1 + np.exp(-(lg + c)))
        print(f"Zakotwiczone do {anchor:,} osób: przesunięcie logit c = {c:.2f}, średnie P = {A[key].mean():.4f}. Poniżej wartości zakotwiczone.")
    print("Główny powód (udział):"); print(A.reason.value_counts(normalize=True).round(3).to_string())
    by = A.groupby("district").agg(p=(key, "mean"), personas=("id", "size"))
    by["people_15p"] = (by.personas * scale).round(-2); by["expected"] = (by.p * by.people_15p).round(-2)
    by = by.sort_values("p", ascending=False); by.round(3).to_csv(out / f"{name}_by_district.csv")
    if S.questions:   # every question summarised per district
        for q in S.questions:
            if q.type == "choice": by = by.join(pd.crosstab(A.district, A[q.id], normalize="index").add_prefix(f"{q.id}=").round(3))
            elif q.id != key: by = by.join(A.groupby("district")[q.id].mean().round(3).rename(q.id))
        by.round(3).to_csv(out / f"{name}_by_district.csv")
    print(f"\nWg dzielnic (P({key})):"); print(by.round(3).to_string())
    print("\nWg wieku:"); print(A.groupby(pd.cut(A.age, [14, 24, 34, 49, 64, 120]), observed=True)[key].mean().round(3).to_string())
    if S.kind == "policy" and "effect_on_their_car" in json.dumps(items[0][1]):
        A["banned"] = [facts_policy(r, S)["effect_on_their_car"].startswith("their current car would be banned") for r in A.itertuples()]
        print("\nPoparcie: auto zakazane vs reszta:"); print(A.groupby("banned")[key].mean().round(3).to_string())
    if S.kind == "event" and Path("out/sim_heuristic.csv").exists():
        H = pd.read_csv("out/sim_heuristic.csv")[["id", "p_attend"]]; A = A.merge(H, on="id")
        print(f"\nHeurystyka na tych samych osobach: {int(A.p_attend.sum() * scale // 100 * 100):,} (korelacja z Jev {A[key].corr(A.p_attend):.2f})")
    A.to_csv(out / f"{name}_personas.csv", index=False)


if __name__ == "__main__":
    main()
