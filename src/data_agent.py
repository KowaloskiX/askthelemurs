"""Data agent: full control over the normalized data (src/datastore.py) through typed tools, in a tool-calling loop.
The LLM decides what to compute; code computes it; the answer may only contain numbers that came out of the tools.
Tools:
  query_residents  filter / group / count / share / mean / median over the synthetic residents (people-weighted)
  get_table        any official table (GUS BDL Kraków, GUS national surveys, HETUS, venues, districts)
  find_place       coordinates of a venue / district / neighbourhood
  ask_residents    a Jev survey of a sample of residents (yes/no or choice), with travel time / cost computed in code,
                   aggregated by group. The only paid tool: capped per turn (MAX_SURVEY_PEOPLE).
Guards: max iterations, survey cap, grounding check of the final answer (every number must be in a tool result, a
share of two tool numbers, or in the question), one corrective turn, unverified numbers flagged.
Usage: python3 src/data_agent.py "pytanie"
"""
import asyncio, hashlib, json, os, re, sys
os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "__all__")
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import datastore as ds, jev_sim, event_agent, gus_facts

MODEL = "gpt-5.4"
VOTES = pd.read_csv("out/attitudes.csv").set_index("id").vote.to_dict() if os.path.exists("out/attitudes.csv") else {}
from policy import VOTE_EN
MAX_ITER = 10
MAX_SURVEY_PEOPLE = 1500   # Jev calls (archetypes) per turn (~$0.05)
FILTER = {"type": "object", "additionalProperties": False, "required": ["field", "op", "value"],
          "properties": {"field": {"type": "string", "description": "a residents field, or 'home' with op within_km"},
                         "op": {"type": "string", "enum": ds.OPS}, "value": {"type": "string", "description": "number, true/false, comma list for in/not_in, 'lat,lon,km' for within_km"}}}
FILTERS = {"type": "array", "items": FILTER}
TITLE = {"type": "string", "description": "short Polish title of this result for the user's screen"}
TOOLS = [
    {"type": "function", "name": "query_residents", "strict": True,
     "description": "Count / share / mean / median over synthetic Kraków residents (weighted to real population). measure 'share' = within each group of the base population, the fraction that matches filters (e.g. share of swimmers in each age band); for a distribution across groups use 'count' and divide.",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["title_pl", "filters", "group_by", "measure", "field", "base_filters"],
                    "properties": {"title_pl": TITLE, "filters": FILTERS, "group_by": {"type": "array", "items": {"type": "string"}, "description": "0-2 fields"},
                                   "measure": {"type": "string", "enum": ["count", "share", "mean", "median"]},
                                   "field": {"type": ["string", "null"], "description": "numeric field for mean/median"}, "base_filters": FILTERS}}},
    {"type": "function", "name": "get_table", "strict": True, "description": "Fetch an official table by key (see the table index).",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["key"], "properties": {"key": {"type": "string"}}}},
    {"type": "function", "name": "find_place", "strict": True, "description": "Coordinates of a named venue, district or neighbourhood in Kraków.",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["name"], "properties": {"name": {"type": "string"}}}},
    {"type": "function", "name": "count_places", "strict": True,
     "description": "Supply side from OpenStreetMap: number of places of a category (cafe, gym, school, tram_stop...) per district and residents per place. Use for 'where to open X' (competition vs demand).",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["title_pl", "category"], "properties": {"title_pl": TITLE, "category": {"type": "string"}}}},
    {"type": "function", "name": "nearby_places", "strict": True, "description": "Places of a category within km of a point (OpenStreetMap), with the nearest ones.",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["category", "lat", "lon", "km"],
                    "properties": {"category": {"type": "string"}, "lat": {"type": "number"}, "lon": {"type": "number"}, "km": {"type": "number"}}}},
    {"type": "function", "name": "travel_by_district", "strict": True,
     "description": "Real travel time from each district to a point (R5 on the MPK timetable and OSM roads): mean door-to-door minutes of residents, by district. mode: transit | car | as_they_travel.",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["title_pl", "lat", "lon", "mode"],
                    "properties": {"title_pl": TITLE, "lat": {"type": "number"}, "lon": {"type": "number"}, "mode": {"type": "string", "enum": ["transit", "car", "as_they_travel"]}}}},
    {"type": "function", "name": "ask_residents", "strict": True,
     "description": "Survey a random sample of residents with the Jev decision model (each person answers for themselves, from their profile). "
                    "Use for opinions, intentions, preferences that the data cannot give directly. Write the question in English, literal, about 'this person'. "
                    "Do not ask about travel time or price in words: pass travel_to / cost_pln and code computes them per person. Returns the expected share by group.",
     "parameters": {"type": "object", "additionalProperties": False,
                    "required": ["title_pl", "statement_en", "options", "context_en", "filters", "group_by", "n", "travel_to", "cost_pln"],
                    "properties": {"title_pl": TITLE,
                                   "statement_en": {"type": "string", "description": "yes/no statement about this person, e.g. 'This person would visit a new public swimming pool at least once a month.' or, with options, the choice question"},
                                   "options": {"type": "array", "description": "empty for yes/no; else 2-8 choices", "items": {"type": "object", "additionalProperties": False, "required": ["key", "text_en"],
                                               "properties": {"key": {"type": "string"}, "text_en": {"type": "string"}}}},
                                   "context_en": {"type": "string", "description": "neutral English description of the situation they react to"},
                                   "filters": FILTERS, "group_by": {"type": "array", "items": {"type": "string"}, "description": "0-1 field"},
                                   "n": {"type": "integer", "description": "ignored (every matching resident is covered through archetypes); pass 0"},
                                   "travel_to": {"type": ["object", "null"], "additionalProperties": False, "required": ["lat", "lon"],
                                                 "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}}},
                                   "cost_pln": {"type": ["number", "null"], "description": "one-off price for the person, if any"}}}},
]
SYSTEM = """You are the analyst of Kraków Sim: a public digital twin of Kraków's residents built only from official GUS (Statistics Poland) data.
The user (a small organiser, a café owner, a district council member, a journalist) asks in Polish. Plan the analysis, call the tools, then answer.
Rules:
- Prefer hard data: residents counts and official tables first (incl. election results, civic budget votes, OpenStreetMap supply); ask_residents only for opinions and intentions, with a focused sample (it costs money).
- Every number in your answer must come from a tool result (you may round, or state a share of two numbers from the same result). Never invent numbers.
- When you mix them, make clear which numbers are official statistics (name the source briefly, e.g. "PKW", "GUS") and which come from the simulated residents.
- Synthetic residents: 1 persona ~ 55 people; results with n_personas < 30 are noisy: say so or merge groups. favourite_music is an assumption, not GUS.
- Write shares as percentages (65%, not 0,647) and large numbers with spaces (14 600).
- Style: Polish, plain and direct, like a good analyst talking to a client. First sentence = the answer. Then at most 4 short bullets with the key numbers.
  Bold only the 1-3 most important numbers. No headings, no "Ograniczenia:" label, no tables (the screen shows your tool results as charts).
  Mention a limit in one short sentence only when it changes the conclusion (e.g. OSM incomplete for very small counts). Do not repeat that results are a simulation:
  the screen already says so.
DATA CATALOG:
""" + json.dumps(ds.catalog(), ensure_ascii=False, separators=(",", ":"))


# ---------------- tools
async def ask_residents(a, budget, progress=None):
    from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, TypeSafeError
    # archetypes (as in src/policy.py, src/planner.py): EVERY matching resident gets the answer of their archetype's
    # representative; the key coarsens until it fits the turn's budget of Jev calls
    R = ds.residents(); sel = R[ds.mask(R, a["filters"]) & (R.age >= 15)].copy()
    if len(sel) < 30: return {"error": f"only {len(sel)} personas match the filters"}
    if budget["left"] < 50: return {"error": f"survey budget for this turn used up ({MAX_SURVEY_PEOPLE} Jev calls); answer with the data you have"}
    sel["inc"] = pd.qcut(sel.net_income.rank(method="first"), 3, labels=["lo", "mid", "hi"]).astype(str); sel["kids"] = sel.children > 0
    sel["vote"] = sel.vote_2023.fillna("-")
    for key in (["age_band", "sex", "status", "kids", "inc", "has_car", "vote"], ["age_band", "sex", "status", "kids", "has_car", "vote"],
                ["age_band", "sex", "status", "kids", "has_car"], ["age_band", "sex", "status"]):
        if sel.groupby(key).ngroups <= budget["left"]: break
    sel["arch"] = sel.groupby(key, sort=False).ngroup(); rng = np.random.default_rng(7)
    reps = sel.groupby("arch").id.apply(lambda x: int(rng.choice(x.values))); sel["rep"] = sel.arch.map(reps); budget["left"] -= len(reps)
    S = sel; P = pd.read_csv("out/personas.csv").set_index("id").loc[sorted(set(reps.values))]
    q = (Choice(instructions=a["statement_en"], criteria={o["key"]: o["text_en"] for o in a["options"]}) if a["options"] else
         Noul(instructions=a["statement_en"], criteria={"true": "yes, true for this person", "false": "no"}))
    import travel   # real door-to-door minutes (R5 on MPK timetable / OSM roads), per representative
    tmin = dict(zip(P.index, travel.minutes(P.assign(cell=P.cell), a["travel_to"]["lat"], a["travel_to"]["lon"]))) if a["travel_to"] else {}
    def state(r):
        ps = {}
        if a["travel_to"]: ps["travel_time_one_way"] = f"about {int(round(tmin[r.Index] / 5) * 5)} minutes {'by car' if r.has_car else 'by tram/bus'}"
        if a["cost_pln"]: ps["cost_for_this_person"] = jev_sim.cost_text(a["cost_pln"], r.net_income)
        st = {"person": jev_sim.person_state(r, "all"), "situation": a["context_en"]}
        if (v := VOTES.get(r.Index)): st["person"]["voted_in_2023_parliamentary_election"] = VOTE_EN[v]
        if ps: st["personal_situation"] = ps
        return st
    key = hashlib.sha1(json.dumps([a["statement_en"], a["options"], a["context_en"], a["travel_to"], a["cost_pln"], jev_sim.pop_version()], sort_keys=True).encode()).hexdigest()[:10]
    cache = Path("out/agent/survey"); cache.mkdir(parents=True, exist_ok=True); cache = cache / f"{key}.jsonl"
    done = {json.loads(l)["id"]: json.loads(l)["a"] for l in cache.read_text().splitlines()} if cache.exists() else {}
    todo = [r for r in P.itertuples() if r.Index not in done]; sem = asyncio.Semaphore(jev_sim.JEV_CONCURRENCY); k = [len(P) - len(todo)]
    # live map: every covered resident appears, then lights up when their archetype's representative answers
    members = S.groupby("rep").id.apply(list).to_dict()
    pv = lambda v: (round(float(v), 3), None) if not a["options"] else (0.5, str(v))
    rows_of = lambda rep, v: [[int(m), *pv(v)] for m in members.get(rep, [])]
    if progress:
        import live as lv
        PP = pd.read_csv("out/personas.csv").set_index("id").loc[S.id].reset_index()
        await progress({"type": "people", "people": lv.people(PP), "archetypes": int(len(P))})
        if done: await progress({"type": "answers", "rows": [row for rep, v in done.items() if rep in members for row in rows_of(rep, v)]})
    async with AsyncTypeSafeClient(timeout=30.0) as client:
        with cache.open("a") as f:
            async def one(r):
                async with sem:
                    try: res = await client.system_one(state=state(r), questions={"q": q})
                    except TypeSafeError: return
                v = res.choices["q"].choice if a["options"] else res.nouls["q"].noul
                done[r.Index] = v; f.write(json.dumps({"id": int(r.Index), "a": v}) + "\n"); k[0] += 1
                if progress: await progress({"type": "answers", "rows": rows_of(r.Index, v)})
                if progress and k[0] % 25 == 0: await progress({"type": "progress", "done": k[0], "total": len(P)})
            await asyncio.gather(*(one(r) for r in todo))
    S = S[S.rep.isin(done)].assign(ans=S.rep.map(done))
    g = (a["group_by"] or [None])[0]
    def agg(G):
        w = G.weight / G.weight.sum()
        if a["options"]: return {"n_personas": len(G), **{f"share_{o['key']}": round(float(w[G.ans == o["key"]].sum()), 3) for o in a["options"]}}
        return {"n_personas": len(G), "share_yes": round(float((w * G.ans).sum()), 3)}
    rows = [{**({g: v} if g else {}), **agg(G)} for v, G in (S.groupby(g) if g else [(None, S)])]
    return {"rows": rows, "residents": len(S), "archetypes": len(reps), "archetype_key": key,
            "note": "expected share among ALL residents matching the filters (one Jev answer per archetype, people-weighted); a model estimate, not a poll"}


DISTRICTS = set(pd.read_csv("out/districts.csv").district)


def district_values(obj, label=""):
    """Find the first {district: number} (or {district: {metric: number}}) inside a tool result, for the live map.
    Police areas ('KP I'...) are spread to their districts. Returns (label, {district: value}) or None."""
    if isinstance(obj, dict):
        keys = [k for k in obj if k in DISTRICTS]
        if len(keys) >= 2:
            first = obj[keys[0]]
            if isinstance(first, (int, float)): return label, {k: float(obj[k]) for k in keys if isinstance(obj[k], (int, float))}
            if isinstance(first, dict):
                if isinstance(first.get("shares"), dict) and first["shares"]:   # election tables: the first candidate's / party's share
                    c = sorted(first["shares"])[-1] if any(x.startswith("TRZASKOWSKI") for x in first["shares"]) else next(iter(first["shares"]))
                    c = next((x for x in first["shares"] if x.startswith("TRZASKOWSKI")), c)
                    return f"{label}: {c.split()[0].capitalize()} %".strip(), {k: 100 * float(obj[k]["shares"].get(c, 0)) for k in keys}
                for m, v in first.items():   # first numeric metric (shares dicts: the first party / candidate)
                    if isinstance(v, (int, float)): return f"{label} {m}".strip(), {k: float(obj[k][m]) for k in keys if isinstance(obj[k].get(m), (int, float))}
                    if isinstance(v, dict) and v and isinstance(next(iter(v.values())), (int, float)):
                        sub = next(iter(v)); return f"{label} {sub}".strip(), {k: float(obj[k][m][sub]) for k in keys if isinstance(obj[k].get(m, {}).get(sub), (int, float))}
        if any(str(k).startswith("KP ") for k in obj) and all(isinstance(v, dict) and "districts" in v for v in obj.values()):
            m = "crimes_per_1000_residents"; return f"{label} {m}".strip(), {d: float(v[m]) for v in obj.values() for d in v["districts"]}
        for k, v in obj.items():
            r = district_values(v, f"{label} {k}".strip() if not str(k).startswith("_") else label)
            if r: return r
    if isinstance(obj, list) and obj and isinstance(obj[0], dict) and "district" in obj[0]:   # query rows grouped by district
        metric = next((m for m in ("share_yes", "share", "minutes", "residents_per_place", "mean", "median", "people") if m in obj[0]), None)
        if metric and len({r["district"] for r in obj}) >= 2 and len(obj) == len({r["district"] for r in obj}):
            return f"{label} {metric}".strip(), {r["district"]: float(r[metric]) for r in obj if r.get(metric) is not None}
    return None


MAP_LABEL = {"safety.perceived_by_district": "poczucie bezpieczeństwa: % „moja dzielnica jest bezpieczna” (ankieta miasta 2025)",
             "safety.crimes_by_police_area": "przestępstwa na 1000 mieszkańców, rejony policji 2016–2020",
             "elections.prezydent2025_r2.by_district": "Trzaskowski w 2. turze 2025, % (PKW)", "elections.prezydent2025_r1.by_district": "1. tura 2025, % (PKW)",
             "elections.sejm2023.by_district": "Sejm 2023, % (PKW)"}
SKIP_MAP = {"districts"}   # helper tables (population) must not replace the map the answer is about


async def call_tool(name, a, budget, progress=None):
    res = await _call_tool(name, a, budget, progress)
    if progress and isinstance(res, dict) and not res.get("error") and a.get("key") not in SKIP_MAP:
        dv = district_values(res.get("rows") if "rows" in res else res.get("value", res), a.get("title_pl") or a.get("key", ""))
        if dv and len(dv[1]) >= 2:
            await progress({"type": "choropleth", "label": a.get("title_pl") or MAP_LABEL.get(a.get("key"), dv[0]), "values": dv[1]})
    return res


async def _call_tool(name, a, budget, progress=None):
    try:
        if name == "query_residents":
            if progress:   # live map: highlight the residents this query is about
                R = ds.residents(); m = ds.mask(R, a["base_filters"]) & ds.mask(R, a["filters"])
                await progress({"type": "highlight", "label": a.get("title_pl", ""), "points": R.loc[m, ["lon", "lat"]].round(5).values.tolist()})
            return ds.query(a["filters"], a["group_by"], a["measure"], a["field"], a["base_filters"])
        if name == "get_table":
            s = ds.get_table(a["key"]); return json.loads(s) if not s.endswith('chars)"') else {"truncated_json": s}
        if name == "find_place": return ds.find_place(a["name"])
        if name == "travel_by_district": return ds.travel_by_district(a["lat"], a["lon"], a["mode"])
        if name in ("count_places", "nearby_places"):
            if progress:   # live map: the competition appears as points
                D = ds.poi(); D = D[D.cat == a["category"]]
                if name == "nearby_places": D = D[ds._km(D.lat.values, D.lon.values, a["lat"], a["lon"]) <= a["km"]]
                await progress({"type": "places", "label": a.get("title_pl") or a["category"], "points": [[round(x, 5), round(y, 5), n] for y, x, n in zip(D.lat, D.lon, D.name.fillna(""))]})
            return ds.count_places(a["category"]) if name == "count_places" else ds.nearby_places(a["category"], a["lat"], a["lon"], a["km"])
        if name == "ask_residents": return await ask_residents(a, budget, progress)
        return {"error": f"unknown tool {name}"}
    except Exception as e:   # errors go back to the model as data
        return {"error": f"{type(e).__name__}: {e}"}


# ---------------- grounding
def _nums(obj):
    if isinstance(obj, dict):
        for v in obj.values(): yield from _nums(v)
    elif isinstance(obj, list):
        for v in obj: yield from _nums(v)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool): yield float(obj)
    elif isinstance(obj, str):
        for x, _ in gus_facts._numbers(obj): yield x


def unverified(answer, results, question):
    vals = set()
    for r in results: vals |= set(_nums(r))
    pct = {v * 100 for v in vals if 0 <= v <= 1}
    free = {x for x, _ in gus_facts._numbers(question)}
    bad = []
    for x, is_pct in gus_facts._numbers(answer.replace("–", " ").replace("-", " ")):
        if x in free or (x <= 12 and x == int(x)) or 1990 <= x <= 2030: continue
        if is_pct:
            if any(abs(x - p) <= 1.0 for p in pct | vals): continue
            # a share of two numbers from the results (e.g. 15 200 of 31 800)
            if any(b and abs(100 * a2 / b - x) <= 1.0 for a2 in vals for b in vals if 0 < a2 < b): continue
        elif any(abs(x - v) <= max(0.02 * abs(v), 0.51) for v in vals): continue
        # a share applied to a count from the results (65% of 81 800 = ~53 000)
        elif any(abs(x - s * v) <= 0.03 * s * v for s in vals if 0 < s < 1 for v in vals if v > 1): continue
        bad.append(x)
    return bad


LABEL = {"travel_by_district": "Liczę dojazdy (rozkład MPK)", "count_places": "Liczę konkurencję (OpenStreetMap)", "nearby_places": "Sprawdzam okolicę (OpenStreetMap)", "query_residents": "Liczę na danych mieszkańców", "get_table": "Sprawdzam tabelę GUS", "find_place": "Szukam miejsca", "ask_residents": "Pytam mieszkańców (Jev)"}


# tool results of each conversation, by the last response id: a follow-up may answer from numbers computed in earlier
# turns, and the grounding check must accept them (otherwise every follow-up answer was flagged)
HISTORY = {}


async def run(question, prev_id=None, progress=None):
    """-> {answer, steps: [{tool, args, result}], flagged: [numbers], response_id, usage}"""
    event_agent.load_env(); jev_sim.load_env()
    from openai import AsyncOpenAI
    budget = {"left": MAX_SURVEY_PEOPLE}; steps = []; usage = [0, 0]
    async with AsyncOpenAI() as client:
        async def call(inp, prev, tools=True):
            r = await client.responses.create(model=MODEL, instructions=SYSTEM, input=inp, previous_response_id=prev, reasoning={"effort": "low"},
                                              tools=TOOLS, tool_choice="auto" if tools else "none")
            usage[0] += r.usage.input_tokens; usage[1] += r.usage.output_tokens
            return r
        r = await call([{"role": "user", "content": question}], prev_id)
        for it in range(MAX_ITER):
            calls = [o for o in r.output if o.type == "function_call"]
            if not calls: break
            outs = []
            args = [json.loads(c.arguments) for c in calls]
            if progress: await progress({"type": "status", "message": " · ".join(f"{LABEL.get(c.name, c.name)}: {a.get('title_pl') or a.get('key') or a.get('name', '')}" for c, a in zip(calls, args))})
            results = await asyncio.gather(*(call_tool(c.name, a, budget, progress) for c, a in zip(calls, args)))   # one model turn's tools run in parallel
            for c, a, res in zip(calls, args, results):
                steps.append({"tool": c.name, "args": a, "result": res})
                outs.append({"type": "function_call_output", "call_id": c.call_id, "output": json.dumps(res, ensure_ascii=False, default=str)[:12000]})
            r = await call(outs, r.id, tools=it < MAX_ITER - 1)
        answer = r.output_text
        earlier = HISTORY.get(prev_id, [])
        bad = unverified(answer, earlier + [s["result"] for s in steps], question)
        if bad:   # one corrective turn
            if progress: await progress({"type": "status", "message": "Sprawdzam liczby w odpowiedzi…"})
            r = await call([{"role": "user", "content": "These numbers in your answer are not in any tool result: " + ", ".join(f"{x:g}" for x in bad) +
                             ". Rewrite the answer: use only numbers from the tool results (or remove them). Same format."}], r.id, tools=False)
            answer = r.output_text; bad = unverified(answer, earlier + [s["result"] for s in steps], question)
    HISTORY[r.id] = (earlier + [s["result"] for s in steps])[-40:]
    if len(HISTORY) > 500: HISTORY.pop(next(iter(HISTORY)))
    return {"answer": answer, "steps": steps, "flagged": bad, "response_id": r.id, "usage": usage, "survey_people": MAX_SURVEY_PEOPLE - budget["left"],
            "survey_residents": sum(s["result"].get("residents", 0) for s in steps if s["tool"] == "ask_residents" and isinstance(s["result"], dict))}


if __name__ == "__main__":
    async def pr(m): print("  ·", m.get("message") or f"{m.get('done')}/{m.get('total')}")
    out = asyncio.run(run(sys.argv[1], progress=pr))
    for s in out["steps"]: print(f"\n[{s['tool']}] {json.dumps(s['args'], ensure_ascii=False)[:300]}\n  -> {json.dumps(s['result'], ensure_ascii=False, default=str)[:400]}")
    print("\n" + out["answer"]); print("\nniesprawdzone liczby:", out["flagged"], "| tokens", out["usage"], "| Jev osób", out["survey_people"])
