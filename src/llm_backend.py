"""Second decision engine: an OpenAI LLM answering the same states and questions as Jev.
Same state JSON (src/jev_sim.py, src/planner.py), same typesafe question objects (Noul / Choice); the LLM returns
a probability 0-100 per yes/no question and one option per choice question, through a strict JSON schema.
Used by src/compare_models.py to benchmark against Jev on the same personas.
Key: OPENAI_API_KEY in .env.
"""
import asyncio, json
from pathlib import Path

MODEL = "gpt-5.4-mini"
SYSTEM = """You simulate one resident of Kraków, Poland, answering a survey. You get the person's profile and a situation as JSON.
For every yes/no statement, estimate the probability (0-100) that it is true for this specific person.
Use realistic everyday knowledge: work and family schedules, days of the week, travel across Kraków, prices relative to income,
and the fact that most people do not go to most events. Judge this person, not people in general. Answer only with the JSON."""


def schema(qs):
    props, req = {}, []
    for k, q in qs.items():
        if q.type == "noul": props[k] = {"type": "integer", "description": f"probability 0-100 that: {q.instructions}"}
        elif q.type == "choice": props[k] = {"type": "string", "enum": list(q.criteria), "description": q.instructions}
        else: continue
        req.append(k)
    return {"type": "object", "properties": props, "required": req, "additionalProperties": False}


def prompt(state, qs):
    lines = []
    for k, q in qs.items():
        if q.type == "noul":
            c = q.criteria or {}
            lines.append(f"- {k}: {q.instructions}" + (f" (true = {c.get('true')}; false = {c.get('false')})" if c else ""))
        elif q.type == "choice":
            lines.append(f"- {k}: {q.instructions} Options: " + "; ".join(f"{o} = {d}" for o, d in q.criteria.items()))
    return json.dumps(state, ensure_ascii=False) + "\n\nQuestions:\n" + "\n".join(lines)


async def run(items, qs, cache: Path, conc=16, model=MODEL):
    """items: [(persona_id, state)]. Appends one JSON line per persona to cache (probabilities as 0-1)."""
    from openai import AsyncOpenAI, OpenAIError
    sem = asyncio.Semaphore(conc); fmt = {"type": "json_schema", "json_schema": {"name": "answers", "strict": True, "schema": schema(qs)}}
    stats = {"done": 0, "in": 0, "out": 0, "err": {}}
    async with AsyncOpenAI() as client:
        with cache.open("a") as f:
            async def one(pid, state):
                async with sem:
                    try:
                        r = await client.chat.completions.create(model=model, reasoning_effort="low", response_format=fmt,
                                                                 messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt(state, qs)}])
                        a = json.loads(r.choices[0].message.content)
                    except (OpenAIError, json.JSONDecodeError, KeyError) as e:
                        stats["err"][type(e).__name__] = stats["err"].get(type(e).__name__, 0) + 1
                        if sum(stats["err"].values()) <= 3: print("  !", pid, type(e).__name__, str(e)[:160])
                        return
                row = {"id": pid, **{k: (v / 100 if qs[k].type == "noul" else v) for k, v in a.items()}}
                f.write(json.dumps(row) + "\n"); stats["done"] += 1
                stats["in"] += r.usage.prompt_tokens; stats["out"] += r.usage.completion_tokens
            await asyncio.gather(*(one(i, s) for i, s in items))
    print(f"  {model}: gotowe {stats['done']}/{len(items)}, tokeny in {stats['in']:,} out {stats['out']:,}" + (f", błędy {stats['err']}" if stats["err"] else ""))
    return stats
