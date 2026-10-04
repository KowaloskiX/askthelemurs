"""Validate the simulated Clean Transport Zone (SCT) consultation against a real representative poll.
Ground truth: CEM Instytut Badań Rynku i Opinii Publicznej for Krakowski Alarm Smogowy, published 2024-01-03,
N = 600 adult Kraków residents, CAWI. After learning the actual rules (petrol cars up to ~26 years, diesel up to
~17 years; the same rules as scenarios/sct.json), 61% support the SCT; support is higher among women and among people
who rarely use a car; even among frequent car users supporters outnumber opponents.
https://krakowskialarmsmogowy.pl/2024/01/03/wiekszosc-mieszkancow-krakowa-popiera-strefe-czystego-transportu-wyniki-badan-spolecznych/
(Only these four facts are published; no crosstab numbers.)
Input: out/jev/sct_personas.csv (python3 src/jev_sim.py scenarios/sct.json --n 3000) or the app's policy mode
(python3 src/policy.py "<opis SCT>" --n 600, then out/policy/last_personas.csv).
Level = mean of Jev's calibrated P(support) (expected share of supporters); the share with P > 0.5 is shown for reference.
"""
import sys
import pandas as pd

f = sys.argv[1] if len(sys.argv) > 1 else "out/jev/sct_personas.csv"
A = pd.read_csv(f)
K = A[(A.age >= 18) & (A.district != "poza Krakowem")]          # same population as the poll: adults living in Kraków
yes = K.support > 0.5
drv = K[K.has_car]
rows = [
    ("poparcie SCT (średnie P)", "61%", f"{K.support.mean():.0%} (udział P>0,5: {yes.mean():.0%})", abs(K.support.mean() - 0.61) <= 0.07),
    ("kobiety popierają częściej niż mężczyźni", "tak", f"K {K[K.sex == 'K'].support.mean():.1%} vs M {K[K.sex == 'M'].support.mean():.1%}",
     K[K.sex == "K"].support.mean() > K[K.sex == "M"].support.mean()),
    ("bez auta / rzadko autem popierają częściej", "tak", f"bez auta {K[~K.has_car].support.mean():.0%} vs z autem {drv.support.mean():.0%}",
     K[~K.has_car].support.mean() > drv.support.mean()),
    ("wśród kierowców więcej popierających niż przeciwnych", "tak", f"popiera {(drv.support > .5).mean():.0%} vs przeciw {(drv.support < .5).mean():.0%}",
     (drv.support > .5).mean() > (drv.support < .5).mean()),
]
print(f"Walidacja SCT: {f}, dorośli w Krakowie n={len(K)}\n")
print(f"{'fakt z badania CEM (N=600, 2024)':52s} {'badanie':>8s}  {'symulacja':38s} zgodne?")
for name, truth, sim, ok in rows:
    print(f"{name:52s} {truth:>8s}  {sim:38s} {'TAK' if ok else 'NIE'}")
print(f"\nzgodność: {sum(r[3] for r in rows)}/{len(rows)}")
