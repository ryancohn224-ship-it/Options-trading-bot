FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv && apt-get update && apt-get install -y --no-install-recommends cron tzdata && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
RUN uv pip install --system -e ".[dashboard]"
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENV TZ=America/New_York OTB_CONFIG=/app/config/default.yaml
VOLUME ["/app/warehouse", "/app/state"]
ENTRYPOINT ["/entrypoint.sh"]
CMD ["cron"]
