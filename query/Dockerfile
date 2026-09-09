FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

# values.yaml and data/ are deliberately excluded from the image. They are
# mounted at runtime so credentials and SQLite state outlive a container.
COPY query_service/ ./query_service/

RUN mkdir -p /app/data /app/screenshots

VOLUME ["/app/data"]
STOPSIGNAL SIGINT

CMD ["python", "-m", "query_service"]
