# Pipecat voice agent — ARM64 (Graviton) image for AWS Bedrock AgentCore Runtime.
# AgentCore Runtime requires linux/arm64 containers and auto-scales 0 -> thousands of
# isolated microVM sessions (you pay for active reasoning, not I/O wait).
#
# Build from the REPO ROOT (needs packages/spec):
#   docker build --platform linux/arm64 -f apps/agent/Dockerfile -t lineforge-agent .
# Deploy: see scripts/deploy-agentcore.sh and docs/TECH.md §AWS.
FROM --platform=linux/arm64 python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv

WORKDIR /app
COPY packages/spec /app/packages/spec
COPY apps/agent /app/apps/agent

WORKDIR /app/apps/agent
RUN uv sync --python 3.12

ENV PYTHONUNBUFFERED=1
EXPOSE 8080
# TODO(team): align the entry with the Pipecat + AgentCore contract (see docs/TECH.md §AWS).
# This serves the explicit Twilio Media Streams server; the runner pattern also works.
CMD ["uv", "run", "--python", "3.12", "uvicorn", "twilio_server:app", "--host", "0.0.0.0", "--port", "8080"]
