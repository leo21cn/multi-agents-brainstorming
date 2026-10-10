PY := ./backend/.venv/Scripts/python.exe
RUN := $(PY) -c "import sys; sys.path[:0]=['.','backend']; from scripts.$(M).$(F) import $(FN) as f; print(f())"

.PHONY: up seed test benchmark

up:
	cd backend && ./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000

seed:
	$(PY) -c "import sys; sys.path[:0]=['.','backend']; from scripts.seed_config import main as s; s()"
	$(PY) -c "import sys; sys.path[:0]=['.','backend']; from scripts.seed_corpus import main as c; c()"

test:
	cd backend && ./.venv/Scripts/python.exe -m pytest -q

benchmark:
	$(PY) -c "import sys; sys.path[:0]=['.','backend']; from scripts.run_benchmark import run; r=run('scripts/benchmark_propositions.yaml'); [print(f'{k}: {v}') for k,v in r.items()]"
