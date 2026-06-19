FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini .
EXPOSE 8000
# readiness/liveness used by blue-green health gating
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/healthz')"
CMD ["sh","-c","python scripts/bootstrap_db.py && python -m app.seed && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2"]
