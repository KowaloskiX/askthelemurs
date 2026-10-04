"""LLM layer, cheapest pattern: archetypes x packets x Batch API.
1. Personas aged 15+ are grouped into archetypes by the traits that change an answer
   (age band, status, income band, car, household, education, distance band to the scenario).
2. One representative per archetype; 10 representatives per request; answer = schema.Packet (typed).
3. Requests go through the Message Batches API (-50%) at low effort; prompt prefix cached.
4. Every persona gets its archetype's answer; results are summed by district with persona weights.

Usage: python3 src/agents.py SCENARIO.json [--mode batch|sync|manual] [--dry-run] [--packet 10] [--model claude-opus-5-5]
  --mode batch    (default) submit a batch, poll until done; re-running resumes the same batch
  --mode sync     parallel normal calls (small tests)
  --report        only summarise answers already in the cache (no API calls)
  --mode manual   write prompts to out/llm/<name>_manual_prompts.md; answers pasted as JSONL into
                  out/llm/<name>_<tag>.jsonl (lets someone answer by hand, e.g. in a chat session, for free)
Output: out/llm/<name>_<tag>.jsonl (one line per archetype), <name>_by_district.csv, <name>_personas.csv
Key: ANTHROPIC_API_KEY in env or .env.
"""
import hashlib, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema import Packet, Persona, load_scenario, LIKERT_P

MODEL = "claude-opus-5-5"
PRICE = {"in": 4, "cache_w": 5, "cache_r": 0.2, "out": 20}   # $/MTok, Opus 5.5; batch = half
RYNEK = (50.0617, 19.9373)

SYSTEM = """Jesteś symulatorem mieszkańców Krakowa w badaniu ankietowym. Dostajesz listę osób i jeden scenariusz. Każda osoba reprezentuje grupę podobnych mieszkańców. Dla każdej osoby odpowiadasz tak, jak odpowiedziałaby ona, biorąc pod uwagę jej budżet, czas, wiek, miejsce zamieszkania, dojazd, rodzinę, zainteresowania i obowiązki. Nie Twoje poglądy. Osoby są niezależne: nie porównuj ich ze sobą i nie wyrównuj odpowiedzi.

Zasady:
- Realizm: większość ludzi na większość wydarzeń nie chodzi, a większość zmian w mieście przyjmuje obojętnie albo z lekkim niezadowoleniem. „Zdecydowanie tak” i „zdecydowanie nie” rezerwuj dla osób, których scenariusz naprawdę dotyczy.
- Bez stereotypów: trzymaj się podanych cech, reszty nie dopowiadaj skrajnie.
- Cenę porównuj z dochodem osoby. Dojazd w Krakowie: 5 km komunikacją to ok. 25–35 min, autem wieczorem 15–25 min, parkowanie w centrum trudne i płatne.
- Dzielnice: Stare Miasto, Grzegórzki, Krowodrza to centrum. Nowa Huta, Bieńczyce, Mistrzejowice, Wzgórza Krzesławickie to wschód, 8–12 km od centrum. Swoszowice i Dębniki to południe, Bronowice i Prądnik Biały to północny zachód. „poza Krakowem” to podkrakowskie gminy: dojazd autem albo rzadką komunikacją.
- Powody: 1 do 3 pozycji z listy, od najważniejszego.
- Cytat: jedno zdanie, potocznie, w pierwszej osobie, jak do znajomego.
- Zwróć dokładnie jedną odpowiedź dla każdego identyfikatora z listy.

Scenariusz: {name}
{description}

Pytanie: {question}"""

AGE_BANDS = [15, 20, 25, 30, 40, 50, 65, 75, 200]
INC_BANDS = [-1, 0, 3000, 6000, 10000, 1e9]                   # net zł/month: none, <3k, 3-6k, 6-10k, 10k+
DIST_BANDS = [0, 3, 8, 15, 1e9]                                # km to the scenario point (or to Rynek)
MIN_ARCH = 12                                                  # smaller fine groups merge into a coarse one


def load_env():  # .env without python-dotenv
    if Path(".env").exists():
        for line in Path(".env").read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip().strip('"'))


def km(lat, lon, la2, lo2):
    r = np.radians; h = np.sin(r(la2 - lat) / 2) ** 2 + np.cos(r(lat)) * np.cos(r(la2)) * np.sin(r(lo2 - lon) / 2) ** 2
    return 2 * 6371 * np.arcsin(np.sqrt(h))


def archetypes(P, S):
    """Key = traits that plausibly change the answer. Returns P (15+) with an 'arch' column and one representative per arch."""
    Q = P[P.age >= 15].copy()
    pt = S.where() or RYNEK
    Q["_d"] = km(Q.lat, Q.lon, *pt)
    # scenario-relevant leisure trait from GUS surveys (src/bio.py), e.g. "goes to concerts" for a concert
    flag = {"koncert": "c_concert_other", "festiwal": "c_festival", "teatr": "c_theatre", "sport": "sport"}.get(getattr(S, "category", None))
    rel = Q[flag].astype(str) if flag and flag in Q else Q.transport.fillna("-").astype(str) if "transport" in Q else Q.has_car.astype(str)
    parts = [pd.cut(Q.age, AGE_BANDS, right=False, labels=False).astype(str), Q.sex, Q.status,
             pd.cut(Q.net_income, INC_BANDS, labels=False).astype(str), rel,
             pd.cut(Q._d, DIST_BANDS, labels=False).astype(str), (Q.district == "poza Krakowem").astype(str)]
    for col in ("household", "education"):
        if col in Q: parts.append(Q[col].fillna("-").astype(str))
    if "children" in Q: parts.append(Q.children.fillna(0).clip(upper=2).astype(int).astype(str))
    h = lambda cols: pd.Series(["|".join(t) for t in zip(*cols)], index=Q.index).map(lambda s: hashlib.md5(s.encode()).hexdigest()[:10])
    # three levels: fine (all traits) -> mid (no household/education) -> coarse (age, sex, status, scenario trait);
    # each persona takes the finest level whose group has at least MIN_ARCH personas
    levels = [h(parts), "m" + h(parts[:6]).str[1:], "c" + h([parts[0], parts[1], parts[2], parts[4]]).str[1:]]
    Q["arch"] = levels[-1]
    for lv in reversed(levels[:-1]):
        ok = lv.map(lv.value_counts()) >= MIN_ARCH
        Q.loc[ok, "arch"] = lv[ok]
    # representative = member closest to the group's median age and income
    med = Q.groupby("arch")[["age", "net_income"]].transform("median")
    Q["_r"] = (Q.age - med.age).abs() / 10 + (Q.net_income - med.net_income).abs() / 3000
    reps = Q.sort_values("_r").groupby("arch").head(1).set_index("arch")
    reps["n"] = Q.arch.value_counts()
    return Q.drop(columns=["_d", "_r"]), reps.sort_values("n", ascending=False)


def packets(reps, size):
    ids = list(reps.index)
    return [ids[i:i + size] for i in range(0, len(ids), size)]


def user_msg(reps, ids):
    """Short labels A1..A10 in the prompt (models copy them reliably); accept() maps them back by position."""
    clean = lambda d: {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in d.items()}   # empty CSV cells
    lines = [f"A{i + 1}: {Persona(**clean(reps.loc[a].to_dict())).describe()}" for i, a in enumerate(ids)]
    return "Osoby:\n" + "\n".join(lines) + "\n\nOdpowiedz za każdą osobę."


def params(system, msg, model):
    return dict(model=model, max_tokens=8000, output_config={"effort": "low"},
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": msg}])


def accept(pkt: Packet, ids, f):
    """Keep only answers for the asked ids; returns how many were saved."""
    got = {a.key.strip().upper(): a for a in pkt.answers}; n = 0
    for i, a in enumerate(ids):
        if (r := got.get(f"A{i + 1}")) is not None:
            f.write(json.dumps({"arch": a, **r.model_dump(exclude={"key"}), "p": r.p}, ensure_ascii=False) + "\n"); n += 1
    return n


def run_batch(client, todo, reps, system, model, state_path, cache):
    import anthropic
    from anthropic.types.messages.batch_create_params import Request
    state = json.loads(state_path.read_text()) if state_path.exists() else None
    if not state:
        fmt = {"type": "json_schema", "schema": anthropic.transform_schema(Packet)}
        reqs = []
        for i, ids in enumerate(todo):
            p = params(system, user_msg(reps, ids), model); p["output_config"]["format"] = fmt
            reqs.append(Request(custom_id=f"p{i}", params=p))
        b = client.messages.batches.create(requests=reqs)
        state = {"id": b.id, "packets": {f"p{i}": ids for i, ids in enumerate(todo)}}
        state_path.write_text(json.dumps(state)); print(f"batch {b.id}: {len(reqs)} zapytań wysłanych")
    while (b := client.messages.batches.retrieve(state["id"])).processing_status != "ended":
        c = b.request_counts; print(f"  {time.strftime('%H:%M:%S')} w toku {c.processing}, gotowe {c.succeeded}, błędy {c.errored}"); time.sleep(30)
    tot = {"in": 0, "cache_w": 0, "cache_r": 0, "out": 0}; saved = 0
    with cache.open("a") as f:
        for r in client.messages.batches.results(state["id"]):
            if r.result.type != "succeeded": print("  !", r.custom_id, r.result.type); continue
            m = r.result.message; u = m.usage
            tot["in"] += u.input_tokens; tot["out"] += u.output_tokens
            tot["cache_w"] += u.cache_creation_input_tokens or 0; tot["cache_r"] += u.cache_read_input_tokens or 0
            if m.stop_reason != "end_turn": print("  !", r.custom_id, m.stop_reason); continue
            try: pkt = Packet.model_validate_json(next(b.text for b in m.content if b.type == "text"))
            except Exception as e: print("  !", r.custom_id, type(e).__name__); continue
            saved += accept(pkt, state["packets"][r.custom_id], f)
    state_path.unlink()
    print(f"zapisano {saved} archetypów; tokeny {tot}; koszt ≈ {sum(tot[k] * PRICE[k] for k in tot) / 2e6:.2f} $ (batch -50%)")


def run_sync(client, todo, reps, system, model, cache, workers=6):
    def one(ids):
        try: return ids, client.messages.parse(**params(system, user_msg(reps, ids), model), output_format=Packet)
        except Exception as e: print("  !", type(e).__name__, str(e)[:100]); return ids, None
    saved = 0; tot = {"in": 0, "cache_w": 0, "cache_r": 0, "out": 0}
    with cache.open("a") as f, ThreadPoolExecutor(workers) as ex:
        for ids, r in ex.map(one, todo):
            if r is None or r.parsed_output is None: continue
            u = r.usage; tot["in"] += u.input_tokens; tot["out"] += u.output_tokens
            tot["cache_w"] += u.cache_creation_input_tokens or 0; tot["cache_r"] += u.cache_read_input_tokens or 0
            saved += accept(r.parsed_output, ids, f)
    print(f"zapisano {saved} archetypów; tokeny {tot}; koszt ≈ {sum(tot[k] * PRICE[k] for k in tot) / 1e6:.2f} $")


def main():
    load_env(); args = sys.argv[1:]
    opt = lambda k, d: args[args.index(k) + 1] if k in args else d
    path = args[0]; mode = opt("--mode", "batch"); size = int(opt("--packet", 10)); model = opt("--model", MODEL); dry = "--dry-run" in args
    S = load_scenario(path); system = SYSTEM.format(name=S.name, description=S.description, question=S.ask())
    P = pd.read_csv("out/personas.csv"); DS = pd.read_csv("out/districts.csv", index_col="district")
    scale = DS.people.sum() / DS.personas.sum()
    Q, reps = archetypes(P, S)
    tag = hashlib.sha1(f"{system}|{model}|{','.join(reps.index)}".encode()).hexdigest()[:8]   # personas or prompt change -> new cache
    out = Path("out/llm"); out.mkdir(exist_ok=True); name = Path(path).stem; cache = out / f"{name}_{tag}.jsonl"
    done = {json.loads(l)["arch"] for l in cache.read_text().splitlines()} if cache.exists() else set()
    todo = packets(reps.drop(index=[a for a in done if a in reps.index]), size)
    n_todo = sum(map(len, todo))
    print(f"{S.kind}: {S.name}\nPytanie: {S.ask()}\nOsoby 15+: {len(Q)} person → {len(reps)} archetypów "
          f"(mediana {int(reps.n.median())}, max {reps.n.max()} person na archetyp); w cache {len(done)}, do zapytania {n_todo} w {len(todo)} paczkach po {size}")

    if dry or (todo and mode == "manual"):
        est = len(todo) * (1000 * PRICE["cache_r"] + size * 70 * PRICE["in"] + (size * 110 + 400) * PRICE["out"]) / 1e6
        print(f"Szacunek: ~{est:.2f} $ zwykłe wywołania, ~{est / 2:.2f} $ przez Batch API")
        if todo:
            print("\n--- przykładowa paczka ---\n" + user_msg(reps, todo[0][:3]) + "\n...")
        if mode == "manual" and todo:
            mp = out / f"{name}_manual_prompts.md"
            mp.write_text("# SYSTEM\n\n" + system + "\n\n" + "\n\n".join(f"# PACZKA {i}\n\n{user_msg(reps, ids)}" for i, ids in enumerate(todo)) +
                          f"\n\n# FORMAT ODPOWIEDZI\nJedna linia JSON na osobę, do pliku {cache}:\n" +
                          '{"arch": "<id>", "answer": "...", "impact_on_me": "...", "reasons": ["..."], "quote": "..."}\n'
                          f"Dozwolone wartości: patrz src/schema.py (Reaction).\n")
            print(f"\nPrompty → {mp}; odpowiedzi wklej do {cache}, potem uruchom ponownie.")
        return

    if todo and "--report" not in args:
        import anthropic; client = anthropic.Anthropic()
        if mode == "sync": run_sync(client, todo, reps, system, model, cache)
        else: run_batch(client, todo, reps, system, model, out / f"{name}_{tag}.batch.json", cache)

    # ---------- results
    R = pd.read_json(cache, lines=True)
    if "p" not in R: R["p"] = R.answer.map(LIKERT_P)
    R = R.drop_duplicates("arch", keep="last").set_index("arch")
    covered = reps.index.isin(R.index); print(f"\nPokrycie: {covered.sum()}/{len(reps)} archetypów, {reps.n[covered].sum() / reps.n.sum():.0%} person 15+")
    Q = Q.join(R[["answer", "p", "reasons", "quote"]], on="arch")
    A = Q.dropna(subset=["p"])
    print("\nOdpowiedzi (ważone liczbą person):"); print(A.answer.value_counts(normalize=True).round(3).to_string())
    for lbl, g in (("tak", A[A.p >= 0.75]), ("nie", A[A.p <= 0.2])):
        if len(g): print(f"Powody „{lbl}”: " + ", ".join(f"{k} {v / len(g):.0%}" for k, v in g.reasons.explode().value_counts().head(5).items()))
    by = A.groupby("district").agg(p=("p", "mean"), personas=("id", "size"))
    by["people_15p"] = (by.personas * scale).round(-2); by["expected"] = (by.p * by.people_15p).round(-2)
    by = by.sort_values("p", ascending=False); by.round(3).to_csv(out / f"{name}_by_district.csv")
    print("\nWg dzielnic (p = średnia szansa „tak”, osoby 15+):"); print(by.round(3).to_string())
    print(f"\nRazem 15+: p = {A.p.mean():.3f} → ok. {int(A.p.sum() * scale // 100 * 100)} osób")
    if S.kind == "event" and Path("out/sim_heuristic.csv").exists():
        H = pd.read_csv("out/sim_heuristic.csv")[["id", "p_attend"]]; A = A.merge(H, on="id")
        print(f"Heurystyka na tych samych osobach: {int(A.p_attend.sum() * scale // 100 * 100)} (korelacja z LLM {A.p.corr(A.p_attend):.2f})")
    A.drop(columns=["reasons"]).to_csv(out / f"{name}_personas.csv", index=False)
    print("\nCytaty:")
    for a in reps[covered].head(40).sample(min(6, covered.sum()), random_state=1).index:
        r = reps.loc[a]; print(f"  [{R.answer[a]}] {r.age} l., {r.district}, {r.status} (×{r.n}): „{R.quote[a]}”")


if __name__ == "__main__":
    main()
