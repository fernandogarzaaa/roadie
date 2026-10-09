PY ?= python
CORE = app/planner.py app/geo.py app/qloo/client.py app/qloo/models.py app/qloo/cache.py

.PHONY: help install dev lint typecheck test check run mock docker mutation mutation-ci clean

help:            ## list targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

install:         ## editable install with dev tools
	$(PY) -m pip install -e ".[dev]"

lint:            ## ruff lint + format check
	ruff check .
	ruff format --check .

typecheck:       ## mypy
	mypy app

test:            ## pytest (fully offline: mock Qloo + mocked HTTP + scripted LLM)
	pytest -q

check: lint typecheck test  ## everything CI runs except docker

run:             ## dev server with whatever .env / env vars are set
	uvicorn app.main:app --reload --port 8000

mock:            ## dev server on synthetic Qloo fixtures and the scripted LLM
	QLOO_MOCK=1 LLM_MOCK=1 uvicorn app.main:app --reload --port 8000

docker:          ## build and run the container in mock mode
	docker build -t roadie .
	docker run --rm -p 8000:8000 -e QLOO_MOCK=1 -e LLM_MOCK=1 roadie

mutation:        ## full testteeth mutation run on the core modules
	testteeth run $(CORE) --pytest-args "tests -q" --json mutation.json

mutation-ci:     ## scoped, sampled variant used by the non-blocking CI job
	testteeth run app/planner.py app/geo.py app/qloo/models.py --pytest-args "tests/test_planner.py tests/test_geo.py tests/test_models.py -x -q -p no:cacheprovider" --max-mutants 150 --fail-under 80 --json mutation.json

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache mutation.json build dist *.egg-info
