"""Calendar agent: what a SPECIFIC date means for an event in Kraków (the weekday rhythm comes from HETUS, the residents
model ignores dates). gpt-5.4 with web search returns typed flags: public holidays / long weekends, the academic calendar
(lectures, exam session, breaks, Juwenalia), school holidays, large competing events in Kraków that day.
Effects are applied in code per resident group (calendar_factor), with every multiplier marked ASSUMPTION: they are
judgement, not measured (no Kraków data on attendance by date). Shown to the user as such.
HETUS monthly participation is NOT used for seasonality: the Polish 2020 wave was collected during COVID (March 1.54 vs
August 3.01), so it measures lockdowns, not seasons.
Key: OPENAI_API_KEY in .env. One call per (date, event type), cached.
"""
import datetime, hashlib, json, re
from pathlib import Path
import numpy as np
import event_agent

MODEL = "gpt-5.4"
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["holiday", "academic", "school", "competing", "rationale"],
          "properties": {
              "holiday": {"type": "string", "enum": ["none", "public_holiday", "day_before_holiday", "long_weekend", "christmas_new_year"],
                          "description": "Polish public holidays and the long weekends around them (majówka, Boże Ciało, 15 August, 1 and 11 November)"},
              "academic": {"type": "string", "enum": ["lectures", "exam_session", "winter_break", "summer_break", "juwenalia"],
                           "description": "Kraków universities' calendar on that date (Juwenalia = the student festival week, usually May)"},
              "school": {"type": "string", "enum": ["school_days", "winter_holidays", "summer_holidays", "other_school_break"],
                         "description": "Małopolska primary/secondary school calendar on that date"},
              "competing": {"type": "array", "description": "large events in Kraków on that date or evening that compete for the same audience (only if you find them)",
                            "items": {"type": "object", "additionalProperties": False, "required": ["name", "size", "same_audience", "confirmed"],
                                      "properties": {"name": {"type": "string"}, "size": {"type": "string", "enum": ["large", "huge"]},
                                                     "same_audience": {"type": "boolean"},
                                                     "confirmed": {"type": "boolean", "description": "true only if a source announces this event on this date; false for 'usually happens around then'"}}}},
              "rationale": {"type": "string", "description": "2-3 sentences in Polish: what this date means for the event"}}}
SYSTEM = """You know the Polish calendar and Kraków. For the given date and event, say whether it is a public holiday or long weekend,
where Kraków's universities and schools are in their calendar, and which large events in Kraków compete for the same audience that day.
Use web search for academic calendars, Juwenalia dates and events. If unsure, choose the typical value for that date. Answer only with the JSON."""

MONTHS = {"stycz": 1, "lut": 2, "mar": 3, "kwie": 4, "maj": 5, "czerw": 6, "lip": 7, "sierp": 8, "wrze": 9, "paźdz": 10, "pazdz": 10, "listop": 11, "grud": 12}


def parse_date(text, today=None):
    """'15.05', '15.05.2027', '2027-05-15', '15 maja' -> ISO date (next occurrence if the year is missing)."""
    today = today or datetime.date.today(); t = text.lower()
    m = re.search(r"\b(20\d\d)-(\d\d)-(\d\d)\b", t)
    if m: return datetime.date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    m = re.search(r"\b(\d{1,2})\.(\d{1,2})(?:\.(20\d\d))?\b", t)
    d = mo = y = None
    if m: d, mo, y = int(m[1]), int(m[2]), m[3]
    else:
        m = re.search(r"\b(\d{1,2})\s+(" + "|".join(MONTHS) + r")\w*(?:\s+(20\d\d))?", t)
        if m: d, mo, y = int(m[1]), MONTHS[m[2]], m[3]
    if d is None or not (1 <= mo <= 12 and 1 <= d <= 31): return None
    try:
        dt = datetime.date(int(y) if y else today.year, mo, d)
        if not y and dt < today: dt = dt.replace(year=today.year + 1)
        return dt.isoformat()
    except ValueError: return None


def easter(y):   # Anonymous Gregorian algorithm
    a, b, c = y % 19, y // 100, y % 100; d, e = b // 4, b % 4; g = (8 * b + 13) // 25; h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4; l = (32 + 2 * e + 2 * i - h - k) % 7; m = (a + 11 * h + 19 * l) // 433
    return datetime.date(y, (h + l - 7 * m + 90) // 25, (h + l - 7 * m + 33 * ((h + l - 7 * m + 90) // 25) + 19) % 32)


def holidays(y):
    """Polish statutory days off (Wigilia since 2025)."""
    E = easter(y); D = lambda m, d: datetime.date(y, m, d); td = datetime.timedelta
    out = {D(1, 1), D(1, 6), E, E + td(1), D(5, 1), D(5, 3), E + td(49), E + td(60), D(8, 15), D(11, 1), D(11, 11), D(12, 25), D(12, 26)}
    if y >= 2025: out.add(D(12, 24))
    return out


def holiday_flag(date):
    """Computed, not asked: the LLM was inconsistent (the same Friday called a long weekend once and not the next time)."""
    d = datetime.date.fromisoformat(date); td = datetime.timedelta; H = holidays(d.year) | holidays(d.year + 1) | holidays(d.year - 1)
    off = lambda x: x.weekday() >= 5 or x in H
    if datetime.date(d.year, 12, 23) <= d or d <= datetime.date(d.year, 1, 1): return "christmas_new_year"
    # the run of days off around d, letting one working 'bridge' day join a holiday to a weekend
    def run(x):
        lo = hi = x
        while off(lo - td(1)): lo -= td(1)
        while off(hi + td(1)): hi += td(1)
        return (hi - lo).days + 1
    is_bridge = lambda x: (not off(x)) and ((x - td(1) in H and off(x + td(1))) or (x + td(1) in H and off(x - td(1))))
    bridge = is_bridge(d)
    if d in H: return "long_weekend" if run(d) >= 3 or is_bridge(d + td(1)) or is_bridge(d - td(1)) else "public_holiday"
    if bridge or (off(d) and run(d) >= 3): return "long_weekend"
    if d + td(1) in H: return "day_before_holiday"
    return "none"


def assess(date, s, cache_dir="out/agent"):
    event_agent.load_env()
    from openai import OpenAI
    inp = json.dumps({"date": date, "weekday": datetime.date.fromisoformat(date).strftime("%A"), "city": "Kraków, Poland",
                      "event": {k: s.get(k) for k in ("category", "genre", "time", "age", "setting")}}, ensure_ascii=False)
    key = f'{inp}|{MODEL}|{SYSTEM}|{json.dumps(SCHEMA)}'   # schema in the key: a changed schema must not reuse old answers
    f = Path(cache_dir) / f"cal_{hashlib.sha1(key.encode()).hexdigest()[:12]}.json"
    if f.exists(): return json.loads(f.read_text())
    r = OpenAI().responses.create(model=MODEL, reasoning={"effort": "low"}, instructions=SYSTEM, input=inp, tools=[{"type": "web_search"}],
                                  text={"format": {"type": "json_schema", "name": "calendar", "schema": SCHEMA, "strict": True}})
    out = json.loads(r.output_text); f.parent.mkdir(parents=True, exist_ok=True); f.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return out


def assess_full(date, s):
    cal = dict(assess(date, s)); cal["holiday_llm"] = cal["holiday"]; cal["holiday"] = holiday_flag(date)
    return cal


# ASSUMPTION (judgement, not measured): multipliers on the chance of going, by resident group
STUDENT_PRESENCE = {"lectures": (1.0, 1.0), "exam_session": (0.85, 0.85), "winter_break": (0.5, 0.85), "summer_break": (0.25, 0.8), "juwenalia": (1.0, 1.0)}   # (from outside, from Kraków)
HOLIDAY = {"none": 1.0, "public_holiday": 0.9, "day_before_holiday": 1.0, "long_weekend": 0.8, "christmas_new_year": 0.3}


def calendar_factor(A, cal, s):
    """Per-resident multiplier for the date. A: residents frame (origin, status, studying, children, age)."""
    f = np.full(len(A), HOLIDAY[cal["holiday"]])
    outside = (A.origin == "student_spoza").values; local_st = ((A.status == "student") | A.studying.fillna(False).astype(bool)).values & ~outside
    po, pl = STUDENT_PRESENCE[cal["academic"]]
    f = np.where(outside, f * po, np.where(local_st, f * pl, f))
    if cal["academic"] == "juwenalia" and s.get("category") in ("koncert", "klub", "festiwal"):   # the student festival competes for students' evenings
        f = np.where(outside | local_st, f * 0.75, f)
    if cal["school"] == "summer_holidays":   # many families travel in July/August
        f = np.where((A.children.fillna(0) > 0).values, f * 0.8, f)
    f *= competition_factor(cal)   # only events a source announces for that date; 'usually around then' is shown as a risk
    return f


def competition_factor(cal):
    """Multiplier for the demand estimate (event_agent.estimate has no web search, so it cannot see same-evening events)."""
    f = 1.0
    for c in cal["competing"]:
        if c["same_audience"] and c.get("confirmed"): f *= 0.85 if c["size"] == "large" else 0.7
    return f


def describe(cal):
    L = {"public_holiday": "święto", "day_before_holiday": "dzień przed świętem", "long_weekend": "długi weekend (część mieszkańców wyjeżdża)", "christmas_new_year": "okres świąteczny",
         "exam_session": "sesja egzaminacyjna", "winter_break": "przerwa międzysemestralna (część studentów wyjeżdża)", "summer_break": "wakacje akademickie (większość studentów spoza Krakowa wyjeżdża)",
         "juwenalia": "Juwenalia (konkurencja o studentów)", "summer_holidays": "wakacje szkolne (rodziny wyjeżdżają)", "winter_holidays": "ferie zimowe"}
    flags = [L[x] for x in (cal["holiday"], cal["academic"], cal["school"]) if x in L]
    flags += [f"konkurencja: {c['name']}" for c in cal["competing"] if c["same_audience"] and c.get("confirmed")]
    flags += [f"możliwa konkurencja (niepotwierdzona): {c['name']}" for c in cal["competing"] if c["same_audience"] and not c.get("confirmed")]
    return flags
