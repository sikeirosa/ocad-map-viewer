#!/usr/bin/env bash
# Applies the Artifact Registry cleanup policy (policy-as-code).
# Keeps the 5 most recent image versions, deletes versions older than 30 days,
# and untagged versions older than 7 days. Idempotent — safe to re-run.
#
# Required IAM for the caller (e.g. the CI service account):
#   roles/artifactregistry.admin (or artifactregistry.repositories.update) on the repo.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-ocad-map-viewer}"
REGION="${REGION:-europe-west1}"
REPO="${AR_REPO:-ocad-map-viewer}"
POLICY_FILE="$(dirname "$0")/artifact-registry-cleanup-policy.json"

echo "Applying AR cleanup policy to ${REGION}/${REPO} (project ${PROJECT_ID})..."
gcloud artifacts repositories set-cleanup-policies "${REPO}" \
  --project="${PROJECT_ID}" \
  --location="${REGION}" \
  --policy="${POLICY_FILE}" \
  --no-dry-run
echo "Done."
