# Logintel API - Project Context

## What is this
Logintel API predicts weather-related delays for road freight transport along specific routes and suggests alternatives. It uses Smart Heuristics (not ML) with a calibration system that improves over time with user feedback.

## Stack (all free tier)
- **Framework:** Python 3.11+ / FastAPI
- **Database + Auth:** Supabase (PostgreSQL, JWT, API Keys)
- **Cache:** Upstash Redis (10k commands/day)
- **Hosting:** Railway (500 hours/month)
- **Weather data:** Open-Meteo (free, no rate limit)
- **Routing:** OpenRouteService (2,000 req/day - MAIN CONSTRAINT)
- **Elevation:** Open-Elevation (free)
- **Road type detection:** OpenStreetMap Overpass API (free)

## Critical constraint
OpenRouteService has a 2,000 req/day free limit. Every routing call must be cached aggressively (TTL 24h). Route alternatives should only be calculated when predicted delay exceeds a threshold. Use route hashing for nearby coordinates.

## How the prediction engine works
1. Receive origin, destination, departure_time
2. Calculate route via OpenRouteService
3. Sample points every 50km along the route
4. For each point, estimate arrival time based on departure + cumulative travel time
5. Fetch weather forecast at each point for its estimated arrival time (Open-Meteo)
6. Fetch elevation for each point
7. Apply heuristics: delay = base_impact x severity x F_road x F_altitude x F_time x C_calibration
8. Calculate confidence score (weighted: 40% time horizon, 30% weather stability, 20% historical accuracy, 10% data completeness)
9. Sum delays across all segments
10. Return prediction with total delay, per-segment breakdown, and confidence

## Weather impact rules (base delay per 100km)
- Rain: light 3min, moderate 8min, heavy 15min, very heavy 25min
- Snow: light 10min, moderate 25min, heavy 45min, very heavy 70min
- Wind: strong 6min, very strong 12min, storm 25min
- Fog: light 8min, moderate 18min, dense 35min

## Context multipliers
- Road type: highway 0.8, state road 1.0, provincial 1.3, mountain 1.8
- Altitude: 0-300m 1.0, 300-600m 1.1, 600-1000m 1.3, 1000-1500m 1.6, >1500m 2.0
- Time: night 0.7, rush hour 1.4, weekend 0.85, summer exodus 1.5

## Project structure
```
app/
  main.py              - FastAPI entry point
  config.py            - Settings from env vars
  auth.py              - Supabase JWT + API key authentication
  errors.py            - Centralized error handling and JSON error responses
  middleware.py         - Request/response middleware
  rate_limit.py        - Rate limiting logic
  metrics.py           - Application metrics collection
  alerting.py          - Alerting system for anomalies/failures
  circuit_breaker.py   - Circuit breaker for external API resilience
  logging_config.py    - Structured logging configuration
  routes/
    predictions.py     - POST/GET /v1/predictions, feedback
    analytics.py       - GET /v1/analytics/accuracy
    health.py          - GET /v1/health
  services/
    ors.py             - OpenRouteService routing integration
    weather.py         - Open-Meteo weather data
    elevation.py       - Open-Elevation altitude data
    overpass.py         - OpenStreetMap Overpass for road type detection
    cache.py           - Redis (Upstash) caching layer
    http_client.py     - Shared async httpx client
    prediction.py      - Prediction orchestration service
    supabase.py        - Supabase client wrapper
  engine/
    heuristics.py      - Weather impact & delay calculation
    confidence.py      - Confidence score computation
    sampler.py         - Route point sampling (every 50km)
    calibration.py     - Calibration coefficient management
    special_elements.py - Road-specific elements (tunnels, passes, etc.)
  models/
    schemas.py         - Pydantic request/response schemas
  stores/
    prediction_store.py  - Prediction persistence (Supabase)
    calibration_store.py - Calibration data persistence
    supabase_store.py    - Base Supabase store
    memory.py            - In-memory fallback store
docs/                - FRD and documentation
tests/               - Test files
supabase/
  migrations/
    001_initial_schema.sql - DB schema: predictions, feedback, calibration_versions
```

## Database schema (Supabase PostgreSQL)
- **predictions** — `id` (TEXT PK), `organization_id`, `data` (JSONB), `total_delay_minutes`, `departure_time`, `created_at`
- **feedback** — `id` (BIGINT auto), `prediction_id` (FK → predictions, UNIQUE), `organization_id`, `actual_delay_minutes`, `predicted_delay_minutes`, `deviation_minutes`, `received_at`
- **calibration_versions** — `version` (BIGINT auto PK), `coefficients` (JSONB), `feedback_count`, `created_at`
- RLS enabled on all tables. Migration in `supabase/migrations/001_initial_schema.sql`.

## API endpoints (v1)
- POST /v1/predictions - Create a new prediction
- GET /v1/predictions/{id} - Retrieve a prediction
- GET /v1/predictions - List predictions
- POST /v1/predictions/{id}/feedback - Submit actual delay feedback
- GET /v1/analytics/accuracy - Accuracy metrics
- GET /v1/health - Service health check

## Development rules
- Zero cost: only use free APIs and free tiers
- Cache everything: routes (24h), weather (1h)
- All responses follow consistent JSON error format
- Dates in ISO 8601 with timezone
- Code in English, comments in English
- Type hints everywhere
- Async where possible (httpx for external calls)

## Reference
Full requirements in docs/FRD.md
