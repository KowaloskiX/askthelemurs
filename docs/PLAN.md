# Plan i stan projektu (do przekazania pracy)

Czytaj po `README.md` i `docs/CONTEXT.md`. Ten plik mówi, **dokąd idziemy i co jest następne**; szczegóły metod i wyników są w CONTEXT.

## Cel
Publiczny „cyfrowy bliźniak” mieszkańców Krakowa z otwartych danych GUS, któremu każdy zadaje pytanie po polsku i w minutę dostaje odpowiedź: ile, kto, skąd, dlaczego, kiedy, gdzie. Nie tylko koncerty: wydarzenia, decyzje miasta / konsultacje, lokalizacja biznesu i usług, kampanie.

**Pitch (EN, luźno-profesjonalnie):**
- *Problem:* city analytics is a privilege of the rich. City halls buy mobile-phone data, corporations hire research agencies; small organisers, cafés, district councils, NGOs and journalists decide blindly. Surveys cost thousands and take weeks; free census data needs a team of analysts. The public debate is lopsided.
- *Solution:* a virtual Kraków: 20,000 simulated residents built only from public census and survey data. Type a question ("Where should I host a student rap night?", "How would my neighbourhood feel about paid parking?"), the assistant asks for missing details and shows who's interested, where they live, which venue and day work best, and what holds people back. No research budget, no data team; as many what-ifs as you want; open data, checked against real polls and attendance.
- *Tagline:* "Big cities and corporations have analysts. Now everyone does."
- Nazwa w aplikacji: **AskTheLemurs!** (wybór użytkownika, 2026-10-04; lemur = maskotka HackYeah). Repo i kod nadal „krakow-sim”.

## Co już działa (skrót)
- Persony z GUS + walidacja sum (`src/personas.py`, `src/bio.py`), ~20 tys. osób, 1 ≈ 55 mieszkańców.
- Jev jako silnik decyzji (`src/jev_sim.py`), kalibracja na badaniach GUS (korelacja 0,75–0,97, trzyma się poza próbką).
- Planer w aplikacji (`make app`, `app/`, `src/planner.py`): czat z agentem (Jev wyciąga pola, rozpoznaje miejsca, nowe wydarzenie vs zmiana), agent oceny wydarzenia + szacunek popytu (`src/event_agent.py`, gpt-5.4 z wyszukiwarką), ranking miejsc z liczbami i „wyprzedane”, terminy z HETUS, segmenty, bariery, „co jeśli”.
- Walidacje: SCT vs sondaż CEM 3/4 (`src/validate_sct.py`); frekwencja 13 wydarzeń: reguła „pojemność albo szacunek LLM” 18% mediany błędu (`src/backtest.py`, `src/forecast.py`).
- Klucze w `.env`: `BDL_KEY`, `TYPESAFE_API_KEY`, `OPENAI_API_KEY` (brak Anthropic).

## Priorytety (w tej kolejności)
1. **Pierwszy commit.** Repo nie ma ani jednego commita. Sprawdzić `.gitignore` (`.env`, `out/` poza repo), potem commit.
2. ~~**Tryb „decyzja miasta / konsultacje” w aplikacji.**~~ ZROBIONE (`src/router.py`, `src/policy.py`, SCT 4/4). Zostało: pytanie doprecyzowujące w praktyce, sugestie „co jeśli” dla decyzji. Agent rozpoznaje tryb z pierwszej wiadomości (wydarzenie / decyzja miasta / lokal / kampania). Dla decyzji: wynik = poparcie wg dzielnic i grup, kto traci, argumenty (Choice powodów), fakty osobiste liczone w kodzie (jak w `facts_policy`). Przy okazji naprawić znany błąd: kierowcy zbyt negatywni (symulacja 42% za / 50% przeciw, sondaż: więcej za); test neutralniejszego opisu i faktu „ok. 9/10 aut spełnia normy”; sprawdzić `src/validate_sct.py`.
3. **Tryb „gdzie otworzyć lokal / usługę”.** Częściowo: agent danych (`src/data_agent.py`) odpowiada na pytania o lokal z danych mieszkańców i GUS; brakuje konkurencji z OSM jako narzędzia. Konkurencja z OSM (Overpass, jak `src/fetch_districts.py`), popyt z person (Jev), mapa luk popyt − podaż, ranking lokalizacji, profil klientów.
4. **Dokładność frekwencji.** Rozszerzać `data/raw/events_ground_truth.json` (każda liczba ze źródłem); flaga derby / czynniki specjalne z agenta; dzień tygodnia dla innych kategorii; analogi Jev (`src/forecast.py`) stają się mocne przy większej bazie (wtedy ewentualnie Elastic / wektorowa).
5. **Porządki.** `src/ask.py` przepiąć z Claude na ekstrakcję z planera (Jev + gpt); scenariusze w `scenarios/` traktować jako przypadki testowe; usunąć martwe ścieżki (`src/agents.py` Claude, `archive/`) albo opisać; README dopasować do aplikacji.
6. **Demo i pitch.** Dwa przypadki obok siebie: mały organizator (koncert: gdzie i kiedy) oraz rada dzielnicy (jak SCT uderzy w osiedle). Slajd z walidacjami i uczciwymi granicami.

## Zasady pracy (ustalone z użytkownikiem)
- Oszczędnie z API: najpierw próbki 300–600 osób, cache, pełne przebiegi dopiero gdy model jest gotowy; mówić koszt przed większym przebiegiem.
- Uczciwie o dokładności: każdy wniosek agenta tylko z tego, co model faktycznie rozróżnia (lekcja z „terminu” i „niszy”); liczby ze źródłami; ASSUMPTION w kodzie.
- Jev: arytmetyka w kodzie, stan po angielsku, dosłowne pytania z opisem obu odpowiedzi; dzień/dojazd z danych w kodzie (Jev ich nie rozróżnia).
- Przeglądarka do testów: tylko własna nowa karta (`new_tab`), nie `goto_url` (użytkownik ma otwarte inne aplikacje).

## Znane ograniczenia
- Frekwencja: błąd ~20–30%; przy biletowanych hitach główne pytanie to „czy się wyprzeda”.
- Brak sieci znajomych i dynamiki w czasie; niedziela w HETUS to cały dzień (wieczorem pewnie zawyżona).
- Piknik / darmowe wydarzenia: Jev widzi małe różnice między grupami; poziom zależy od świadomości (agent).
- Gust muzyczny persony to założenie (brak danych GUS).
