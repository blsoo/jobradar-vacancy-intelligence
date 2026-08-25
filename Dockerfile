FROM python:3.12-slim

WORKDIR /app

COPY src /app/src

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    JOBRADAR_DB=/data/jobradar.db

RUN mkdir -p /data

VOLUME ["/data"]

CMD ["python", "-m", "jobradar.app", "run"]
