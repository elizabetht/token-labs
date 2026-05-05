FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY incident_harness/ incident_harness/

RUN pip install --no-cache-dir -e .

EXPOSE 9000

CMD ["uvicorn", "incident_harness.server:app", "--host", "0.0.0.0", "--port", "9000"]
