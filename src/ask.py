"""Ask the simulated city anything: a question in Polish -> typed scenario (Claude, structured outputs)
-> every persona answers via Jev (src/jev_sim.py) -> map-ready results by district.
Usage: python3 src/ask.py "Czy mieszkańcy poszliby na kino plenerowe na Bulwarach w sierpniu?" [--n 300] [--no-run]
Writes scenarios/ask_<slug>.json (review or edit it, then re-run: python3 src/jev_sim.py scenarios/ask_<slug>.json).
Keys: ANTHROPIC_API_KEY (scenario writing) and TYPESAFE_API_KEY (answers) in .env.
Without ANTHROPIC_API_KEY: write the scenario JSON by hand (same schema: schema.Custom) and run jev_sim.py on it.
"""
import json, os, re, subprocess, sys, unicodedata
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema import Custom
from jev_sim import load_env

MODEL = "claude-opus-5-5"
SYSTEM = """You turn a question about residents of Kraków, Poland into a survey scenario for a decision model (Jev).
Every simulated resident (age, gender, district, occupation, income, education, household, car, how they get around,
and hobbies if relevant) will be shown the scenario and answer your typed questions independently.

Rules:
- kind = "custom". name/description in Polish (for humans), name_en/description_en in English (what residents read).
  The description states concrete facts: what, where, when, price, who is affected. Do not hint at the expected answer.
- 1 to 3 questions, written in English, as literal statements or questions about "this person". The first one is the main
  question and should usually be a noul (yes/no) with explicit `yes` and `no` descriptions; make "no" the natural default
  for things most people would not do. Use score (ordered levels, lowest first, 3-5 levels) for attitudes or intensity,
  choice (2-8 mutually exclusive options, include a "none / would not" option) for picking between alternatives.
- The decision model cannot do arithmetic, compare numbers or dates. Never ask it to; the code computes personal facts:
  * price_pln: one-off or monthly cost to the person -> code tells each person what share of their income it is.
  * places: named places with coordinates in Kraków -> code tells each person their travel time to each place.
    Use one place for a single location, several for a choice between locations (then use the same names in options).
    Use real coordinates of the named place; omit places when location does not matter.
- leisure_relevant = true only if hobbies, culture or sport habits matter for the answer.
- main_question = id of the main question. Keep ids snake_case."""


def slug(t):
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", t).strip("_")[:40]


def main():
    load_env(); args = sys.argv[1:]
    q = args[0]; n = args[args.index("--n") + 1] if "--n" in args else None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Brak ANTHROPIC_API_KEY w .env. Napisz scenariusz ręcznie (schemat schema.Custom, przykład: scenarios/ask_kino_plenerowe.json) "
                 "i uruchom: python3 src/jev_sim.py scenarios/<plik>.json")
    import anthropic
    r = anthropic.Anthropic().messages.parse(model=MODEL, max_tokens=8000, output_config={"effort": "medium"},
                                             system=SYSTEM, messages=[{"role": "user", "content": q}], output_format=Custom)
    if r.stop_reason == "refusal" or r.parsed_output is None: sys.exit(f"Claude nie zwrócił scenariusza ({r.stop_reason})")
    S = r.parsed_output
    path = f"scenarios/ask_{slug(q)}.json"
    json.dump(S.model_dump(exclude_none=True), open(path, "w"), ensure_ascii=False, indent=1)
    print(f"Scenariusz → {path}\n{S.name}\n{S.description}\nPytania: " + "; ".join(f"{x.id} ({x.type})" for x in S.questions))
    if "--no-run" not in args:
        subprocess.run([sys.executable, "src/jev_sim.py", path] + (["--n", n] if n else []), check=False)


if __name__ == "__main__":
    main()
