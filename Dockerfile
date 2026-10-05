# AIGP26 use case 8: citizen grievance desk - Streamlit UI. Offline: no outbound calls at runtime, no telemetry.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_FILE_WATCHER_TYPE=none

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pre-build the critic reports so the report tabs are filled the first time the UI opens.
RUN python -m grievdesk all || echo "Critic run failed at build time; use the Run button in the UI."

RUN useradd --create-home appuser && mkdir -p /app/reports/ui && chown -R appuser /app
USER appuser

EXPOSE 8501
HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://localhost:%s/_stcore/health' % os.environ.get('PORT', '8501'), timeout=4)"

# Listens on $PORT when the hosting platform sets it (ECS, App Runner, Render ...), otherwise 8501.
CMD ["sh", "-c", "exec streamlit run app.py --server.address=0.0.0.0 --server.port=${PORT:-8501} --server.headless=true"]
