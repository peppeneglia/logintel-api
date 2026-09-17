# API Guide

Everything you need to integrate Logintel API. The interactive OpenAPI
reference is served by the running application at `/docs` (Swagger UI) and
`/redoc`.

All request and response bodies are JSON (UTF-8). Dates are ISO 8601 with a
timezone offset. The base URL below is the local development server; replace
it with your deployment URL.

```
http://localhost:8000
```

## Authentication

Every endpoint except `GET /v1/health` requires one of:

| Method | Header | Notes |
|---|---|---|
| API key | `X-API-Key: <key>` | Long-lived, for server-to-server integrations. Only the SHA-256 hash of the key is stored (`api_keys` table). |
| Bearer token | `Authorization: Bearer <JWT>` | Supabase access token (`aud: authenticated`) whose `app_metadata.org_id` identifies the organization. |

Both resolve to an **organization**, which scopes every prediction and
feedback entry. When Supabase is not configured (local development) the API
skips authentication and uses a built-in `dev` organization.

## Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/predictions` | Create a delay prediction for a route |
| `GET` | `/v1/predictions/{id}` | Retrieve a prediction |
| `GET` | `/v1/predictions` | List predictions (paginated) |
| `POST` | `/v1/predictions/{id}/feedback` | Report the delay actually observed |
| `GET` | `/v1/analytics/accuracy` | Accuracy metrics for your organization |
| `GET` | `/v1/health` | Service and dependency health (public) |

### Create a prediction

`POST /v1/predictions`

```json
{
  "origin": {"lat": 45.4642, "lon": 9.1900},
  "destination": {"lat": 41.9028, "lon": 12.4964},
  "departure_time": "2026-09-18T08:00:00+02:00",
  "include_alternatives": true
}
```

| Field | Type | Rules |
|---|---|---|
| `origin`, `destination` | object | `lat` in [-90, 90], `lon` in [-180, 180] |
| `departure_time` | string | ISO 8601 **with timezone**, at most 72 hours in the future |
| `include_alternatives` | boolean | Default `false`. Alternatives are computed only when the main-route delay exceeds the threshold (15 min by default). |

Response `201 Created`:

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "completed",
  "origin": {"lat": 45.4642, "lon": 9.19},
  "destination": {"lat": 41.9028, "lon": 12.4964},
  "departure_time": "2026-09-18T08:00:00+02:00",
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
  "segments": [
    {
      "index": 0,
      "start_point": {"lat": 45.4642, "lon": 9.19},
      "end_point": {"lat": 45.0, "lon": 9.8},
      "length_km": 50.0,
      "estimated_arrival": "2026-09-18T08:40:00+02:00",
      "weather": [
        {"type": "rain", "severity": "moderate", "raw_value": 4.5,
         "description": "Moderate rain, aquaplaning possible"}
      ],
      "factors": {
        "road_type": "highway",
        "road_factor": 0.8,
        "altitude_m": 120.0,
        "altitude_factor": 1.0,
        "time_factor": 1.4,
        "calibration_factor": 1.0,
        "special_elements": []
      },
      "delay_minutes": 4.48
    }
  ],
  "alternatives": [
    {
      "route_index": 1,
      "total_delay_minutes": 12.0,
      "duration_minutes": 340.0,
      "distance_km": 560.2,
      "delay_savings_minutes": 16.5,
      "summary": "Alternative 1: 560.2 km, 340 min base travel, saves 16 min delay"
    }
  ],
  "created_at": "2026-09-17T07:00:00Z"
}
```

Reading the response:

| Field | Meaning |
|---|---|
| `total_delay_minutes` | Weather-related delay expected over the whole route, on top of the normal travel time |
| `confidence.overall` / `level` | 0–100 reliability score: `high` ≥ 85, `good` ≥ 70, `moderate` ≥ 55, otherwise `low` |
| `segments[]` | One entry per ~50 km stretch: weather at the estimated arrival time, the multipliers applied and the resulting delay |
| `segments[].factors.special_elements` | Tunnels, bridges, mountain passes and urban centres found on the segment, with the multiplier each contributed |
| `alternatives[]` | Up to 2 alternative routes with their own delay and the minutes saved versus the main route (empty when not requested or below threshold) |

### Retrieve and list predictions

`GET /v1/predictions/{id}` returns the full prediction document above.

`GET /v1/predictions?page=1&per_page=20` returns summaries, newest first
(`per_page` ≤ 100):

```json
{
  "predictions": [
    {
      "id": "a1b2c3d4-…",
      "origin": {"lat": 45.4642, "lon": 9.19},
      "destination": {"lat": 41.9028, "lon": 12.4964},
      "departure_time": "2026-09-18T08:00:00+02:00",
      "total_delay_minutes": 28.5,
      "confidence_level": "good",
      "created_at": "2026-09-17T07:00:00Z"
    }
  ],
  "total": 1,
  "page": 1,
  "per_page": 20
}
```

### Submit feedback

`POST /v1/predictions/{id}/feedback`

```json
{"actual_delay_minutes": 35, "notes": "Heavy rain near Florence"}
```

| Rule | Value |
|---|---|
| `actual_delay_minutes` | Integer in [-60, 1440]; negative means the trip was faster than planned |
| `notes` | Optional, ≤ 500 characters |
| Window | Within 7 days of the prediction's `departure_time` |
| Cardinality | One feedback entry per prediction |

Response `201 Created`:

```json
{
  "prediction_id": "a1b2c3d4-…",
  "actual_delay_minutes": 35,
  "predicted_delay_minutes": 28.5,
  "deviation_minutes": 6.5,
  "notes": "Heavy rain near Florence",
  "received_at": "2026-09-19T18:30:00Z"
}
```

Feedback feeds the calibration system: once at least 20 entries spanning 14
days and 3 distinct weather conditions exist, a new calibration version is
computed automatically (see the README for the formula).

### Accuracy analytics

`GET /v1/analytics/accuracy`

```json
{
  "total_predictions": 150,
  "total_feedback": 42,
  "feedback_rate": 28.0,
  "mae": 8.3,
  "within_10min_pct": 71.4,
  "within_20min_pct": 90.5,
  "calibration_version": 3,
  "breakdown_by_weather": [
    {"weather_type": "rain", "count": 18, "mae": 6.2, "within_10min_pct": 77.8, "within_20min_pct": 94.4}
  ]
}
```

`mae` is the mean absolute error in minutes between predicted and actual
delays. The breakdown groups each prediction by its dominant weather
condition.

### Health

`GET /v1/health` — no authentication.

```json
{
  "status": "healthy",
  "version": "0.1.0",
  "environment": "production",
  "dependencies": {
    "redis": {"status": "healthy"},
    "supabase": {"status": "healthy"},
    "cb:ors": {"name": "ors", "state": "CLOSED", "consecutive_failures": 0, "time_in_state_seconds": 3600.0}
  },
  "metrics": {"uptime_seconds": 3600.0, "total_requests": 120, "error_rate_pct": 0.0, "latency_p95_ms": 840.0, "cache_hit_rate_pct": 72.5},
  "alerts": []
}
```

`status` is `degraded` when Redis or Supabase is unhealthy or any upstream
circuit breaker is open.

## Errors

Every error has the same shape:

```json
{
  "error": {
    "code": "INVALID_REQUEST",
    "message": "Validation error",
    "details": [
      {"field": "body -> departure_time", "message": "Value error, departure_time must include timezone info"}
    ]
  }
}
```

| HTTP | `code` | When |
|---|---|---|
| 400 | `INVALID_REQUEST` | Malformed body, invalid values, departure beyond the forecast horizon, feedback window expired or already submitted |
| 401 | `UNAUTHORIZED` | Missing, invalid or expired credentials |
| 404 | `NOT_FOUND` | Unknown prediction id (or one that belongs to another organization) |
| 429 | `RATE_LIMIT_EXCEEDED` | Hourly limit reached — wait for the seconds in the `Retry-After` header |
| 502 | `SERVICE_UNAVAILABLE` | An upstream service (routing, weather) is down or its circuit breaker is open — retry after 30–60 s |
| 500 | `INTERNAL_ERROR` | Unexpected failure; the `X-Request-ID` response header identifies the request in the logs |

## Rate limiting

`POST` requests are counted per organization in a sliding one-hour window
(`rate_limit_hour` column of the `organizations` table, 100 by default).
`GET` requests are not limited. Exceeding the limit returns `429` with a
`Retry-After` header.

## Request tracing

Send an `X-Request-ID` header (up to 128 characters, `[A-Za-z0-9._:-]`) to
correlate a call with your own logs; otherwise one is generated. The id is
always echoed back in the response.
