#!/usr/bin/env bash
set -euo pipefail

CODE_DIR="${CODE_DIR:-/home/azureuser/friska-ccm-prod/NouriqAi-Friska-CCM}"
COMPOSE_DIR="${COMPOSE_DIR:-/home/azureuser/friska-ccm-prod}"
IMAGE_REPO="nouriqfriskaccm.azurecr.io/friskaccm_mg"
MG_SERVICES=(mg mg-celery-worker mg-celery-beat)

CURRENT_TAG="$(grep -oP 'friskaccm_mg:\Kv\K[\d.]+' "${COMPOSE_DIR}/docker-compose.yml" | head -1)"

if [[ -z "${CURRENT_TAG}" ]]; then
  echo "Could not find friskaccm_mg image tag in ${COMPOSE_DIR}/docker-compose.yml"
  exit 1
fi

if [[ "${CURRENT_TAG}" =~ ^([0-9]+)\.([0-9]+)$ ]]; then
  NEW_TAG="v${BASH_REMATCH[1]}.$((BASH_REMATCH[2] + 1))"
elif [[ "${CURRENT_TAG}" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
  NEW_TAG="v${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.$((BASH_REMATCH[3] + 1))"
else
  echo "Unsupported tag format: v${CURRENT_TAG}"
  exit 1
fi

echo "Building ${IMAGE_REPO}:${NEW_TAG} (previous: v${CURRENT_TAG})"
cd "${CODE_DIR}"
docker build -t "${IMAGE_REPO}:${NEW_TAG}" .

sed -i "s|${IMAGE_REPO}:v${CURRENT_TAG}|${IMAGE_REPO}:${NEW_TAG}|g" "${COMPOSE_DIR}/docker-compose.yml"

cd "${COMPOSE_DIR}"
docker compose up -d --force-recreate "${MG_SERVICES[@]}"

echo "Deployment complete: ${IMAGE_REPO}:${NEW_TAG}"
docker compose ps "${MG_SERVICES[@]}"
