PYTHON ?= python3
VENV := .venv
BIN := $(VENV)/bin

.PHONY: setup doctor validate-data build sync-freight scrape-prices run test lint clean

setup:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/python -m pip install --upgrade pip
	$(BIN)/pip install -e ".[dev]"

doctor:
	$(BIN)/kestrel doctor

validate-data:
	$(BIN)/kestrel validate-data

build:
	$(BIN)/kestrel build

sync-freight:
	$(BIN)/kestrel sync-freight

scrape-prices:
	$(BIN)/kestrel scrape-prices

run:
	$(BIN)/streamlit run app.py

test:
	$(BIN)/pytest

lint:
	$(BIN)/ruff check .
	$(BIN)/mypy src

clean:
	$(BIN)/kestrel clean-generated
