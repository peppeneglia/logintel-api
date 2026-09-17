# FAQ & Troubleshooting

## General

### What does Logintel API predict?
The extra travel time caused by **weather** — rain, snow, wind and fog —
along a specific road route, on top of the normal driving time. It does not
model traffic, accidents or road works.

### How is the delay calculated?
1. The route is computed with OpenRouteService (heavy-goods-vehicle profile).
2. Points are sampled every 50 km along it, each with an estimated arrival time.
3. The hourly forecast at each point and hour is fetched from Open-Meteo.
4. Each weather condition maps to a base delay per 100 km, then multiplied by
   road type, altitude, time of day and a calibration coefficient.
5. Tunnels, bridges, mountain passes and urban centres from OpenStreetMap
   adjust the multipliers.
6. Segment delays are summed.

The README describes every table and formula.

### How far ahead can I predict?
Up to **72 hours** from now (`MAX_FORECAST_HOURS`). Requests with a later
`departure_time` are rejected with `400`.

### What geographic area is covered?
Anywhere OpenRouteService can route and Open-Meteo has forecasts — in
practice most of the world, with the heuristics tuned on European roads.

### Does it use machine learning?
No. Predictions come from transparent heuristics whose coefficients are
calibrated over time from the feedback you submit.

## Authentication

### API key or Bearer token?
- **API key** (`X-API-Key`): long-lived, best for server-to-server integrations.
- **Bearer token** (`Authorization: Bearer`): a Supabase access token, best for
  apps where users sign in.

Both give the same access. Keys are stored as SHA-256 hashes; if you lose a
key it cannot be recovered, only replaced.

### I get `401 UNAUTHORIZED`
- The header is missing, or the key/token contains extra spaces or newlines.
- The Supabase token has expired, or its `app_metadata` has no `org_id`.
- The API key was deactivated (`is_active = false`).

## Predictions

### What do the confidence levels mean?

| Level | Score | Reading |
|---|---|---|
| `high` | 85–100 | Near-term departure, stable weather, complete data |
| `good` | 70–84 | Reliable; some uncertainty in the forecast |
| `moderate` | 55–69 | Use with caution; longer horizon or unstable weather |
| `low` | < 55 | Distant departure and/or highly variable conditions |

The score weights the time horizon (40%), weather stability across segments
(30%), the historical accuracy of past predictions (20%) and the share of
segments with forecast data (10%).

### When do I get alternative routes?
Only when **both** hold: `include_alternatives` is `true` in the request and
the predicted delay on the main route exceeds the configured threshold
(15 minutes by default). Up to 2 alternatives are returned, each with the
minutes it saves versus the main route.

### Why is `data_completeness` below 100?
The weather provider had no forecast for one or more sample points at their
arrival hour. Those segments are treated as clear weather, and the confidence
score is lowered accordingly.

## Feedback and calibration

### Why submit feedback?
Feedback is the only input of the calibration system. When at least 20
entries spanning 14 days and 3 distinct weather conditions exist, the
coefficients are re-estimated and a new calibration version is created.
Calibration is global: every organization's feedback improves the model for
everyone.

### What are the feedback rules?
- Within **7 days** of the prediction's departure time.
- **One** entry per prediction.
- `actual_delay_minutes` between -60 and 1440 (negative = faster than planned).

## Troubleshooting

### `502 SERVICE_UNAVAILABLE`
An upstream service (routing or weather) is down. After 5 consecutive
failures the circuit breaker opens and the API fails fast for 30 seconds
before probing again. Retry after 30–60 s; `GET /v1/health` shows the state
of each breaker (`cb:ors`, `cb:open_meteo`, …).

### `429 RATE_LIMIT_EXCEEDED`
You exceeded your organization's hourly limit on `POST` requests. Wait for the
number of seconds in the `Retry-After` header before retrying.

### The first request is slow
Routes are cached for 24 h, weather for 1 h and OpenStreetMap data for 7 days.
A cold route needs 3–4 upstream calls (routing, elevation, OpenStreetMap,
weather) and typically takes a few seconds; repeated or nearby routes are
served from cache.

### `/v1/health` reports `degraded`
Redis or Supabase is unreachable, or a circuit breaker is open. The
`dependencies` object says which. The API keeps working without Redis (no
cache) but every request will hit the upstream services.
