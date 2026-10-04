"""Real GUS numbers next to every simulation: the agent picks the Kraków BDL tables that matter for the user's question
(data/raw/krakow_gus_extra.json, 53 tables, fetched by src/fetch_bdl_extra.py) and writes 2-4 short Polish facts.
Grounding check in code: every number in a fact must appear in the cited table (allowing rounding and 'tys.'/'mln');
facts that fail are dropped. Mode-agnostic: used for events and city decisions.
Key: OPENAI_API_KEY in .env. One gpt-5.4-mini call (~8k input tokens), cached per question.
"""
import hashlib, json, re
from pathlib import Path
import event_agent

MODEL = "gpt-5.4-mini"
RAW = json.load(open("data/raw/krakow_gus_extra.json"))
TABLES = {f"{g}.{k}": v for g, items in RAW.items() if not g.startswith("_") and isinstance(items, dict)
          for k, v in items.items() if isinstance(v, dict) and "value" in v}


def _annual(x):
    """Monthly series {'01': .., ..., '12': ..} -> mean of the months (the model quoted one month as 'the 2025 median')."""
    if isinstance(x, dict) and x and all(k.isdigit() and len(k) == 2 for k in x):
        vals = [v for v in x.values() if isinstance(v, (int, float))]; return {"mean_of_monthly_values": round(sum(vals) / len(vals), 2)} if vals else x
    return {k: _annual(v) for k, v in x.items()} if isinstance(x, dict) else x


def compact(v):
    """Table without BDL variable ids: what the LLM needs to read it."""
    return {k: (_annual(v[k]) if k == "value" else v[k]) for k in ("value", "unit", "year", "level", "note") if k in v}


SCHEMA = {"type": "object", "additionalProperties": False, "required": ["facts"],
          "properties": {"facts": {"type": "array", "description": "0-4 facts that help judge the user's question",
                                   "items": {"type": "object", "additionalProperties": False, "required": ["table", "text_pl"],
                                             "properties": {"table": {"type": "string", "enum": list(TABLES)},
                                                            "text_pl": {"type": "string", "description": "one short Polish sentence with the number(s) exactly as in the table "
                                                                                                         "(you may round or write 'tys.' / 'mln'), the year, and what it means for the question"}}}}}}
SYSTEM = """You help a user who asks about Kraków (an event they plan, or a city decision). Below are official GUS (Statistics Poland) tables for Kraków.
Pick at most 4 facts that genuinely help judge the user's question (scale of the affected group, existing supply, demand, context).
Use only numbers that are in the tables; for monthly series name the month; do not compute new numbers except simple shares you can state as 'ok. X%' from two numbers of one table.
Levels: 'powiat 011212161000' and 'gmina 011212161011' both mean the city of Kraków: write 'w Krakowie', never 'powiat krakowski' (a different unit,
the suburbs). 'region ...' means the Małopolska voivodeship: say so.
If no table is relevant, return an empty list. Answer only with the JSON.
TABLES:
""" + json.dumps({k: compact(v) for k, v in TABLES.items()}, ensure_ascii=False, separators=(",", ":"))


def _numbers(text):
    """Numbers in a Polish sentence, scaled by 'tys.' / 'mln'; years are kept as they are. Thousands separators: space, NBSP, narrow NBSP."""
    out = []
    for m in re.finditer(r"(\d{1,3}(?:[ \u00a0\u202f\u2009]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)\s*(tys\.?|mln|%)?", text):
        raw = re.sub(r"[\s\u00a0\u202f\u2009]", "", m.group(1)).replace(",", ".")
        try: x = float(raw)
        except ValueError: continue
        out.append((x * {"tys": 1e3, "tys.": 1e3, "mln": 1e6}.get(m.group(2) or "", 1), m.group(2) == "%"))
    return out


def _values(v):
    if isinstance(v, dict):
        for x in v.values(): yield from _values(x)
    elif isinstance(v, (int, float)) and not isinstance(v, bool): yield float(v)


def _keys(v):
    if isinstance(v, dict):
        for k, x in v.items(): yield str(k); yield from _keys(x)


def grounded(fact, question=""):
    """Every number in the sentence is a table value (within 1% or rounding to the stated precision), a share of two values,
    or a number that is not data: the year, a category label of the table ('16-20' years), or a number from the user's question."""
    t = TABLES[fact["table"]]; vals = list(_values(t["value"])) + list(_values(_annual(t["value"])))
    labels = " ".join([str(t.get("year") or ""), str(t.get("note") or ""), question, fact["table"].replace("_", " ")] + list(_keys(t["value"])))
    free = {x for x, _ in _numbers(labels.replace("-", " ").replace("_", " "))}
    for x, is_pct in _numbers(fact["text_pl"].replace("–", " ").replace("-", " ")):
        if x in free or x == 0: continue
        if is_pct:   # a stated share: a table percentage, or a / b of two table values
            if any(abs(x - v) <= 0.6 for v in vals) or any(b and abs(100 * a / b - x) <= 0.6 for a in vals for b in vals): continue
            return False
        if not any(abs(x - v) <= max(0.01 * abs(v), 0.51) for v in vals): return False
    return True


def facts(question, cache_dir="out/agent"):
    event_agent.load_env()
    f = Path(cache_dir) / f"gus_{hashlib.sha1(f'{question}|{MODEL}|{SYSTEM}'.encode()).hexdigest()[:12]}.json"
    if f.exists(): out = json.loads(f.read_text())
    else:
        from openai import OpenAI
        r = OpenAI().responses.create(model=MODEL, reasoning={"effort": "low"}, instructions=SYSTEM, input=question,
                                      text={"format": {"type": "json_schema", "name": "gus_facts", "schema": SCHEMA, "strict": True}})
        out = json.loads(r.output_text)["facts"]
        f.parent.mkdir(parents=True, exist_ok=True); f.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    res = []
    for x in out:
        t = TABLES[x["table"]]
        res.append({**x, "ok": grounded(x, question), "source": f"GUS BDL {t.get('year', '')}, {t.get('note', x['table']).split(';')[0]}"})
    return [x for x in res if x["ok"]], [x for x in res if not x["ok"]]


if __name__ == "__main__":
    import sys
    good, bad = facts(sys.argv[1])
    for x in good: print("OK  ", x["text_pl"], "|", x["source"])
    for x in bad: print("DROP", x["text_pl"], "|", x["table"])
