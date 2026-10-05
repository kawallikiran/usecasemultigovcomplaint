#!/bin/sh
# Start command for hosting platforms: listens on $PORT if set, otherwise 8501.
exec streamlit run app.py --server.address=0.0.0.0 --server.port="${PORT:-8501}" --server.headless=true
