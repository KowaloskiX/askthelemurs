"""Event planner: 'where is the best place to hold X?'
1. extract(): Jev reads the organiser's Polish message and fills closed slots (category, genre, day, time, age, scale,
   setting), each with a 'not stated' option; price by regex. missing() lists what the agent should still ask.
2. run(): Jev asks a sample of personas 3 location-independent questions (want / afford / free), P = product.
3. rank(): for every candidate venue, expected audience = sum(P x travel decay) computed in code (Jev ignores travel
   time in yes/no questions), so one Jev pass ranks all venues.
"""
import asyncio, hashlib, json, os, re, sys
from functools import lru_cache
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jev_sim
from jev_sim import person_state, cost_text, travel_minutes, TRAVEL_HALFLIFE, km

NS = "not_stated"
SLOTS = {   # slot -> (instruction for Jev, {option: description}); Polish labels for the UI in LABELS
    "category": ("What kind of event does the organiser want to hold?", {
        "koncert": "a concert", "festiwal": "a music festival", "klub": "a club night / party", "teatr": "theatre, stand-up or cabaret",
        "sport": "a sports event or match", "kino": "a film screening / open-air cinema", "targi": "a fair, market or food event",
        "piknik": "a family picnic or outdoor family event", "warsztaty": "a workshop, talk or meetup", NS: "not stated"}),
    "genre": ("What music genre is the event, if it is music?", {
        "pop": "pop", "rock": "rock", "hip-hop": "hip-hop / rap", "elektronika": "electronic / techno / house",
        "klasyka/jazz": "classical or jazz", "disco polo": "disco polo", NS: "not stated or not music"}),
    "day": ("On which day of the week is the event?", {
        "Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday", "Fri": "Friday", "Sat": "Saturday", "Sun": "Sunday", NS: "not stated"}),
    "time": ("At what time of day is the event?", {"morning": "morning", "afternoon": "afternoon", "evening": "evening or night", NS: "not stated"}),
    "age": ("Which age group is the event aimed at?", {
        "15-19": "teenagers", "18-30": "young adults / students", "25-40": "adults 25-40", "40-60": "middle-aged adults",
        "60+": "seniors", "all": "everyone / families", NS: "not stated"}),
    "scale": ("How big is the event (expected audience)?", {
        "small": "small, under 300 people", "club": "club size, 300 to 2,000 people", "medium": "medium, 2,000 to 8,000 people",
        "large": "large, over 8,000 people", NS: "not stated"}),
    "setting": ("Is the event indoors or outdoors?", {"indoor": "indoors", "outdoor": "outdoors / open-air", NS: "not stated"}),
}
LABELS = {
    "category": {"koncert": "koncert", "festiwal": "festiwal", "klub": "impreza klubowa", "teatr": "teatr / stand-up", "sport": "sport",
                 "kino": "kino / seans", "targi": "targi / jarmark / food", "piknik": "piknik rodzinny", "warsztaty": "warsztaty / spotkanie"},
    "genre": {"pop": "pop", "rock": "rock", "hip-hop": "hip-hop", "elektronika": "elektronika", "klasyka/jazz": "klasyka / jazz", "disco polo": "disco polo", "none": "bez muzyki / inne"},
    "day": {"Mon": "poniedziałek", "Tue": "wtorek", "Wed": "środa", "Thu": "czwartek", "Fri": "piątek", "Sat": "sobota", "Sun": "niedziela"},
    "time": {"morning": "rano", "afternoon": "popołudnie", "evening": "wieczór"},
    "age": {"15-19": "nastolatki", "18-30": "18–30", "25-40": "25–40", "40-60": "40–60", "60+": "60+", "all": "wszyscy / rodziny"},
    "scale": {"small": "do 300 osób", "club": "300–2000", "medium": "2000–8000", "large": "8000+"},
    "setting": {"indoor": "w środku", "outdoor": "plener"},
}
ASK = {"category": "Jakie to wydarzenie?", "genre": "Jaki gatunek muzyki?", "price": "Ile kosztuje bilet (zł)? 0 = za darmo.",
       "day": "W jaki dzień tygodnia?", "time": "O jakiej porze?", "age": "Do kogo jest skierowane?", "scale": "Ilu ludzi się spodziewasz?",
       "setting": "W środku czy w plenerze?"}
MUSIC = {"koncert", "festiwal", "klub"}


# neighbourhoods people name in messages; coordinates approximate (ASSUMPTION). Districts and venues are added at runtime.
AREAS = {"Kazimierz": (50.0510, 19.9450), "Zabłocie": (50.0480, 19.9590), "Ruczaj": (50.0230, 19.9080), "Czarna Wieś": (50.0700, 19.9150),
         "Kleparz": (50.0690, 19.9420), "Salwator": (50.0540, 19.9050), "Wola Justowska": (50.0640, 19.8650), "Azory": (50.0870, 19.9090),
         "Kurdwanów": (50.0150, 19.9620), "Kliny": (50.0090, 19.9300), "Prokocim": (50.0190, 20.0070), "Bieżanów": (50.0200, 20.0350),
         "Krowodrza Górka": (50.0880, 19.9310), "Zakrzówek": (50.0400, 19.9150), "centrum / Stare Miasto": (50.0617, 19.9373),
         "Nowa Huta (centrum)": (50.0722, 20.0381), "Bulwary Wiślane": (50.0545, 19.9330)}


def gazetteer(district_centres):
    g = {f"v{i}": {"name": v["name"], "lat": v["latlon"][0], "lon": v["latlon"][1], "kind": "venue"} for i, v in enumerate(VENUES)}
    g.update({f"d{i}": {"name": d["name"], "lat": d["lat"], "lon": d["lon"], "kind": "area"} for i, d in enumerate(district_centres)})
    g.update({f"a{i}": {"name": n, "lat": la, "lon": lo, "kind": "area"} for i, (n, (la, lo)) in enumerate(AREAS.items())})
    return g


def required(s):
    req = ["category", "price", "day", "time", "age", "scale", "setting"]
    if s.get("category") in MUSIC: req.insert(1, "genre")
    return req


def parse_price(text):
    t = text.lower()
    if re.search(r"\b(za darmo|darmow\w*|bezpłatn\w*|wstęp wolny|free)\b", t): return 0.0
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(zł|zl|pln|złotych)", t)
    return float(m.group(1).replace(",", ".")) if m else None


async def extract(text, known=None, districts=None, current=None):
    """Fill slots (and a named place) from the organiser's message in one Jev call. Known slots are kept;
    answers are kept only when confident. With `current` (summary of the event discussed so far) Jev also judges
    whether the message starts a different event; then the caller resets the slots."""
    from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul
    s = dict(known or {}); s.pop("_new_event", None)
    if s.get("price") is None and (p := parse_price(text)) is not None: s["price"] = p
    import calendar_agent   # a concrete date ('22.05', '15 maja') also fixes the weekday
    if (d := calendar_agent.parse_date(text)): s["date"] = d; s["day"] = __import__("datetime").date.fromisoformat(d).strftime("%a")
    qs = {k: Choice(instructions=i, criteria=c) for k, (i, c) in SLOTS.items() if k not in s}
    gz = gazetteer(districts or [])
    qs["place"] = Choice(instructions="Which specific place, venue, district or neighbourhood in Kraków does the organiser name as the location? Choose 'none' if no location is named.",
                         criteria={**{k: v["name"] for k, v in gz.items()}, "none": "no location named"})
    state = {"organiser_message_in_polish": text, "city": "Kraków, Poland"}
    async with AsyncTypeSafeClient(timeout=30.0) as client:
        # two calls: slots must be read from the message alone (with the previous event in the state, Jev filled slots
        # from it), the 'new event?' judgement needs both
        calls = [client.system_one(state=state, questions=qs)]
        if current:
            calls.append(client.system_one(state={"event_discussed_so_far": current, "new_message_in_polish": text}, questions={
                "new_event": Noul(instructions="The new message describes a different kind of event than the one discussed so far (not just changing a detail such as day, price or size).",
                                  criteria={"true": "a new, different event", "false": "the same event, possibly with changed details"})}))
        res = await asyncio.gather(*calls)
    r = res[0]
    for k, a in r.choices.items():
        if k == "place":
            if a.choice != "none" and a.confidence >= 0.6: s["place"] = gz[a.choice]
        elif a.choice != NS and a.confidence >= 0.5: s[k] = a.choice
    if current and res[1].nouls["new_event"].noul > 0.6: s["_new_event"] = True
    if s.get("category") and s["category"] not in MUSIC: s.setdefault("genre", "none")
    s["text"] = (s.get("text", "") + " " + text).strip()
    return s


def missing(s):
    out = []
    for k in required(s):
        if s.get(k) is None:
            out.append({"slot": k, "question": ASK[k], "options": [{"value": v, "label": l} for v, l in LABELS[k].items()] if k in LABELS else None})
    return out


# ---------------- day of week and time of day from data (Jev barely distinguishes them; tested Mon vs Sat: 1.09x)
# HETUS 2020, Poland (GUS): share of people taking part in 'Entertainment and culture' that day, by sex;
# share of people in leisure (excl. TV) per 10-min slot, by sex. ASSUMPTION: the chance of going to an event scales
# with that participation. Sunday covers the whole day (museums, daytime cinema), so it may flatter Sunday evenings.
HETUS = json.load(open("data/raw/hetus_pl_2020.json"))
DAY_TYPE = {"Mon": "Monday to Thursday", "Tue": "Monday to Thursday", "Wed": "Monday to Thursday", "Thu": "Monday to Thursday",
            "Fri": "Friday", "Sat": "Saturday", "Sun": "Sunday"}
TIME_WINDOW = {"morning": (9, 12), "afternoon": (14, 18), "evening": (18, 22)}


def _hour(lbl):   # "From 18:10 to 18:19" -> 18.17
    h, m = map(int, lbl.split()[1].split(":")); return h + m / 60


DAY_PROFILE = {"sport": "Socialising with others (visits, celebrations and other)"}   # a match is a social outing; others: culture


@lru_cache(maxsize=None)
def day_factor(sex, day, cat=None):
    act = DAY_PROFILE.get(cat, "Entertainment and culture")
    w = HETUS["week"]["M" if sex == "M" else "K"]; v = {d: w[t][act] for d, t in DAY_TYPE.items()}
    return v.get(day, max(v.values())) / max(v.values())


@lru_cache(maxsize=None)   # depends only on sex and time window; was re-parsing 144 HETUS slots per person
def time_factor(sex, time):
    h = HETUS["hours"]["M" if sex == "M" else "K"]
    rate = {k: sum(v for lbl, v in h.items() if lo <= _hour(lbl) < hi) / ((hi - lo) * 6) for k, (lo, hi) in TIME_WINDOW.items()}
    return rate.get(time, max(rate.values())) / max(rate.values())


def when_factor(A, day, time, cat=None):
    f = {s: day_factor(s, day, cat) * time_factor(s, time) for s in ("M", "K")}
    return A.sex.map(lambda x: f["M" if x == "M" else "K"]).values.astype(float)


def day_rates():
    return {d: HETUS["week"]["total"][t]["Entertainment and culture"] for d, t in DAY_TYPE.items()}


# ---------------- venues
VENUES = json.load(open("viz/venues.json"))
SCALE_CAP = {"small": (0, 300), "club": (300, 2000), "medium": (2000, 8000), "large": (8000, 1e9)}


def candidates(s, district_centres):
    """Venues that fit the event: category, indoor/outdoor, capacity at least the expected size and not absurdly larger
    (open spaces like Błonia are always allowed outdoors). Small events also get one generic spot per district."""
    lo, hi = SCALE_CAP.get(s.get("scale"), (0, 1e9)); cat, setting = s.get("category"), s.get("setting")
    ok = lambda v: ((not cat or cat in v.get("for", [])) and (not setting or v["setting"] == setting)
                    and v["capacity"] >= lo and (v["capacity"] <= max(hi * 3, 3000) or (v["setting"] == "outdoor" and v["capacity"] >= 20000)))
    out = [dict(v, kind="venue") for v in VENUES if ok(v)]
    if s.get("scale") in ("small", "club", None) and cat not in ("sport",):
        out += [{"name": d["name"], "latlon": [d["lat"], d["lon"]], "capacity": 300, "kind": "district",
                 "setting": setting or "indoor"} for d in district_centres]
    pl = s.get("place")
    if pl and pl["kind"] == "area":   # "somewhere in Kazimierz": venues within ~3 km (+ a generic spot there for small events)
        out = [v for v in out if km(v["latlon"][0], v["latlon"][1], pl["lat"], pl["lon"]) <= 3.0]
        if s.get("scale") in ("small", "club", None): out.append({"name": f"{pl['name']} (dowolny lokal)", "latlon": [pl["lat"], pl["lon"]], "capacity": 300, "kind": "district", "setting": setting or "indoor"})
    if pl and pl["kind"] == "venue":  # named venue: always in the ranking, marked
        v = next(dict(x, kind="venue") for x in VENUES if x["name"] == pl["name"])
        out = [x for x in out if x["name"] != v["name"]] + [dict(v, pinned=True)]
    hi_cap = SCALE_CAP.get(s.get("scale"), (0, 1e9))[1]
    for v in out: v["fit"] = "za duże na tę skalę" if v["capacity"] > hi_cap * 3 and v["kind"] == "venue" else ("za małe" if v["capacity"] < SCALE_CAP.get(s.get("scale"), (0, 0))[0] else "pasuje")
    return out


# ---------------- Jev pass
DAY_EN = jev_sim.DAY
CAT_TO_FLAG = {"koncert": "koncert", "festiwal": "festiwal", "teatr": "teatr", "sport": "sport"}


def event_text(s):
    cat = SLOTS["category"][1].get(s.get("category"), "an event")
    genre = f" ({SLOTS['genre'][1][s['genre']]})" if s.get("genre") in SLOTS["genre"][1] and s["genre"] != NS else ""
    price = "free entry" if not s.get("price") else f"tickets {int(s['price'])} PLN"
    age = SLOTS["age"][1].get(s.get("age"), "")
    # no day / time here: they come from HETUS data in code, so one Jev pass serves every day and time
    txt = (f"{cat[0].upper() + cat[1:]}{genre} in Kraków, "
           f"{SLOTS['setting'][1].get(s.get('setting'), '')}, {SLOTS['scale'][1].get(s.get('scale'), '')}; {price}. Aimed at: {age}.")
    m = s.get("meta")   # from src/event_agent.py: who/what headlines it and who it is for
    if m: txt += f" Headliner: {m['headliner']} ({POP_TEXT.get(m['headliner_popularity'], '')}). Who it is for: {m['audience']}"
    return txt


POP_TEXT = {"none": "no headliner", "local": "known locally", "national": "known nationally", "national_top": "one of the most popular in Poland",
            "international_star": "an international star"}


def _sc(s):
    class _S: pass
    sc = _S(); sc.category = CAT_TO_FLAG.get(s.get("category")); sc.genre = s["genre"] if s.get("genre") in ("pop", "rock", "hip-hop", "elektronika", "klasyka/jazz", "disco polo") else None
    sc.adjacent = {"hip-hop": ["pop"], "rock": ["pop"], "elektronika": ["pop"], "disco polo": ["pop"], "pop": ["rock", "hip-hop", "elektronika"]}
    return sc


# Archetypes (as in src/policy.py): Jev's answer for an event depends on these traits only (travel is computed in code per venue)
EVENT_KEY = ["band", "sex", "status", "kids", "inc", "habit", "fit"]


def arch_frame(s):
    P = pd.read_csv("out/personas.csv"); K = P[P.age >= 15].copy(); sc = _sc(s)
    K["band"] = pd.cut(K.age, [14, 19, 24, 34, 49, 64, 74, 120]).astype(str)
    K["inc"] = pd.qcut(K.net_income.rank(method="first"), 3, labels=["lo", "mid", "hi"]).astype(str)
    K["kids"] = K.children.fillna(0) > 0
    K["habit"] = [(jev_sim.habit(r, sc) or (None, "none"))[1] for r in K.itertuples()]
    K["fit"] = [jev_sim.fit_facts(r, sc).get("music_fit", "none") if sc.genre else "none" for r in K.itertuples()]
    return K


def state_for(r, s):
    sc = _sc(s)
    p = person_state(r, "none")
    if (h := jev_sim.habit(r, sc)): p[h[0]] = h[1]
    if r.net_income <= 0: p["monthly_net_income_pln"] = jev_sim.no_income_text(r)
    ps = {"ticket_cost_for_this_person": "free entry" if not s.get("price") else ("paid from the household budget" if r.net_income <= 0 else cost_text(s["price"], r.net_income))}
    if sc.genre: ps.update(jev_sim.fit_facts(r, sc))
    # the organiser's raw message is not sent: it repeats the day/time (handled in code) and everything else is in the slots
    return {"person": p, "event": {"description": event_text(s)}, "personal_situation": ps}


def questions(s):
    from typesafe_sdk import Choice, Noul
    # stricter than 'would enjoy': a free family picnic 'appeals' to 94% and the differences between groups vanish at the ceiling
    q = {"want": Noul(instructions="This person would actually want to spend their time at this event: it appeals to them, and if they live with children or a partner, to their household too.",
                      criteria={"true": "they would want to go", "false": "it does not appeal to them enough to go"}),
         "free": Noul(instructions="This person can realistically find the time and energy for an outing like this, given work, family duties and health.",
                      criteria={"true": "they can realistically go", "false": "work, family or health makes it hard"}),
         "reason": Choice(instructions="Which single factor matters most for this person's decision about this event?", criteria=jev_sim.REASONS)}
    if s.get("price"): q["afford"] = Noul(instructions="Paying for the ticket is comfortable for this person's budget (their own income or their household's).",
                                          criteria={"true": "the ticket fits their budget comfortably", "false": "the ticket would be a strain on their budget"})
    return q


def spec_key(s):
    """Cache key: event spec + exact question wording (changing a question must not reuse old answers)."""
    spec = {k: s.get(k) for k in ["category", "genre", "price", "age", "scale", "setting"]}   # day/time applied in code
    if s.get("meta"): spec["meta"] = {k: s["meta"][k] for k in ("headliner", "headliner_popularity", "audience")}
    qs = {k: v.model_dump(exclude_none=True) for k, v in questions(s).items()}
    return hashlib.sha1(json.dumps([spec, qs, jev_sim.pop_version()], sort_keys=True).encode()).hexdigest()[:10]


async def run(s, n=1200, progress=None, seed=7, live=None, archetypes=False):
    """archetypes=False: a random sample of n residents 15+ asked one by one. archetypes=True: every resident 15+, one Jev
    call per archetype (EVENT_KEY; src/archetypes_event.py: 912 archetypes, p_base r = 0.93 vs 1200 individual answers).
    live: async callback for the UI map: {'type': 'people'} once, then {'type': 'answers', rows: [[id, p, reason]]}."""
    from typesafe_sdk import AsyncTypeSafeClient, TypeSafeError
    cache = Path("out/planner"); cache.mkdir(parents=True, exist_ok=True)
    if archetypes:
        Q = arch_frame(s); Q["arch"] = Q.groupby(EVENT_KEY, sort=False).ngroup(); rng = np.random.default_rng(seed)
        reps = Q.groupby("arch").id.apply(lambda x: int(rng.choice(x.values))); Q["rep"] = Q.arch.map(reps)
        ask = Q[Q.id.isin(set(reps.values))]; cache = cache / f"{spec_key(s)}_arch_{seed}.jsonl"; members = Q.groupby("rep").id.apply(list).to_dict()
    else:
        P = pd.read_csv("out/personas.csv"); Q = P[P.age >= 15].sample(n, random_state=seed); Q = Q.assign(rep=Q.id); ask = Q
        cache = cache / f"{spec_key(s)}_{n}_{seed}.jsonl"; members = {i: [i] for i in Q.id}
    done = {json.loads(l)["id"]: json.loads(l) for l in cache.read_text().splitlines()} if cache.exists() else {}
    qs = questions(s); todo = [r for r in ask.itertuples() if r.id not in done]; sem = asyncio.Semaphore(jev_sim.JEV_CONCURRENCY); k = [len(ask) - len(todo)]
    pb = lambda x: x["want"] * x["free"] * x.get("afford", 1)
    rows_of = lambda i, x: [[m, round(pb(x), 3), x["reason"]] for m in members.get(i, [])]
    if live:
        import live as lv
        await live({"type": "people", "people": lv.people(Q), "archetypes": int(len(ask)) if archetypes else None})
        if done: await live({"type": "answers", "rows": [row for i, x in done.items() if i in members for row in rows_of(i, x)]})
    async with AsyncTypeSafeClient(timeout=30.0) as client:
        with cache.open("a") as f:
            async def one(r):
                async with sem:
                    try: a = await client.system_one(state=state_for(r, s), questions=qs)
                    except TypeSafeError: return
                row = {"id": int(r.id), **{q: v.noul for q, v in a.nouls.items()}, "reason": a.choices["reason"].choice}
                done[row["id"]] = row; f.write(json.dumps(row) + "\n"); k[0] += 1
                if live: await live({"type": "answers", "rows": rows_of(row["id"], row)})
                if progress and k[0] % 10 == 0: await progress(k[0], len(ask))
            await asyncio.gather(*(one(r) for r in todo))
    R = pd.DataFrame([x for i, x in done.items() if i in members]).rename(columns={"id": "rep"})
    R["p_base"] = R.want * R.free * (R.afford if "afford" in R else 1)   # Jev, independent of day and time
    A = Q.merge(R, on="rep"); A["p"] = A.p_base * when_factor(A, s.get("day"), s.get("time"), s.get("category"))
    A.attrs["archetypes"] = int(len(ask)) if archetypes else None
    return A


def minutes(A, lat, lon):
    """Door-to-door minutes per resident: R5 matrices on the real MPK timetable and OSM roads (src/travel.py), falling back
    to the old ASSUMPTION formula (car 5 + 2.5 min/km, tram/bus 10 + 3.5 min/km) where no matrix value exists."""
    import travel
    return travel.minutes(A, lat, lon)


# ASSUMPTION: people travel further for bigger events (minutes of one-way travel beyond 15 min that halve the chance)
SCALE_HALFLIFE = {"small": 25, "club": 35, "medium": 45, "large": 60}


def halflife(s): return SCALE_HALFLIFE.get(s.get("scale"), TRAVEL_HALFLIFE)


def rank(A, s, districts):
    """Expected audience per candidate venue, relative index (best = 100). Absolute level needs an anchor (see CONTEXT)."""
    DS = pd.read_csv("out/districts.csv", index_col="district"); w = DS.people.sum() / DS.personas.sum() * (P15 := (pd.read_csv("out/personas.csv").age >= 15).sum()) / len(A)
    rows = []
    for v in candidates(s, districts):
        mins = minutes(A, *v["latlon"])
        p = A.p.values * 0.5 ** (np.maximum(0, mins - 15) / halflife(s))
        rows.append({**v, "score": float(p.sum() * w), "within_20min": float(p[mins <= 20].sum() / max(p.sum(), 1e-9))})
    rows.sort(key=lambda x: -x["score"]); top = rows[0]["score"] if rows else 1
    for x in rows: x["index"] = round(100 * x["score"] / top)
    return rows


# ---------------- who comes and what stops them (computed from the answers, no extra API calls)
def segment(r):
    """Life-stage segment, rule-based so names are stable and readable."""
    if r.origin == "student_spoza": return "studenci spoza Krakowa"
    if r.status == "uczeń": return "uczniowie"
    if r.status == "student" or (r.age < 26 and r.studying is True): return "studenci z Krakowa"
    if r.status == "emeryt" or r.age >= 65: return "seniorzy i emeryci"
    if (r.children or 0) > 0: return "rodzice z dziećmi"
    if r.status in ("niepracujący", "bezrobotny"): return "niepracujący"
    return "młodzi pracujący bez dzieci" if r.age < 35 else "pracujący 35+ bez dzieci"


AGE_RANGE = {"15-19": (15, 19), "18-30": (18, 30), "25-40": (25, 40), "40-60": (40, 60), "60+": (60, 120)}


def barriers(A, s):
    """Only what Jev actually distinguishes, expressed as shares of PEOPLE (not 'lost interest', which mostly measured
    Jev's baseline caution: 'free' sits at 0.6-0.8 for everyone and does not change with the day of the week).
    - appeal: share who want to go, overall and in the target age group
    - price: among people who want to go, share for whom the ticket would strain the budget (afford < 0.5)
    - evening availability: segments whose 'free' is clearly below average (a property of the person, not of the date)"""
    out = {"want_share": float((A.want >= 0.5).mean())}
    if s.get("age") in AGE_RANGE:
        lo, hi = AGE_RANGE[s["age"]]; T = A[A.age.between(lo, hi)]
        if len(T) >= 30: out["want_target"] = float((T.want >= 0.5).mean())
    W = A[A.want >= 0.5]
    if "afford" in A and s.get("price") and len(W) >= 15: out["price_strain"] = float((W.afford < 0.5).mean())
    seg = pd.Series([segment(r) for r in A.itertuples()], index=A.index)
    f = A.free.groupby(seg).agg(["mean", "size"]); f = f[f["size"] >= 20]; m = A.free.mean()
    low = f[f["mean"] <= 0.8 * m].sort_values("mean")
    if len(low): out["busy"] = [{"name": k, "ratio": round(float(r["mean"] / m), 2)} for k, r in low.head(2).iterrows()]
    return out
