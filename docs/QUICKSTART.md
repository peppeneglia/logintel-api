# Logintel API — Quick Start Guide

## What is Logintel API?

Logintel API predicts weather-related delays for road freight transport along specific routes. It samples weather forecasts at points every 50 km, applies smart heuristics (road type, altitude, time of day), and returns a per-segment delay breakdown with a confidence score. When delays are significant, it suggests alternative routes.

## Authentication

All endpoints except `/v1/health` require authentication. Two methods are supported:

**Bearer Token (JWT)**
```
Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
```

**API Key**
```
X-API-Key: lntl_a1b2c3d4e5f6g7h8...
```

Contact support@logintel.io to obtain your credentials.

## Your First Prediction

Create a prediction for a Milan → Rome route departing tomorrow at 8:00 AM:

```bash
curl -X POST https://api.logintel.io/v1/predictions \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{
    "origin": {"lat": 45.4642, "lon": 9.1900},
    "destination": {"lat": 41.9028, "lon": 12.4964},
    "departure_time": "2026-02-15T08:00:00+01:00",
    "include_alternatives": false
  }'
```

**Response (201 Created):**
```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "completed",
  "origin": {"lat": 45.4642, "lon": 9.19},
  "destination": {"lat": 41.9028, "lon": 12.4964},
  "departure_time": "2026-02-15T08:00:00+01:00",
  "total_delay_minutes": 28.5,
  "confidence": {
    "overall": 82.3,
    "level": "good",
    "components": {
      "time_horizon": 95.0,
      "weather_stability": 72.0,
      "historical_accuracy": 70.0,
      "data_completeness": 100.0
    }
  },
  "segments": [ ... ],
  "alternatives": [],
  "created_at": "2026-02-14T07:00:00Z"
}
```

## Understanding the Response

| Field | Description |
|---|---|
| `total_delay_minutes` | Estimated total weather-related delay in minutes for the entire route. |
| `confidence.overall` | Confidence score 0–100%. Higher = more reliable prediction. |
| `confidence.level` | Human-readable level: `high` (85-100%), `good` (70-84%), `moderate` (55-69%), `low` (<55%). |
| `segments` | Array of per-segment details, each with weather conditions, delay, and context factors. |
| `segments[].delay_minutes` | Delay contribution of this specific 50 km segment. |
| `segments[].weather` | Weather conditions at this segment (type, severity, raw value). |
| `segments[].factors` | Context multipliers applied: road type, altitude, time of day, calibration, special elements. |
| `alternatives` | Alternative routes with lower delay (only populated when `include_alternatives=true` and delay > 20 min). |

## Submitting Feedback

After the trip, submit the actual delay to improve future predictions:

```bash
curl -X POST https://api.logintel.io/v1/predictions/a1b2c3d4-e5f6-7890-abcd-ef1234567890/feedback \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{
    "actual_delay_minutes": 35,
    "notes": "Heavy rain near Florence caused slowdown"
  }'
```

**Response (201 Created):**
```json
{
  "prediction_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "actual_delay_minutes": 35,
  "predicted_delay_minutes": 28.5,
  "deviation_minutes": 6.5,
  "received_at": "2026-02-15T18:30:00Z"
}
```

The `deviation_minutes` shows how far off the prediction was. Feedback must be submitted within 7 days of the departure time. Only one feedback entry per prediction.

## Alternative Routes

To receive alternative route suggestions, set `include_alternatives` to `true`:

```bash
curl -X POST https://api.logintel.io/v1/predictions \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{
    "origin": {"lat": 45.4642, "lon": 9.1900},
    "destination": {"lat": 41.9028, "lon": 12.4964},
    "departure_time": "2026-02-15T08:00:00+01:00",
    "include_alternatives": true
  }'
```

Alternatives are only computed when the main route delay exceeds 20 minutes. Each alternative includes:
- `total_delay_minutes` — predicted delay for the alternative route
- `delay_savings_minutes` — how many minutes you save vs. the main route
- `distance_km` and `duration_minutes` — route length and base travel time
- `summary` — human-readable description

## Error Handling

All errors follow a consistent JSON format:

```json
{
  "error": {
    "code": "INVALID_REQUEST",
    "message": "departure_time must include timezone info",
    "details": [
      {"field": "departure_time", "message": "Value error, departure_time must include timezone info"}
    ]
  }
}
```

**Common error codes:**

| HTTP Status | Code | Description |
|---|---|---|
| 400 | `INVALID_REQUEST` | Malformed request body or invalid field values. |
| 401 | `UNAUTHORIZED` | Missing or invalid authentication credentials. |
| 404 | `NOT_FOUND` | Prediction ID does not exist. |
| 429 | `RATE_LIMIT_EXCEEDED` | Too many requests. Wait for the time indicated in `Retry-After` header. |
| 502 | `SERVICE_UNAVAILABLE` | Upstream service temporarily down. Retry after a few seconds. |

**Retry strategy for 429:** Read the `Retry-After` response header (seconds) and wait before retrying.

## Rate Limits

| Tier | Requests/hour | Predictions/month |
|---|---|---|
| Free | 50 | 500 |
| Starter | 200 | 5,000 |
| Professional | 1,000 | 50,000 |
| Enterprise | Custom | Custom |

## Next Steps

- **Interactive docs** — visit `/docs` (Swagger UI) to explore and test all endpoints.
- **Integration examples** — see `docs/examples/` for Python, JavaScript, cURL, and PHP code samples.
- **FAQ** — see `docs/FAQ.md` for common questions and troubleshooting.
