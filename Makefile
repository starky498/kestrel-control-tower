PYTHON ?= python3
VENV := .venv
BIN := $(VENV)/bin
RUN_ENV := PYTHONPATH=$(CURDIR)/src

.PHONY: setup start prepare doctor validate-data build sync-freight scrape-prices run test lint clean

setup:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/python -m pip install --upgrade pip
	$(BIN)/pip install ".[dev]"

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

scrape-prices:
	$(RUN_ENV) $(BIN)/kestrel scrape-prices

run:
	$(RUN_ENV) $(BIN)/streamlit run app.py

test:
	$(RUN_ENV) $(BIN)/pytest

lint:
	$(RUN_ENV) $(BIN)/ruff check .
	$(RUN_ENV) $(BIN)/mypy src

clean:
	$(RUN_ENV) $(BIN)/kestrel clean-generated
