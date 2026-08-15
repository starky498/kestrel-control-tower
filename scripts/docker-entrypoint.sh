#!/bin/sh
set -eu

if [ ! -f "${KESTREL_ANALYTICS_DB:-/app/.kestrel/kestrel.duckdb}" ]; then
  kestrel doctor
  kestrel validate-data
  kestrel build
  kestrel scrape-prices
fi

exec streamlit run app.py --server.address=0.0.0.0 --server.port=8501
