"""Download GUS survey publications (XLSX table attachments) used for persona leisure traits.

Run from repo root:  python src/fetch_gus_surveys.py
Files land in data/raw/gus_surveys/ with their original names. ZIPs are extracted
into a subfolder named after the archive. Existing files are not re-downloaded.
"""
import os
import time
import urllib.request
import zipfile

OUT = "data/raw/gus_surveys"
BASE = "https://stat.gov.pl/download/gfx/portalinformacyjny/pl/defaultaktualnosci"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"

# publication page -> attachment URLs
SOURCES = {
    "culture_2024": {
        "publication": "Uczestnictwo ludności w kulturze w 2024 r. (GUS, 18.12.2025)",
        "page": "https://stat.gov.pl/obszary-tematyczne/kultura-turystyka-sport/kultura/uczestnictwo-ludnosci-w-kulturze-w-2024-r-,6,4.html",
        "files": [
            f"{BASE}/5493/6/4/1/uczestnictwo_ludnosci_w_kulturze_2024_tablice.xlsx",
            f"{BASE}/5493/6/4/1/uczestnictwo_ludnosci_w_kulturze_2024_precyzja_wynikow_badan.xlsx",
        ],
    },
    "sport_2025": {
        "publication": "Uczestnictwo w sporcie i rekreacji ruchowej w 2025 r. (GUS, full publication)",
        "page": "https://stat.gov.pl/obszary-tematyczne/kultura-turystyka-sport/sport/uczestnictwo-w-sporcie-i-rekreacji-ruchowej-w-2025-r-,4,5.html",
        "files": [
            f"{BASE}/5495/4/5/1/uczestnictwo_w_sporcie_i_rekreacji_ruchowej_w_2025_r._tablice.xlsx",
            f"{BASE}/5495/4/5/1/uczestnictwo_w_sporcie_i_rekreacji_ruchowej_w_2025_r._tablice_precyzji.xlsx",
        ],
    },
    # signal editions (charts only; kept for reference, not parsed)
    "sport_2025_signal": {
        "publication": "Uczestnictwo w sporcie i rekreacji ruchowej w 2025 r. (informacja sygnalna)",
        "page": "https://stat.gov.pl/obszary-tematyczne/kultura-turystyka-sport/sport/uczestnictwo-w-sporcie-i-rekreacji-ruchowej-w-2025-r-,5,3.html",
        "files": [
            f"{BASE}/5495/5/3/1/uczestnictwo_w_sporcie_i_rekreacji_ruchowej_w_2025r_tab.xlsx",
        ],
    },
    "time_use_2023": {
        "publication": "Budżet czasu ludności w 2023 r. (GUS, 26.06.2025)",
        "page": "https://stat.gov.pl/obszary-tematyczne/warunki-zycia/dochody-wydatki-i-warunki-zycia-ludnosci/budzet-czasu-ludnosci-w-2023-r-,36,1.html",
        "files": [
            f"{BASE}/5486/36/1/1/budzet_czasu_ludnosci_2023_aneks_tabelaryczny_korekta.xlsx",
        ],
    },
    "budgets_2025": {
        "publication": "Budżety gospodarstw domowych w 2025 r. (GUS)",
        "page": "https://stat.gov.pl/obszary-tematyczne/warunki-zycia/dochody-wydatki-i-warunki-zycia-ludnosci/budzety-gospodarstw-domowych-w-2025-r-,9,24.html",
        "files": [
            f"{BASE}/5486/9/24/1/budzety_gospodarstw_domowych_w_2025_r_tablice.zip",
        ],
    },
}


def fetch(url: str) -> str:
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, url.rsplit("/", 1)[-1])
    if not os.path.exists(path):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
            f.write(r.read())
        print(f"downloaded {path} ({os.path.getsize(path)} B)")
        time.sleep(1)
    else:
        print(f"exists     {path}")
    if path.endswith(".zip"):
        dest = path[:-4]
        if not os.path.isdir(dest):
            with zipfile.ZipFile(path) as z:
                z.extractall(dest)
            print(f"extracted  {dest}")
    return path


if __name__ == "__main__":
    for key, src in SOURCES.items():
        for u in src["files"]:
            fetch(u)
