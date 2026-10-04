# Instrukcje dla agentów AI

Przeczytaj najpierw `README.md`, potem `docs/PLAN.md` (cel, priorytety, zasady pracy), potem `docs/CONTEXT.md` (dane, model, wyniki, pułapki).

- Uruchamiaj z katalogu głównego repo: `make`, `make sim`, `make viz`. Ścieżki w skryptach są względne do roota.
- Model decyzji istnieje w dwóch miejscach: `utility()` w `src/simulate.py` i port w `viz/template.html`. Zmieniaj oba i sprawdź, czy suma z `make sim` ≈ kafelek „Zainteresowani” w `viz/index.html`.
- Założenia oznaczaj komentarzem `ASSUMPTION`.
- GUS BDL: zawsze z kluczem (`.env`, nagłówek `X-ClientId`), throttling ≥1 s, `page-size=100`. Nie obchodź limitów zmianą IP.
- Nie commituj `.env` ani `out/`.
- Nie odpytuj GUS na żywo w demie: dane są w `data/raw/`.
