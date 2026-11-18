.PHONY: install fixtures run audit test lint

PYTHON ?= python

install:
	$(PYTHON) -m pip install -e ".[dev]"

fixtures:
	$(PYTHON) scripts/generate_synthetic_fixtures.py

run:
	$(PYTHON) -m compliance_engine.cli run

audit:
	$(PYTHON) scripts/audit_confidentiality.py

test:
	$(PYTHON) -m pytest -v

lint:
	ruff check src tests scripts
