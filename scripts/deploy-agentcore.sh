#!/usr/bin/env bash
# Deploy the LineForge Pipecat agent to AWS Bedrock AgentCore Runtime (Graviton/ARM64),
# which auto-scales 0 -> thousands of isolated microVM sessions, pay-per-active-reasoning.
#
# Prereqs: AWS CLI configured, an ECR repo, Docker. ⚠️ SET A BUDGET ALARM FIRST.
# This is the documented deploy path; confirm the AgentCore runtime API against the
# current AWS docs (docs/TECH.md §AWS links the official Pipecat + AgentCore guide).
set -euo pipefail
cd "$(dirname "$0")/.."

: "${AWS_REGION:=us-east-1}"
: "${ECR_REPO:?set ECR_REPO=<account>.dkr.ecr.${AWS_REGION}.amazonaws.com/lineforge-agent}"
TAG="${TAG:-latest}"

echo "▸ build ARM64 image (AgentCore requires linux/arm64)"
docker build --platform linux/arm64 -f apps/agent/Dockerfile -t "$ECR_REPO:$TAG" .

echo "▸ push to ECR"
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "${ECR_REPO%%/*}"
docker push "$ECR_REPO:$TAG"

echo "▸ create / update the AgentCore runtime"
echo "  TODO(team): point a Bedrock AgentCore runtime at $ECR_REPO:$TAG via the"
echo "  console or the 'aws bedrock-agentcore' CLI (confirm the exact command in the"
echo "  current AWS docs). Set memory/CPU; it scales to thousands of sessions on its own."
echo "✓ image ready: $ECR_REPO:$TAG"
