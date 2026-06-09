FROM python:3.12-slim

WORKDIR /app

# 建置情境是 server/（見 docker-compose 的 build.context），所以路徑相對 server/。
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/app.py api/db.py ./
COPY db/schema.sql ./schema.sql
# taxonomy.yaml 在 repo 根（server/ 之外），由 compose 以 volume 掛進來。

ENV FOODPRINT_DATA=/data \
    SCHEMA_PATH=/app/schema.sql \
    TAXONOMY_PATH=/app/taxonomy.yaml

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
