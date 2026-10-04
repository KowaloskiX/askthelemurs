"""Attendance forecast = ensemble of three independent predictors, capped by venue capacity. Evaluated leave-one-out
on the comparable real events (out/backtest_agent_web.csv from `python3 src/backtest.py --agent web`).
  1. LLM direct: gpt-5.4 (no web) estimates attendance from the event description incl. date / weekday; median of 3 samples.
  2. Agent + residents: event agent metadata -> Jev x synthetic residents -> awareness, outside visitors (backtest.py, LOO).
  3. Analogs: Jev scores how similar each pair of events is for audience size (Score 0-4, from description + agent
     metadata); the forecast transfers the fill rate (attendance / capacity) of the most similar OTHER events, or their
     attendance when capacity is unknown. Retrieval over our event database; with hundreds of events this becomes a
     search index (Elastic / vector DB), with 13 a JSON file is enough.
Ensemble = geometric mean of the available predictors, then min(capacity). No weights are fitted (13 events).
Usage: python3 src/forecast.py
"""
import asyncio, hashlib, json, os, sys
from pathlib import Path
import numpy as np, pandas as pd
os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "__all__")
sys.path.insert(0, "src")
import jev_sim, event_agent, backtest

OUT = Path("out/event_meta")


def direct_samples(e, k=3):
    """Median of k LLM-direct estimates (one sample once returned 0)."""
    vals = []
    for i in range(k):
        f = OUT / f"direct_{hashlib.sha1((event_agent.describe(e) + 'gpt-5.4').encode()).hexdigest()[:12]}{'' if i == 0 else f'_s{i}'}.json"
        if not f.exists():
            from openai import OpenAI
            r = OpenAI().responses.create(model="gpt-5.4", reasoning={"effort": "low"}, input=event_agent.describe(e),
                instructions="You are an event analyst in Kraków, Poland. Before the event takes place, estimate how many people will attend it. "
                             "Do not use any knowledge of the actual attendance, ticket sales or sell-out of this specific event. Answer only with the JSON.",
                text={"format": {"type": "json_schema", "name": "direct", "schema": backtest.DIRECT_SCHEMA, "strict": True}})
            f.write_text(r.output_text)
        v = json.loads(f.read_text())["expected_attendance"]
        if v > 0: vals.append(v)
    return float(np.median(vals)) if vals else np.nan


def card(e, m):
    """Short structured description used for similarity (no attendance!)."""
    return {"event": e["name"], "category": e["category"], "genre": e.get("genre"), "venue": e.get("venue_name"),
            "venue_capacity": e.get("venue_capacity") or backtest.venue_capacity(e), "setting": e.get("setting"),
            "weekday": e.get("weekday_of_main_day"), "time": e.get("time_of_day"), "month": (e.get("start_date") or "")[5:7],
            "price_pln": e.get("price_pln_typical"), "headliner": m["headliner"], "headliner_popularity": m["headliner_popularity"],
            "reach": m["reach"], "promotion": m["promotion"], "outside_share": m["outside_share"]}


async def similarity(cards):
    """Jev Score for every unordered pair: how similar the two events are in expected audience size and draw."""
    from typesafe_sdk import Score
    q = {"sim": Score(instructions="How similar are event A and event B in the number of people they will draw "
                                   "(consider type, headliner popularity, reach, promotion, price, venue size, day and time)?",
                      criteria=["very different", "somewhat different", "moderately similar", "similar", "nearly the same draw"])}
    pairs = [(i, j) for i in range(len(cards)) for j in range(i + 1, len(cards))]
    cache = OUT / "similarity.json"; S = json.loads(cache.read_text()) if cache.exists() else {}
    key = lambda i, j: hashlib.sha1(json.dumps([cards[i], cards[j]], sort_keys=True).encode()).hexdigest()[:12]
    todo = [(i, j) for i, j in pairs if key(i, j) not in S]
    if todo:
        from typesafe_sdk import AsyncTypeSafeClient
        sem = asyncio.Semaphore(20)
        async with AsyncTypeSafeClient(timeout=30.0) as c:
            async def one(i, j):
                async with sem:
                    r = await c.system_one(state={"event_A": cards[i], "event_B": cards[j]}, questions=q)
                S[key(i, j)] = r.scores["sim"].score
            await asyncio.gather(*(one(i, j) for i, j in todo))
        cache.write_text(json.dumps(S))
    M = np.zeros((len(cards), len(cards)))
    for i, j in pairs: M[i, j] = M[j, i] = S[key(i, j)]
    return M


def analog(i, R, M, k=3):
    """Weighted transfer from the k most similar other events (weights = similarity^2)."""
    others = [j for j in np.argsort(-M[i]) if j != i][:k]
    w = np.array([M[i, j] ** 2 + 1e-6 for j in others])
    if pd.notna(R.cap[i]):
        fills = [R.actual[j] / R.cap[j] if pd.notna(R.cap[j]) else np.nan for j in others]
        ok = [x == x for x in fills]
        if any(ok): return float(R.cap[i] * np.exp(np.average(np.log(np.array(fills)[ok]), weights=w[ok])))
    return float(np.exp(np.average(np.log(R.actual.values[others]), weights=w)))


def main():
    jev_sim.load_env(); event_agent.load_env()
    from scipy.stats import spearmanr
    R = pd.read_csv("out/backtest_agent_web.csv")
    E = {e["name"][:42]: e for e in json.load(open("data/raw/events_ground_truth.json"))}
    ev = [E[n] for n in R.name]
    metas = [event_agent.assess_stable(e, web=True, runs=3) for e in ev]
    R["direct3"] = [direct_samples(e) for e in ev]
    M = asyncio.run(similarity([card(e, m) for e, m in zip(ev, metas)]))
    R["analog"] = [analog(i, R, M) for i in range(len(R))]
    cap = lambda p: np.where(R.cap.notna(), np.minimum(p, R.cap.fillna(np.inf)), p)
    preds = {"sam LLM (mediana 3)": cap(R.direct3), "agent + mieszkańcy (LOO)": cap(R.pred_loo), "analogi (Jev, LOO)": cap(R.analog)}
    logs = np.vstack([np.log(np.maximum(v, 1)) for v in preds.values()])
    preds["ENSEMBLE (średnia geometryczna)"] = cap(np.exp(np.nanmean(logs, axis=0)))
    preds["naiwnie: pojemność"] = R.cap.values
    print(f"{len(R)} wydarzeń z prawdziwą frekwencją\n{'wariant':34s} {'mediana błędu':>13s} {'średni |log|':>12s} {'Spearman':>9s} {'w ±25%':>7s}")
    for k, p in preds.items():
        ok = ~np.isnan(p); err = np.abs(p[ok] / R.actual.values[ok] - 1)
        print(f"{k:34s} {np.median(err):>13.0%} {np.mean(abs(np.log(p[ok] / R.actual.values[ok]))):>12.2f} "
              f"{spearmanr(p[ok], R.actual.values[ok]).correlation if ok.sum() > 2 else float('nan'):>9.2f} {np.mean(err <= .25):>7.0%}" + (f"  (n={ok.sum()})" if ok.sum() < len(R) else ""))
    T = R[["name", "actual", "cap"]].copy()
    for k in ["sam LLM (mediana 3)", "agent + mieszkańcy (LOO)", "analogi (Jev, LOO)", "ENSEMBLE (średnia geometryczna)"]: T[k.split(" ")[0]] = preds[k]
    print("\n" + T.round(0).to_string(index=False))
    T.to_csv("out/forecast_backtest.csv", index=False)


if __name__ == "__main__":
    main()
