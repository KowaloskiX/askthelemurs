"""Typed contracts for the LLM layer: Persona, Scenario (any kind, not only concerts) and Reaction.
Claude structured outputs guarantee the answer parses into `Reaction`, so everything downstream
(aggregation by district, surrogate model, viz) works on enums and numbers, never on free text.
"""
from __future__ import annotations
import json
from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

Weekday = Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
PL_DAY = {"Mon": "poniedziałek", "Tue": "wtorek", "Wed": "środa", "Thu": "czwartek", "Fri": "piątek", "Sat": "sobota", "Sun": "niedziela"}


class Persona(BaseModel):
    """One row of out/personas.csv. Bio fields are optional so older CSVs still load."""
    model_config = ConfigDict(extra="ignore")
    id: int
    age: int
    sex: Literal["M", "K"]
    district: str
    origin: Literal["mieszkaniec", "student_spoza"]
    status: Literal["uczeń", "student", "pracuje", "bezrobotny", "emeryt", "niepracujący"]
    net_income: float
    has_car: bool
    fav_genre: str
    lat: float
    lon: float
    education: str | None = None
    household: str | None = None
    children: int | None = None
    transport: str | None = None
    hobbies: str | None = None          # "a;b;c" in the CSV
    studying: bool | None = None
    born_abroad: bool | None = None

    def describe(self) -> str:
        who = f"{self.age}-letni mężczyzna" if self.sex == "M" else f"{self.age}-letnia kobieta"
        where = f"mieszka: {self.district}" + (" (student spoza Krakowa, wynajem albo akademik)" if self.origin == "student_spoza" else "")
        inc = "bez własnych dochodów" if self.net_income <= 0 else f"dochód netto ok. {int(round(self.net_income, -2))} zł/mies."
        st = self.status + (" (i studiuje)" if self.studying and self.status == "pracuje" else "")
        parts = [who, where, st, inc]
        if self.born_abroad: parts.append("urodzony(a) za granicą")
        if isinstance(self.education, str): parts.append(f"wykształcenie: {self.education}")
        if isinstance(self.household, str): parts.append(self.household + (f", dzieci: {self.children}" if self.children else ""))
        parts.append(f"po mieście: {self.transport}" if isinstance(self.transport, str) else ("ma samochód" if self.has_car else "bez samochodu"))
        if isinstance(self.hobbies, str) and self.hobbies: parts.append("w wolnym czasie: " + ", ".join(self.hobbies.split(";")))
        elif self.hobbies is not None or self.age >= 15: parts.append("w wolnym czasie: nic szczególnego, dom, telewizja, internet")
        parts.append(f"lubi muzykę: {self.fav_genre}")
        return ", ".join(parts)


# ---------- custom questions (any scenario can define its own; otherwise the kind's defaults are used)
class Option(BaseModel):
    key: str = Field(description="short snake_case id, e.g. bulwary")
    description: str = Field(description="what this option means, in English")


class Question(BaseModel):
    """One typed question for Jev. Lists instead of dicts so the same schema works for Claude structured outputs."""
    id: str = Field(description="snake_case id, e.g. would_go")
    type: Literal["noul", "score", "choice"]
    instructions: str = Field(description="English, literal statement or question about this person")
    yes: str | None = Field(None, description="noul only: what 'true' means")
    no: str | None = Field(None, description="noul only: what 'false' means")
    levels: list[str] | None = Field(None, description="score only: ordered levels, lowest first")
    options: list[Option] | None = Field(None, description="choice only: mutually exclusive options")


class Place(BaseModel):
    name: str
    lat: float
    lon: float


# ---------- scenarios: one question asked to every persona, kind decides the default question
class _Scenario(BaseModel):
    name: str
    description: str                          # what the persona is told, plain Polish
    question: str | None = None               # overrides the kind's default question
    # English versions for Jev (src/jev_sim.py); Jev's accuracy is best in English
    name_en: str | None = None
    description_en: str | None = None
    questions: list[Question] | None = None   # custom questions for Jev (src/jev_sim.py)
    main_question: str | None = None          # id of the question to map; default: first noul
    # known total for the main yes/no question (e.g. tickets sold = capacity of a sold-out venue). Jev ranks people
    # well but its absolute level is too high; jev_sim shifts all logits by one constant so the total matches.
    anchor_total: int | None = None
    # travel time effect for yes/no event decisions, applied in code: P x 0.5 ** (max(0, minutes - 15) / halflife).
    # Jev ignores travel time in yes/no questions (within-person test: -0.004 logit per +10 min), uses it only in choices.
    travel_halflife_min: float | None = None

    def ask(self) -> str: return self.question or self.default_question()
    def where(self) -> tuple[float, float] | None: return None   # point the scenario happens at (for distance features)


class Event(_Scenario):
    kind: Literal["event"] = "event"
    category: Literal["koncert", "festiwal", "sport", "teatr", "kino", "targi", "piknik", "inne"] = "koncert"
    genre: str | None = None
    price: float
    weekday: Weekday
    venue_latlon: tuple[float, float]
    capacity: int | None = None
    # used only by the heuristic model in src/simulate.py
    target_age: int | None = None
    adjacent: dict[str, list[str]] | None = None
    distance_decay: float | None = None

    def default_question(self): return "Czy poszedłbyś / poszłabyś na to wydarzenie i kupił(a) bilet?"
    def where(self): return self.venue_latlon


class TransitChange(_Scenario):
    """New line, timetable or fare change. Question is about the persona's own behaviour."""
    kind: Literal["transit"]
    fare_change_pct: float | None = None
    latlon: tuple[float, float] | None = None   # e.g. new stop / line centre

    def default_question(self): return "Czy ta zmiana sprawi, że będziesz korzystać z komunikacji miejskiej inaczej niż dziś?"
    def where(self): return self.latlon


class Policy(_Scenario):
    """City policy (clean transport zone, parking zone, Sunday trading...). Question is about support."""
    kind: Literal["policy"]
    latlon: tuple[float, float] | None = None   # where it applies, if local

    def default_question(self): return "Czy popierasz wprowadzenie tej zmiany?"
    def where(self): return self.latlon


class Custom(_Scenario):
    """Anything: one or more typed questions about a described situation. Facts computed in code from
    price_pln (cost as % of income), places (travel time from home to each), leisure_relevant (include hobbies)."""
    kind: Literal["custom"]
    price_pln: float | None = None
    places: list[Place] | None = None
    leisure_relevant: bool = False

    def default_question(self): return self.questions[0].instructions if self.questions else ""
    def where(self): return (self.places[0].lat, self.places[0].lon) if self.places and len(self.places) == 1 else None


Scenario = Annotated[Union[Event, TransitChange, Policy, Custom], Field(discriminator="kind")]
_SCENARIO = TypeAdapter(Scenario)


def load_scenario(path: str):
    d = json.load(open(path)); d.setdefault("kind", "event")   # old concert files have no kind
    return _SCENARIO.validate_python(d)


# ---------- the typed answer
Likert = Literal["zdecydowanie nie", "raczej nie", "nie wiem", "raczej tak", "zdecydowanie tak"]
Impact = Literal["duży negatywny", "mały negatywny", "żaden", "mały pozytywny", "duży pozytywny"]
Reason = Literal["cena", "dojazd/odległość", "czas/obowiązki", "zainteresowania/gust", "znajomi/rodzina", "wiek/zdrowie",
                 "samochód", "komunikacja miejska", "hałas/tłok", "bezpieczeństwo", "koszty życia", "środowisko/zdrowie publiczne",
                 "przyzwyczajenie", "brak informacji", "inne"]
# ASSUMPTION: Likert -> probability. LLM numeric probabilities are poorly calibrated, a 5-point scale is a
# standard survey instrument; the mapping is the thing to calibrate on real events.
LIKERT_P = {"zdecydowanie nie": 0.03, "raczej nie": 0.2, "nie wiem": 0.5, "raczej tak": 0.75, "zdecydowanie tak": 0.95}


class Reaction(BaseModel):
    """One persona's answer. Enums only, so results aggregate without parsing text."""
    answer: Likert = Field(description="odpowiedź na pytanie scenariusza")
    impact_on_me: Impact = Field(description="jak scenariusz wpływa na życie tej osoby")
    reasons: list[Reason] = Field(description="1 do 3 najważniejszych powodów odpowiedzi, od najważniejszego")
    quote: str = Field(description="jedno zdanie w pierwszej osobie, potocznie, jak w rozmowie, bez wyliczania własnych cech")

    @property
    def p(self) -> float: return LIKERT_P[self.answer]


class KeyedReaction(Reaction):
    key: str = Field(description="identyfikator osoby z listy, np. A3")


class Packet(BaseModel):
    """Answers for several personas in one call (cheaper: shared prompt and thinking)."""
    answers: list[KeyedReaction]
