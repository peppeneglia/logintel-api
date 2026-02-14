# Logintel API — FAQ & Troubleshooting

## General

### What is Logintel API?
Logintel API predicts weather-related delays for road freight transport. Given an origin, destination, and departure time, it estimates per-segment delays based on weather forecasts, road type, altitude, and time of day.

### How accurate are the predictions?
Accuracy depends on the forecast time horizon. Predictions within 6 hours of departure typically achieve 85%+ confidence. Predictions 48+ hours out have lower confidence. The system improves over time through user feedback and automatic calibration.

### What geographic area is covered?
Logintel API covers any road routable by OpenRouteService, with weather data from Open-Meteo. In practice, this means most of Europe and many other regions worldwide. Weather forecast quality is best in Europe.

### How far ahead can I predict?
The weather forecast horizon is up to 72 hours. Predictions beyond 48 hours have reduced confidence (typically below 55%).

---

## Authentication

### How do I get an API key?
Contact support@logintel.io to receive your organization credentials. You will get either a JWT token or an API key depending on your integration preference.

### What's the difference between JWT and API Key?
- **JWT (Bearer token)**: Short-lived, obtained through Supabase auth. Best for web/mobile apps with user sessions.
- **API Key (X-API-Key)**: Long-lived, best for server-to-server integrations and scripts.

Both provide the same access level. Use whichever fits your architecture.

### I'm getting a 401 Unauthorized error
Check that:
1. Your `Authorization: Bearer <token>` or `X-API-Key: <key>` header is present.
2. The token/key is valid and not expired (JWTs expire).
3. There are no extra spaces or newlines in the header value.

---

## API Usage

### What are the rate limits?
| Tier | Requests/hour | Predictions/month |
|---|---|---|
| Free | 50 | 500 |
| Starter | 200 | 5,000 |
| Professional | 1,000 | 50,000 |
| Enterprise | Custom | Custom |

Rate limits apply to `POST /v1/predictions` and `POST .../feedback`. Read operations (`GET`) are not rate-limited.

### I'm getting a 429 Too Many Requests error
You've exceeded your tier's rate limit. The response includes a `Retry-After` header indicating how many seconds to wait. Implement exponential backoff:

```python
import time

retry_after = int(response.headers.get("Retry-After", 60))
time.sleep(retry_after)
# then retry the request
```

### What date format should I use?
All dates must be in **ISO 8601 with timezone**. Examples:
- `2026-02-15T08:00:00+01:00` (CET)
- `2026-02-15T07:00:00Z` (UTC)

A `departure_time` without timezone info will be rejected with a 400 error.

### What coordinate format is expected?
Coordinates use decimal degrees: `lat` (-90 to 90), `lon` (-180 to 180).

Example for Milan: `{"lat": 45.4642, "lon": 9.1900}`

---

## Predictions

### How is the delay calculated?
1. The route is calculated via OpenRouteService (heavy goods vehicle profile).
2. Points are sampled every 50 km along the route.
3. For each point, the weather forecast at the estimated arrival time is fetched.
4. A heuristic formula computes the delay: `base_impact × road_factor × altitude_factor × time_factor × calibration_factor`.
5. Special route elements (tunnels, bridges, mountain passes) modify the multipliers.
6. Delays across all segments are summed.

### What are segments?
The route is divided into ~50 km segments. Each segment has its own weather conditions, road type, altitude, and delay contribution. This lets you identify exactly where on the route the delay is expected.

### What do the confidence levels mean?
| Level | Range | Meaning |
|---|---|---|
| `high` | 85–100% | Very reliable — near-term forecast, stable weather, good historical data. |
| `good` | 70–84% | Reliable — some uncertainty in weather or moderate time horizon. |
| `moderate` | 55–69% | Use with caution — longer time horizon or unstable weather patterns. |
| `low` | < 55% | Low reliability — distant forecast, high weather variability. |

Confidence is computed from 4 components (weighted):
- **Time horizon (40%)**: shorter = higher confidence.
- **Weather stability (30%)**: stable conditions across segments = higher.
- **Historical accuracy (20%)**: based on past prediction accuracy from feedback.
- **Data completeness (10%)**: % of sample points with weather data available.

### When are alternative routes suggested?
Alternatives appear only when **both** conditions are met:
1. `include_alternatives` is set to `true` in the request.
2. The main route's predicted delay exceeds 20 minutes.

Up to 2 alternatives are returned, each with delay savings compared to the main route.

---

## Feedback

### Why should I submit feedback?
Feedback is the input for the calibration system. After enough feedback (20+ entries over 14+ days), the system automatically adjusts its prediction coefficients. More feedback = more accurate predictions for your organization.

### What is the feedback window?
Feedback must be submitted within **7 days** of the prediction's departure time. After that, the feedback endpoint returns a 400 error.

### Can I submit feedback more than once?
No. Only one feedback entry is allowed per prediction. Attempting a second submission returns a 400 error with the message "Feedback already submitted for this prediction".

### What values can actual_delay_minutes take?
- **Range**: -60 to 1440 minutes.
- **Negative values**: the trip was faster than expected (e.g., -10 means 10 minutes early).
- **Zero**: no delay at all.
- **Positive**: actual delay in minutes.

---

## Troubleshooting

### I'm getting a 502 Service Unavailable error
An upstream service (routing, weather, or elevation) is temporarily down. The API uses circuit breakers — after multiple failures, it will fail fast to avoid long waits.

**What to do**: Wait 30–60 seconds and retry. If the issue persists for more than 5 minutes, check `/v1/health` for dependency status.

### My requests are timing out
Large routes (1000+ km) require more weather API calls and may take 5–10 seconds. This is normal. If requests consistently take more than 15 seconds, check `/v1/health` for degraded dependencies.

### The response seems slow for the first request
First requests may be slower due to cache misses. Routes are cached for 24 hours and weather data for 1 hour. Subsequent requests for similar routes will be significantly faster.

### The health endpoint shows "degraded" status
A `degraded` status means either Redis (cache) is down or a circuit breaker is open for an upstream service. The API still functions but may be slower (cache misses) or unable to create predictions (if the routing service is down).

Check the `dependencies` object in the health response for details.
