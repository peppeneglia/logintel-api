#!/usr/bin/env bash
#
# Logintel API — cURL examples for all endpoints.
#
# Usage: BASE_URL=https://your-deployment API_KEY=... ./curl_examples.sh
# Requires: curl, python3 (for pretty-printing)

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
API_KEY="${API_KEY:-YOUR_API_KEY}"

# Departure tomorrow at the current hour (UTC). Works with GNU and BSD date.
DEPARTURE=$(date -u -d '+1 day' '+%Y-%m-%dT%H:00:00Z' 2>/dev/null || date -u -v+1d '+%Y-%m-%dT%H:00:00Z')

# ──────────────────────────────────────────────
# 1. Health check (no auth required)
# ──────────────────────────────────────────────
echo "=== Health Check ==="
curl -s "${BASE_URL}/v1/health" | python3 -m json.tool

# ──────────────────────────────────────────────
# 2. Create a prediction (Milan -> Rome)
# ──────────────────────────────────────────────
echo -e "\n=== Create Prediction ==="
PREDICTION=$(curl -s -X POST "${BASE_URL}/v1/predictions" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${API_KEY}" \
  -d "{
    \"origin\": {\"lat\": 45.4642, \"lon\": 9.1900},
    \"destination\": {\"lat\": 41.9028, \"lon\": 12.4964},
    \"departure_time\": \"${DEPARTURE}\",
    \"include_alternatives\": true
  }")
echo "${PREDICTION}" | python3 -m json.tool

PREDICTION_ID=$(echo "${PREDICTION}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')

# ──────────────────────────────────────────────
# 3. Get a prediction by ID
# ──────────────────────────────────────────────
echo -e "\n=== Get Prediction ==="
curl -s "${BASE_URL}/v1/predictions/${PREDICTION_ID}" \
  -H "X-API-Key: ${API_KEY}" | python3 -m json.tool

# ──────────────────────────────────────────────
# 4. List predictions (paginated)
# ──────────────────────────────────────────────
echo -e "\n=== List Predictions (page 1, 10 per page) ==="
curl -s "${BASE_URL}/v1/predictions?page=1&per_page=10" \
  -H "X-API-Key: ${API_KEY}" | python3 -m json.tool

# ──────────────────────────────────────────────
# 5. Submit feedback
# ──────────────────────────────────────────────
echo -e "\n=== Submit Feedback ==="
curl -s -X POST "${BASE_URL}/v1/predictions/${PREDICTION_ID}/feedback" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${API_KEY}" \
  -d '{
    "actual_delay_minutes": 35,
    "notes": "Heavy rain near Florence caused slowdown"
  }' | python3 -m json.tool

# ──────────────────────────────────────────────
# 6. Get accuracy analytics
# ──────────────────────────────────────────────
echo -e "\n=== Accuracy Analytics ==="
curl -s "${BASE_URL}/v1/analytics/accuracy" \
  -H "X-API-Key: ${API_KEY}" | python3 -m json.tool

# ──────────────────────────────────────────────
# 7. Using a Bearer token (Supabase JWT) instead of an API key
# ──────────────────────────────────────────────
# curl -s "${BASE_URL}/v1/predictions" -H "Authorization: Bearer ${JWT_TOKEN}"
