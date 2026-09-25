PYTHON ?= python3
DEV_PYTHON ?= .venv/bin/python

.PHONY: bootstrap verify verify-fast test format lint-fix
bootstrap:
	$(PYTHON) -m venv .venv
	.venv/bin/python -m pip install -r requirements-dev.txt
	.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
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
