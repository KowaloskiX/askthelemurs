"""City decision / consultation mode: 'how would residents react to X?'
1. spec(): an LLM (gpt-5.4) turns the user's Polish message into a typed policy spec: neutral English description,
   affected area, WHO is directly affected as rules over persona fields (e.g. diesel car older than 17 years),
   arguments for and against, assumptions it had to make, at most two questions it must ask.
2. facts(): the rules are evaluated in code for every persona ('their current car would be banned'); Jev never
   has to infer from raw fields who is affected (same pattern as jev_sim.facts_policy).
3. run(): Jev asks a sample of adult Kraków residents: support (Noul), stance (Score 0-4), the argument that matters
   most to them (Choice over the spec's arguments).
4. result(): support overall, by district, by life-stage segment, affected vs not, arguments of both sides.
Validation: scenarios/sct.json through this module vs the CEM 2024 poll (src/validate_sct.py).
"""
import asyncio, hashlib, json, os, sys
os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "__all__")
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jev_sim, event_agent, planner
from jev_sim import person_state, nn

MODEL = "gpt-5.4"
EFFORT = os.environ.get("KS_EFFORT", "low")   # 'medium' was used for the SCT validation; low is ~2-3x faster
HOUSEHOLD = {"mieszka sam(a)": "alone", "z partnerem/partnerką": "partner", "z partnerem i dziećmi": "partner_children", "samotny rodzic z dziećmi": "single_parent",
             "z rodzicami": "with_parents", "ze współlokatorami / w akademiku": "flatmates", "z dalszą rodziną (wielopokoleniowo)": "extended_family",
             "z dorosłymi dziećmi (bez partnera)": "adult_children"}
ACTIVITY = {"czyta książki": "reading", "muzea": "museums", "teatr": "theatre", "rower": "cycling", "koncerty (pop, rock, inne)": "concerts",
            "kabaret, stand-up": "cabaret_standup", "pływanie": "swimming", "festiwale": "festivals", "kluby, dyskoteki": "clubbing",
            "filharmonia, koncerty klasyczne": "classical_concerts", "taniec": "dancing", "fitness": "fitness", "majsterkowanie": "diy",
            "kino co miesiąc": "cinema_monthly", "gra na instrumencie / śpiewa": "plays_music", "narty / snowboard": "skiing", "potańcówki, dancingi": "dances",
            "siłownia": "gym", "bieganie / nordic walking": "running", "rolki / deskorolka": "skating", "fotografia": "photography",
            "tenis / badminton / squash": "racket_sports", "piłka nożna": "football", "joga": "yoga", "wspinaczka": "climbing", "siatkówka": "volleyball",
            "kolekcjonowanie": "collecting", "sporty walki": "martial_arts", "wędkarstwo": "fishing"}
OUTSIDE = jev_sim.OUTSIDE
DISTRICTS = sorted(d for d in pd.read_csv("out/districts.csv").district if d != OUTSIDE)
FIELDS = {   # field the LLM may use -> (description for the LLM, getter on a persona row)
    "has_car": ("true / false", lambda r: "true" if r.has_car else "false"),
    "car_age": ("age of their car in years (only people with a car)", lambda r: nn(r.car_age) if r.has_car else None),
    "car_fuel": ("petrol | diesel | lpg (a petrol car with an LPG installation: emission rules treat it as petrol) | hybrid_electric (only people with a car)",
                 lambda r: {"benzyna": "petrol", "diesel": "diesel", "LPG": "lpg"}.get(r.car_fuel, "hybrid_electric") if r.has_car else None),
    "age": ("age in years", lambda r: r.age),
    "status": ("employed | university_student | school_pupil | retired | unemployed | inactive",
               lambda r: {"pracuje": "employed", "student": "university_student", "uczeń": "school_pupil", "emeryt": "retired",
                          "bezrobotny": "unemployed", "niepracujący": "inactive"}[r.status]),
    "children": ("number of children living with them", lambda r: int(nn(r.children) or 0)),
    "net_income": ("own monthly net income in PLN (0 = no own income)", lambda r: r.net_income),
    "uses_public_transport": ("true if they get around mostly by tram/bus (no car)", lambda r: "false" if r.has_car else "true"),
    "cycles": ("true if they cycle", lambda r: "true" if isinstance(r.hobbies, str) and "rower" in r.hobbies.split(";") else "false"),
    "education": ("higher | secondary | vocational | primary (NSP 2021)",
                  lambda r: {"wyższe": "higher", "średnie": "secondary", "zasadnicze zawodowe": "vocational", "podstawowe": "primary"}.get(r.education)),
    "household": ("alone | partner | partner_children | single_parent | with_parents | flatmates | extended_family | adult_children (NSP 2021)",
                  lambda r: HOUSEHOLD.get(r.household)),
    "born_abroad": ("true / false (census grid 2021)", lambda r: "true" if nn(r.born_abroad) else "false"),
    "activity": ("free-time activities (GUS culture and sport surveys); op 'in' with any of: " + ", ".join(ACTIVITY.values()),
                 lambda r: [ACTIVITY[x] for x in r.hobbies.split(";") if x in ACTIVITY] if isinstance(r.hobbies, str) and r.hobbies else []),
    "lives_in_area": ("true if they live in the policy's area (see 'area')", None),
    "km_from_location": ("distance in km from their home to the policy's location (see 'location'); homes are known to about 1 km", None),
}
OPS = ["=", "!=", ">", "<", ">=", "<=", "in"]
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["title_pl", "title_en", "description_en", "area", "location", "impacts", "leisure_relevant", "arguments", "assumptions_pl", "questions_pl"],
          "properties": {
              "title_pl": {"type": "string", "description": "short Polish title"},
              "title_en": {"type": "string"},
              "description_en": {"type": "string", "description": "3-5 neutral sentences in English: what changes, from when, for whom, what it costs people, "
                                                                 "the city's stated reason. Facts only, no persuasion, no numbers you do not know."},
              "area": {"type": "object", "additionalProperties": False, "required": ["whole_city", "districts"],
                       "properties": {"whole_city": {"type": "boolean"},
                                      "districts": {"type": "array", "items": {"type": "string", "enum": DISTRICTS},
                                                    "description": "districts where the policy applies, if not the whole city"}}},
              "location": {"type": "object", "additionalProperties": False, "required": ["has_point", "name", "lat", "lon"],
                           "description": "if the decision is about one place (a street, square, park, building), its approximate coordinates",
                           "properties": {"has_point": {"type": "boolean"}, "name": {"type": "string"}, "lat": {"type": "number"}, "lon": {"type": "number"}}},
              "impacts": {"type": "array", "description": "1-4 groups of residents DIRECTLY affected (cost, ban, benefit), each defined by rules that must all hold",
                          "items": {"type": "object", "additionalProperties": False, "required": ["label_pl", "effect_en", "rules"],
                                    "properties": {"label_pl": {"type": "string", "description": "Polish name of the group, e.g. 'właściciele starych diesli'"},
                                                   "effect_en": {"type": "string", "description": "what changes for a person in this group, in English, second person avoided: 'their current car would be banned from the city'"},
                                                   "rules": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["field", "op", "value"],
                                                             "properties": {"field": {"type": "string", "enum": list(FIELDS)}, "op": {"type": "string", "enum": OPS},
                                                                            "value": {"type": "string", "description": "number, true/false, or a comma-separated list for 'in'"}}}}}}},
              "arguments": {"type": "array", "description": "4-8 arguments people actually raise, both sides",
                            "items": {"type": "object", "additionalProperties": False, "required": ["key", "side", "text_en", "label_pl"],
                                      "properties": {"key": {"type": "string", "description": "snake_case id"}, "side": {"type": "string", "enum": ["for", "against"]},
                                                     "text_en": {"type": "string"}, "label_pl": {"type": "string", "description": "2-5 Polish words"}}}},
              "leisure_relevant": {"type": "boolean", "description": "true if residents' free-time activities (sport, culture) matter for this decision, e.g. a pool, park, concert hall, bike lanes"},
              "assumptions_pl": {"type": "array", "items": {"type": "string"}, "description": "details you had to assume because the user did not give them (Polish, short)"},
              "questions_pl": {"type": "array", "items": {"type": "string"},
                               "description": "at most 2 Polish questions, ONLY if residents' reaction cannot be judged without the answer (e.g. the fee amount); else empty"}}}
FIELD_DOC = "\n".join(f"  {k}: {d}" for k, (d, _) in FIELDS.items())
SYSTEM = f"""You prepare a public-consultation scenario for a simulation of Kraków residents. The user describes a city decision in Polish.
Write a neutral, factual description, say where it applies, and define the groups of residents DIRECTLY affected using only these persona fields:
{FIELD_DOC}
A group is only for people whose situation the decision itself changes (a ban, a fee, a lost or new service, a cost, a saving).
For a decision about one place, give its location and define nearby residents with km_from_location (e.g. <= 1), not with a whole district.
Do NOT make groups for people who are unaffected, and do not make groups for diffuse benefits everyone shares (cleaner air, less traffic):
those belong in the description and the arguments. Rules within one group must all hold (AND). Use concrete thresholds from the user's message; if the user gives none, use the real Kraków rules if you
know them, otherwise make a reasonable assumption and list it in assumptions_pl. Arguments: those that residents really raise in Polish debates, both sides.
Ask a question only if the reaction truly depends on a missing number or detail. Answer only with the JSON."""


def spec(text, cache_dir="out/agent"):
    event_agent.load_env()
    from openai import OpenAI
    f = Path(cache_dir) / f"policy_{hashlib.sha1(f'{text}|{MODEL}|{SYSTEM}'.encode()).hexdigest()[:12]}.json"
    if f.exists(): return json.loads(f.read_text())
    r = OpenAI().responses.create(model=MODEL, reasoning={"effort": EFFORT}, instructions=SYSTEM, input=text,
                                  text={"format": {"type": "json_schema", "name": "policy", "schema": SCHEMA, "strict": True}})
    out = json.loads(r.output_text); out["_tokens"] = [r.usage.input_tokens, r.usage.output_tokens]
    f.parent.mkdir(parents=True, exist_ok=True); f.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return out


def _num(x):
    try: return float(x)
    except (TypeError, ValueError): return None


def holds(r, rule, sp):
    f, op, val = rule["field"], rule["op"], str(rule["value"]).strip().lower()
    if f == "lives_in_area": v = "true" if in_area(r, sp) else "false"
    elif f == "km_from_location": v = home_km(r, sp)
    else: v = FIELDS[f][1](r)
    if v is None: return False
    if isinstance(v, list):   # activities: 'in' / '=' = has any of them, '!=' = has none
        hit = bool(set(v) & {x.strip() for x in val.split(",")})
        return not hit if op == "!=" else hit
    if op == "in": return str(v).lower() in [x.strip() for x in val.split(",")]
    a, b = _num(v), _num(val)
    if op in ("=", "!="):
        eq = (a == b) if a is not None and b is not None else str(v).lower() == val
        return eq if op == "=" else not eq
    if a is None or b is None: return False
    return {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[op]


def home_km(r, sp):   # ASSUMPTION: the LLM's coordinates of the place are approximate
    L = sp.get("location") or {}
    return float(jev_sim.km(r.lat, r.lon, L["lat"], L["lon"])) if L.get("has_point") else None


def in_area(r, sp): return sp["area"]["whole_city"] or r.district in sp["area"]["districts"]


def affected(r, sp):
    """Labels of the impact groups this persona belongs to (all rules hold; a group with no rules matches nobody)."""
    return [g["label_pl"] for g in sp["impacts"] if g["rules"] and all(holds(r, x, sp) for x in g["rules"])]


def facts(r, sp):
    eff = [g["effect_en"] for g in sp["impacts"] if g["rules"] and all(holds(r, x, sp) for x in g["rules"])]
    ps = {"direct_effect_on_them": eff or ["no direct effect on them"]}
    if not sp["area"]["whole_city"]: ps["where_it_applies"] = "in their district" if in_area(r, sp) else "not in their district"
    if (d := home_km(r, sp)) is not None:
        ps["distance_from_their_home"] = "they live right next to it" if d < 0.8 else f"about {max(1, round(d))} km from their home"
    return ps


# Sejm 2023 vote per adult persona (src/attitudes.py: PKW precincts near home x IPSOS exit-poll odds ratios, raked to districts)
USE_VOTE = os.environ.get("KS_VOTE", "1") == "1"
VOTE_EN = {"PiS": "Law and Justice (PiS, national-conservative)", "KO": "Civic Coalition (KO, liberal centre)", "TD": "Third Way (centrist / Christian-democratic)",
           "Lewica": "The Left (Lewica)", "Konf": "Confederation (Konfederacja, free-market right)", "Inne": "a small local list"}
_VOTES = None


def vote_of(r):
    global _VOTES
    if _VOTES is None:
        f = Path("out/attitudes.csv"); _VOTES = pd.read_csv(f).set_index("id").vote.to_dict() if f.exists() else {}
    return _VOTES.get(r.id)


def state_for(r, sp):
    person = person_state(r, "all" if sp.get("leisure_relevant") else "none")
    if USE_VOTE and (v := vote_of(r)): person["voted_in_2023_parliamentary_election"] = VOTE_EN[v]
    return {"person": person, "city_decision": {"title": sp["title_en"], "description": sp["description_en"]}, "personal_situation": facts(r, sp)}


def questions(sp):
    from typesafe_sdk import Choice, Noul, Score
    side = lambda s: {a["key"]: a["text_en"] for a in sp["arguments"] if a["side"] == s}
    return {"support": Noul(instructions="In a city consultation, this person would say they support this decision.",
                            criteria={"true": "supports it (somewhat or strongly)", "false": "opposes it or does not care"}),
            "stance": Score(instructions="What is this person's attitude to this decision?",
                            criteria=["strongly against", "rather against", "indifferent", "rather in favour", "strongly in favour"]),
            # one question per side (a mixed list: opponents named 'cleaner air' as their top argument); no 'none' option:
            # with it Jev picked 'none' for 96% (Karmelicka test)
            "arg_for": Choice(instructions="Of the arguments in favour of this decision, which one would matter most to this person?", criteria=side("for")),
            "arg_against": Choice(instructions="Of the arguments against this decision, which one would matter most to this person?", criteria=side("against"))}


def spec_key(sp):
    qs = {k: v.model_dump(exclude_none=True) for k, v in questions(sp).items()}
    core = {k: sp[k] for k in ("title_en", "description_en", "area", "impacts")}
    return hashlib.sha1(json.dumps([core, qs, jev_sim.pop_version()] + (["vote"] if USE_VOTE else []), sort_keys=True).encode()).hexdigest()[:10]


# Archetypes (idea from MIT Media Lab's AgentTorch): residents with the same decision-relevant traits get the answer of one
# representative. src/archetypes.py on SCT: 279 archetypes vs 3000 individual answers: mean 53.9% vs 52.3%, person-level
# r = 0.80, district map r = 0.88, two members of one archetype r = 0.93. Covers ALL adults for ~280 Jev calls.
ARCH_KEY = ["band", "sex", "status", "has_car", "aff", "inc"] + (["vote"] if os.environ.get("KS_VOTE", "1") == "1" else [])


def adults(sp):
    """Adults (18+) living in Kraków (the population of a city consultation and of the CEM poll) with archetype traits."""
    P = pd.read_csv("out/personas.csv"); K = P[(P.age >= 18) & (P.district != OUTSIDE)].copy()
    K["band"] = pd.cut(K.age, [17, 24, 34, 49, 64, 74, 120]).astype(str)
    K["inc"] = pd.qcut(K.net_income.rank(method="first"), 3, labels=["lo", "mid", "hi"]).astype(str)
    K["aff"] = ["|".join(affected(r, sp)) for r in K.itertuples()]
    K["kids"] = K.children.fillna(0) > 0
    K["vote"] = [vote_of(r) or "Inne" for r in K.itertuples()]
    return K


async def run(sp, n=1500, progress=None, seed=7, live=None, archetypes=False):
    """archetypes=False: ask a random sample of n adults one by one. archetypes=True: every adult, one Jev call per archetype."""
    from typesafe_sdk import AsyncTypeSafeClient, TypeSafeError
    K = adults(sp); cache = Path("out/policy"); cache.mkdir(parents=True, exist_ok=True)
    if not sp["area"]["whole_city"] and sp["area"]["districts"]:   # a district decision is consulted with that district's residents
        K = K[K.district.isin(sp["area"]["districts"])]
    if archetypes:
        K["arch"] = K.groupby(ARCH_KEY, sort=False).ngroup(); rng = np.random.default_rng(seed)
        reps = K.groupby("arch").id.apply(lambda s: int(rng.choice(s.values))); K["rep"] = K.arch.map(reps)
        Q = K; ask = K[K.id.isin(set(reps.values))]; cache = cache / f"{spec_key(sp)}_arch_{seed}.jsonl"
        members = K.groupby("rep").id.apply(list).to_dict()
    else:
        Q = K.sample(min(n, len(K)), random_state=seed); Q = Q.assign(rep=Q.id); ask = Q
        cache = cache / f"{spec_key(sp)}_{len(Q)}_{seed}.jsonl"; members = {i: [i] for i in Q.id}
    done = {json.loads(l)["id"]: json.loads(l) for l in cache.read_text().splitlines()} if cache.exists() else {}
    qs = questions(sp); todo = [r for r in ask.itertuples() if r.id not in done]; sem = asyncio.Semaphore(jev_sim.JEV_CONCURRENCY); k = [len(ask) - len(todo)]
    arg = lambda x: x.get("arg_for") if x["support"] > 0.5 else x.get("arg_against")
    rows_of = lambda i, x: [[m, round(x["support"], 3), arg(x)] for m in members.get(i, [])]
    if live:
        import live as lv
        await live({"type": "people", "people": lv.people(Q), "labels": {a["key"]: a["label_pl"] for a in sp["arguments"]},
                    "archetypes": int(len(ask)) if archetypes else None})
        if done: await live({"type": "answers", "rows": [row for i, x in done.items() if i in members for row in rows_of(i, x)]})
    async with AsyncTypeSafeClient(timeout=30.0) as client:
        with cache.open("a") as f:
            async def one(r):
                async with sem:
                    try: a = await client.system_one(state=state_for(r, sp), questions=qs)
                    except TypeSafeError: return
                row = {"id": int(r.id), "support": a.nouls["support"].noul, "stance": a.scores["stance"].score,
                       "arg_for": a.choices["arg_for"].choice, "arg_against": a.choices["arg_against"].choice}
                done[row["id"]] = row; f.write(json.dumps(row) + "\n"); k[0] += 1
                if live: await live({"type": "answers", "rows": rows_of(row["id"], row)})
                if progress and k[0] % 10 == 0: await progress(k[0], len(ask))
            await asyncio.gather(*(one(r) for r in todo))
    R = pd.DataFrame([x for i, x in done.items() if i in members]).rename(columns={"id": "rep"})
    A = Q.merge(R, on="rep"); A.attrs["archetypes"] = int(len(ask)) if archetypes else None
    A["groups"] = [affected(r, sp) for r in A.itertuples()]
    A["segment"] = [planner.segment(r) for r in A.itertuples()]
    return A


# ---------------- result (numbers only; every sentence comes from them)
def pct(x): return f"{x * 100:.0f}%"


def result(A, sp):
    P = pd.read_csv("out/personas.csv"); K = P[(P.age >= 18) & (P.district != OUTSIDE)]
    if not sp["area"]["whole_city"] and sp["area"]["districts"]: K = K[K.district.isin(sp["area"]["districts"])]   # shares within the area
    DS = pd.read_csv("out/districts.csv", index_col="district"); scale = DS.people.sum() / DS.personas.sum()
    # support = mean of Jev's calibrated P(support): the expected share of supporters (SCT: 55% vs CEM poll 61%; the share
    # of people with P > 0.5 gave 72%, a threshold artefact). Jev's stance Score is an expected value squeezed into ~0.5-3.2,
    # so it is only reported as a mean, not cut into for / against
    out = {"title": sp["title_pl"], "n": len(A), "archetypes": A.attrs.get("archetypes"),
           "area": None if sp["area"]["whole_city"] or not sp["area"]["districts"] else sp["area"]["districts"],
           "location": sp["location"] if sp.get("location", {}).get("has_point") else None, "support": float(A.support.mean()), "stance": float(A.stance.mean()), "assumptions": sp["assumptions_pl"], "description": sp["description_en"]}
    by = A.groupby("district").agg(support=("support", "mean"), n=("id", "size"))
    out["districts"] = {d: {"support": round(float(r.support), 3), "n": int(r.n)} for d, r in by.iterrows()}
    seg = A.groupby("segment").agg(support=("support", "mean"), n=("id", "size")).sort_values("support", ascending=False)
    out["segments"] = [{"name": k, "support": round(float(r.support), 3), "n": int(r.n)} for k, r in seg.iterrows() if r.n >= 20]
    groups = []
    for g in sp["impacts"]:
        m = A.groups.apply(lambda x: g["label_pl"] in x)
        share = np.mean([g["label_pl"] in affected(r, sp) for r in K.itertuples()]) if g["rules"] else 0
        groups.append({"name": g["label_pl"], "share": float(share), "people": int(round(share * len(K) * scale, -2)), "n": int(m.sum()),
                       "support": float(A.support[m].mean()) if m.sum() >= 10 else None, "support_rest": float(A.support[~m].mean())})
    out["groups"] = groups
    lab = {a["key"]: a for a in sp["arguments"]}
    def args(w, col):
        s = pd.Series(w.values, index=A[col].values).groupby(level=0).sum(); s = s / (s.sum() or 1)
        return [{"key": k, "label": lab[k]["label_pl"] if k in lab else "żaden z tych", "side": lab[k]["side"] if k in lab else None, "share": round(float(v), 3)}
                for k, v in s.sort_values(ascending=False).items() if v >= 0.02]
    # supporters' pick among the 'for' arguments, opponents' pick among the 'against' ones (hard split: weighting by
    # P(support) left both sides almost identical, supporters and opponents are similar people)
    out["args_for"] = args((A.support > 0.5).astype(float), "arg_for"); out["args_against"] = args((A.support < 0.5).astype(float), "arg_against")
    out["insights"] = insights(out); out["notes"] = notes(out)
    return out


def insights(o):
    """One short sentence per point; method notes go to notes (shown as small print)."""
    who = "Krakowa" if o.get("area") is None else ", ".join(o["area"])
    out = [f"Popiera {pct(o['support'])} dorosłych mieszkańców ({who})."]
    D = {k: v for k, v in o["districts"].items() if v["n"] >= 40}
    if len(D) >= 2:
        hi = max(D, key=lambda k: D[k]["support"]); lo = min(D, key=lambda k: D[k]["support"])
        if D[hi]["support"] - D[lo]["support"] >= 0.03:
            out.append(f"Najbardziej za: {hi} ({pct(D[hi]['support'])}), najmniej: {lo} ({pct(D[lo]['support'])}).")
    for g in o["groups"]:
        if g["share"] >= 0.95 or g["support_rest"] != g["support_rest"] or g["support"] is None: continue
        out.append(f"{g['name'][0].upper() + g['name'][1:]} ({pct(g['share'])} dorosłych): {pct(g['support'])} za, reszta {pct(g['support_rest'])}.")
    if len(o["segments"]) >= 2:
        s0, s1 = o["segments"][0], o["segments"][-1]
        out.append(f"Etap życia: najbardziej za {s0['name']} ({pct(s0['support'])}), najmniej {s1['name']} ({pct(s1['support'])}).")
    af = next((a for a in o["args_for"] if a["side"] == "for" and a["share"] >= 0.15), None)
    ag = next((a for a in o["args_against"] if a["side"] == "against" and a["share"] >= 0.15), None)
    if af or ag: out.append("Argumenty: " + "; ".join(x for x in [f"za „{af['label']}”" if af else "", f"przeciw „{ag['label']}”" if ag else ""] if x) + ".")
    return out


def notes(o):
    n = f"{o['n']:,}".replace(",", " ")
    out = [f"{n} symulowanych dorosłych, {o['archetypes']} archetypów (jedna decyzja AI na grupę podobnych osób)." if o.get("archetypes") else f"Próba {n} osób."]
    if o.get("archetypes") and len(o["districts"]) > 1: out.append("Różnice między dzielnicami wynikają ze składu mieszkańców i ich poglądów (wybory 2023).")
    out.append("Walidacja: SCT vs sondaż CEM 2024, 4/4 zgodnych kierunków; poziom ±10 pkt.")
    return out

if __name__ == "__main__":   # python3 src/policy.py "opis decyzji" [--n 600] [--seed 7]
    jev_sim.load_env(); a = sys.argv[1:]; opt = lambda k, d: a[a.index(k) + 1] if k in a else d
    text = a[0]; sp = spec(text); print(json.dumps({k: v for k, v in sp.items() if k != "description_en"}, ensure_ascii=False, indent=1))
    A = asyncio.run(run(sp, n=int(opt("--n", 600)), seed=int(opt("--seed", 7))))
    R = result(A, sp); print("\n".join("- " + x for x in R["insights"]))
    A.to_csv(f"out/policy/last_personas.csv", index=False)
