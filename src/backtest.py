"""Backtest: predicted vs reported attendance of real Kraków events (data/raw/events_ground_truth.json).
For every event: planner spec (category, genre, price, day, time, age, scale from capacity, setting, real venue)
-> Jev answers for a sample of personas (src/planner.py, cached) -> raw expected metro audience at that venue
(want x afford x free x HETUS day/time factor x travel decay, summed with persona weights).

Jev's absolute level is known to be too high, so the level is calibrated honestly, leave-one-out: for each event a
single multiplier is the median of log(actual / raw) over the OTHER comparable events. Reported:
  - Spearman rank correlation raw prediction vs actual (does the model order events correctly?)
  - LOO absolute % error, median and per event, vs two naive baselines (median attendance; venue capacity)
  - sold-out events separately (actual = capacity, true demand higher): is the calibrated prediction >= capacity?
Not comparable and only listed: counts of admissions / visits over several days (one person counted many times).
Caveat: the model has only the metro population; visitors from outside Kraków are missing.
Usage: python3 src/backtest.py [--n 1200] [--agent web|noweb]
  --agent: first an LLM assesses each event (src/event_agent.py: reach, headliner popularity, promotion, outside share);
           headliner and audience go into the Jev state; in code
           attendance = min(capacity, local x awareness(reach, promotion) / (1 - outside share)),
           awareness = sigmoid(a + b * (reach level + 0.5 * promotion)), a and b fitted leave-one-out.
"""
import asyncio, json, os, sys
from pathlib import Path
import numpy as np, pandas as pd
os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "__all__")
sys.path.insert(0, "src")
import jev_sim, planner
from jev_sim import travel_minutes



def scale_of(cap):
    if not cap: return None
    return "small" if cap < 300 else "club" if cap < 2000 else "medium" if cap < 8000 else "large"


def spec(e):
    cat = e["category"] if e["category"] in planner.LABELS["category"] else "warsztaty"
    g = e.get("genre") if e.get("genre") in planner.LABELS["genre"] else ("none" if cat not in planner.MUSIC else "pop")
    price = e.get("price_pln_typical")
    if price is None and cat == "sport": price = 45          # ASSUMPTION: typical Kraków league ticket (Wisła 45-65 zł)
    return {"category": cat, "genre": g, "price": float(price or 0), "day": e.get("weekday_of_main_day") or "Sat",
            "time": e.get("time_of_day") or "evening", "age": e.get("target_age") or "all", "scale": scale_of(e.get("venue_capacity")) or "large",
            "setting": e.get("setting") or "indoor"}


def kind(e):
    """unique = people at one event (comparable); sold_out = attendance capped by capacity; not_unique = admissions,
    visits summed over days or venues, month-long estimates, or participants rather than audience (listed only)."""
    m = (e.get("attendance_measure") or "").lower()
    if any(x in m for x in ("admission", "summed", "visits", "total over", "whole period", "runners")) or (e.get("n_days") or 1) > 7: return "not_unique"
    if e.get("sold_out"): return "sold_out"
    return "unique"


def predict(e, n, w15, meta=None):
    s = spec(e)
    if meta: s["meta"] = meta
    A = asyncio.run(planner.run(s, n=n))
    mins = np.array([travel_minutes(r, e["venue_lat"], e["venue_lon"]) for r in A.itertuples()])
    p = A.p.values * 0.5 ** (np.maximum(0, mins - 15) / planner.halflife(s))
    return float(p.sum() * w15 / len(A)), s


def main():
    jev_sim.load_env(); args = sys.argv[1:]
    n = int(args[args.index("--n") + 1]) if "--n" in args else 1200
    E = json.load(open("data/raw/events_ground_truth.json"))
    E = [e for e in E if e.get("attendance_reported") and e.get("venue_lat") and e.get("venue_lon")]
    P = pd.read_csv("out/personas.csv"); D = pd.read_csv("out/districts.csv")
    w15 = D.people.sum() / D.personas.sum() * (P.age >= 15).sum()   # people aged 15+ represented by the whole persona set
    rows = []
    for e in E:
        raw, s = predict(e, n, w15)
        rows.append(dict(name=f"{e['name']} {e.get('edition_year', '')}".strip(), kind=kind(e), actual=float(e["attendance_reported"]),
                         cap=e.get("venue_capacity"), raw=raw, days=e.get("n_days") or 1, cat=s["category"], price=s["price"], day=s["day"]))
        print(f"  {rows[-1]['name'][:45]:45s} {rows[-1]['kind']:10s} raw {raw:>10,.0f}  actual {rows[-1]['actual']:>10,.0f}")
    R = pd.DataFrame(rows); R["log_ratio"] = np.log(R.actual / R.raw)
    U = R[R.kind == "unique"].copy()
    print(f"\nWydarzenia: {len(R)} (porównywalne: {len(U)}, wyprzedane: {(R.kind == 'sold_out').sum()}, liczone wielokrotnie: {(R.kind == 'not_unique').sum()})")
    if len(U) >= 3:
        from scipy.stats import spearmanr
        rho = spearmanr(U.raw, U.actual).correlation
        U["pred_loo"] = [r.raw * np.exp(np.median(U.log_ratio.drop(i))) for i, r in U.iterrows()]
        U["err"] = (U.pred_loo / U.actual - 1)
        med_att = [np.median(U.actual.drop(i)) for i in U.index]
        base_med = np.median(abs(np.array(med_att) / U.actual - 1))
        capU = U.dropna(subset=["cap"])
        base_cap = np.median(abs(capU.cap / capU.actual - 1)) if len(capU) else np.nan
        print(f"Spearman (kolejność wydarzeń, surowa predykcja vs prawda): {rho:.2f}")
        print(f"Mediana błędu po kalibracji leave-one-out: {np.median(abs(U.err)):.0%}  |  naiwnie (mediana innych wydarzeń): {base_med:.0%}  |  naiwnie (pojemność miejsca): {base_cap:.0%}")
        print(f"Wspólny mnożnik poziomu (wszystkie porównywalne): {np.exp(np.median(U.log_ratio)):.3f}")
        print("\n" + U[["name", "actual", "pred_loo", "err", "cap", "price", "day"]].assign(err=lambda x: (x.err * 100).round(0).astype(int).astype(str) + "%")
              .rename(columns={"actual": "prawda", "pred_loo": "model (LOO)", "err": "błąd", "cap": "pojemność", "price": "cena", "day": "dzień"})
              .round(0).to_string(index=False))
        k = np.exp(np.median(U.log_ratio))
    else:
        k = np.exp(np.median(R.log_ratio)); print("Za mało porównywalnych wydarzeń do leave-one-out.")
    S = R[R.kind == "sold_out"].copy()
    if len(S):
        S["pred"] = S.raw * k
        print("\nWyprzedane (prawdziwy popyt ≥ pojemność): model po kalibracji vs pojemność")
        print(S.assign(ok=S.pred >= 0.8 * S.actual)[["name", "actual", "pred", "ok"]].round(0).to_string(index=False))
    N = R[R.kind == "not_unique"].copy()
    if len(N):
        N["pred_unique"] = N.raw * k
        print("\nLiczone wielokrotnie (wejściówki, wizyty z wielu dni): tylko dla orientacji, model liczy unikalne osoby z aglomeracji")
        print(N[["name", "actual", "pred_unique", "days"]].round(0).to_string(index=False))
    R.to_csv("out/backtest.csv", index=False); print("\n→ out/backtest.csv")


def venue_capacity(e):
    """Capacity known before the event: nearest venue from viz/venues.json within 300 m (e.g. Stadion Cracovii)."""
    best = min(planner.VENUES, key=lambda v: jev_sim.km(e["venue_lat"], e["venue_lon"], *v["latlon"]))
    return best["capacity"] if jev_sim.km(e["venue_lat"], e["venue_lon"], *best["latlon"]) < 0.3 else None


OUT_MID = {"0-10": 0.05, "10-30": 0.2, "30-60": 0.45, "60+": 0.7}


DIRECT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["expected_attendance", "rationale"],
                 "properties": {"expected_attendance": {"type": "integer", "description": "expected number of people attending"},
                                "rationale": {"type": "string"}}}


def llm_direct(e, model="gpt-5.4"):
    """Baseline: just ask the LLM (no web search) how many people will come. Cached."""
    import event_agent
    event_agent.load_env()
    from openai import OpenAI
    d = event_agent.describe(e); f = Path("out/event_meta") / f"direct_{__import__('hashlib').sha1((d + model).encode()).hexdigest()[:12]}.json"
    if f.exists(): return json.loads(f.read_text())
    r = OpenAI().responses.create(model=model, reasoning={"effort": "low"}, input=d,
        instructions="You are an event analyst in Kraków, Poland. Before the event takes place, estimate how many people will attend it. "
                     "Do not use any knowledge of the actual attendance, ticket sales or sell-out of this specific event. Answer only with the JSON.",
        text={"format": {"type": "json_schema", "name": "direct", "schema": DIRECT_SCHEMA, "strict": True}})
    out = json.loads(r.output_text); f.parent.mkdir(parents=True, exist_ok=True); f.write_text(json.dumps(out, ensure_ascii=False)); return out


def agent_backtest(n, web):
    """Workflow: event agent -> Jev with headliner/audience -> awareness, outside visitors, capacity in code."""
    import event_agent
    from scipy.stats import spearmanr
    jev_sim.load_env()
    E = [e for e in json.load(open("data/raw/events_ground_truth.json")) if e.get("attendance_reported") and e.get("venue_lat") and kind(e) == "unique"]
    P = pd.read_csv("out/personas.csv"); D = pd.read_csv("out/districts.csv")
    w15 = D.people.sum() / D.personas.sum() * (P.age >= 15).sum()
    # stable ratings: median of 3 independent assessments, then the same headliner (club / artist) gets the same reach and
    # popularity across its events (one Wisła match was rated 'regional / medium', the others 'national / high')
    metas = [event_agent.assess_stable(e, web=web, runs=3) for e in E]
    groups = {}
    for i, m in enumerate(metas): groups.setdefault((E[i]["category"], m["headliner"].lower().split(" – ")[0].split(" vs")[0][:20]), []).append(i)
    for key, idx in groups.items():
        if len(idx) > 1:
            for k in ("reach", "headliner_popularity"):
                sc = event_agent.ORD[k]; med = sorted(sc.index(metas[i][k]) for i in idx)[len(idx) // 2]
                for i in idx: metas[i][k] = sc[med]
    rows = []
    for e, m in zip(E, metas):
        local, _ = predict(e, n, w15, meta=m)
        base, _ = predict(e, n, w15)                       # same event without the agent, for comparison
        actual = float(e["attendance_reported"])
        txt = m["rationale"].lower(); nums = [float(x.replace(" ", "").replace(",", "")) for x in __import__("re").findall(r"\d[\d ,]{2,}", txt)]
        leak = any(abs(v / actual - 1) < 0.15 for v in nums if v > 0) or "sold out" in txt or "wyprzed" in txt
        cap = e.get("venue_capacity") or venue_capacity(e)
        direct = float(llm_direct(e)["expected_attendance"])
        rows.append(dict(name=f"{e['name'][:42]}", actual=actual, cap=cap, local=local, base=base, leak=leak, direct=direct,
                         reach=event_agent.REACH.index(m["reach"]), promo=event_agent.PROMO.index(m["promotion"]) - 1,
                         out=OUT_MID[m["outside_share"]], pop=m["headliner_popularity"], reach_lbl=m["reach"], promo_lbl=m["promotion"]))
        print(f"  {rows[-1]['name']:42s} {m['reach']:13s} {m['headliner_popularity']:18s} {m['promotion']:6s} out {m['outside_share']:5s} "
              f"local {local:>9,.0f}  actual {actual:>8,.0f}" + ("  [!leak?]" if leak else ""))
    R = pd.DataFrame(rows)
    sig = lambda x: 1 / (1 + np.exp(-x))
    def pred(df, a, b):
        p = df.local * sig(a + b * (df.reach + 0.5 * df.promo)) / (1 - df.out)
        return np.where(df.cap.notna(), np.minimum(p, df.cap.fillna(np.inf)), p)
    grid = [(a, b) for a in np.arange(-9, 1.01, 0.25) for b in np.arange(0, 3.01, 0.1)]
    def fit(df): return min(grid, key=lambda ab: np.median(abs(np.log(pred(df, *ab) / df.actual))))
    R["pred_loo"] = [pred(R.loc[[i]], *fit(R.drop(i)))[0] for i in R.index]
    R["base_loo"] = [r.base * np.exp(np.median(np.log(R.actual.drop(i) / R.base.drop(i)))) for i, r in R.iterrows()]
    a, b = fit(R)
    err = lambda p: np.median(abs(p / R.actual - 1))
    capR = R.dropna(subset=["cap"])
    print(f"\nAgent {'z wyszukiwarką' if web else 'bez wyszukiwarki'} ({event_agent.MODEL}), {len(R)} porównywalnych wydarzeń; podejrzenie przecieku: {int(R.leak.sum())}")
    print(f"  {'wariant':38s} {'mediana błędu':>14s} {'Spearman':>9s}")
    print(f"  {'agent + Jev + świadomość (LOO)':38s} {err(R.pred_loo):>14.0%} {spearmanr(R.pred_loo, R.actual).correlation:>9.2f}")
    print(f"  {'bez agenta (sam Jev, LOO)':38s} {err(R.base_loo):>14.0%} {spearmanr(R.base_loo, R.actual).correlation:>9.2f}")
    print(f"  {'sam LLM (gpt-5.4, wprost, bez kalibracji)':38s} {err(R.direct):>14.0%} {spearmanr(R.direct, R.actual).correlation:>9.2f}")
    print(f"  {'naiwnie: mediana innych wydarzeń':38s} {err(np.array([np.median(R.actual.drop(i)) for i in R.index])):>14.0%} {'–':>9s}")
    print(f"  {'naiwnie: pojemność (gdzie znana)':38s} {np.median(abs(capR.cap / capR.actual - 1)):>14.0%} {'–':>9s}")
    print(f"  świadomość (dopasowanie na wszystkich): a={a:.2f} b={b:.1f} → " + ", ".join(f"{r}: {sig(a + b * i):.1%}" for i, r in enumerate(event_agent.REACH)))
    print("\n" + R[["name", "actual", "pred_loo", "direct", "reach_lbl", "promo_lbl", "pop"]].assign(
        err=lambda x: ((x.pred_loo / x.actual - 1) * 100).round(0).astype(int).astype(str) + "%").round(0).to_string(index=False))
    R.to_csv(f"out/backtest_agent_{'web' if web else 'noweb'}.csv", index=False)


if __name__ == "__main__":
    if "--agent" in sys.argv:
        n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 1200
        agent_backtest(n, web=sys.argv[sys.argv.index("--agent") + 1] != "noweb")
    else:
        main()
