#!/usr/bin/env bash
#
# Logintel API — cURL examples for all endpoints.
#
# Replace YOUR_API_KEY with your actual API key.
# Replace prediction IDs with real values from your responses.

BASE_URL="https://api.logintel.io"
API_KEY="YOUR_API_KEY"

# ──────────────────────────────────────────────
# 1. Health check (no auth required)
# ──────────────────────────────────────────────
echo "=== Health Check ==="
curl -s "${BASE_URL}/v1/health" | python3 -m json.tool

# ──────────────────────────────────────────────
# 2. Create a prediction (Milan -> Rome)
# ──────────────────────────────────────────────
echo -e "\n=== Create Prediction ==="
curl -s -X POST "${BASE_URL}/v1/predictions" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${API_KEY}" \
  -d '{
    "origin": {"lat": 45.4642, "lon": 9.1900},
    "destination": {"lat": 41.9028, "lon": 12.4964},
    "departure_time": "2026-02-15T08:00:00+01:00",
    "include_alternatives": true
  }' | python3 -m json.tool

# ──────────────────────────────────────────────
# 3. Get a prediction by ID
# ──────────────────────────────────────────────
PREDICTION_ID="a1b2c3d4-e5f6-7890-abcd-ef1234567890"  # replace with real ID

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
# 7. Using Bearer token instead of API key
# ──────────────────────────────────────────────
JWT_TOKEN="eyJhbGciOiJIUzI1NiIs..."  # replace with real JWT

echo -e "\n=== Create Prediction (Bearer auth) ==="
curl -s -X POST "${BASE_URL}/v1/predictions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${JWT_TOKEN}" \
  -d '{
    "origin": {"lat": 45.4642, "lon": 9.1900},
    "destination": {"lat": 41.9028, "lon": 12.4964},
    "departure_time": "2026-02-15T08:00:00+01:00",
    "include_alternatives": false
  }' | python3 -m json.tool
