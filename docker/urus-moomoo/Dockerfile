# syntax=docker/dockerfile:1

FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

RUN pip install --no-cache-dir \
    "exchange-calendars>=4.5,<5.0" \
    "futu-api>=10.8.6808,<11.0" \
    "pydantic>=2.6,<3.0"

COPY urus-moomoo/src ./src

ENTRYPOINT ["python", "-m", "urus_moomoo.cli"]
CMD ["schedule", "--config", "/data/deploy/collection_tasks.local.toml"]
