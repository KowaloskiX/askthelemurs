"""Follow-up questions after every answer: 3 short Polish questions the user could ask next, that the app can answer
(change one parameter of the event or decision, zoom into a district or group, or ask the data agent). gpt-5.4-mini,
strict JSON, cached per (question, result summary). ~0.1 cent per answer."""
import hashlib, json
from pathlib import Path
import event_agent

MODEL = "gpt-5.4-mini"
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["questions"],
          "properties": {"questions": {"type": "array", "items": {"type": "string", "description": "max 80 characters, Polish, natural"}}}}
SYSTEM = """You suggest 3 follow-up questions for an app that simulates Kraków's residents (built from GUS data). The app can:
plan events (where, when, how many people; changing price, day, size, indoor/outdoor), test city decisions (support by district, group,
age, political views; changing details), and answer data questions about residents, districts, elections (PKW), services and competition
(OpenStreetMap), public-transport travel times, safety. Given the user's question and a short summary of the answer, propose 3 different,
concrete next questions a curious user would ask: one that changes a key detail ("a gdyby..."), one that zooms in (a district or group
mentioned in the answer), one that opens a related angle. Short, natural Polish, no numbering, max 80 characters each. Answer only with the JSON."""


def make(question, mode, summary, cache_dir="out/agent"):
    event_agent.load_env()
    inp = json.dumps({"mode": mode, "question": question, "answer_summary": summary[:1500]}, ensure_ascii=False)
    f = Path(cache_dir) / f"fu_{hashlib.sha1(f'{inp}|{MODEL}|{SYSTEM}'.encode()).hexdigest()[:12]}.json"
    if f.exists(): return json.loads(f.read_text())
    from openai import OpenAI
    r = OpenAI().responses.create(model=MODEL, reasoning={"effort": "low"}, instructions=SYSTEM, input=inp,
                                  text={"format": {"type": "json_schema", "name": "followups", "schema": SCHEMA, "strict": True}})
    out = [q.strip() for q in json.loads(r.output_text)["questions"] if q.strip()][:3]
    f.parent.mkdir(parents=True, exist_ok=True); f.write_text(json.dumps(out, ensure_ascii=False))
    return out
