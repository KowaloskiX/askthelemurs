"""Live view of a simulation run: the sampled residents (position + short profile) and every answer as it arrives,
streamed to the UI map. Positions: the persona's 1 km census grid cell centre plus a deterministic offset inside the cell
(ASSUMPTION: exact homes are unknown, only the cell)."""
import numpy as np

STATUS = {"pracuje": "pracuje", "student": "studiuje", "uczeń": "uczeń", "emeryt": "emeryt(ka)", "bezrobotny": "bezrobotny(-a)", "niepracujący": "nie pracuje"}


def _jitter(i, salt):
    return ((int(i) * 2654435761 + salt) % 10007) / 10007 - 0.5


def people(Q):
    """Q: personas sample (rows of out/personas.csv) -> compact list for the map."""
    out = []
    for r in Q.itertuples():
        hob = [h for h in r.hobbies.split(";")[:3]] if isinstance(r.hobbies, str) and r.hobbies else []
        out.append({"id": int(r.id), "lat": round(r.lat + _jitter(r.id, 17) * 0.009, 5), "lon": round(r.lon + _jitter(r.id, 91) * 0.014, 5),
                    "age": int(r.age), "sex": r.sex, "district": r.district, "status": STATUS.get(r.status, r.status),
                    "household": r.household if isinstance(r.household, str) else None, "car": bool(r.has_car),
                    "income": int(r.net_income) if r.net_income > 0 else 0, "hobbies": hob,
                    "music": r.fav_genre if isinstance(r.fav_genre, str) else None})
    return out
