"""Jev vs an OpenAI LLM on the same personas, on the three tests where Jev was weak or strong:
 1. GUS calibration (culture questions, persona without hobbies) -> correlation with GUS by age x sex, level
 2. day of week: the same hip-hop concert on Monday vs Saturday -> does Saturday win?
 3. travel: the same pop concert at TAURON Arena vs Klub Studio -> do people prefer the closer venue?
Jev answers come from existing caches when available (otherwise Jev is asked too).
Usage: python3 src/compare_models.py [--model gpt-5.4-mini] [--n 300]
"""
import asyncio, glob, json, os, sys
from pathlib import Path
import numpy as np, pandas as pd
os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "__all__")
sys.path.insert(0, "src")
import jev_sim, planner, llm_backend, bio
from schema import load_scenario

OUT = Path("out/compare"); OUT.mkdir(parents=True, exist_ok=True)
lg = lambda p: np.log(np.clip(p, .01, .99) / (1 - np.clip(p, .01, .99)))


def env():
    jev_sim.load_env()


def cached(path):
    return pd.read_json(path, lines=True).drop_duplicates("id").set_index("id") if Path(path).exists() and Path(path).stat().st_size else None


async def answers(engine, items, qs, path):
    path = Path(path); have = cached(path)
    todo = [(i, s) for i, s in items if have is None or i not in have.index]
    if todo:
        if engine == "jev": await jev_sim.run(todo, qs, path, 20)
        else: await llm_backend.run(todo, qs, path, model=engine)
    return cached(path)


def test_calibration(Q, model):
    S = load_scenario("scenarios/concert.json"); items, qs = jev_sim.build(Q, S, calibrate=True)
    res = {}
    for eng in ("jev", model):
        R = asyncio.run(answers(eng, items, qs, OUT / f"cal_{eng}.jsonl")); A = Q.set_index("id").join(R, how="inner")
        A["band"] = A.age.map(bio.band5); rows = []
        for k in jev_sim.GUS_CAL:
            gus = np.array([bio.culture_rate(bio.CU[k], a, s) for a, s in zip(A.age, A.sex)])
            g = pd.DataFrame({"band": A.band, "sex": A.sex, "m": A[k], "gus": gus}).groupby(["band", "sex"]).agg(m=("m", "mean"), gus=("gus", "mean"), n=("m", "size"))
            rows.append(dict(question=k, model=np.average(g.m, weights=g.n), gus=np.average(g.gus, weights=g.n), corr=g.m.corr(g.gus),
                             mae=np.average(abs(g.m - g.gus), weights=g.n)))
        res[eng] = pd.DataFrame(rows).set_index("question")
    return res


def test_day(Q, model):
    base = {"category": "koncert", "genre": "hip-hop", "price": 60.0, "time": "evening", "age": "18-30", "scale": "club", "setting": "indoor"}
    out = {}
    for eng in ("jev", model):
        for d in ("Mon", "Sat"):
            s = {**base, "day": d}; items = [(int(r.id), planner.state_for(r, s)) for r in Q.itertuples()]
            R = asyncio.run(answers(eng, items, planner.questions(s), OUT / f"day_{d}_{eng}.jsonl"))
            R["p"] = R.want * R.free * R.afford
            out[(eng, d)] = R
    return out


def test_travel(Q, model):
    S0 = load_scenario("scenarios/concert.json")
    V = {"tauron": ((50.0675, 19.9915), "TAURON Arena Kraków (Czyżyny district, east of the city centre)"),
         "studio": ((50.0688, 19.9070), "Klub Studio arena-style hall (Krowodrza district, west of the city centre, next to the AGH campus)")}
    out = {}
    for eng in ("jev", model):
        for k, (ll, place) in V.items():
            S = S0.model_copy(update={"venue_latlon": ll, "description_en": S0.description_en.replace(
                "TAURON Arena Kraków (Czyżyny district, east of the city centre, about 15 minutes by tram from the Main Square)", place)})
            items, qs = jev_sim.build(Q, S, False)
            qs = {k2: v for k2, v in qs.items() if k2 in ("want", "afford", "free", "reason")}
            R = asyncio.run(answers(eng, items, qs, OUT / f"travel_{k}_{eng}.jsonl"))
            R["p"] = R.want * R.afford * R.free
            R["mins"] = pd.Series({int(r.id): jev_sim.travel_minutes(r, *ll) for r in Q.itertuples()})
            out[(eng, k)] = R
    return out


def main():
    env(); args = sys.argv[1:]
    model = args[args.index("--model") + 1] if "--model" in args else llm_backend.MODEL
    n = int(args[args.index("--n") + 1]) if "--n" in args else 300
    P = pd.read_csv("out/personas.csv"); Q = P[P.age >= 15].sample(n, random_state=7)

    print(f"=== 1. Kalibracja GUS ({n} osób, bez hobby w opisie) ===")
    C = test_calibration(Q, model)
    T = pd.concat({e: C[e][["model", "corr", "mae"]] for e in C}, axis=1); T[("GUS", "poziom")] = C["jev"].gus
    print(T.round(3).to_string())

    print(f"\n=== 2. Dzień tygodnia: koncert hip-hop, poniedziałek vs sobota ({n} osób) ===")
    D = test_day(Q, model)
    for e in ("jev", model):
        m, s = D[(e, "Mon")], D[(e, "Sat")]
        print(f"  {e:14s} P(pójdzie) pon {m.p.mean():.3f}  sob {s.p.mean():.3f}  sob/pon {s.p.mean() / m.p.mean():.2f}×   "
              f"ma czas pon {m.free.mean():.2f} sob {s.free.mean():.2f}")

    print(f"\n=== 3. Dojazd: koncert pop w Tauronie vs Klubie Studio ({n} osób) ===")
    Tr = test_travel(Q, model)
    for e in ("jev", model):
        t, w = Tr[(e, "tauron")], Tr[(e, "studio")]; ids = t.index.intersection(w.index)
        dl = lg(w.p[ids]) - lg(t.p[ids]); dm = w.mins[ids] - t.mins[ids]
        slope = np.polyfit(dm, dl, 1)[0] * 10
        near_w = dm < -15; near_t = dm > 15
        print(f"  {e:14s} logit na +10 min dojazdu {slope:+.3f}   bliżej Studia: Tauron {t.p[ids][near_w].mean():.3f} → Studio {w.p[ids][near_w].mean():.3f}   "
              f"bliżej Tauronu: Tauron {t.p[ids][near_t].mean():.3f} → Studio {w.p[ids][near_t].mean():.3f}")

    print(f"\n=== 4. Różnice między grupami (koncert hip-hop, sobota) ===")
    Qi = Q.set_index("id")
    for e in ("jev", model):
        R = D[(e, "Sat")].join(Qi, how="inner"); R["seg"] = [planner.segment(r) for r in R.itertuples()]
        lift = (R.groupby("seg").p.mean() / R.p.mean()).round(2).sort_values(ascending=False)
        print(f"  {e}: " + ", ".join(f"{k} {v}×" for k, v in lift.items()))


if __name__ == "__main__":
    main()
