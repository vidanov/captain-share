ifeq ($(OS),Windows_NT)
PYTHON ?= python
VENV_BIN := .venv/Scripts
else
PYTHON ?= python3
VENV_BIN := .venv/bin
endif
DEV_PYTHON ?= $(VENV_BIN)/python

.PHONY: bootstrap verify verify-fast test format lint-fix
bootstrap:
	$(PYTHON) -m venv .venv
	$(VENV_BIN)/python -m pip install -r requirements-dev.txt
	$(VENV_BIN)/python -m pip install --no-build-isolation --no-deps -e .
	npm ci --ignore-scripts --no-audit --no-fund

verify:
	$(DEV_PYTHON) scripts/verify.py

verify-fast:
	$(DEV_PYTHON) scripts/verify.py --fast

test:
	$(DEV_PYTHON) -m unittest discover -s tests -v

format:
	$(DEV_PYTHON) -m ruff format captain_share tests scripts
	npm run format

lint-fix:
	$(DEV_PYTHON) -m ruff check --fix captain_share tests scripts
