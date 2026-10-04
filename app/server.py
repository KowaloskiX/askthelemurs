"""Kraków Sim: ask the simulated residents. The router (src/router.py) picks the mode from the first message:
event  -> planner ('gdzie i kiedy zorganizować X?'), policy -> consultation ('jak mieszkańcy przyjmą decyzję?').
Run from the repo root:  python3 -m uvicorn app.server:app --port 8000   then open http://localhost:8000
Needs TYPESAFE_API_KEY in .env and out/personas.csv (make personas).
"""
import os
os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "__all__")   # broken logfire plugin in some environments
import asyncio, json, sys
from pathlib import Path
import numpy as np, pandas as pd
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.chdir(Path(__file__).resolve().parent.parent)
import jev_sim, planner, event_agent, router, policy, gus_facts, data_agent, calendar_agent, followups
from jev_sim import travel_minutes

jev_sim.load_env()
app = FastAPI()
from fastapi.middleware.cors import CORSMiddleware
ORIGINS = os.environ.get("KS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in ORIGINS if o.strip()], allow_methods=["*"], allow_headers=["*"])

# A public demo pays for every question (OpenAI + Jev, ~6-15 cents), so cap questions per visitor and per day.
# ASSUMPTION: 30 questions per IP per hour and 500 per day in total are enough for a hackathon demo and its judges.
import time
from collections import defaultdict, deque
from fastapi import Request
from fastapi.responses import JSONResponse
LIMIT_IP, LIMIT_DAY = int(os.environ.get("KS_LIMIT_IP", "30")), int(os.environ.get("KS_LIMIT_DAY", "500"))
_hits, _day = defaultdict(deque), {"date": "", "n": 0}
LIMIT_MSG = "Limit pytań w demie wyczerpany. Spróbuj za jakiś czas."


def over_limit(ip):
    now, today = time.time(), time.strftime("%Y-%m-%d")
    if _day["date"] != today: _day.update(date=today, n=0)
    q = _hits[ip]
    while q and now - q[0] > 3600: q.popleft()
    if len(q) >= LIMIT_IP or _day["n"] >= LIMIT_DAY: return True
    q.append(now); _day["n"] += 1; return False


@app.middleware("http")
async def limit(request: Request, call_next):
    # the run is where the money goes (Jev for every archetype, the data agent loop); chat turns are one cheap call each
    paid = request.method == "GET" and request.url.path == "/api/run"
    if paid:
        ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "")).split(",")[0].strip()
        if over_limit(ip):
            body = f"data: {json.dumps({'type': 'error', 'message': LIMIT_MSG}, ensure_ascii=False)}\n\n"
            return StreamingResponse(iter([body]), media_type="text/event-stream")
    return await call_next(request)
OUTSIDE = "poza Krakowem"
GEO = json.load(open("data/geo/krakow_districts.geojson"))["features"]
DS = pd.read_csv("out/districts.csv")
ring = lambda g: [[round(x, 4), round(y, 4)] for x, y in (g["coordinates"][0] if g["type"] == "Polygon" else g["coordinates"][0][0])]
DISTRICTS = [{"name": f["properties"]["name"], "ring": ring(f["geometry"]),
              "lat": float(DS.set_index("district").lat.get(f["properties"]["name"], np.nan)),
              "lon": float(DS.set_index("district").lon.get(f["properties"]["name"], np.nan)),
              "people": int(DS.set_index("district").people.get(f["properties"]["name"], 0))}
             for f in GEO if f["properties"]["kind"] == "district"]
CITY = next(ring(f["geometry"]) for f in GEO if f["properties"]["kind"] == "city")
AGE_BANDS = [(15, 24), (25, 34), (35, 49), (50, 64), (65, 120)]


class Chat(BaseModel):
    text: str = ""
    slots: dict = {}
    slot: str | None = None      # answer to a follow-up question
    value: str | float | None = None


def summary(s):
    L = planner.LABELS; parts = []
    for k in ["category", "genre", "day", "time", "age", "scale", "setting"]:
        if s.get(k) and s[k] in L.get(k, {}): parts.append(L[k][s[k]])
    if s.get("price") is not None: parts.append("za darmo" if not s["price"] else f"{int(s['price'])} zł")
    if s.get("date"): parts.append(".".join(reversed(s["date"].split("-"))))
    if s.get("place"): parts.append(("📍 " if s["place"]["kind"] == "venue" else "okolice: ") + s["place"]["name"])
    return " · ".join(parts)


def pills(s):
    """Current settings as editable pills for the UI."""
    L = planner.LABELS; out = []
    for k in planner.required(s):
        if k == "price": out.append({"slot": k, "label": "cena", "value": None if s.get(k) is None else ("za darmo" if not s[k] else f"{int(s[k])} zł")})
        else: out.append({"slot": k, "label": {"category": "rodzaj", "genre": "gatunek", "day": "dzień", "time": "pora", "age": "dla kogo", "scale": "skala", "setting": "miejsce"}[k],
                          "value": L[k].get(s.get(k)) if s.get(k) else None})
    if s.get("place"): out.append({"slot": "place", "label": "lokalizacja", "value": s["place"]["name"]})
    return out


@app.get("/")
def index(): return FileResponse("app/static/index.html")


@app.get("/api/geo")
def geo(): return {"districts": DISTRICTS, "city": CITY}


NOT_YET = {"business": "Tryb „gdzie otworzyć lokal / usługę” jeszcze powstaje. Na razie umiem: zaplanować wydarzenie (gdzie, kiedy, ile osób) "
                       "i sprawdzić, jak mieszkańcy przyjmą decyzję miasta.",
           "other": "Umiem dwie rzeczy: zaplanować wydarzenie (gdzie i kiedy, ile osób przyjdzie) albo sprawdzić, jak mieszkańcy przyjmą decyzję miasta "
                    "(zakaz, opłata, inwestycja, zmiana w komunikacji). Opisz jedno albo drugie."}


def mode_summary(s):
    if s.get("mode") == "policy" and s.get("spec"): return s["spec"]["title_pl"]
    if s.get("mode") == "agent": return "data analysis: " + s.get("text", "")[:200]
    if s.get("mode") == "event" and s.get("category"): return summary(s)
    return None


@app.on_event("startup")
async def warmup():
    """Load the heavy data once at start (first question was ~40 s slower: personas, attitudes, residents table, travel matrix)."""
    def load():
        import datastore, travel
        datastore.residents(); datastore.tables(); datastore.poi(); travel._matrix(); travel._cells()
        policy.vote_of(next(pd.read_csv("out/personas.csv", nrows=1).itertuples()))
        population()
    asyncio.get_event_loop().run_in_executor(None, load)


POPULATION = None


@app.get("/api/population")
def population():
    """All personas as [lon, lat, age, kind] for the idle city view (positions jittered inside the 1 km cell, see src/live.py).
    kind: 0 resident, 1 student from outside."""
    global POPULATION
    if POPULATION is None:
        import live
        P = pd.read_csv("out/personas.csv")
        POPULATION = [[round(r.lon + live._jitter(r.id, 91) * 0.014, 5), round(r.lat + live._jitter(r.id, 17) * 0.009, 5), int(r.age), int(r.origin == "student_spoza")]
                      for r in P.itertuples()]
    return {"people": POPULATION, "weight": round(float(DS.people.sum() / DS.personas.sum()), 1)}


OFFTOPIC = ("Odpowiadam tylko na pytania o Kraków i jego mieszkańców. Możesz zapytać np.:\n"
            "• Gdzie zorganizować koncert hip-hopowy, żeby przyszło najwięcej osób?\n"
            "• Jak mieszkańcy przyjmą płatne parkowanie w Podgórzu?\n"
            "• Gdzie otworzyć kawiarnię, żeby było dużo studentów i mało konkurencji?")


@app.post("/api/chat")
async def chat(c: Chat):
    s = dict(c.slots)
    if c.text.strip() and not c.slot:   # agent step 1: which mode, and is this a new topic?
        rt = await asyncio.to_thread(router.route, c.text, s.get("mode"), mode_summary(s))
        if rt["mode"] == "offtopic": return {"slots": s, "ready": False, "pills": [], "missing": [], "message": OFFTOPIC}
        mode = "agent" if rt["mode"] in ("business", "other") else rt["mode"]
        if not s.get("mode") or rt["new_topic"]: s = {"mode": mode}
        if s["mode"] == "agent":   # the data agent answers directly; follow-ups continue its conversation (prev = last response id)
            s["text"] = c.text
            return {"slots": s, "ready": True, "auto": True, "pills": [], "missing": []}
    if s.get("mode") == "policy": return await chat_policy(s, c.text)
    s["mode"] = "event"
    if c.slot:
        s[c.slot] = float(c.value) if c.slot == "price" else c.value
        if c.slot == "category" and c.value not in planner.MUSIC: s.setdefault("genre", "none")
    if c.text.strip():   # a new message may change earlier answers ("a może w sobotę?"): new values win
        cur = planner.event_text(s) if s.get("category") else None
        new = await planner.extract(c.text, {}, DISTRICTS, cur)
        if new.pop("_new_event", False): s = {"mode": "event"}   # a different event: start over
        s.update({k: v for k, v in new.items() if k != "text" and v is not None}); s["text"] = (s.get("text", "") + " " + c.text).strip()
    if c.slot == "place" and c.value in (None, "", "none"): s.pop("place", None)
    m = planner.missing(s)
    return {"slots": s, "missing": m, "summary": summary(s), "pills": pills(s), "ready": not m,
            "options": {k: [{"value": v, "label": l} for v, l in planner.LABELS[k].items()] for k in planner.LABELS}}


async def chat_policy(s, text):
    """Agent step 2 (policy): the LLM structures the decision; answers to its questions are appended and it re-reads all."""
    s["text"] = (s.get("text", "") + "\n" + text).strip()
    sp = await asyncio.to_thread(policy.spec, s["text"]); s["spec"] = sp
    if sp["questions_pl"] and not s.get("asked"):
        s["asked"] = True
        return {"slots": s, "ready": False, "pills": [], "missing": [{"slot": "text", "question": "\n".join(sp["questions_pl"]), "options": None, "free": True}]}
    groups = [f"{g['name']} (ok. {pct(g['share'])} dorosłych)" for g in policy_groups(sp)]
    n_adults = len(ADULTS) if sp["area"]["whole_city"] or not sp["area"]["districts"] else int(ADULTS.district.isin(sp["area"]["districts"]).sum())
    who = (f"wszystkich {n_adults:,} symulowanych dorosłych mieszkańców " + ("Krakowa" if sp["area"]["whole_city"] or not sp["area"]["districts"] else "dzielnicy " + ", ".join(sp["area"]["districts"]))).replace(",", " ", 1)
    intro = (f"Rozumiem: {sp['title_pl']}.\nBezpośrednio dotknięci: " + ("; ".join(groups) if groups else "nikt konkretny, zmiana dotyczy wszystkich podobnie") +
             (".\nZałożenia: " + "; ".join(x.rstrip('. ') for x in sp["assumptions_pl"]) if sp["assumptions_pl"] else "") +
             f".\nJeśli coś się nie zgadza, popraw w czacie. Zapytam {who} przez archetypy.")
    return {"slots": s, "ready": True, "pills": [], "missing": [], "summary": sp["title_pl"], "intro": intro}


ADULTS = None
def policy_groups(sp):
    global ADULTS
    if ADULTS is None: P = pd.read_csv("out/personas.csv"); ADULTS = P[(P.age >= 18) & (P.district != OUTSIDE)]
    K = ADULTS if sp["area"]["whole_city"] or not sp["area"]["districts"] else ADULTS[ADULTS.district.isin(sp["area"]["districts"])]
    return [{"name": g["label_pl"], "share": float(np.mean([g["label_pl"] in policy.affected(r, sp) for r in K.itertuples()]))} for g in sp["impacts"] if g["rules"]]


def agent_input(s):
    """Planner slots -> event description for the agent (same fields the backtest used)."""
    L = planner.LABELS
    return {"name": s.get("text", "")[:200], "organiser_description": s.get("text", "")[:400], "category": s.get("category"),
            "genre": s.get("genre") if s.get("genre") not in (None, "none") else None, "price_pln_typical": s.get("price"),
            "weekday_of_main_day": s.get("day"), "start_date": s.get("date"), "time_of_day": s.get("time"), "setting": s.get("setting"),
            "expected_scale": L["scale"].get(s.get("scale")), "target_age": L["age"].get(s.get("age")),
            "venue_name": s["place"]["name"] if s.get("place") and s["place"]["kind"] == "venue" else None}


def result(A, s, meta=None, est=None):
    hl = planner.halflife(s)
    allv = planner.rank(A, s, DISTRICTS); venues = allv[:12]
    pinned = next((v for v in allv if v.get("pinned")), None)
    if pinned and pinned not in venues: venues.append(pinned)
    worst = {k: allv[-1][k] for k in ("name", "index", "within_20min", "kind")} if len(allv) > 12 else None
    dist_names = [d["name"] for d in DISTRICTS] + [OUTSIDE]
    A = A.copy(); A["segment"] = [planner.segment(r) for r in A.itertuples()]
    for v in venues:   # where the audience of this venue lives, how old it is, which life-stage segments
        mins = planner.minutes(A, *v["latlon"])
        p = A.p.values * 0.5 ** (np.maximum(0, mins - 15) / hl); tot = p.sum() or 1
        v["rank"] = allv.index(v) + 1
        v["districts"] = {d: round(float(p[A.district.values == d].sum() / tot), 4) for d in dist_names}
        v["ages"] = {f"{a}–{b}" if b < 120 else "65+": round(float(p[(A.age.values >= a) & (A.age.values <= b)].sum() / tot), 4) for a, b in AGE_BANDS}
        seg = pd.DataFrame({"seg": A.segment.values, "p": p}).groupby("seg").p.agg(["sum", "mean", "size"])
        v["segments"] = [{"name": k, "share": round(float(r["sum"] / tot), 4), "lift": round(float(r["mean"] / (p.mean() or 1)), 2), "n": int(r["size"])}
                         for k, r in seg.sort_values("sum", ascending=False).iterrows()]
        v["median_travel"] = int(np.median(mins[p >= np.quantile(p, 0.5)])) if len(p) else None
    B = planner.barriers(A, s)
    W = when(A, s, venues[0] if venues else None, hl)
    # level: the agent's demand estimate (backtest: best predictor without a capacity limit), spread over venues by the
    # residents model (relative reach); with a capacity: min(capacity, demand) and a sell-out signal
    if est and venues:
        top = venues[0]["score"] or 1
        for v in venues:
            d = est * v["score"] / top
            v["expected"] = int(round(min(d, v["capacity"]) if v["kind"] == "venue" else d, -1)); v["sellout"] = v["kind"] == "venue" and d >= v["capacity"]
        if W: W["expected_days"] = {d: int(round(est * x, -1)) for d, x in W["days"].items()}
    return {"mode": "event", "summary": summary(s), "n": len(A), "venues": venues, "when": W, "worst": worst, "n_candidates": len(allv),
            "reasons": A.reason.value_counts(normalize=True).round(3).to_dict(), "barriers": B, "halflife": hl,
            "p_by_age": {f"{a}–{b}" if b < 120 else "65+": round(float(A.p[(A.age >= a) & (A.age <= b)].mean()), 4) for a, b in AGE_BANDS},
            "insights": insights(venues, B, s, pinned, W, meta, est), "notes": notes(s, est, W), "suggestions": suggestions(B, s, W), "meta": meta, "estimate": est}


DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def when(A, s, v, hl):
    """Relative audience at the best venue for every day (at the chosen time) and every time (on the chosen day).
    Jev's answers do not depend on the day/time; the HETUS factors do, so this needs no extra API calls."""
    if v is None: return None
    mins = planner.minutes(A, *v["latlon"])
    base = A.p_base.values * 0.5 ** (np.maximum(0, mins - 15) / hl)
    tot = lambda d, t: float((base * planner.when_factor(A, d, t, s.get("category"))).sum())
    cur = tot(s.get("day"), s.get("time")) or 1
    days = {d: round(tot(d, s.get("time")) / cur, 3) for d in DAYS}
    times = {t: round(tot(s.get("day"), t) / cur, 3) for t in planner.TIME_WINDOW}
    # HETUS has no day x hour table and its Sunday covers the whole day (museums, daytime cinema): for evening events
    # we do not recommend Sunday, we only show it with a note
    pool = [d for d in DAYS if not (d == "Sun" and s.get("time") == "evening")]
    best = max(pool, key=days.get)
    return {"days": days, "times": times, "rates": planner.day_rates(), "best": best, "sunday_note": s.get("time") == "evening"}


def pct(x): return f"{x * 100:.0f}%"


def insights(venues, B, s, pinned, W=None, meta=None, est=None):
    """The agent's reply, written from the numbers: one short sentence per point; caveats go to notes()."""
    if not venues: return ["Żadne miejsce z bazy nie pasuje do tych warunków. Zmień skalę, rodzaj albo plener / halę."]
    v0 = venues[0]; out = []; n = lambda x: f"{x:,}".replace(",", " ")
    if est:
        RPL = {"neighbourhood": "osiedle", "city": "miasto", "regional": "region", "national": "cała Polska", "international": "międzynarodowy"}
        line = f"Popyt: ok. {n(est)} osób" + (f" (zasięg: {RPL.get(meta['reach'], meta['reach'])}, {meta['outside_share']}% spoza aglomeracji)" if meta else "") + "."
        if v0["kind"] == "venue": line += f" {v0['name']} mieści {n(v0['capacity'])}" + (", więc pewnie się wyprzeda." if v0.get("sellout") else ".")
        out.append(line)
    close = [v for v in venues[1:6] if v["index"] >= 97]
    out.append(f"Najlepiej: {v0['name']}, {pct(v0['within_20min'])} chętnych ma tam do 20 minut. Dalej {' i '.join(v['name'] for v in venues[1:3])}"
               + (" (różnice małe, decyduje dostępność sali)." if close else "."))
    if pinned and pinned is not v0:
        out.append(f"{pinned['name']}: {pinned['rank']}. miejsce w rankingu" + (f" ({pinned['fit']})." if pinned.get("fit") != "pasuje" else "."))
    segs = [x for x in v0["segments"] if x["n"] >= 15]
    if segs:
        big = segs[0]; hot = max(segs, key=lambda x: x["lift"])
        out.append(f"Publiczność: głównie {big['name']} ({pct(big['share'])})" + (f", najchętniej {hot['name']} ({hot['lift']:.1f}×)".replace(".", ",") if hot is not big else "") + ".")
    if B.get("price_strain") is not None and B["price_strain"] >= 0.1:
        out.append(f"Cena: dla {pct(B['price_strain'])} zainteresowanych bilet to obciążenie.")
    if W:
        L = planner.LABELS; best = W["best"]
        if best != s.get("day") and W["days"][best] >= 1.15:
            if s.get("time") == "evening" and best in ("Sat", "Sun") and s.get("day") in ("Fri", "Sat"):
                out.append(f"Termin: {L['day'][best]} może przyciągnąć więcej osób, ale dla wieczoru dane są niepewne.")
            else:
                out.append(f"Termin: {L['day'][best]} daje ok. {pct(W['days'][best] - 1)} więcej niż {L['day'].get(s.get('day'), 'wybrany dzień')}.")
        bt = max(W["times"], key=W["times"].get)
        if bt != s.get("time") and W["times"][bt] >= 1.15: out.append(f"Pora: {L['time'][bt]} daje ok. {pct(W['times'][bt] - 1)} więcej.")
    if "want_target" in B:
        out.append(f"Podoba się {pct(B['want_target'])} grupy docelowej ({pct(B['want_share'])} wszystkich 15+)." + (" Wąska grupa: celuj promocją." if B["want_target"] < 0.15 else ""))
    elif B["want_share"] >= 0.85:   # near the ceiling the share says nothing; awareness and access decide
        out.append(f"Odpowiada prawie każdemu ({pct(B['want_share'])}), więc liczy się promocja i dojazd.")
    else:
        out.append(f"Podoba się {pct(B['want_share'])} mieszkańców 15+.")
    return out


def notes(s, est=None, W=None):
    """Small print under the card (sources and limits), instead of caveats inside every sentence."""
    out = ["Mieszkańcy: archetypy, jedna decyzja AI na grupę podobnych osób (dane GUS)."]
    if est: out.append("Popyt: szacunek agenta; na 13 prawdziwych wydarzeniach mediana błędu 18–28%.")
    if W: out.append("Dzień i pora: HETUS 2020 (GUS), cały dzień, zebrane w pandemii. Dojazd: rozkład MPK + drogi OSM.")
    return out


def suggestions(B, s, W=None):
    out = []
    if W:   # day suggestions come from HETUS data, not from Jev
        best = W["best"]
        if best != s.get("day") and W["days"][best] >= 1.15:
            uncertain = s.get("time") == "evening" and best in ("Sat", "Sun") and s.get("day") in ("Fri", "Sat")
            out.append({"label": f"Sprawdź: {planner.LABELS['day'][best]}" + (" (efekt niepewny)" if uncertain else ""), "slot": "day", "value": best})
    if s.get("price") and B.get("price_strain", 0) >= 0.1:   # same threshold as the price insight
        out.append({"label": f"Sprawdź taniej: {int(round(s['price'] * 0.6, -1))} zł", "slot": "price", "value": round(s["price"] * 0.6, -1)})
    if s.get("setting") == "indoor" and s.get("category") in ("koncert", "festiwal", "kino", "piknik", "targi"):
        out.append({"label": "Sprawdź w plenerze", "slot": "setting", "value": "outdoor"})
    elif s.get("setting") == "outdoor":
        out.append({"label": "Sprawdź w hali / klubie", "slot": "setting", "value": "indoor"})
    scale_up = {"small": "club", "club": "medium", "medium": "large"}.get(s.get("scale"))
    if scale_up: out.append({"label": "Sprawdź większą skalę", "slot": "scale", "value": scale_up})
    return out[:3]


@app.get("/api/run")
async def run(slots: str, n: int = 1200):
    s = json.loads(slots); q: asyncio.Queue = asyncio.Queue(); pending = []

    async def progress(k, total): await q.put({"type": "progress", "done": k, "total": total})

    async def work():
        try:
            # real GUS numbers for the question, in parallel with the simulation (only facts whose numbers are in the table)
            gus = asyncio.create_task(asyncio.to_thread(gus_facts.facts, s.get("text", "")))
            async def gus_ok():
                try: return (await gus)[0]
                except Exception: return []
            if s.get("mode") == "agent":
                gus.cancel()
                out = await data_agent.run(s["text"], s.get("prev"), progress=q.put)
                await q.put({"type": "result", "data": await with_followups({"mode": "agent", **out})}); await asyncio.gather(*pending); await q.put(None); return
            if s.get("mode") == "policy":
                await q.put({"type": "status", "message": "Pytam mieszkańców o decyzję…"})
                A = await policy.run(s["spec"], n=n, progress=progress, live=q.put, archetypes=True)
                await q.put({"type": "result", "data": await with_followups({"mode": "policy", **policy.result(A, s["spec"]), "gus": await gus_ok()})}); await asyncio.gather(*pending); await q.put(None); return
            await q.put({"type": "status", "message": "Agent ocenia wydarzenie (zasięg, gwiazda, przyjezdni) i szacuje popyt…"})
            desc = agent_input(s)
            calls = [asyncio.to_thread(event_agent.assess, desc, True), asyncio.to_thread(event_agent.estimate, desc)]
            if s.get("date"): calls.append(asyncio.to_thread(calendar_agent.assess_full, s["date"], s))   # what this specific date means
            meta, est, *cal = await asyncio.gather(*calls); cal = cal[0] if cal else None
            s["meta"] = {k: meta[k] for k in ("headliner", "headliner_popularity", "audience")}
            A = await planner.run(s, n=n, progress=progress, live=q.put, archetypes=True)
            if cal:   # date effects per resident group (ASSUMPTION multipliers); the demand estimate already saw the date, not the competition
                before = A.p.sum(); A["p"] = A.p * calendar_agent.calendar_factor(A, cal, s); cal["effect"] = float(A.p.sum() / before - 1)
                cf = calendar_agent.competition_factor(cal)
                if est and cf < 1: est = int(round(est * cf, -1))
            R = result(A, s, meta, est)
            if cal:
                R["calendar"] = cal
                # text built from the flags (computed holidays + the agent's academic calendar and competition), not the LLM's own prose
                R["insights"].insert(1, f"Data {'.'.join(reversed(s['date'].split('-')[1:]))}: " + ("; ".join(calendar_agent.describe(cal)) or "zwykły dzień") + "." +
                                     (f" Chętnych {cal['effect'] * 100:+.0f}%." if abs(cal["effect"]) >= 0.03 else ""))
                R["notes"].append("Kalendarz: święta liczone w kodzie, uczelnie i konkurencja z wyszukiwarki; wpływ daty to szacunek z założeń.")
            await q.put({"type": "result", "data": await with_followups({**R, "gus": await gus_ok()})}); await asyncio.gather(*pending)
        except Exception:
            # details stay in the server log: provider errors can echo parts of the request, such as a masked API key
            import traceback; traceback.print_exc()
            await q.put({"type": "error", "message": "Coś poszło nie tak po stronie serwera. Spróbuj ponownie za chwilę."})
        await q.put(None)

    async def with_followups(R):
        """The result goes out at once; 3 suggested next questions follow as their own event (best effort)."""
        async def later():
            summary = R.get("answer") or "\n".join(R.get("insights", []))
            try: items = await asyncio.to_thread(followups.make, s.get("text", ""), R.get("mode"), summary)
            except Exception: items = []
            await q.put({"type": "followups", "items": items})
        pending.append(asyncio.create_task(later()))
        return R

    async def stream():
        task = asyncio.create_task(work())
        while (msg := await q.get()) is not None:
            yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
        await task
    return StreamingResponse(stream(), media_type="text/event-stream")
