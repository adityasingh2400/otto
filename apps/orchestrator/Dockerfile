# LineForge orchestrator + dashboard. Build from the REPO ROOT (needs packages/spec):
#   docker build -f apps/orchestrator/Dockerfile -t lineforge-orchestrator .
#   docker run -p 8000:8000 --env-file .env lineforge-orchestrator
FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
# shared spec package (path dependency), the orchestrator, and the dashboard it serves
COPY packages/spec /app/packages/spec
COPY apps/orchestrator /app/apps/orchestrator
COPY apps/web /app/apps/web

WORKDIR /app/apps/orchestrator
RUN uv sync --python 3.12

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uv", "run", "--python", "3.12", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
