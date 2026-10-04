N ?= 50000
SCENARIO ?= scenarios/concert.json
all: personas sim viz
data:      ; python3 src/fetch_bdl.py && python3 src/fetch_bdl_extra.py && python3 src/fetch_nsp_grid.py && python3 src/fetch_grid_extra.py && python3 src/fetch_districts.py && python3 src/fetch_gus_surveys.py && python3 src/parse_gus_surveys.py
personas:  ; python3 src/personas.py $(N)
sim:       ; python3 src/simulate.py $(SCENARIO)
agents:    ; python3 src/agents.py $(SCENARIO)
jev:       ; python3 src/jev_sim.py $(SCENARIO)
calibrate: ; python3 src/jev_sim.py $(SCENARIO) --calibrate
ask:       ; python3 src/ask.py "$(Q)"
app:       ; python3 -m uvicorn app.server:app --port 8000
web:       ; cd web && pnpm dev   # Next.js front on :3000, needs `make app` (API on :8000)
viz:       ; python3 viz/build_map.py
.PHONY: all data personas sim agents jev calibrate ask app web viz
