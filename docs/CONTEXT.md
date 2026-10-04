# Kraków Sim: kontekst projektu

Dokument do przekazania pracy: dla zespołu i dla agentów AI (Claude, Cursor, Codex…), którzy przejmują repo. Czytaj po README.

## 1. Skąd pomysł
Szukaliśmy „innego” projektu AI na hackathon. Inspiracja: projekt z MIT hackathonu (widziany na X), który symulował demografię mieszkańców miasta i przewidywał ich decyzje, np. pod planowanie koncertów. Pokrewne prace:
- Stanford HAI: 1052 agentów LLM zbudowanych z 2-godzinnych wywiadów, ~85% zgodności z odpowiedziami prawdziwych osób w GSS.
- MIT Media Lab: *Large Population Models* / AgentTorch (github.com/AgentTorch/AgentTorch), 8,4 mln agentów na 1 GPU.
- Tsinghua: AgentSociety (github.com/tsinghua-fib-lab/AgentSociety).

**Nasz wyróżnik:** prawdziwe, publiczne dane GUS dla polskiego miasta, mapa 1 km i (docelowo) walidacja na prawdziwym wydarzeniu. Jury zwykle widzi same symulacje bez walidacji.

## 2. Dane

### 2a. GUS BDL (Bank Danych Lokalnych), `data/raw/krakow_demo.json`
- API: `https://bdl.stat.gov.pl/api/v1`, klucz w nagłówku `X-ClientId`.
- **Limity:** anonimowo 5/s, 100/15 min, 1000/12 h, 10 000/7 dni **na IP**. Z kluczem 5× więcej. Anonimowy limit bywa wyczerpany na współdzielonych IP (biuro, VPN, chmura) i wtedy przychodzi 429 już przy 1. zapytaniu. **Używaj klucza.**
- Odpowiedzi są stronicowane (domyślnie 10 wyników). Przy `data/by-unit` z wieloma `var-id` dawaj `page-size=100`, inaczej część zmiennych „znika”.
- Jednostki: gmina Kraków `011212161011` (poziom 6), powiat m. Kraków `011212161000` (poziom 5). **BDL nie ma podziału Krakowa na dzielnice ani delegatury.**
- Zmienne (rok 2025):

| Co | Temat | ID | Poziom |
|---|---|---|---|
| Ludność ogółem | P2137 | 72305 | gmina |
| Wiek×płeć, 18 grup 5-letnich | P2137 | lista M/K w `src/fetch_bdl.py` | gmina |
| Przeciętne wynagrodzenie brutto | P2497 | 64428 | powiat |
| Stopa bezrobocia rejestrowanego | P2392 | 60270 | powiat |
| Samochody osobowe / 1000 ludności | P2420 | 60533 | powiat |

- Wartości 2025: populacja 816 614, płaca 11 404,02 zł, bezrobocie 2,5%, auta 638,1/1000.
- Wyszukiwarka zmiennych (`/variables/search`) jest rozmyta i zwraca też dane kwartalne i wojewódzkie. Sprawdzaj `level` i temat.
- Inne miasto: podmień ID jednostek w `fetch_bdl.py`, a ID zmiennych zostają te same.

### 2b. NSP 2021, siatka 1 km, `data/raw/krakow_grid_nsp2021.json`
- Źródło: INSPIRE ATOM GUS `https://geo.stat.gov.pl/atom-web/atom/PD` → dataset `PD.GRID.2021` → `GRID_NSP2021_RES.zip` (SHP, EPSG:2180, ~23,5 MB, **bez logowania**). Na Portalu Geostatystycznym ten sam zasób (nr 18433) wymaga konta.
- Pola: `CODE` (np. `CRS3035RES1000mN3045000E5035000`, czyli lewy dolny róg komórki w EPSG:3035), `RES`, `RES_MALE`, `RES_FEM`, `RES_0_14`, `RES_15_64`, `RES_65_`.
- **Sztuczka:** współrzędne są w `CODE`, więc wystarczy przeczytać `.dbf` bez geometrii.
- **Tajemnica statystyczna:** w małych komórkach GUS zostawia puste pola (31 komórek, 610 osób). `personas.py` dzieli brakującą resztę równo między puste pola.
- Wycięty prostokąt E 5018–5052 km, N 3029–3053 km (EPSG:3035) **zawiera przedmieścia**: 852 komórki i 1 026 494 osoby, a w samym mieście jest ~817 tys.
- Pierwotnie wycinek zrobiono w przeglądarce (fetch + DecompressionStream). `src/fetch_nsp_grid.py` robi to samo w Pythonie, ale **nie był jeszcze testowany end-to-end**: sprawdź przy pierwszym `make data`.

### 2b'. Dodatkowe dane GUS (pobrane przez `make data`)
| Plik | Skrypt | Co | Źródło |
|---|---|---|---|
| `data/raw/krakow_gus_extra.json` | `src/fetch_bdl_extra.py` (11 zapytań BDL) | 53 tabele: wykształcenie×wiek i ×płeć, gospodarstwa domowe, rodziny wg dzieci, stan cywilny, studenci (137 784, 2024/25), pracujący×wiek×płeć, aktywność ekonomiczna, bezrobotni×wiek×płeć, płace mediana/średnia×płeć (2025), emerytury (tylko Małopolska), źródło utrzymania, kultura (kina, teatry, imprezy masowe), kluby sportowe, auta wg paliwa/wieku, migracje, mieszkania, turystyka | GUS BDL, NSP 2021 |
| `data/raw/krakow_grid_extra.json` | `src/fetch_grid_extra.py` | siatka 1 km: pracujący (EMP), miejsce urodzenia, miejsce zamieszkania rok wcześniej. `res1y_moved_within_pl` = rok temu mieszkał gdzie indziej w Polsce (NIE „wprowadził się do kwadratu”). Ukryte wartości = null | Eurostat Census Grid 2021 V3 (dane GUS); plik 540 MB czytany zakresami HTTP, zapisany tylko wycinek |
| `data/raw/gus_leisure_rates.json` | `src/fetch_gus_surveys.py` + `src/parse_gus_surveys.py` | ~55 aktywności kulturalnych (kultura 2024, 15+), 33 dyscypliny sportu (sport 2025, 5+), wydatki na kulturę wg kwintyla (budżety 2025), budżet czasu 2023. Podziały: wiek×płeć, miasto/wieś×wiek×płeć, wielkość miasta, wykształcenie | GUS, publikacje „Uczestnictwo ludności w kulturze”, „Uczestnictwo w sporcie i rekreacji ruchowej”, „Budżety gospodarstw domowych”, „Budżet czasu ludności” |

Braki: wykształcenie / gospodarstwa / mieszkania na siatce 1 km (tylko Portal Geostatystyczny z kontem), emerytury na poziomie Krakowa, wykształcenie×wiek×płeć razem, GUS nie krzyżuje wieku z wielkością miasta w badaniach ankietowych.

### 2c. Dzielnice i granica miasta, `data/geo/krakow_districts.geojson`
- OpenStreetMap (ODbL) przez Overpass: 18 relacji `admin_level=9` (dzielnice) + relacja 2768922 (Kraków, `admin_level=8`). Skrypt `src/fetch_districts.py`, 1 zapytanie.
- Overpass zwraca 406 bez nagłówka `User-Agent`.
- Geometrie uproszczone do ~25 m (51 KB). Powierzchnia miasta wychodzi 326,5 km² (oficjalnie 326,8).
- Demografii według dzielnic nie bierzemy z urzędu: liczymy ją z siatki NSP. Każda persona dostaje losowy punkt w swoim kwadracie 1 km, a dzielnica to point-in-polygon. Kwadrat na granicy dzieli się więc proporcjonalnie do powierzchni (zakładamy równą gęstość w kwadracie). W granicach miasta wychodzi ~793 tys. mieszkańców (NSP 2021: 800 tys.).

### 2d. Czego nie ma / gdzie szukać
- Oficjalna demografia według dzielnic (do walidacji naszej): nie ma na dane.gov.pl. Można poszukać na msip.krakow.pl lub w raportach o stanie miasta.
- Studenci: GUS liczy zameldowanych/rezydentów, więc grupa 20–24 lata ma tylko 29,5 tys. osób (30–34: 81,8 tys.). Ile studentów jest w Krakowie: GUS „Szkolnictwo wyższe” / POL-on, rzędu 125–130 tys. (**do zweryfikowania**, od tego zależy `EXTRA_STUDENTS`).
- Dochód według miejsca: brak. Dochód nie zależy od dzielnicy.

## 3. Model

### Persony (`src/personas.py`, `src/bio.py`)
Kolejność, każdy krok z danych GUS (założenia oznaczone `ASSUMPTION` w kodzie):
1. **Miejsce, wiek, płeć:** kwadrat 1 km ∝ mieszkańcy, szeroka grupa wieku i płeć z kwadratu, wiek 5-letni z BDL 2025. Punkt losowy w kwadracie → dzielnica (OSM). Urodzony za granicą: udział z siatki.
2. **Wykształcenie (13+):** NSP 2021 wg wieku; płeć zmienia tylko szansę wyższego (odds ratio z tabeli wg płci).
3. **Zatrudnienie:** NSP 2021 pracujący wg wieku×płci / ludność, potem dla każdego kwadratu mnożnik szans tak, by suma = EMP z siatki spisowej.
4. **Studenci:** studiuje 90% niepracujących i 30% pracujących 19–24 (ASSUMPTION); studentów spoza Krakowa dosypujemy tyle, by razem było 137 784 (BDL) minus 15% dojeżdżających spoza obszaru (ASSUMPTION). Kampusy jak wcześniej, rozproszeni wg `res1y_moved_within_pl`.
5. **Status:** pracuje / student / uczeń / emeryt (wiek emerytalny K60, M65; 75% niepracujących 50+ = wcześniejsza emerytura lub renta, dostrojone do sumy z NSP) / bezrobotny (dokładnie tylu, ilu zarejestrowanych wg wieku×płci, BDL 2025) / **niepracujący** (bierni zawodowo).
6. **Dochód:** pracujący: lognormal z mediany i średniej płacy brutto wg płci (BDL 2025), mnożniki wieku i wykształcenia (ASSUMPTION) normalizowane do mediany, netto = 71%. Emeryci: średnia emerytura ZUS Małopolska. Studenci, bezrobotni: ASSUMPTION.
7. **Gospodarstwo (20+):** 6 typów, skłonności wg wieku (ASSUMPTION) dopasowane (raking) do sum NSP 2021: mieszkający sami 104 198, pary bez dzieci, z dziećmi, samotni rodzice, dorosłe dzieci u rodziców, reszta. Twarde limity wieku. Liczba dzieci wg NSP.
8. **Auto:** auta/1000 × wiek × dzieci × dochód (ASSUMPTION).
9. **Kultura i sport (`bio.py`):** każda aktywność losowana z tabeli GUS miasto×wiek×płeć, przesunięta szansami do poziomu miast 500 tys.+ i (tłumione) wykształcenia. Sport: najpierw „czy uprawia”, potem dyscypliny warunkowo. Wydatki na kulturę wg kwintyla dochodu.
10. **Gust muzyczny:** dalej ASSUMPTION (GUS nie ma gatunków).

**Walidacja** (drukowana przez `personas.py`): mieszkańcy 792 tys. vs 801 tys., studenci 137,8 tys. vs 137,8 tys., pracujący 394 tys. vs 406 tys., bezrobotni 13,1 tys. vs 13,1 tys., emeryci+renciści 166 tys. vs 179 tys., wyższe 44,3% vs 44,8%, sami 105 tys. vs 104 tys., mediana płacy 8 972 vs 8 712 zł, koncerty 35% vs 33% (500k+), sport 58% vs 60% (500k+). Koncerty i sport nie były dopasowywane, wynikają z łączenia tabel.

### Decyzja (`utility()` w `src/simulate.py`, port w `viz/template.html`)
```
u = -2.2
  + (2.0 jeśli ulubiony gatunek == gatunek wydarzenia; 0.4 jeśli pokrewny; -1.0 inaczej)
  - |wiek - wiek_docelowy| / 12
  - 1.2 * max(0, ln(cena / budżet)),         budżet = 4% dochodu netto (min 300 zł)
  - distance_decay * km_do_miejsca  + 0.2 jeśli ma auto      (distance_decay domyślnie 0.06, ze scenariusza)
  + 0.4 jeśli pt/sob i (pracuje lub student)
  - 0.3 pt / 0.8 sob, nd  jeśli student spoza Krakowa (wyjazdy do domu na weekend)
  - 2.5 jeśli wiek < 16 lub > 80
p = sigmoid(u)
```
- Kara za cenę była liniowa (`-1.5 × (cena − budżet) / budżet`) i dla studenta (budżet ~85 zł) przy bilecie 249 zł dawała −2,9, czyli praktycznie zero młodych. Logarytm daje −1,3.
- Skalowanie: suma p × (populacja / liczba person).
- **Ranking dzielnic** (`out/district_ranking.csv`, karta „Gdzie zrobić” na mapie): to samo wydarzenie stawiamy w środku ciężkości person danej dzielnicy i sumujemy p z całej aglomeracji. Przy `distance_decay = 0.06` różnice są małe (duże wydarzenie, ludzie dojadą), przy 0,2+ (klub, wydarzenie lokalne) ranking mocno się różnicuje. Wartość `distance_decay` to ASSUMPTION, nadaje się do kalibracji.
- **To jest „chciałbym pójść”, nie „kupiłem bilet”.** Dla koncertu pop w TAURON Arenie wychodzi ok. 72 tys. przy 15 tys. miejsc. Wagi są ręczne, nic nie jest dopasowane do danych.
- **Zmieniając model, zmień oba miejsca** (Python i JS). Kontrola: łączny wynik z `make sim` ≈ kafelek „Zainteresowani” na mapie przy tych samych parametrach (71 700 vs 71 667, różnica to zaokrąglenie). W JS model jest rozbity na część niezależną od miejsca + `−decay × km`, żeby ranking 18 dzielnic liczył się na żywo.

### Model decyzji Jev (`src/jev_sim.py`), główna ścieżka
Jev (TypeSafe, „System One model”, https://docs.typesafe.ai) to nie LLM: dostaje `state` (JSON) i typowane pytania (Noul = P(tak), Score = skala z rozkładem, Choice = wybór z rozkładem) i zwraca skalibrowane prawdopodobieństwa. 0,042 $ / mln tokenów wejścia, wyjście darmowe, 70–500 ms, limit 80 zapytań/s. Dlatego **każda persona 15+ jest pytana osobno** (~17 tys., ~550 tokenów każda, ~0,40 $ za scenariusz), bez archetypów.
- **Stan:** persona po angielsku (wiek, płeć, dzielnica, zajęcie, dochód, wykształcenie, gospodarstwo, auto z wiekiem i paliwem, sposób poruszania się; hobby i gust tylko przy wydarzeniach) + scenariusz + **fakty osobiste policzone w kodzie**: koszt biletu jako % dochodu i słownie, czas dojazdu (ASSUMPTION: auto 5 + 2,5 min/km, MPK 10 + 3,5 min/km), czy SCT zakazuje jej auta (benzyna/LPG > 27 lat, diesel > 17 lat), dopłata do biletu miesięcznego z ulgami.
- **Zasady z dokumentacji Jev:** arytmetyka w kodzie, angielski (po polsku gorsza dokładność), dosłowne instrukcje z opisem obu odpowiedzi, mały stan bez zbędnych szczegółów.
- **Pytania:** event: `attend` (Noul), `interest` (Score 0–3), `reason` (Choice); policy: `support`, `stance` (0–4), `reason`; transit: `use_less`, `impact`, `reason`.
- **Wynik:** P z Nouli wprost (bez mapowania Likerta), suma × waga wg dzielnic i wieku, główne powody, dla SCT poparcie: auto zakazane vs reszta, dla wydarzeń porównanie z heurystyką.
- **Kalibracja (`--calibrate`):** pytania z ankiety GUS kultura 2024 (koncert inny niż klasyczny, teatr, kino, klub/dancing w ostatnich 12 miesiącach), stan **bez hobby** (inaczej model przepisałby odpowiedź). Porównanie z tymi samymi tabelami GUS, z których powstały persony, wg wieku×płci → `out/jev/gus_calibration_table.csv`. Pokazuje przesunięcie poziomu i czy Jev trafia w różnice między grupami.
- **Wiek i paliwo aut** (do SCT): z rejestru aut Krakowa (BDL 2025, wiek w 12 przedziałach, paliwo); bogatsi jeżdżą nowszymi (ASSUMPTION). W modelu ~12% aut nie wjechałoby do strefy.
- Cache per persona w `out/jev/`, hash z pytań i scenariusza. `--n 300` = próbka; waga persony jest wtedy przeskalowana na całą populację 15+.
- **Dowolne pytania (`kind: "custom"`, `schema.Custom`):** scenariusz definiuje 1–3 pytania (`Question`: noul z opisem tak/nie, score z poziomami, choice z opcjami), `main_question`, opcjonalnie `price_pln` (koszt jako % dochodu) i `places` (czas dojazdu do każdego miejsca; kilka miejsc = wybór lokalizacji), `leisure_relevant` (czy dołączyć hobby). Pytania mogą mieć też scenariusze event/policy/transit (nadpisują domyślne). Każde pytanie jest agregowane wg dzielnic (noul/score: średnia, choice: udziały opcji). `src/ask.py` pisze taki scenariusz z pytania po polsku przez Claude (schemat bez słowników, żeby działał w structured outputs).
- **Wyniki testów (300 osób):** kalibracja GUS: korelacja wg wieku×płci kino 0,94, kluby 0,97, koncerty 0,77, teatr 0,33; poziom koncertów/klubów ±5 pkt, kino −19 pkt (Jev ściska skrajności, korekta logit b=2,4 naprawia). Kino plenerowe (custom, 3 miejsca): wybór miejsca wg bliskości (Nowa Huta → Zalew 100%, Dębniki → Bulwary 100%, Krowodrza → Park Jordana 75%), ale P(pójdzie) = 58%, czyli zawyżone tak jak przy koncercie (49%). **Kolejność i podziały są wiarygodne, poziom pytań „czy faktycznie zrobi X” wymaga kotwicy.**
- **Eksperyment ze sformułowaniem (`src/jev_experiment.py`, koncert, te same 300 osób):** obecne pytanie 53%, ostrzejsze z domyślnym „nie” 28%, ostrzejsze + kontekst częstości w stanie 21%; kolejność osób prawie bez zmian (Spearman 0,93–0,94 względem wersji pierwotnej, 0,70–0,76 względem heurystyki). Sformułowanie przesuwa poziom, ale nie dochodzi do realnego (~1,5% na jeden koncert w Tauronie). Domyślne pytanie o wydarzenie to teraz wersja ostrzejsza.
- **Jak pytać o wydarzenia (iteracje na 300 osobach, `src/jev_experiment.py`, miara: iloraz P między grupami po zakotwiczeniu):**

  | wariant | 25–34 / 65+ | chodzi na koncerty / nie | lubi gatunek / nie |
  |---|---|---|---|
  | B: jedno pytanie, surowa lista hobby | 1,56× | 1,56× | 1,25× |
  | E: jedno pytanie, fakty o dopasowaniu policzone w kodzie | 2,05× | 1,35× | 1,91× |
  | F: 3 atomowe pytania (chce × stać × ma czas), iloczyn w kodzie | 2,76× | 1,36× | 1,67× |
  | **F2 (domyślny):** F + nawyk w opisie osoby, neutralne „stać”, wyjaśnienie z czego żyją osoby bez dochodu | **3,3×** | **2,9×** | 1,4× |
  | GUS (miasta, koncerty) | ~2,5–3× | | |

  Lekcje: (1) Jev ignoruje fakty, z których musi coś wywnioskować; nawyk jako cecha osoby działa, jako osobny fakt prawie nie. (2) Opis odpowiedzi „nie” mocno steruje: „albo ktoś inny musiałby zapłacić” zabił wszystkie osoby bez dochodu (pracujący / niepracujący 27×). (3) Iloczyn atomowych pytań rozciąga spłaszczone odpowiedzi (Jev docs: composite scoring).
- **Dojazd:** test wewnątrz osoby (ten sam koncert w Tauronie i w Klubie Studio, te same 300 osób): −0,004 logit na +10 min, czyli Jev **ignoruje czas dojazdu w pytaniach tak/nie** (w wyborze między miejscami go używa, por. kino plenerowe). Dlatego dla wydarzeń dojazd jest w kodzie: `attend = want × afford × free × 0,5^(max(0, min − 15) / 45)` (ASSUMPTION, `travel_halflife_min` w scenariuszu). Efekt: dojazd < 25 min vs > 45 min 1,8×.
- Cache Jev: klucz = pytania + pełny stan wybranej persony (wcześniej tylko scenariusz, przez co zmiany stanu dawały stare odpowiedzi).
- **Kotwica poziomu (`anchor_total` w scenariuszu albo `--anchor N`):** wszystkie logity przesunięte o jedną stałą c tak, by suma osób = N; kolejność i ilorazy szans bez zmian. Koncert: `anchor_total` = 15 000 (ASSUMPTION: wyprzedana hala), c = −3,37, średnio 1,6% osób 15+. Po zakotwiczeniu: 25–34 lata 2,0%, 65+ 1,3%, chodzący na koncerty (GUS) 2,1% vs reszta 1,3%, studenci i pracujący ~1,9%, bezrobotni i uczniowie ~0,8–0,9%. Kierunki dobre, różnice prawdopodobnie za płaskie (Jev ściska skrajności, por. kalibracja). Różnice między dzielnicami przy 300 osobach to szum (7–25 osób na dzielnicę); do mapy trzeba puścić całe miasto (~17 tys. osób, ~0,6 $).

### Warstwa LLM (Claude, `src/agents.py`): archetypy × paczki × Batch API (alternatywa: cytaty i wyjaśnienia)
Działa dla dowolnego scenariusza (`Event` / `TransitChange` / `Policy`, unia Pydantic po `kind`).
- **Archetypy:** persony 15+ grupowane po cechach zmieniających odpowiedź: wiek, płeć, status, dochód, cecha ze scenariusza (koncert → „chodzi na koncerty” z GUS, inne → sposób poruszania się), odległość do miejsca, poza Krakowem, gospodarstwo, wykształcenie, dzieci. Trzy poziomy scalania (pełny → bez gospodarstwa/wykształcenia → wiek/płeć/status/cecha), min. 12 person w grupie. ~630–720 archetypów z 17 tys. person.
- **Paczki:** 10 reprezentantów w jednym zapytaniu, etykiety A1…A10, odpowiedź `Packet` = lista `KeyedReaction` (Likert, wpływ, powody z zamkniętej listy, cytat). Mapowanie po etykiecie, nie po kolejności.
- **Batch API** (−50%), `effort: low`, prefix w cache. ~1 $ za pytanie (szacunek z `--dry-run`). `--mode sync` do testów, `--mode manual` zapisuje prompty do pliku (odpowiedzi można wpisać ręcznie albo odegrać w sesji czatu), `--report` podsumowuje cache bez API.
- **Wynik:** każda persona dostaje odpowiedź swojego archetypu; suma × waga wg dzielnic, powody, cytaty, porównanie z heurystyką.
- **Likert → p:** `LIKERT_P` w `schema.py`, ASSUMPTION do kalibracji. Prompt celowo studzi optymizm.
- Test ręczny (20 archetypów, koncert pop, 249 zł): powody „nie” to gust 76%, cena 61%, dojazd 28%. Ścieżka działa; liczb z 11% pokrycia nie interpretować.

### Planer „gdzie najlepiej zorganizować X” (`make app`, `app/`, `src/planner.py`)
- **Agent bez LLM:** Jev czyta polską wiadomość organizatora i wypełnia zamknięte pola (rodzaj, gatunek, dzień, pora, docelowy wiek, skala, plener/hala), każde z opcją „nie podano”; odpowiedź zostaje tylko przy pewności ≥ 0,5. Cenę wyciąga regex. Brakujące pola agent dopytuje przyciskami. Nowa wiadomość („a może w sobotę za 40 zł?”) nadpisuje wcześniejsze wartości. Test na 4 zdaniach: wszystkie pola poprawnie.
- **Jeden przebieg Jev na zapytanie, nie na miejsce:** pytania chce / stać / ma czas nie zależą od lokalizacji (dojazd i tak w kodzie), więc ranking ~20 miejsc z `viz/venues.json` + dzielnic liczy się w kodzie z czasu dojazdu każdej osoby. 600 osób ≈ 15 s i ~0,02 $; cache w `out/planner/` per specyfikacja.
- **Miejsca** (`viz/venues.json`, 22): hale, kluby, sale kultury, plenery z pojemnością, `setting` i listą pasujących rodzajów wydarzeń. Współrzędne i pojemności przybliżone (ASSUMPTION). Filtr: rodzaj, plener/hala, pojemność ≥ skala i nie absurdalnie większa; małe wydarzenia dostają też „dowolny lokal” w środku każdej dzielnicy.
- **Wynik:** indeks względny (100 = najlepsze), % chętnych do 20 min, typowy dojazd, mapa chętnych na mieszkańca dzielnicy, wiek publiczności, powody, najsłabsze miejsce dla kontrastu. Bez bezwzględnej liczby osób (wymaga kotwicy).
- **Chat (wersja 2):**
  - *Miejsca w wiadomości:* Choice na liście ~60 nazw (22 miejsca, 18 dzielnic, ~17 osiedli/okolic, współrzędne przybliżone). Miejsce → przypięte w rankingu (📍, pokazana pozycja nawet poza top 12); okolica/dzielnica → kandydaci w promieniu 3 km.
  - *Nowe wydarzenie vs zmiana:* osobne zapytanie Noul („czy to inny rodzaj wydarzenia”) z poprzednim wydarzeniem w stanie. Pola wyciągane w osobnym zapytaniu z samej wiadomości: gdy poprzednie wydarzenie było w tym samym stanie, Jev uzupełniał z niego pola (przeciek gatunku i wieku). Test: „targi książki w niedzielę” → reset; „a może w sobotę za 40 zł?”, „na 5000 osób”, „na Kazimierzu” → zmiany. Wszystkie 4 poprawnie.
  - *UI:* przykładowe pytania na start, pigułki z ustawieniami (klik = zmiana jednego pola), odpowiedź agenta napisana z liczb (najlepsze miejsce, czy czołówka jest wyrównana, gdzie jest przypięte miejsce, główny segment i najchętniejszy segment, bariery, nisza), przyciski „co jeśli” (taniej, sobota/piątek, plener/hala) z automatycznym przeliczeniem i porównaniem zasięgu z poprzednim wariantem.
- **Szukanie:** zasięg dojazdu zależy od skali (ASSUMPTION: połowa szansy co 25 / 35 / 45 / 60 min ponad 15 min dla small / club / medium / large); oznaczenie pojemności „pasuje / za duże / za małe”; zastępczy „dowolny lokal” tylko dla małych wydarzeń.
- **Kto przyjdzie (segmenty):** reguły etapu życia (studenci spoza Krakowa, studenci z Krakowa, uczniowie, młodzi pracujący bez dzieci, pracujący 35+ bez dzieci, rodzice z dziećmi, niepracujący, seniorzy), udział w publiczności i iloraz szansy względem średniej. Hip-hop 60 zł: studenci 2,4–2,5×, młodzi pracujący 2,1×, rodzice 0,5×, seniorzy 0,46×. Piknik rodzinny: słabe różnice (rodzice 1,15×), bo „chce” wychodzi wysokie dla prawie wszystkich (76% przy zaostrzonym pytaniu, 94% przy „would enjoy”). Ograniczenie Jev.
- **Bariery:** ciągła miara utraty zainteresowania, 1 − Σ(chce × czynnik) / Σ(chce), osobno dla ceny i terminu (próg „< 0,5” dawał 0%, bo Jev rzadko schodzi poniżej 0,5). Festiwal techno 300 zł: cena 54%; hip-hop 60 zł: cena 13%, termin 35%; piknik: termin 27%.
- **Dzień tygodnia (test: ten sam koncert hip-hop pn/śr/pt/sob/nd, te same 600 osób):** „ma czas” 0,56 / 0,57 / 0,59 / 0,59 / 0,58, szansa pójścia pn 0,099 vs sob 0,108 (+9%). Jev prawie nie rozróżnia dni; „ma czas” zależy od osoby (rodzice 0,40, pracujący 0,52–0,57, 18–30 lat 0,70–0,73). Dlatego planer nie podaje „straty przez termin” ani podpowiedzi „sprawdź w sobotę”, tylko segmenty z najniższym „ma czas”. Opcja na później: mnożnik dnia w kodzie (jak dojazd), ale bez danych byłby czystym założeniem.
- **Atrakcyjność:** udział „chce” w grupie docelowej (wiek ze specyfikacji) i w całej populacji 15+; hip-hop 60 zł: 30% vs 9%. Wcześniejszy komunikat „nisza” (próg 25% całej populacji) był mylący.
- **Komunikaty agenta tylko z tego, co model rozróżnia:** wcześniejsze „przez termin tracisz 35%” liczyłem jako 1 − Σ(chce × ma czas)/Σ(chce); Jev daje „ma czas” 0,6–0,8 prawie każdemu i niezależnie od dnia, więc wychodziło ~30–35% dla każdego terminu, a szablon robił z tego wniosek o dniu. Teraz: (1) „najtrudniej znaleźć czas: …” tylko dla segmentów z „ma czas” ≤ 0,8 × średnia (cecha osoby, nie daty), bez zdań o dniu tygodnia; (2) cena jako udział zainteresowanych, dla których bilet byłby obciążeniem (afford < 0,5): hip-hop 60 zł ok. 5% → „cena nie jest problemem”, festiwal 300 zł 68%; (3) bez podpowiedzi zmiany dnia.
- **Cache planera:** klucz = specyfikacja + treść pytań (wcześniej tylko specyfikacja; po zaostrzeniu pytania „chce” stare odpowiedzi dawały 18% zamiast 10%).
- Przykład (koncert hip-hop, piątek wieczór, 60 zł, 300–2000 osób, w środku, 600 osób): top Grzegórzki / Stare Miasto / Hype Park / ICE (indeks 99–100), najsłabsze Wzgórza Krzesławickie 82; publiczność 15–34 lata 61%; powód: gust 81%. Różnice między centralnymi miejscami są małe, między centrum a obrzeżami wyraźne.

### Agent: router + tryby (`src/router.py`, `src/policy.py`, `app/server.py`)
Hybryda (wybór użytkownika): router (gpt-5.4-mini, ścisły schemat JSON) rozpoznaje z wiadomości tryb (`event` / `policy` / `business` / `other`) i czy to nowy temat; każdy tryb to stały pipeline w kodzie. `business` jeszcze nie istnieje (agent mówi to wprost).
Tryb **decyzja miasta** (`src/policy.py`): gpt-5.4 zamienia polski opis na typowaną specyfikację (neutralny opis EN, obszar, punkt dla decyzji o jednym miejscu, grupy bezpośrednio dotknięte jako reguły na polach persony, np. `car_fuel=diesel AND car_age>17` albo `km_from_location<=1`, argumenty obu stron, założenia, max 2 pytania). Kod liczy reguły dla każdej persony, Jev pyta dorosłych mieszkańców Krakowa: popiera (Noul), postawa (Score), najważniejszy argument (Choice). Wynik: poparcie ogółem, wg dzielnic, etapów życia, grup dotkniętych vs reszta, argumenty zwolenników (P>0,5) i przeciwników (P<0,5).
- Poziom poparcia = średnie P(popiera) (oczekiwany udział). Udział osób z P>0,5 to artefakt progu (SCT: 72–74%).
- SCT przez ten tryb (600 osób): średnie P 54–55% vs sondaż CEM 61%; kierowcy 52–56% za vs 38–44% przeciw (stara ścieżka `scenarios/sct.json`: 42/50, błąd). `validate_sct.py`: 4/4. Różnica: fakty z reguł zamiast „their current car is allowed”. Fakt kontekstowy „ok. 7% aut zakazanych” nic nie zmienił (±1 pkt), usunięty.
- Pułapki: z opcją „none” w pytaniu o argument Jev wybierał ją w 96% (usunięta); argumenty ważone P(popiera) wychodziły identyczne dla obu stron (podział twardy); LLM bez instrukcji robił grupy „niedotkniętych” i traktował LPG jako dozwolone.
- Reguły mogą używać cech z GUS: wykształcenie, typ gospodarstwa (NSP 2021), urodzenie za granicą (siatka 2021), aktywności z badań kultury i sportu GUS (`activity in swimming,...`); przy `leisure_relevant` Jev widzi też czas wolny persony. Przykład: zamknięcie Parku Wodnego: agent sam zrobił grupę „pływacy do 3 km” (16% poparcia vs 25% reszta).
- Argumenty: dwa pytania Choice (osobno argumenty za i przeciw); przy jednej wspólnej liście przeciwnicy wskazywali „czystsze powietrze”.
- Przykład: deptak na Karmelickiej: 45% poparcia; sąsiedzi (≤1 km) z autem 26%, bez auta 75%.

### Test archetypów (pomysł z MIT AgentTorch, `src/archetypes.py`)
Dorośli mieszkańcy grupowani po cechach istotnych dla decyzji; Jev pyta 1 przedstawiciela archetypu (spoza ocenianej próby), odpowiedź dostają wszyscy członkowie. SCT (krótki opis, bez okresu przejściowego), porównanie z 600 osobami pytanymi indywidualnie:
| | 6 cech (wiek, płeć, status, auto, grupa dotknięta, dochód): 279 archetypów | 7 cech (+dzieci): 457 |
|---|---|---|
| średnie P (te same 600 osób) | 52,3% vs indyw. 52,7% | 53,6% vs 52,7% |
| całe miasto (13,5 tys. dorosłych) | 51,1% | 52,7% |
| korelacja osoba po osobie | 0,81 | 0,78 |
| grupy dotknięte (stare benzyny / diesle) | 22% / 25% vs 21% / 26% | 22% / 24% |
| kierowcy za vs przeciw | 34% / 64% (indyw. 33% / 62%) | 47% / 51% |
Dwóch różnych członków tego samego archetypu: korelacja 0,93, średnia różnica 0,045, czyli Jev odpowiada prawie wyłącznie z tych cech; archetyp traci mało. Wariant 7 cech nie jest lepszy (więcej szumu progowego). Mapa dzielnic: archetypy dają gładszą mapę (odch. std 0,013 vs 0,022 w próbie 600, gdzie dzielnica ma 19–65 osób); korelacja obu map 0,36, bo próba to głównie szum. Uwaga: dzielnica nie jest cechą archetypu, więc różnice między dzielnicami wynikają tylko ze składu mieszkańców. Kierowcy 33/62 w tej wersji opisu (bez okresu przejściowego) nie zgadzają się z CEM także indywidualnie: to kwestia opisu, nie archetypów (z okresem przejściowym 56/38).
Koszt: ~280 zapytań Jev na CAŁE miasto zamiast 600 na próbę.
Większa próba (3000 indywidualnie, ok. 170 osób na dzielnicę): archetypy 6 cech 53,9% vs indywidualnie 52,3% (7 cech: 52,4% vs 52,4%); korelacja osoba po osobie 0,80; grupy dotknięte 23%/25% vs 22%/25%; **mapa dzielnic: korelacja 0,88** (przy 600 osobach było 0,36, bo próba była szumem). Różnice między dzielnicami wyjaśnia więc skład mieszkańców, a archetypy go oddają. Niestabilny jest tylko udział kierowców z P > 0,5 vs < 0,5 (49/43 przy 6 cechach, 33/61 przy 7): większość kierowców ma P ≈ 0,5, więc próg przeskakuje od wyboru przedstawiciela; raportować średnie P, nie progi.

### Reprezentatywność i 50 tys. person (`src/representativeness.py`)
Populacja przegenerowana do 50 196 person (1 ≈ 22 mieszkańców; `make personas`, N ?= 50000, generacja ~10 s); kopia 20 tys. w `out/*_20k_backup.csv`, stare cache w `out/_cache_20k/`. Wiek aut skalibrowany rangowo do CEPiK (wcześniej korekta dochodowa odmładzała auta: >15 lat 32,6% vs 37,5%; teraz 37,7%). Raport vs GUS/PKW: wiek, płeć, wykształcenie, gospodarstwa, zatrudnienie, paliwo aut, studenci, głos 2023: wszystkie w ±0,7 pkt. Luka: emeryci i renciści 162 tys. vs 179 tys. (−10%). Precyzja 95%: miasto ±0,5 pkt, dzielnica ±1,6–3,2 (najmniejsza 920 person), grupa 4% dorosłych ±2,6. Dopróbkowania małych dzielnic nie robiono: wiele obliczeń to proste średnie po personach, nierówne wagi by je przekłamały.
Pułapka: cache odpowiedzi Jev jest per id persony, a id się powtarzają po przegenerowaniu; do kluczy doszło `jev_sim.pop_version()` (hash rozmiaru i czasu pliku). Archetypy przy 50 tys.: decyzja 1417, wydarzenie 1085.

### Prawdziwe dojazdy: R5 na rozkładzie MPK (`src/travel_times.py`, `src/travel.py`, `out/travel_times.parquet`)
r5py (Java 21 z Homebrew, `JAVA_HOME` ustawiane w skrypcie) na GTFS ZTP Kraków (tramwaje + autobusy, kursy tylko w `calendar_dates.txt`) i wycinku OSM Kraków + obwarzanek (`osmium extract`, 59 MB; cała Małopolska zapychała stertę Javy). Piątek 18:00, okno odjazdów 30 min (mediana), auto bez korków + 5 min na parkowanie (ASSUMPTION). 827 kratek × (827 kratek + 22 miejsca) = 702 tys. par; sieć 87 s, komunikacja 99 s, auto 9 min.
Kontrola: Rynek → Plac Centralny 36 min komunikacją / 20 autem, Ruczaj → Rynek 35 / 18, w zwartym Krakowie mediana 49 / 19 min. 40% par bez dojazdu komunikacją w 150 min: obwarzanek (GTFS MPK nie ma kolei ani prywatnych busów); tam zostaje stary wzór. Stadion Wisły: punkt w ogrodzonym terenie, piesza część nie dochodzi; wtedy brana jest jego kratka.
`planner.minutes` i ankieta agenta danych używają macierzy (auto dla właścicieli aut, komunikacja dla reszty). Ranking koncertu rap: stary wzór 8 miejsc z indeksem 97–100, R5: Stare Miasto 100, reszta 93–95 (węzeł tramwajowy wygrywa, Grzegórzki wypadają). Półokresy zaniku z dojazdem (25–60 min) dobrane jeszcze pod stary wzór.

### Podaż z OpenStreetMap (`src/fetch_osm_poi.py`, `data/raw/osm_poi_krakow.json`)
30 kategorii (kawiarnie 443, restauracje 872, siłownie 112, szkoły 330, przedszkola 387, apteki 282, przystanki tramwajowe 415 i autobusowe 1873...), 19 968 obiektów w granicach Krakowa, z pliku Geofabrik `malopolskie-latest.osm.pbf` (~200 MB, md5, przetwarzanie lokalne pyosmium ~80 s; plik poza repo). Narzędzia agenta danych: `count_places` (obiekty i mieszkańcy na obiekt wg dzielnic), `nearby_places` (w promieniu od punktu).
Pułapka: najpierw Overpass API kategoria po kategorii: główny serwer zrywał połączenia (najpewniej limit IP po jednym dużym zapytaniu), zapasowe zwracały timeouty i puste odpowiedzi bez błędu (0 kawiarni); po ~2 h było 18/30 kategorii. Do większych wyciągów OSM używać plików Geofabrik, nie Overpass.

### Agent kalendarza (`src/calendar_agent.py`)
Konkretna data z wiadomości („13.11”, „15 maja”) ustawia też dzień tygodnia. Święta, długie weekendy (z dniami pomostowymi) i okres świąteczny liczone w kodzie (Wielkanoc algorytmem, Wigilia wolna od 2025): LLM był niespójny (ten sam piątek raz „długi weekend”, raz nie). gpt-5.4 z wyszukiwarką daje kalendarz uczelni (sesja, przerwy, Juwenalia), szkół i konkurencyjne wydarzenia z flagą „potwierdzone w źródle”; efekt liczony tylko dla potwierdzonych, reszta jako ryzyko. Mnożniki per grupa (studenci spoza Krakowa w wakacje 0,25 itd.) to ASSUMPTION. Szacunek popytu (bez wyszukiwarki) mnożony przez efekt konkurencji. Test 13.11.2026: znalazł 2 koncerty rap tego wieczoru (Ezhel, Hype Park; Peja, Klub Kwadrat), −28%. Test gpt-5.4 na dniach tygodnia (ten sam koncert): pn 0,83×, śr 0,92×, pt 1, sob 1,0×, nd 0,87×, Wigilia 0,15×, wakacje 0,83×; HETUS: sob 1,73×. Dzień tygodnia wieczorem pokazywany jako przedział (HETUS liczy cały dzień i pochodzi z czasu COVID).
**HETUS 2020 zebrany w pandemii:** miesiące nieużyteczne (marzec 1,54, sierpień 3,01), rytm tygodnia też może być zniekształcony.

### Precyzja: skąd szum i archetypy wszędzie
- Powtarzalność Jeva (40 osób × 3 te same zapytania): średnie odch. std 0,004–0,006, maks. rozrzut 0,04. Jev jest prawie deterministyczny, więc cały szum wyników to szum próby.
- Wydarzenia (`src/archetypes_event.py`, koncert rap 60 zł): 912 archetypów (wiek 7 przedziałów, płeć, status, dzieci, dochód, nawyk kategorii, dopasowanie gustu) pokrywa 17 312 osób 15+; vs 1200 indywidualnie: want r = 0,98, free 0,86, afford 0,65, p_base 0,93, średnie 0,119 vs 0,123, segmenty w granicach 1–2 pkt.
- Aplikacja: wydarzenia i decyzje zawsze przez archetypy (`planner.run(..., archetypes=True)`, `policy.run(..., archetypes=True)`), agent danych `ask_residents` też (klucz zgrubia się do limitu 1500 wywołań na turę). Front: jeden przycisk „Zapytaj całe miasto”, licznik decyzji AI i mieszkańców z odpowiedzią.
- Pułapka wydajności: `time_factor` parsował 144 przedziały HETUS dla każdej osoby (wynik 35 s przy 17 tys.); `lru_cache` + wektoryzacja dojazdu (`planner.minutes`) → 0,5 s.
- Pełne odpytanie wszystkich 20 tys. person: ~20 M tokenów ≈ $0,85 i ~15 min na pytanie; sens jako tryb „dokładny” w tle.

### Poglądy mieszkańców: głos w wyborach do Sejmu 2023 (`src/attitudes.py`, `out/attitudes.csv`)
Źródła: PKW wyniki w 454 obwodach Krakowa, 100% lokali zgeokodowanych (`src/fetch_pkw_krakow.py`, `data/raw/pkw_krakow.json`; sumy zgodne z oficjalnymi co do głosu dla Sejmu 2023 i prezydenta 2025; są też prezydent 2025 obie tury i prezydent miasta 2024) oraz exit poll IPSOS 2023 (N = 75 675): ilorazy szans KO/TD/Lewica/Konf vs PiS wg płci, wieku, wykształcenia, zawodu (Cześnik, Szczupska, Sychowiec, Polish Sociological Review 2025, tab. 1 model 1; `data/raw/exitpoll_2023_czesnik.pdf`). Metoda: lokalny rozkład z obwodów w pobliżu domu (jądro 0,7 km, maks. 1,5 km) × ilorazy szans z cech osoby, potem raking do wyniku PKW w każdej dzielnicy; 1 wylosowana partia na osobę. Wynik: Kraków zgodny z PKW (PiS 24,6/24,9, KO 34,5/34,4...), gradienty jak w exit pollu (18–29: PiS 9%, Lewica 24%, Konf 15%; 60+: PiS 42%). ASSUMPTION: niegłosujący (18%) jak głosujący w swojej grupie i miejscu; zawód z wykształcenia.
W trybie decyzji Jev widzi `voted_in_2023_parliamentary_election` (wyłączenie: `KS_VOTE=0`). SCT pełny opis, 1200 osób: bez głosu 57,4%, z głosem 58,7% (CEM 61%); kobiety–mężczyźni +1,4 → +3,3 pkt; wg partii bez głosu wszystkie 53–58% (model nie widział polaryzacji), z głosem KO 68%, Lewica 67%, TD 59%, PiS 47%, Konf 41%; korelacja poparcia dzielnic z udziałem KO+Lewica 0,50 → 0,78. Wszystkie 4 fakty CEM w obu wariantach. Archetypy z głosem w kluczu: 1105 (bez: 279).

### Walidacja na Budżecie Obywatelskim: wynik negatywny (`src/validate_bo.py`, `data/raw/bo_krakow.json`)
Dane: 4434 projekty BO 2019–2025 z punktami (3/2/1), zwycięzcami, kosztami (`src/fetch_bo_krakow.py`, źródła BIP + budzet.krakow.pl). Test 2025, 4 dzielnice (126 projektów, 30 zwycięzców): mieszkańcy (archetypy dzielnicy, Jev: „czy wybrałby ten projekt do swoich 3”) Spearman średnio 0,16 i 6/30 zwycięzców (losowo ok. 8); gpt-5.4 bez mieszkańców 0,59 i 9/30; sama reguła „droższy projekt wygrywa” 0,56. Wniosek: w BO wygrywają duże, widoczne projekty i mobilizacja, czego model gustu osoby nie widzi; GPT mógł też znać wyniki 2025 (niesprawdzone). Decyzja użytkownika: odpuszczamy BO jako walidację; do trybu decyzji zostaje SCT/CEM. Koszt testu ~$0.13.

### Front Next.js z animacją symulacji (`web/`)
Next 16 + Tailwind + deck.gl 9 + MapLibre 6 (podkład CARTO dark-matter bez etykiet) + framer-motion. Backend bez zmian poza: CORS dla :3000, `GET /api/population` (wszystkie persony, pozycja = kratka 1 km + deterministyczne przesunięcie, `src/live.py`), zdarzenia SSE `people` (próba z profilem) i `answers` (każda odpowiedź Jev w chwili przyjścia; z cache wszystkie naraz, front odsłania je falą ~4 s). Warstwy: populacja, próba (kolor = P), pierścień przy każdej świeżej odpowiedzi, TripsLayer przelotów chętnych do wybranego miejsca (top ~28% wg P × spadek z odległością) + PathLayer śladu, licznik ~N osób nad miejscem; polityka: dzielnice wyciągnięte w 3D, skala względna (różnice to często szum). SSE idzie z przeglądarki prosto do FastAPI (rewrite Next mógłby buforować). Pułapka: Turbopack nie pakuje modułowego workera MapLibre 6 (czarna mapa, „Worker failed to load”): `postinstall` kopiuje `maplibre-gl-worker.mjs` + `maplibre-gl-shared.mjs` do `web/public/maplibre/`, `setWorkerUrl` w `LiveMap.tsx`.

### Agent danych: pełna kontrola nad danymi (`src/datastore.py`, `src/data_agent.py`)
Router kieruje tu tryby `business` i `other` (pytania o mieszkańców, statystyki, lokal); dopytania kontynuują rozmowę (`previous_response_id`).
- `datastore.py`: jeden znormalizowany katalog. *residents*: persony jako tabela z angielskimi enumami i wagą = mieszkańcy na personę wg dzielnicy (liczby w osobach; 1,115 mln z obwarzankiem); pola: dzielnica, wiek, płeć, status, studiuje, pracuje, wykształcenie, gospodarstwo, dzieci, urodzenie za granicą, dochód, auto (wiek, paliwo), komunikacja, aktywności z badań GUS, wydatki na kulturę, etap życia, współrzędne. `query()` = typowane filtry (w tym `home within_km lat,lon,km`), do 2 grupowań, count/share/mean/median + `n_personas`. *tables*: 119 kluczy (city.* BDL Kraków, survey.* badania GUS, hetus.*, venues, districts). *places*: gazetteer.
- `data_agent.py`: pętla gpt-5.4 (Responses API, ścisłe schematy narzędzi): `query_residents`, `get_table`, `find_place`, `ask_residents` (ankieta Jev na próbie z filtra, czas dojazdu / koszt liczone w kodzie, limit 1500 osób na turę). Limit 10 iteracji. Kontrola liczb odpowiedzi: każda liczba musi być w wynikach narzędzi (±2%), procentem z nich, udziałem dwóch liczb, iloczynem udział × liczba albo pochodzić z pytania; inaczej jedna tura poprawki, potem flaga w UI. UI: każdy krok jako wykres / kafelki z filtrami i n.
- Koszt: pytanie o kawiarnię dla studentów ok. 36k tokenów gpt-5.4 + 800 osób Jev (~$0.03 Jev).
- Pułapki: model pisze liczby z wąską spacją (U+202F) jako separatorem tysięcy; losowanie z wagami pandas wywala się przy małej grupie (losujemy równo, ważymy przy agregacji); niepracujący studenci mają ZAŁOŻONE 1800 zł (agent wziął to za „własny dochód 100%”; opis pola poprawiony, dodane `employed`, `studying`).

### Fakty z GUS w odpowiedzi (`src/gus_facts.py`)
gpt-5.4-mini dostaje 53 tabele BDL dla Krakowa (`data/raw/krakow_gus_extra.json`, bez identyfikatorów zmiennych, ~8k tokenów) i pytanie użytkownika, wybiera do 4 faktów i pisze je po polsku z tabelą źródłową. Kod sprawdza każdą liczbę: musi być wartością z tej tabeli (±1% / zaokrąglenie), udziałem dwóch jej wartości, rokiem, etykietą kategorii („16–20 lat”) albo liczbą z pytania; inaczej fakt odpada. Testy: zmyślone „300 000 aut benzynowych” i „40% diesli” odpadają, „ok. 24% (126 638 z 521 108)” przechodzi. Pułapki: spacja jako separator tysięcy sklejała „21 25” w 2125; poziom `powiat 011212161000` to miasto Kraków, nie „powiat krakowski” (instrukcja w prompcie). Liczone równolegle z symulacją, w obu trybach.

### Jev vs mały LLM (`src/compare_models.py`, `src/llm_backend.py`, gpt-5.4-mini, 300 tych samych osób)
LLM dostaje ten sam stan JSON i te same pytania, zwraca prawdopodobieństwo 0–100 na pytanie tak/nie przez ścisły schemat JSON (`reasoning_effort="low"`).

| test | Jev | gpt-5.4-mini |
|---|---|---|
| GUS koncerty: poziom / korelacja / błąd | 38% / 0,78 / 4,4 pkt | 34% / 0,71 / 7,2 pkt (GUS 34%) |
| GUS teatr | 48% / 0,39 / 9,0 pkt | 28% / **0,75** / 23,5 pkt (GUS 52%) |
| GUS kino | 60% / 0,95 / 18,7 pkt | 54% / 0,94 / 24,5 pkt (GUS 79%) |
| GUS kluby | 36% / **0,97** / 6,0 pkt | 16% / 0,89 / 13,9 pkt (GUS 30%) |
| dzień: P(pójdzie) sobota / poniedziałek | 1,09× | 1,25× |
| dojazd: logit na +10 min (Tauron vs Studio) | −0,058 | −0,059 |
| różnice grup (hip-hop sob.): studenci / seniorzy | 2,8× / 0,49× | 2,1× / 0,67× |

Wnioski: mały LLM nie jest lepszy. Gorzej trafia poziom (zaniża kulturę, zwłaszcza teatr i kluby), bardziej spłaszcza różnice między grupami, tak samo słabo reaguje na dojazd; trochę lepiej rozróżnia dni tygodnia i lepiej układa teatr według grup. Przy tym ~40× więcej tokenów (wejście ~760, wyjście ~90 na osobę) i wolniej. Zostajemy przy Jev; do sprawdzenia większy model (np. gpt-5.4 albo Claude) na tym samym zestawie testów.

### Walidacja: symulowane konsultacje SCT vs prawdziwy sondaż (`src/validate_sct.py`)
- **Punkt odniesienia:** CEM Instytut Badań Rynku i Opinii Publicznej dla Krakowskiego Alarmu Smogowego, opublikowane 3.01.2024, N = 600 dorosłych krakowian, CAWI. Po wyjaśnieniu faktycznych zasad (benzyna do ok. 26 lat, diesel do ok. 17 lat, czyli zasady z `scenarios/sct.json`) **61% popiera SCT**; wyżej kobiety i rzadko jeżdżący autem; nawet wśród często jeżdżących autem popierających jest więcej niż przeciwnych. Opublikowane są tylko te cztery fakty, bez tabel krzyżowych.
- **Symulacja** (Jev, 3000 person, odpowiedzi z wcześniejszego przebiegu, nic nie dopasowywane), dorośli w Krakowie (n = 2370):

  | fakt | badanie | symulacja | zgodne |
  |---|---|---|---|
  | poparcie | 61% | 66% z P > 0,5 (średnie P 57%) | tak |
  | kobiety > mężczyźni | tak | 57,4% vs 56,5% (kierunek dobry, różnica minimalna) | tak |
  | bez auta > z autem | tak | 69% vs 49% | tak |
  | wśród kierowców więcej za niż przeciw | tak | 42% za vs 50% przeciw | **nie** |
- **Wniosek:** poziom i główne kierunki się zgadzają; model jest zbyt negatywny wobec kierowców (w symulacji ok. 12% aut nie spełnia norm, więc większość kierowców nie traci, a i tak częściej są przeciw). Do sprawdzenia: opis scenariusza (mandat 500 zł i kamery mogą nastawiać negatywnie; badani CEM dostali też wyjaśnienie zasad) oraz podanie w stanie faktu, że ok. 9 na 10 aut w Krakowie spełnia normy.
- **Inne źródła (bez podziałów, inne pytania):** More in Common Polska dla Polityki Insight, 6–19.08.2026, N = 800, CAWI/CATI: 72% uważa, że strefy są potrzebne, ale muszą być dobrze zaprojektowane. Oficjalne sprawozdanie z konsultacji (XI 2024 – I 2025, ok. 20 tys. głosów; `data/raw/sct/`) to zbiór uwag tekstowych bez statystyk i z samoselekcji, więc nie nadaje się do porównania liczbowego.

### Backtest frekwencji na prawdziwych wydarzeniach (`src/backtest.py`, `data/raw/events_ground_truth.json`)
- **Dane:** 21 wydarzeń z Krakowa 2024–2026 ze źródłami (zebrane przez agenta, żadnej liczby z głowy). Porównywalne (unikalne osoby na jednym wydarzeniu): 13: sanah na stadionie 40 tys., Billie Eilish w Tauronie ~17 tys., 7 meczów Wisły (18–32,7 tys.), Cracovia 12,8 tys., trzy darmowe wydarzenia (970 / 3 000 / 12 000). Wejściówki z wielu dni (Krakowski Festiwal Filmowy, FKŻ, Noc Muzeów, IEM, jarmark, maraton) tylko wylistowane. Off Camera nie publikuje frekwencji.
- **Sam model mieszkańców (Jev):** kolejność wydarzeń Spearman −0,44, mediana błędu po kalibracji leave-one-out 52%, przy naiwnym „frekwencja = pojemność” 11–14%. Darmowe małe wydarzenia przeszacowane 10–100× (model mierzy, kto chciałby pójść, a nie kto wie o wydarzeniu), gwiazdy niedoszacowane (brak popularności artysty i przyjezdnych).
- **Workflow z agentem (`src/event_agent.py`):** najpierw gpt-5.4 z wyszukiwarką ocenia stronę podaży (zasięg osiedle→świat, popularność gwiazdy, promocja, udział przyjezdnych, czynniki specjalne; typowany JSON; zakaz używania frekwencji/sprzedaży; skan uzasadnienia: 0 podejrzeń przecieku). Gwiazda i „dla kogo” idą do stanu Jev; w kodzie frekwencja = min(pojemność, lokalni × świadomość(zasięg, promocja) / (1 − przyjezdni)), świadomość = sigmoid(a + b·(zasięg + 0,5·promocja)), a i b dopasowane leave-one-out (na wszystkich: osiedle 0,5%, miasto 1,7%, region 5,5%, kraj 16%, świat 39%). Brakującą pojemność bierzemy z `viz/venues.json` (znana przed wydarzeniem).
- **Wynik z agentem:** Spearman **0,45** (z −0,44), mediana błędu **39%** (z 52%); darmowe wydarzenia z błędów rzędu 4 000–11 000% do −64%…+695%; Billie Eilish i większość meczów weekendowych trafione (na limicie pojemności). Nadal **gorzej niż naiwna pojemność (14% na 10 wydarzeniach ze znaną pojemnością)**.
- **Źródła błędów:** (1) niespójne oceny agenta dla tego samego klubu (Wisła–Odra „region, średnia promocja” → 5,7 tys. przy 32,6 tys.; reszta meczów „kraj, duża”), a jeden poziom zasięgu to ~3× świadomości; (2) mnożnik dnia z HETUS za ostry dla meczów (poniedziałek 18 tys., czwartkowe derby 31,8 tys., model 5–9 tys.); (3) tylko 13 wydarzeń do kalibracji.
- **Wniosek:** agent wyraźnie pomaga (kolejność dodatnia, rzędy wielkości dla darmowych wydarzeń), ale liczba osób na konkretne wydarzenie dalej nie jest wiarygodna tam, gdzie znamy pojemność; wartość dodana jest głównie dla wydarzeń bez limitu miejsc (darmowe, plenerowe) i w odpowiedzi „kto / skąd / dlaczego”.

### Prognoza frekwencji: co działa najlepiej (`src/forecast.py`, 13 prawdziwych wydarzeń, leave-one-out)
Poprawki agenta: mediana z 3 niezależnych ocen + te same oceny zasięgu i popularności dla tego samego klubu/artysty (wcześniej jeden mecz Wisły „region”, reszta „kraj”); dzień tygodnia dla sportu z profilu HETUS „spotkania towarzyskie” (pon–czw 0,53 soboty) zamiast „rozrywka i kultura” (0,3), bez dopasowania do frekwencji.

| predyktor | mediana błędu | średni \|log\| | Spearman | w ±25% |
|---|---|---|---|---|
| sam LLM (gpt-5.4 bez wyszukiwarki, mediana 3 szacunków) | 28% | 0,31 | 0,81 | 46% |
| agent + mieszkańcy (Jev, świadomość, przyjezdni, pojemność) | 29% | 0,66 | 0,72 | 46% |
| analogi: Jev ocenia podobieństwo par wydarzeń, przenosimy wypełnienie sali najbliższych | 24% | 0,59 | 0,62 | 54% |
| ensemble (średnia geometryczna trzech) | 27% | 0,42 | 0,76 | 46% |
| **reguła: jest pojemność → pojemność; brak limitu → szacunek LLM** | **18%** | **0,22** | **0,88** | **62%** |
| naiwnie: pojemność (10 wydarzeń z pojemnością) | 14% | 0,17 | 0,70 | 80% |

Wnioski: popularne wydarzenia biletowane zapełniają salę, więc tam pytanie brzmi „czy się wyprzeda”; bez limitu najlepszy jest szacunek LLM. Model mieszkańców zostaje od „kto, skąd, dlaczego, które miejsce, który dzień” (zweryfikowane: kalibracja GUS, sondaż SCT). Zastrzeżenia: 13 wydarzeń, 7 to mecze jednego klubu; LLM mógł znać wydarzenia z 2025 r. (na wydarzeniach z 2026 r. też trafiał).

**Planer po zmianie:** przed pytaniem mieszkańców agent (gpt-5.4 + wyszukiwarka, 1 wywołanie) ocenia wydarzenie, a drugi model szacuje popyt na wybrany termin; gwiazda i grupa docelowa idą do stanu Jev; dla każdego miejsca przewidywana publiczność = min(pojemność, popyt × względny zasięg miejsca z modelu mieszkańców) i sygnał „wyprzedane”; inne dni = popyt × mnożnik HETUS. Koszt zapytania: centy; czas: +ok. 15 s na agenta.

**Gdzie pasuje Jev / wyszukiwarka:** Jev robi tu to, w czym jest najlepszy: wyciąganie pól z wiadomości, tanie decyzje tysięcy person, oceny podobieństwa par wydarzeń (analogi). Wyszukiwarka (Elastic / baza wektorowa) ma sens dopiero przy setkach wydarzeń z frekwencją (kalendarze, prasa): wtedy analogi z bazy staną się mocnym, tanim predyktorem.

### Test funkcjonalny całości (2026-10-04, populacja 50 tys., R5, kalendarz)
- Wydarzenie z datą (koncert rap 13.11, przeglądarka): 32 s, 1085 archetypów za 43 217 osób, konkurencja z 13.11 wykryta (−28%), ranking z R5 (Stare Miasto 100, reszta 93–95), przeloty i licznik działają.
- Decyzja dzielnicowa (płatne parkowanie w Podgórzu, API): BŁĄD znaleziony i poprawiony: oceniało całe miasto; teraz populacja = mieszkańcy obszaru decyzji (1994 dorosłych, 470 archetypów, 45%), udziały grup liczone w obszarze (kierowcy 64%).
- Agent danych (API): siłownie 10 s (OSM ma tylko 112 siłowni w Krakowie: narzędzie zwraca teraz ostrzeżenie o niekompletności), wybory + seniorzy w Bieńczycach 12 s, liczby zweryfikowane.
- Poprawione: fakty GUS z serii miesięcznych (model podawał lutową medianę jako „medianę 2025”; teraz dostaje średnią z miesięcy), „zamiast piątku” (dopełniacz), przycisk „Sprawdź: sobota (efekt niepewny)”, liczby osób w komunikatach z danych zamiast wpisanych na sztywno.
- Testy w przeglądarce kolidują z użytkownikiem pracującym w tej samej przeglądarce na :3000 (browser-harness przełączył się na jego kartę); testować przez API albo na osobnym porcie / zdalnej przeglądarce.

### Zestaw testowy aplikacji (`src/eval_app.py`, przez działające API)
Prawdy liczone w chwili testu z danych (PKW, GUS BDL, OSM, persony), nie wpisane ręcznie. Wynik 2026-10-04: kierowanie do trybów 6/6, fakty agenta danych 8/8 (wynik Trzaskowskiego w Krowodrzy 70,2%, frekwencja Nowa Huta 76,6%, 11 kin, 521 108 aut, 137 784 studentów, 236 kawiarni na Starym Mieście wg OSM, ~9600 seniorów w Bieńczycach, wyższe wykształcenie ~44%; 0 niesprawdzonych liczb, 5–9 s na pytanie), wydarzenia 2/2 (koncert 15 tys. → TAURON Arena, wieczór poezji 60 osób → lokal w okolicy Starego Miasta). Koszt ~$0,5.

### Bezpieczeństwo (`src/fetch_crime_krakow.py`, `data/raw/crime_krakow.json`)
Przestępstwa stwierdzone wg 8 rejonów komisariatów (KP I–VIII = całe dzielnice, mapowanie wg strony KMP) z „Raportów o stanie Miasta” 2004–2020; po 2020 tylko suma miasta (2021–2025: 18–25 tys./rok). Brak danych dzielnicowych po 2020 i brak publicznych danych KMZB. Ankieta miasta o poczuciu bezpieczeństwa wg dzielnic (~100 osób na dzielnicę, ±10 pkt). Tabele agenta: `safety.crimes_by_police_area` (średnia 2016–2020, na 1000 mieszkańców: KP I Stare Miasto 83,8, Grzegórzki 36,5, Prądniki 17,3...), `safety.city_total_2021_2025`, `safety.perceived_by_district`. Liczone tam, gdzie przestępstwo się wydarzyło (Stare Miasto zawyżone przez turystów i życie nocne).

### Przypadki demo (przyciski startowe w `web/src/lib/useSim.ts`, przetestowane przez API 2026-10-04)
1. Koncert rap 13.11 (organizator): konkurencja tego wieczoru (Ezhel, Peja), popyt ~610, Stare Miasto najlepiej dostępne. 2. Kawiarnia dla studentów: Bronowice 7800 studentów / 4 kawiarnie vs Stare Miasto 12 500 / 236. 3. Płatne parkowanie w Podgórzu (rada dzielnicy): 42%, kierowcy 46% vs reszta 36% (tani abonament). 4. Deptak na Karmelickiej: Stare Miasto + Krowodrza, kierowcy z okolicy 43%, sąsiedzi bez auta 73%. 5. Bezpieczeństwo: centrum zgodne z policją, Bieżanów-Prokocim po zmroku 61% mimo niskiej przestępczości. 6. Nowa Huta vs Krowodrza: wiek, głos 2023, oficjalna 2. tura 2025. Też działają: food trucki w majówkę (długi weekend), piknik w Nowej Hucie, przychodnie a seniorzy (Mistrzejowice), SCT.
Pytania „zapytaj cały Kraków” (2026-10-04): nocna prohibicja 22–6 (decyzja): 58%, seniorzy 63% vs studenci 52%; całodobowy żłobek (agent): ~52% rodziców deklaruje skorzystanie, luki Krowodrza 8500 rodziców / 18 przedszkoli, Podgórze 11 800 / 19 (mapa: przedszkola z OSM + dzielnice); tramwaj co 5 min dla kierowców: 64–66% „częściowo by się przesiadło”, dzielnice płasko: typowe zawyżenie deklaracji, nie na demo. Poprawki: router kieruje pytania o zachowania i korzystanie z usług („czy by…”, „zapytaj mieszkańców”) do agenta danych, nie do trybu decyzji (tam pytało wszystkich dorosłych i bez OSM); w decyzji grupa obejmująca ~wszystkich nie jest pokazywana („wobec nan%”); „to oni najbardziej tracą” → „tu sprzeciw jest najsilniejszy” (częstszy tramwaj nikomu nic nie zabiera).
Mapy dzielnic z danych (więcej różnic niż ankiety Jev, gdzie dzielnice różnią się 2–6 pkt, bo tylko składem): dojazd komunikacją na Rynek (nowe narzędzie `travel_by_district`, R5): Stare Miasto 20 min … Wzgórza Krzesławickie 60 min; mieszkańcy na aptekę (OSM): Wzgórza Krzesławickie 7167 … Stare Miasto 1503; seniorzy 65+: Mistrzejowice 25%, Bieńczyce 23%; stare auta pod SCT wg dzielnic 2–4% (płasko: wiek aut w personach nie zależy od dzielnicy, brak danych CEPiK per dzielnica). Agent wysyła zdarzenie `choropleth` dla każdego wyniku z wartościami wg dzielnic (tabele PKW / bezpieczeństwo / przestępczość wg rejonów rozłożona na dzielnice, wiersze `query_residents`, `count_places`, `travel_by_district`).
Poprawione po przeglądzie: „najwyższe = najniższe” przy równych dzielnicach; „podoba się 97%” przy darmowych wydarzeniach zamienione na wyjaśnienie, że decyduje wiedza o wydarzeniu i dojazd.

### Front: jeden panel-raport, mniej „AI slopu”, pytania uzupełniające, szybkość (2026-10-04)
- Układ: mapa ~70% ekranu (liczniki, legenda, karta mieszkańca, przycisk 3D na mapie), po prawej jeden panel: rozmowa, a każda odpowiedź jako karta raportu (kafelki, krótkie wnioski, wykresy, źródła GUS i kroki agenta do rozwinięcia, stopka z metodą).
- Wnioski: jedno krótkie zdanie na punkt; zastrzeżenia (dokładność popytu, HETUS z pandemii, archetypy, walidacja SCT) w stopce `notes`. Agent danych: odpowiedź w 1. zdaniu, ≤4 punkty, pogrubienie tylko 1–3 liczb, bez „Ograniczenia:” i powtórek „to symulacja”.
- Pytania uzupełniające (`src/followups.py`, gpt-5.4-mini, ~0,1 centa): 3 propozycje po każdej odpowiedzi (zmiana parametru, przybliżenie, nowy kąt), wysyłane osobnym zdarzeniem SSE po wyniku; klik = zwykła wiadomość w tej samej rozmowie.
- Szybkość: Jev 40 równoległych zapytań (`KS_JEV_CONC`), gpt-5.4 do scenariusza decyzji i oceny wydarzenia z `reasoning=low` (`KS_EFFORT`; walidacje liczone przy medium), równoległe narzędzia agenta w jednej turze, wczytanie danych przy starcie serwera (pierwsze pytanie po restarcie było ~40 s wolniejsze). Pomiar: scenariusz decyzji 45 → 25 s, od „Zapytaj całe miasto” do wyniku ~10 s, 730 decyzji Jev w 5,5 s.
- Kontrola liczb przy dopytaniach: agent odpowiadał liczbami z poprzedniej tury, a kontrola widziała tylko bieżącą (wszystko oflagowane); `data_agent.HISTORY` trzyma wyniki narzędzi rozmowy w pamięci serwera (gubione po restarcie).

## 4. Wizualizacja (`viz/`)
- `build_map.py` osadza persony (20 tys.), siatkę, scenariusz i `venues.json` w `template.html` → `index.html` (~700 KB, działa offline jako plik).
- Kontrolki: miejsce (lista, klik na mapie albo klik w słupek rankingu), gatunek, dzień, cena, wiek docelowy, wrażliwość na dojazd, pojemność, studenci spoza Krakowa on/off. Model liczy się w JS na żywo.
- Mapa: canvas, proste rzutowanie lat/lon; komórki są lekko skośne, bo siatka jest w EPSG:3035. Dwa widoki: kwadraty 1 km (liczba zainteresowanych) albo dzielnice (zainteresowani na 1000 mieszkańców). Granice dzielnic i miasta jako obrysy, numerki 1–3 = najlepsze dzielnice z rankingu. Brak podkładu mapy.
- Kolory: jedna sekwencyjna skala niebieska (7 kroków, progi kwantylowe), osobne wartości dla ciemnego motywu, czerwony punkt = miejsce.
- Pojemności w `venues.json` są przybliżone.

## 5. Roadmapa
**Na demo (priorytet):**
- [ ] Kalibracja: znaleźć sprzedaż biletów na 1–2 podobne wydarzenia i dopasować wyraz wolny (−2,2) albo przeliczenie „chęć → zakup”.
- [x] Studenci spoza Krakowa w rejonach akademików i uczelni (liczba do zweryfikowania).
- [x] Dzielnice + granica miasta (OSM), ranking „gdzie zrobić”.
- [x] Agenci LLM na próbce person, typowane wyjście, surogat na całe miasto; porównanie z heurystyką w konsoli.
- [ ] Wyniki LLM w `viz/` (przełącznik heurystyka / LLM, powody wg dzielnic, cytaty).
- [ ] Conjoint: persona dostaje 4–6 wariantów scenariusza i je rangując; z wyborów fitujemy wagi utility zamiast wpisywać je ręcznie.
- [ ] Podkład mapy (np. Leaflet + kafelki OSM).
- [ ] Dochód zależny od miejsca (proxy: ceny mieszkań/najmu wg dzielnic).
- [ ] Okres roku: wakacje = studenci wyjeżdżają.
- [x] Scenariusze spoza koncertów: `scenarios/sct.json` (Strefa Czystego Transportu, policy), `scenarios/mpk_fare.json` (podwyżka biletów, transit). Oba hipotetyczne, liczby w opisach wymyślone.

**Dalej:**
- [ ] Transport: czas dojazdu komunikacją zamiast km (GTFS MPK Kraków).
- [ ] Sieć znajomych i efekt „idę, bo idą znajomi”.
- [ ] Inne miasta: parametryzacja ID jednostek i bbox.
- [ ] Batch API (50% taniej) dla większych próbek; hosting i cache wyników. Nie odpytywać GUS na żywo w demie.

## 6. Pułapki, na które już trafiliśmy
- 429 od BDL bez klucza mimo 2 zapytań (limit IP wyczerpany przez kogoś innego). Rozwiązanie: klucz.
- Stronicowanie `data/by-unit` (patrz 2a).
- Puste pola w siatce NSP (patrz 2b).
- `.env` z kluczem jest w `.gitignore`. Nie commituj go.
- Zmiana IP, żeby obejść limity GUS, odpada. Klucz daje wystarczająco dużo zapytań.

## 7. Słowniczek
- **BDL**: Bank Danych Lokalnych GUS. **NSP 2021**: Narodowy Spis Powszechny 2021.
- **Rezydent (NSP)**: osoba mieszkająca w danym miejscu co najmniej 12 miesięcy.
- **EPSG:3035**: europejskie odwzorowanie LAEA (metry), w nim jest siatka 1 km. **EPSG:2180**: PUWG 1992.
- **Persona**: syntetyczny mieszkaniec, czyli jeden wiersz w `out/personas.csv`.
