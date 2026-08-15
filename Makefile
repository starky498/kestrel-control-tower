PYTHON ?= python3
VENV := .venv
BIN := $(VENV)/bin
RUN_ENV := PYTHONPATH=$(CURDIR)/src

.PHONY: setup start prepare doctor validate-data build sync-freight sync-context scrape-prices run market-api test lint audit benchmark quality clean

setup:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/python -m pip install --upgrade pip
	$(BIN)/pip install -r requirements-dev.lock
	$(BIN)/pip install --no-deps --editable .

start: setup doctor validate-data build scrape-prices run

prepare: doctor validate-data build scrape-prices

doctor:
	$(RUN_ENV) $(BIN)/kestrel doctor

validate-data:
	$(RUN_ENV) $(BIN)/kestrel validate-data

build:
	$(RUN_ENV) $(BIN)/kestrel build

sync-freight:
	$(RUN_ENV) $(BIN)/kestrel sync-freight

sync-context:
	$(RUN_ENV) $(BIN)/kestrel sync-context

scrape-prices:
	$(RUN_ENV) $(BIN)/kestrel scrape-prices

run:
	$(RUN_ENV) $(BIN)/streamlit run app.py

market-api:
	$(RUN_ENV) $(BIN)/uvicorn kestrel.market_api:app --reload

test:
	$(RUN_ENV) $(BIN)/pytest

lint:
	$(RUN_ENV) $(BIN)/ruff check .
	$(RUN_ENV) $(BIN)/mypy src

audit:
	$(RUN_ENV) $(BIN)/python scripts/audit_ui.py

benchmark:
	$(RUN_ENV) $(BIN)/python scripts/benchmark_qa.py

quality: lint test audit benchmark

clean:
	$(RUN_ENV) $(BIN)/kestrel clean-generated
