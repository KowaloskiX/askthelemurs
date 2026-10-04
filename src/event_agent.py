"""Event assessment agent: before asking the residents, an LLM (optionally with web search) judges the SUPPLY side
of an event that the resident model cannot know: reach, headliner popularity, promotion, share of visitors from
outside Kraków, special factors. Typed output (strict JSON schema), cached per event.
The agent must NOT look up or use attendance / tickets sold / sold-out status of the event (backtest leakage);
src/backtest.py additionally scans the rationale for the reported number.
Search backend: OpenAI Responses API `web_search` tool (swap `assess()` to use Perplexity/Google if needed).
Key: OPENAI_API_KEY in .env.
"""
import hashlib, json, os
from pathlib import Path

MODEL = "gpt-5.4"
REACH = ["neighbourhood", "city", "regional", "national", "international"]
POP = ["none", "local", "national", "national_top", "international_star"]
PROMO = ["low", "medium", "high"]
OUTSIDE = ["0-10", "10-30", "30-60", "60+"]
SPECIAL = ["rivalry_derby", "public_holiday", "first_edition", "free_entry", "tourist_season", "family_event", "niche_audience", "none"]
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["reach", "headliner", "headliner_popularity", "promotion", "outside_share", "special", "audience", "rationale"],
          "properties": {
              "reach": {"type": "string", "enum": REACH, "description": "from how far people realistically come and know about it"},
              "headliner": {"type": "string", "description": "main artist / team / attraction, or 'none'"},
              "headliner_popularity": {"type": "string", "enum": POP},
              "promotion": {"type": "string", "enum": PROMO, "description": "how widely the event is promoted and talked about in Kraków"},
              "outside_share": {"type": "string", "enum": OUTSIDE, "description": "share of the audience from outside the Kraków metropolitan area"},
              "special": {"type": "array", "items": {"type": "string", "enum": SPECIAL}},
              "audience": {"type": "string", "description": "one sentence in English: who this event is for"},
              "rationale": {"type": "string", "description": "2-3 sentences: why these ratings. No attendance or ticket-sales numbers."}}}
SYSTEM = """You assess an event in Kraków, Poland, as an experienced event analyst would BEFORE it takes place.
Rate its reach, the popularity of its headliner, how widely it is promoted, the likely share of visitors from outside the Kraków
metropolitan area, and special factors. Base your judgement on the headliner, organiser, format, venue, price and date.
Do NOT search for, use or mention the attendance, number of tickets sold, or whether this specific event sold out. If you come
across such numbers, ignore them. Answer only with the JSON."""


def load_env():
    if Path(".env").exists():
        for l in Path(".env").read_text().splitlines():
            if "=" in l and not l.lstrip().startswith("#"):
                k, v = l.split("=", 1); os.environ.setdefault(k.strip(), v.strip().strip('"'))


def describe(e):
    keys = ["name", "edition_year", "start_date", "end_date", "weekday_of_main_day", "time_of_day", "venue_name", "venue_capacity",
            "setting", "category", "genre", "artist_or_headliner", "price_pln_typical", "organiser_description", "expected_scale", "target_age"]
    return json.dumps({k: e.get(k) for k in keys if e.get(k) is not None}, ensure_ascii=False)


def assess(e, web=True, model=MODEL, cache_dir="out/event_meta", run=0):
    """Event dict (fields as in data/raw/events_ground_truth.json or a planner spec) -> metadata dict.
    `run` > 0 asks again (independent sample) for consensus()."""
    load_env()
    from openai import OpenAI
    d = describe(e); key = hashlib.sha1(f"{d}|{web}|{model}|{SYSTEM}".encode() + (f"|run{run}".encode() if run else b"")).hexdigest()[:12]
    p = Path(cache_dir); p.mkdir(parents=True, exist_ok=True); f = p / f"{key}.json"
    if f.exists(): return json.loads(f.read_text())
    r = OpenAI().responses.create(model=model, reasoning={"effort": os.environ.get("KS_EFFORT", "low")}, instructions=SYSTEM, input=d,
                                  tools=[{"type": "web_search"}] if web else [],
                                  text={"format": {"type": "json_schema", "name": "event_meta", "schema": SCHEMA, "strict": True}})
    meta = json.loads(r.output_text); meta["_web"] = web; meta["_model"] = model
    meta["_tokens"] = [r.usage.input_tokens, r.usage.output_tokens]
    f.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return meta


EST_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["expected_attendance", "rationale"],
              "properties": {"expected_attendance": {"type": "integer", "description": "expected number of people attending"},
                             "rationale": {"type": "string"}}}


def estimate(e, model=MODEL, run=0, cache_dir="out/event_meta"):
    """LLM's own attendance estimate (no web search). Backtest: best single predictor for events without a capacity limit."""
    load_env()
    from openai import OpenAI
    d = describe(e); f = Path(cache_dir) / f"est_{hashlib.sha1(f'{d}|{model}|{run}'.encode()).hexdigest()[:12]}.json"
    if f.exists(): return json.loads(f.read_text())["expected_attendance"]
    r = OpenAI().responses.create(model=model, reasoning={"effort": "low"}, input=d,
        instructions="You are an event analyst in Kraków, Poland. Before the event takes place, estimate how many people will attend it. "
                     "Do not use any knowledge of the actual attendance, ticket sales or sell-out of this specific event. Answer only with the JSON.",
        text={"format": {"type": "json_schema", "name": "estimate", "schema": EST_SCHEMA, "strict": True}})
    Path(cache_dir).mkdir(parents=True, exist_ok=True); f.write_text(r.output_text)
    return json.loads(r.output_text)["expected_attendance"]


ORD = {"reach": REACH, "headliner_popularity": POP, "promotion": PROMO, "outside_share": OUTSIDE}


def consensus(metas):
    """Several independent assessments -> one: median of each ordinal rating (single LLM ratings were inconsistent,
    e.g. the same club rated 'regional / medium' once and 'national / high' otherwise); headliner and audience from the first."""
    out = dict(metas[0])
    for k, scale in ORD.items():
        out[k] = scale[int(round(float(sorted(scale.index(m[k]) for m in metas)[len(metas) // 2])))]
    out["special"] = [x for x in SPECIAL if sum(x in m["special"] for m in metas) * 2 > len(metas)] or ["none"]
    out["_runs"] = len(metas)
    return out


def assess_stable(e, web=True, runs=3):
    return consensus([assess(e, web=web, run=i) for i in range(runs)])


if __name__ == "__main__":
    import sys
    E = json.load(open("data/raw/events_ground_truth.json"))
    e = next(x for x in E if sys.argv[1].lower() in x["name"].lower()) if len(sys.argv) > 1 else E[0]
    print(json.dumps(assess(e, web="--no-web" not in sys.argv), ensure_ascii=False, indent=1))
