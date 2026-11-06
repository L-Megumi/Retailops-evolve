FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md requirements-service.lock ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements-service.lock '.[service]' && useradd --uid 10001 --create-home retailops
COPY --chown=10001:10001 data ./data
COPY --chown=10001:10001 plugins ./plugins
RUN mkdir /app/state && chown 10001:10001 /app/state
USER 10001:10001
EXPOSE 8000
CMD ["retailops", "serve", "--host", "0.0.0.0", "--state", "/app/state/tasks.sqlite3"]
