# Logintel API — Development Log

## Blocco 1: Fondamenta (2026-02-14)

### Obiettivo
Costruire lo scheletro funzionante dell'API: modelli dati, motore di predizione core, struttura errori e endpoint con logica stub.

### Cosa è stato implementato

#### 1. Modelli Pydantic (`app/models/schemas.py`)
- **Enums:** `WeatherType` (rain/snow/wind/fog), `Severity` (light → very_heavy), `RoadType` (highway → mountain), `ConfidenceLevel` (high/good/moderate/low)
- **Request:** `PredictionRequest` (origin, destination, departure_time con validazione timezone), `FeedbackRequest` (actual_delay_minutes [-60, 1440])
- **Response:** `PredictionResponse` (id, segments, confidence, total_delay), `PredictionSummary` (per liste), `FeedbackResponse` (con deviation calcolata)
- **Sub-modelli:** `Coordinate`, `WeatherCondition`, `SegmentFactors`, `SegmentDetail`, `ConfidenceScore`, `ConfidenceComponents`
- **Errori:** `ErrorResponse` → `ErrorBody` (code, message, details[])

#### 2. Prediction Engine (`app/engine/`)

**Heuristics (`heuristics.py`):**
- Tabelle impatto meteo dal FRD 6.2: pioggia (3-25 min/100km), neve (10-70), vento (6-25), nebbia (8-35)
- Classificazione automatica severità da valori raw (mm/h, cm/h, km/h, metri visibilità)
- Moltiplicatori contesto dal FRD 6.3: strada (0.8-1.8), altitudine (1.0-2.0), temporale (0.7-1.5)
- Formula: `delay = base_impact × F_road × F_altitude × F_time × C_calibration × (km/100)`
- Condizioni meteo multiple sullo stesso segmento si sommano

**Confidence (`confidence.py`):**
- Formula FRD 6.5: `confidence = (C_horizon × 0.40) + (C_stability × 0.30) + (C_historical × 0.20) + (C_data × 0.10)`
- Orizzonte: <6h→95%, 6-24h→80%, 24-48h→65%, >48h→50%
- Stabilità: basata su varianza valori meteo tra punti consecutivi
- Storico: default 70% (si calibrerà con i feedback, Blocco 4)
- Completezza: % punti con dati disponibili

**Sampler (`sampler.py`):**
- Campionamento punti ogni N km lungo una polyline (default 50km)
- Haversine per distanze reali sulla sfera terrestre
- Interpolazione lineare tra punti della polyline
- Stima tempi di arrivo proporzionale (con durata ORS o velocità media 70 km/h)

#### 3. Gestione errori (`app/errors.py`)
- Eccezioni custom: `InvalidRequestError` (400), `UnauthorizedError` (401), `NotFoundError` (404), `RateLimitError` (429), `ServiceUnavailableError` (502)
- Handler globale per `RequestValidationError` di FastAPI → formato JSON consistente
- Handler catch-all per eccezioni non gestite → 500 `INTERNAL_ERROR`
- Formato risposta errore: `{"error": {"code": "...", "message": "...", "details": [...]}}`

#### 4. Routes API (`app/routes/`)
- `POST /v1/predictions` — Crea predizione, genera segmenti, calcola delay e confidence
- `GET /v1/predictions/{id}` — Recupera per ID
- `GET /v1/predictions` — Lista con paginazione (page, per_page)
- `POST /v1/predictions/{id}/feedback` — Feedback con validazione (singolo per predizione)
- `GET /v1/health` — Health check
- Storage in-memory (dict Python) — sarà sostituito da Supabase

#### 5. Wiring (`app/main.py`)
- FastAPI app con router registrati e error handler globali
- Config aggiornata a Pydantic v2 `SettingsConfigDict` (rimosso deprecation warning)

### Test
56 test, tutti passano:
- `test_heuristics.py` (17): classificazione meteo, moltiplicatori, calcolo delay con verifica valori esatti
- `test_confidence.py` (12): orizzonte temporale, stabilità, completezza, score complessivo
- `test_sampler.py` (8): haversine, campionamento, tempi arrivo, lunghezze segmenti
- `test_api.py` (11): tutti gli endpoint, validazione input, errori 404, feedback duplicato

### Decisioni tecniche
| Decisione | Motivazione |
|---|---|
| Stub "cielo sereno" nei segmenti | Il Blocco 2 integrerà i servizi esterni reali (ORS, Open-Meteo, Elevation) |
| Store in-memory | Supabase arriverà nel Blocco 4-5, per ora basta per sviluppo e test |
| Condizioni meteo additive | Se pioggia + vento sullo stesso segmento, i delay si sommano — approccio conservativo e realistico |
| Velocità default 70 km/h | Media realistica per mezzi pesanti, sovrascritta dalla durata ORS quando disponibile |
| `C_calibration` default 1.0 | Neutro fino a quando il Calibration System (Blocco 4) non avrà dati |

### Cosa manca (prossimi blocchi)
- **Blocco 2:** Integrazioni servizi esterni (ORS routing, Open-Meteo weather, Open-Elevation)
- **Blocco 3:** Cache Redis (Upstash), orchestrazione completa del flusso di predizione
- **Blocco 4:** Feedback loop, calibrazione coefficienti, analytics
- **Blocco 5:** Auth Supabase (JWT + API Key), rate limiting, deploy Railway

---

## Blocco 2: Integrazioni servizi esterni (2026-02-14)

### Obiettivo
Sostituire la logica stub del Blocco 1 con dati reali da ORS, Open-Meteo e Open-Elevation. I segmenti ora hanno meteo reale, altitudine reale e tipo strada estratto dal percorso.

### Cosa è stato implementato

#### 1. Client HTTP condiviso (`app/services/http_client.py`)
- Singleton `httpx.AsyncClient` inizializzato al lifespan dell'app (startup/shutdown)
- Retry con backoff esponenziale: 3 tentativi, `0.5s × 2^attempt` tra i retry
- Retry su errori 5xx, timeout e errori di connessione
- Logging di ogni tentativo fallito e dell'esaurimento retry

#### 2. OpenRouteService (`app/services/ors.py`)
- `get_route(origin, destination)` → polyline decodificata, durata, distanza, tipi strada
- Profilo `driving-hgv` (mezzi pesanti)
- Decodifica Google Encoded Polyline (precision=5) → `list[Coordinate]`
- Estrazione road types da `extras.waytypes` ORS → mappatura su `RoadType` enum
- `get_road_type_at_fraction()` per ottenere il tipo strada in un punto qualsiasi del percorso
- `compute_route_hash()` per cache: arrotondamento coordinate a griglia 0.01° (~1.1km), SHA-256 troncato a 16 char

#### 3. Open-Meteo (`app/services/weather.py`)
- `get_weather_at_points(points, arrival_times)` → `list[list[WeatherCondition]]`
- Una richiesta per punto con parametri hourly: `precipitation`, `snowfall`, `wind_speed_10m`, `visibility`, `weather_code`
- Match dell'ora: trova lo slot orario corrispondente al tempo di arrivo stimato
- Classificazione automatica via `classify_weather()` dell'engine → `WeatherCondition` con tipo, severità, raw value, descrizione
- Fallback graceful: se una richiesta fallisce, il punto risulta "cielo sereno" (lista vuota)

#### 4. Open-Elevation (`app/services/elevation.py`)
- `get_elevations(points)` → `list[float]` (metri)
- Batch POST: tutti i punti in una singola chiamata
- Fallback graceful: se l'API è down, tutti i punti ricevono 200m di default
- Degradazione trasparente (il confidence score tiene conto della completezza dati)

#### 5. Orchestratore predizioni (`app/services/prediction.py`)
- `build_prediction(origin, destination, departure_time)` → `PredictionResponse`
- Pipeline completa in 7 step:
  1. ORS → polyline + durata + tipi strada
  2. Sampler → punti ogni 50km + tempi arrivo proporzionali alla durata ORS
  3. Elevation → altitudini batch (con fallback)
  4. Weather → condizioni meteo per ogni punto al suo tempo di arrivo
  5. Heuristics → delay per segmento con tutti i fattori reali
  6. Confidence → score basato su dati reali (orizzonte, stabilità, completezza)
  7. Assemblaggio → `PredictionResponse` completa
- Gestione errori parziali: elevation fallisce → default 200m, weather fallisce → cielo sereno

#### 6. Modifiche ai file esistenti
- **`app/config.py`** — Aggiunte URL base: `ors_base_url`, `open_meteo_base_url`, `open_elevation_base_url`
- **`app/main.py`** — Lifespan handler per init/close del client httpx condiviso
- **`app/routes/predictions.py`** — Rimossa `_generate_stub_segments()`, `create_prediction()` ora chiama `build_prediction()`
- **`requirements.txt`** — Aggiunto `respx==0.20.2` per mock HTTP nei test

### Test
81 test totali, tutti passano (+25 nuovi):
- `test_services.py` (25):
  - HTTP client: singleton, retry su successo, retry su 500, esaurimento retry
  - ORS: decodifica polyline, route hash (stesse/vicine/lontane coordinate), road type lookup, parsing risposta ORS, gestione errori 403
  - Weather: match ora, classificazione pioggia/multi-condizione/cielo sereno, fetch con mock, fallback su errore
  - Elevation: batch success, fallback su errore, lista vuota
  - Orchestratore: pipeline completa con tutti i mock, pipeline con elevation failure
- `test_api.py` (11): aggiornato con mock di `build_prediction` per isolamento dai servizi esterni

### Decisioni tecniche
| Decisione | Motivazione |
|---|---|
| Client HTTP singleton | Riuso connessioni, un solo punto di init/cleanup |
| Retry 3 tentativi con backoff | FRD 4.5 — resilienza senza sovraccaricare servizi esterni |
| Una richiesta Open-Meteo per punto | Open-Meteo non supporta multi-location; è free e veloce |
| Fallback elevation 200m | Degradazione graceful: meglio dati approssimati che errore totale |
| Route hash con griglia 0.01° | ~1.1km di tolleranza — realistico per logistica, abilita cache aggressiva |
| Mock con `respx` nei test | Isolamento completo da API esterne, test deterministici e veloci |

### Configurazione necessaria
Per test con dati reali, serve solo la API key ORS nel `.env`:
```
ORS_API_KEY=la_tua_chiave
```
Open-Meteo e Open-Elevation non richiedono API key.

### Cosa manca (prossimi blocchi)
- **Blocco 3:** Cache Redis (Upstash) per route (24h) e weather (1h)
- **Blocco 4:** Feedback loop, calibrazione coefficienti, analytics
- **Blocco 5:** Auth Supabase (JWT + API Key), rate limiting, deploy Railway

---

## Blocco 3: Cache Redis — Upstash (2026-02-14)

### Obiettivo
Aggiungere caching aggressivo con Redis (Upstash free tier) per rispettare il limite critico di ORS (2,000 req/giorno) e il budget Upstash (10k comandi/giorno).

### Cosa è stato implementato

#### 1. Cache layer (`app/services/cache.py`)
- `init_redis(url)` — inizializza `redis.asyncio` client da URL Upstash
- `close_redis()` — chiude il connection pool
- `cache_get(key)` — GET + JSON deserialize, ritorna `None` su miss o errore
- `cache_set(key, value, ttl)` — JSON serialize + SETEX
- Ogni operazione wrappata in try/except → log warning, return None
- Se `UPSTASH_REDIS_URL` è vuoto, cache disabilitata silenziosamente (tutte le operazioni sono no-op)
- Helper `set_redis()` / `get_redis()` per injection nei test

#### 2. Cache route ORS (`app/services/ors.py`)
- `get_route()` ora controlla `cache_get(f"route:{hash}")` prima di chiamare ORS
- Su miss: chiama ORS API, poi `cache_set()` con TTL 24h (`cache_ttl_route` da config)
- Serializzazione `RouteResult ↔ dict` con `_route_to_dict()` / `_dict_to_route()`
  - Polyline: list di `{lat, lon}`
  - Road types: list di `[start_pct, end_pct, road_type_value]`
- Il route hash (`compute_route_hash()`) era già implementato nel Blocco 2

#### 3. Cache weather Open-Meteo (`app/services/weather.py`)
- Cache key: `weather:{lat_r}:{lon_r}:{date}` con coordinate arrotondate a 0.1° (~11km)
  - Punti vicini sulla stessa griglia condividono la cache → meno comandi Redis
- Caching dell'intera risposta giornaliera (tutte le 24 ore), non del singolo punto/ora
- Su hit: estrae l'ora specifica dalla risposta cached con `_extract_hour_from_daily()`
- Su miss: fetch da Open-Meteo, salva risposta giornaliera completa (TTL 1h, `cache_ttl_weather` da config)
- La classificazione `WeatherCondition` avviene sempre dopo il fetch (cached o meno)

#### 4. Lifespan handler (`app/main.py`)
- `init_redis()` chiamato allo startup (dopo `init_client()`)
- `close_redis()` chiamato allo shutdown (prima di `close_client()`)

#### 5. Modifiche ai file esistenti
- **`requirements.txt`** — Aggiunto `fakeredis==2.21.1` per test

### Budget comandi Upstash (10k/giorno)
| Operazione | Comandi per predizione |
|---|---|
| Route: GET + SET (miss) | 2 |
| Route: GET (hit) | 1 |
| Weather: GET + SET per cella unica (miss) | ~12 (Milano→Roma ~5-7 celle) |
| Weather: GET per cella (hit) | ~6 |
| **Worst case (tutto miss)** | **~14** |
| **Best case (tutto hit)** | **~7** |

- Worst case: ~700 predizioni/giorno
- Con cache hits: ~1,400 predizioni/giorno

### Test
97 test totali, tutti passano (+16 nuovi):
- `test_cache.py` (13):
  - Init/close: URL vuoto, URL valido, close, close su None
  - Operazioni: set+get, miss, list value, TTL verificato, overwrite
  - Cache disabilitata: get ritorna None, set non causa errori
  - Degradazione graceful: ConnectionError su GET → None, ConnectionError su SET → no raise
- `test_services.py` (+3):
  - ORS cache hit: route da cache, API ORS non chiamata
  - Weather cache hit: meteo da cache, Open-Meteo non chiamato
  - RouteResult serialization roundtrip

### Decisioni tecniche
| Decisione | Motivazione |
|---|---|
| Cache intera giornata meteo | Una sola SET per cella/giorno; ore diverse dello stesso punto → cache hit gratuito |
| Griglia 0.1° per weather | ~11km di tolleranza — bilancia precisione e riuso cache |
| Graceful degradation ovunque | Redis down → sistema funziona identico al Blocco 2, solo più lento |
| `fakeredis` per test | Simula Redis in-memory, test veloci e deterministici senza server |
| Fixture `_disable_redis` autouse | Test esistenti del Blocco 2 passano senza modifiche (cache = no-op) |
| JSON serialization | Semplice, debuggabile, compatibile con Upstash REST API |

### Configurazione
Aggiungere nel `.env` per abilitare la cache:
```
UPSTASH_REDIS_URL=rediss://default:xxx@xxx.upstash.io:6379
```
Senza questa variabile, il sistema funziona normalmente senza cache (log warning all'avvio).

### Cosa manca (prossimi blocchi)
- **Blocco 4:** Feedback loop, calibrazione coefficienti, analytics
- **Blocco 5:** Auth Supabase (JWT + API Key), rate limiting, deploy Railway

### Struttura file attuale
```
app/
  main.py              ← Entry point, lifespan handler (httpx + Redis), router wiring
  config.py            ← Settings da env vars + URL base servizi + TTL cache
  errors.py            ← Eccezioni custom + handler
  models/
    schemas.py         ← Tutti i modelli Pydantic
  engine/
    heuristics.py      ← Regole meteo + moltiplicatori
    confidence.py      ← Calcolo confidence score
    sampler.py         ← Campionamento percorso
  routes/
    health.py          ← GET /v1/health
    predictions.py     ← CRUD predictions + feedback (usa orchestratore)
  services/
    cache.py           ← Cache Redis (Upstash) con graceful degradation
    http_client.py     ← Client HTTP condiviso con retry
    ors.py             ← OpenRouteService (routing HGV) + cache 24h
    weather.py         ← Open-Meteo (previsioni meteo) + cache 1h
    elevation.py       ← Open-Elevation (altitudini)
    prediction.py      ← Orchestratore pipeline completa
tests/
  test_heuristics.py   ← 17 test engine heuristics
  test_confidence.py   ← 12 test confidence scoring
  test_sampler.py      ← 8 test route sampling
  test_api.py          ← 11 test endpoint API (con mock)
  test_services.py     ← 28 test servizi esterni + cache (con mock)
  test_cache.py        ← 13 test cache layer (con fakeredis)
```

---

## Blocco 4: Feedback Loop, Calibrazione e Analytics (2026-02-14)

### Obiettivo
Chiudere il feedback loop: i feedback degli utenti calibrano i coefficienti di predizione e alimentano metriche di accuracy. Analytics endpoint per monitorare le performance del sistema.

### Cosa è stato implementato

#### 1. Calibration Engine (`app/engine/calibration.py`)
- **Prerequisiti calibrazione** (`check_prerequisites`):
  - Minimo 20 feedback entries
  - Span temporale ≥ 14 giorni
  - Almeno 3 gruppi condizioni meteo distinti
- **Error factors** (`compute_error_factors`): media di `actual/predicted` per ogni gruppo (WeatherType, Severity)
- **Formula aggiornamento** (FRD 7.4): `new = old + 0.15 × (error_factor - 1.0) × old`
- **Clamping**: coefficienti vincolati a [0.5, 2.0] per evitare derive
- **Historical accuracy** (`compute_historical_accuracy`): % predizioni entro 15 minuti dall'actual, default 70% con <5 feedback

#### 2. Store condivisi (`app/stores/`)
- **`prediction_store.py`** — Store in-memory per predictions e feedback, estratto da routes per evitare import circolari
- **`calibration_store.py`** — Store in-memory per versioni calibrazione: coefficienti `dict[(WeatherType, Severity), float]`, versioning seriale
- **Costanti**: `COEFF_LOWER = 0.5`, `COEFF_UPPER = 2.0`

#### 3. Analytics endpoint (`app/routes/analytics.py`)
- `GET /v1/analytics/accuracy` → `AnalyticsResponse`:
  - `total_predictions`, `total_feedback`, `feedback_rate` (%)
  - `mae` (Mean Absolute Error in minuti)
  - `within_10min_pct`, `within_20min_pct` (% predizioni entro soglia)
  - `calibration_version` (versione corrente dei coefficienti)
  - `breakdown_by_weather` — breakdown per tipo meteo dominante (count, mae, percentuali)

#### 4. Feedback con calibrazione automatica (`app/routes/predictions.py`)
- `POST /v1/predictions/{id}/feedback` ora triggera `_try_calibrate()`:
  - Costruisce `CalibrationInput` da tutti i feedback+prediction accoppiati
  - Verifica prerequisiti → calcola error factors → aggiorna coefficienti
  - Log della nuova versione calibrazione
- **Finestra feedback**: 7 giorni dalla departure_time (feedback più vecchi rifiutati)
- **Feedback duplicati**: un solo feedback per predizione

#### 5. Confidence con accuracy reale (`app/services/prediction.py`)
- `_get_segment_calibration_factor()` — legge coefficiente calibrazione per condizione meteo dominante del segmento
- `_compute_real_historical_accuracy()` — calcola accuracy reale da feedback, usata nel confidence score al posto del default 70%

### Test
117 test totali, tutti passano (+20 nuovi):
- `test_calibration.py` (12): prerequisiti, error factors, formula coefficienti, clamping, historical accuracy
- `test_analytics.py` (4): endpoint vuoto, MAE/percentuali, breakdown per meteo, feedback rate
- `test_api.py` (+4): feedback duplicato, finestra 7 giorni, validazione

### Decisioni tecniche
| Decisione | Motivazione |
|---|---|
| Prerequisiti stringenti (20 feedback, 14 giorni) | Evita calibrazione su dati insufficienti che porterebbe a coefficienti rumorosi |
| Learning rate 0.15 | Aggiornamento graduale — il sistema converge lentamente ma stabilmente |
| Clamping [0.5, 2.0] | Safety net: un coefficiente non può mai più che raddoppiare o dimezzare l'impatto |
| Store in modulo separato | Evita import circolari tra routes, services e engine |
| Breakdown per weather type | Permette di identificare quale condizione il sistema predice meglio/peggio |

---

## Blocco 5: Supabase Persistence, Auth, Rate Limiting, Deploy (2026-02-14)

### Obiettivo
Rendere l'API production-ready: persistenza Supabase (dati sopravvivono ai restart), autenticazione JWT/API-key, rate limiting per tier, configurazione deploy Railway.

### Cosa è stato implementato

#### 1. Supabase PostgREST client (`app/services/supabase.py`)
- Pattern identico a `cache.py`: `init_supabase()` al startup, module-level state, graceful degradation
- `is_configured()` → bool (controlla `SUPABASE_URL` + `SUPABASE_SERVICE_KEY`)
- `select(table, params, single)` → GET su PostgREST con filtri, supporto single row
- `insert(table, data)` → POST con `Prefer: return=representation`
- Usa `service_role` key (bypassa RLS) per tutte le operazioni
- Se non configurato → warning al log, stores restano in-memory

#### 2. Store refactoring — classi async (`app/stores/`)
- **`memory.py`** — `InMemoryPredictionStore` + `InMemoryCalibrationStore`:
  - Tutti i metodi `async def` per compatibilità di interfaccia
  - Metodi `sync_*` (es. `save_prediction_sync()`) per setup test senza async
  - Parametro `org_id` su tutti i metodi (ignorato nell'implementazione in-memory)
- **`supabase_store.py`** — `SupabasePredictionStore` + `SupabaseCalibrationStore`:
  - `PredictionResponse` stored come JSONB via `model_dump(mode="json")` / `model_validate()`
  - Coefficienti calibrazione: chiavi stringa `"rain:moderate"` in JSONB, convertite a/da `tuple[WeatherType, Severity]`
  - Filtraggio per `org_id` su tutte le query
- **`__init__.py`** — Factory pattern:
  - Singletons module-level (start con InMemory)
  - `init_stores()`: se `supabase.is_configured()`, swap a Supabase implementations
- **Import pattern**: tutti i consumer usano `import app.stores as stores` + `stores.prediction_store` (attribute access) per garantire che il swap funzioni a runtime

#### 3. Autenticazione (`app/auth.py`)
- FastAPI dependency `get_current_org(request) → OrgContext`
- **Dev mode bypass**: se `SUPABASE_URL` vuoto → ritorna org di default (tier=professional, 1000 req/hr)
  - Tutti i test esistenti passano senza modifiche, senza header di auth
- **JWT** (`Authorization: Bearer <token>`): decodifica con PyJWT + `supabase_jwt_secret`, estrae `org_id` da `app_metadata`
- **API Key** (`X-API-Key: <key>`): SHA-256 hash, lookup in tabella `api_keys` via PostgREST
- `OrgContext` dataclass: `org_id`, `org_name`, `tier`, `rate_limit_hour`, `predictions_limit_month`
- Health endpoint **sempre pubblico** (nessun `Depends(get_current_org)`)

#### 4. Rate limiting (`app/rate_limit.py`)
- `RateLimiter` class con `defaultdict[str, deque[float]]`
- Sliding window 1 ora: prune entries scadute, conta richieste nella finestra
- `check(org)` → raise `RateLimitError` con `retry_after` se limite superato
- Singleton module-level `rate_limiter`
- Applicato solo a `create_prediction` e `submit_feedback` (le GET non consumano risorse esterne)
- State resetta al restart — accettabile per MVP single-instance

#### 5. Errors update (`app/errors.py`)
- `RateLimitError` ora ha campo `retry_after: int = 60`
- Handler aggiunge header `Retry-After` nelle risposte 429

#### 6. Schema SQL (`docs/schema.sql`)
- `organizations` — id UUID, name, tier (free/starter/professional/enterprise), rate_limit_hour, predictions_limit_month
- `api_keys` — key_hash SHA-256 (UNIQUE), prefix (primi 8 char), is_active, last_used_at
- `predictions` — id UUID PK, organization_id FK, data JSONB, total_delay_minutes, departure_time
- `feedback` — prediction_id FK UNIQUE, actual/predicted/deviation
- `calibration_versions` — version SERIAL PK, coefficients JSONB, feedback_count
- Indici su `(org_id, created_at)` per predictions, `prediction_id` per feedback

#### 7. Deploy config
- **`Procfile`**: `web: uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`
- **`requirements.txt`**: aggiunto `PyJWT==2.8.0`
- **`app/config.py`**: aggiunto `supabase_jwt_secret`
- **`.env.example`**: aggiunto `SUPABASE_JWT_SECRET`

#### 8. Lifespan (`app/main.py`)
- Startup: `init_client()` → `init_redis()` → `init_supabase()` → `init_stores()`
- Shutdown: `close_redis()` → `close_client()`

#### 9. Test infrastructure (`tests/conftest.py`)
- Fixture `_use_memory_stores` (autouse): inietta fresh `InMemoryPredictionStore` + `InMemoryCalibrationStore` prima di ogni test, ripristina dopo → isolamento totale
- Fixture `_dev_mode_auth` (autouse): azzera `supabase_url` nelle settings → auth bypass in tutti i test
- Rimossi fixture `_clear_stores` duplicati da `test_api.py` e `test_analytics.py`

### Test
129 test totali, tutti passano (+12 nuovi):
- `test_auth.py` (7): dev mode bypass (2), auth enforced: no auth → 401, invalid JWT → 401, valid JWT → 200, invalid API key → 401, valid API key → 200
- `test_rate_limit.py` (5): sotto limite, al limite → 429, window expiry, org indipendenti, retry-after value
- Test esistenti: tutti passano senza modifiche grazie al dev-mode bypass

### Decisioni tecniche
| Decisione | Motivazione |
|---|---|
| httpx diretto su PostgREST (no `supabase-py`) | Il progetto ha già un httpx client condiviso con retry; evita dipendenza pesante |
| `import app.stores as stores` (non `from ... import`) | Attribute access garantisce che il swap del singleton funzioni a runtime nei test e al startup |
| Dev mode bypass automatico | `SUPABASE_URL` vuoto → auth skip, in-memory stores — zero config per dev/test |
| Rate limiting in-memory (no Redis) | Railway free = 1 istanza; risparmia comandi Upstash (budget 10k/day già stretto) |
| `org_id` param su tutti i metodi store | Multi-tenancy ready; in-memory lo ignora, Supabase filtra |
| Schema SQL separato (`docs/schema.sql`) | Applicato manualmente via SQL Editor di Supabase — no migration tool necessario |
| `sync_*` helper nei store | Test setup sincrono senza complessità async (`save_prediction_sync()` ecc.) |

### Modalità operative
| Ambiente | `SUPABASE_URL` | Auth | Store | Rate Limit |
|---|---|---|---|---|
| **Dev/Test** | vuoto | bypass (dev-org) | InMemory | in-memory (1000/hr default) |
| **Production** | configurato | JWT + API Key | Supabase | in-memory (da org tier) |

### Struttura file finale
```
app/
  main.py              ← Entry point, lifespan (httpx + Redis + Supabase + stores)
  config.py            ← Settings + supabase_jwt_secret
  errors.py            ← Eccezioni custom + Retry-After header per 429
  auth.py              ← JWT/API-key auth con dev-mode bypass
  rate_limit.py        ← Sliding-window rate limiter in-memory
  models/
    schemas.py         ← Tutti i modelli Pydantic
  engine/
    heuristics.py      ← Regole meteo + moltiplicatori
    confidence.py      ← Calcolo confidence score
    sampler.py         ← Campionamento percorso
    calibration.py     ← Calibrazione coefficienti da feedback
  routes/
    health.py          ← GET /v1/health (sempre pubblico)
    predictions.py     ← CRUD predictions + feedback + calibrazione auto
    analytics.py       ← GET /v1/analytics/accuracy
  services/
    cache.py           ← Cache Redis (Upstash)
    http_client.py     ← Client HTTP condiviso con retry
    ors.py             ← OpenRouteService + cache 24h
    weather.py         ← Open-Meteo + cache 1h
    elevation.py       ← Open-Elevation
    prediction.py      ← Orchestratore pipeline (async store calls)
    supabase.py        ← PostgREST async client
  stores/
    __init__.py        ← Factory: init_stores() swap InMemory ↔ Supabase
    memory.py          ← InMemoryPredictionStore + InMemoryCalibrationStore
    supabase_store.py  ← SupabasePredictionStore + SupabaseCalibrationStore
    calibration_store.py ← Costanti + CalibrationVersion dataclass
    prediction_store.py  ← (gutted, backward compat only)
docs/
  FRD.md             ← Functional Requirements Document
  DEV_LOG.md         ← Questo file
  schema.sql         ← Schema SQL per Supabase
tests/
  conftest.py          ← Fixture autouse: fresh stores + dev-mode auth
  test_heuristics.py   ← 17 test
  test_confidence.py   ← 12 test
  test_sampler.py      ← 8 test
  test_api.py          ← 16 test
  test_services.py     ← 28 test
  test_cache.py        ← 13 test
  test_calibration.py  ← 12 test
  test_analytics.py    ← 4 test
  test_auth.py         ← 7 test
  test_rate_limit.py   ← 5 test
Procfile               ← Railway deploy
```

---

## Blocco 6: Rotte Alternative (2026-02-14)

### Obiettivo
Suggerire percorsi alternativi quando il ritardo previsto sulla rotta principale supera una soglia (20 min). Alternative calcolate con una pipeline leggera (solo delay totale, senza dettaglio per segmento) nella stessa chiamata ORS (0 costi aggiuntivi).

### Cosa è stato implementato

#### 1. Configurazione soglia (`app/config.py`)
- `alternative_delay_threshold_minutes: int = 20` — soglia sotto la quale le alternative non vengono calcolate

#### 2. Modelli (`app/models/schemas.py`)
- **`AlternativeRoute`** — nuovo modello con: `route_index`, `total_delay_minutes`, `duration_minutes`, `distance_km`, `delay_savings_minutes`, `summary`
- **`PredictionResponse`** — aggiunto campo `alternatives: list[AlternativeRoute] = []` (backward compatible)

#### 3. Refactor ORS (`app/services/ors.py`)
- **`_parse_single_route(route_data)`** — estratta logica di parsing da `get_route()`, riutilizzata per parsare ogni route nella risposta multi-route
- **`get_routes(origin, dest, include_alternatives)`** — nuova funzione:
  - `include_alternatives=False` → delega a `get_route()`, ritorna `[RouteResult]`
  - `include_alternatives=True` → chiama ORS con `alternative_routes: {target_count: 2, share_factor: 0.6, weight_factor: 1.4}`
  - Cache key separata: `route:{hash}:alt`
- **`_routes_to_dicts()` / `_dicts_to_routes()`** — serializzazione lista per cache

#### 4. Pipeline alternative (`app/services/prediction.py`)
- `build_prediction()` — nuovo parametro `include_alternatives: bool = False`
- Usa `get_routes()` invece di `get_route()`: `all_routes[0]` → pipeline completa, `all_routes[1:]` → pipeline leggera (se delay > soglia)
- **`_compute_route_delay(route, departure)`** — stessa pipeline (sample → elevation → weather → heuristics) ma ritorna solo il delay totale
- **`_build_alternatives(alt_routes, departure, main_delay)`** — costruisce `list[AlternativeRoute]`
- **`_build_alternative_summary(index, savings, route)`** — stringa leggibile con km, minuti e risparmio

#### 5. Route handler (`app/routes/predictions.py`)
- Passthrough: `include_alternatives=request.include_alternatives` a `build_prediction()`

### Test
141 test totali, tutti passano (+12 nuovi):
- `test_alternatives.py` (12):
  - `get_routes`: senza alternative (1 route), con alternative (2 routes), cache hit, ORS ritorna 1 sola route
  - `build_prediction`: default → alternatives=[], delay sotto soglia → alternatives=[], delay sopra soglia → alternatives popolate
  - Helpers: delay_savings calcolo, summary con savings, summary no improvement
  - Backward compatibility: PredictionResponse senza alternatives → default []
  - API integration: POST con include_alternatives → JSON con alternatives

### Flusso completo
```
POST /v1/predictions { include_alternatives: true }
  │
  ├─ [ORS] 1 sola chiamata → rotta principale + 2 alternative
  │
  ├─ [Pipeline completa] sulla rotta principale → delay = 45 min
  │
  ├─ delay > 20 min soglia? SÌ
  │
  ├─ [Pipeline leggera] sulle alternative (solo delay totale)
  │
  └─ Risposta: rotta principale + alternatives con delay_savings
```

### Decisioni tecniche
| Decisione | Motivazione |
|---|---|
| Soglia 20 minuti | Sotto questa soglia l'alternativa non ha valore significativo per l'utente |
| Stessa chiamata ORS | `alternative_routes` param nella stessa POST → 0 costi aggiuntivi quota |
| Cache key separata `:alt` | Evita conflitti con cache single-route esistente |
| Pipeline leggera (no SegmentDetail) | Riduce complessità risposta e tempo di calcolo per le alternative |
| Default `alternatives: []` | Backward compatible — client esistenti non vedono cambiamenti |
| `share_factor: 0.6` | ORS genera alternative che condividono max 60% della rotta principale |

### Budget impatto
| Scenario | Costi aggiuntivi ORS | Costi weather/elevation |
|---|---|---|
| `include_alternatives=false` | 0 | 0 |
| `include_alternatives=true`, delay ≤ 20 min | 0 (stessa chiamata) | 0 (alternative non calcolate) |
| `include_alternatives=true`, delay > 20 min | 0 (stessa chiamata) | ~2× per le alternative |

---

## Blocco 7B: Monitoring e Observability (2026-02-14)

### Obiettivo
Infrastruttura di logging strutturato, metriche in-memory e health check arricchito. Prerequisito per il Blocco 7A che ne beneficia automaticamente.

### Cosa è stato implementato

#### 1. Structured JSON Logging (`app/logging_config.py`)
- `ContextVar` per `request_id` e `org_id` — propagazione async-safe attraverso tutto il request lifecycle
- `LogintelJsonFormatter` — ogni riga di log è JSON con: `timestamp`, `level`, `logger`, `message`, `request_id`, `organization_id`
- `setup_logging(log_level)` — configura root logger con JSON su stdout, silenzia `uvicorn.access` e `httpx`

#### 2. In-memory Metrics (`app/metrics.py`)
- `MetricsCollector` thread-safe (singleton):
  - `record_request()` — contatore richieste + errori 5xx, salva latenze
  - `record_cache_hit()` / `record_cache_miss()` — contatori cache
  - `snapshot()` → `MetricsSnapshot`: error_rate (finestra 120s), latency p50/p95/p99, cache hit rate, uptime

#### 3. ASGI Middleware (`app/middleware.py`)
- `RequestIdMiddleware` — genera UUID o propaga `X-Request-ID` dal client, setta `request_id_var`, aggiunge header in risposta
- `TimingMiddleware` — misura durata richiesta, chiama `metrics_collector.record_request()`

#### 4. Modifiche file esistenti
- **`app/main.py`** — `setup_logging()` all'inizio del lifespan, middleware registrati
- **`app/auth.py`** — `org_id_var.set()` in dev mode e dopo `_fetch_org()`
- **`app/services/cache.py`** — `record_cache_hit()`/`record_cache_miss()` in `cache_get()`
- **`app/routes/health.py`** — Health check arricchito con dependencies (Redis ping), metrics snapshot, status `degraded` se Redis unhealthy
- **`app/services/weather.py`** — Coordinate nei log arrotondate a 2 decimali (~1.1km) per privacy

### Test
181 test totali, tutti passano (+18 nuovi `test_monitoring.py`):
- `TestMetricsCollector` (9): record_request, error_rate 5xx, error_rate zero, latency percentiles, latency empty, cache hit/miss, cache rate zero, uptime, snapshot fields
- `TestRequestIdMiddleware` (2): genera request_id, preserva client request_id
- `TestTimingMiddleware` (1): registra durata
- `TestHealthEndpoint` (3): dependencies presenti, metrics presenti, status degraded con Redis down
- `TestStructuredLogging` (3): output JSON valido, request_id nei log, org_id nei log

### Decisioni tecniche
| Decisione | Motivazione |
|---|---|
| `ContextVar` (non threading.local) | Async-safe — funziona con FastAPI/asyncio |
| Metrics in-memory (no Prometheus) | Zero costo, Railway free = 1 istanza |
| Error rate sliding window 120s | Finestra breve per rilevare spike in tempo reale |
| Import inline in `cache.py` | Evita import circolari (`metrics` → `cache` → `metrics`) |
| Redis ping per health | Unica dipendenza critica da monitorare |

---

## Blocco 7A: Elementi Speciali del Percorso (2026-02-14)

### Obiettivo
Considerare tunnel, ponti, valichi e centri urbani nel calcolo del ritardo (FRD 6.4). Dati da Overpass API (OpenStreetMap), gratuita e senza API key.

### Cosa è stato implementato

#### 1. Configurazione (`app/config.py`)
- `enable_special_elements: bool = True` — feature flag per disabilitare
- `cache_ttl_osm: int = 604800` — cache 7 giorni (infrastruttura statica)
- `overpass_base_url: str = "https://overpass-api.de"`

#### 2. Modelli (`app/models/schemas.py`)
- `SpecialElementType(str, Enum)`: TUNNEL, BRIDGE, MOUNTAIN_PASS, URBAN_CENTER
- `SpecialElement(BaseModel)`: type, name, length_m (tunnel), lat, lon
- `SpecialElementFactor(BaseModel)`: element_type, element_name, multiplier
- `SegmentFactors.special_elements: list[SpecialElementFactor] = []` (backward compatible)

#### 3. Overpass API client (`app/services/overpass.py`)
- `_compute_route_bbox(polyline, padding_deg=0.05)` — bounding box dalla polyline
- `_build_overpass_query(bbox)` — query QL per tunnel, bridge, mountain_pass, city/town
- `_parse_overpass_response(data)` — parse JSON, filtra tunnel < 500m
- `get_special_elements(polyline)` — fetch con cache 7d, fallback lista vuota
- Pattern identico a `elevation.py`: async, `request_with_retry`, graceful degradation

#### 4. Special Elements Engine (`app/engine/special_elements.py`)
- `ELEMENT_MULTIPLIERS`: tunnel 0.0, bridge 1.3, mountain_pass 1.9, urban_center 1.3
- `find_elements_for_segment()` — filtra per distanza dal midpoint (5km default)
- `compute_special_element_modifier()` → `(weather_multiplier, time_multiplier, factors)`
  - Tunnel → `weather_multiplier = 0.0` (annulla tutto)
  - Bridge → amplifica delay per WIND e SNOW
  - Mountain pass → amplifica delay per SNOW
  - Urban center → `time_multiplier = 1.3`
  - Tunnel ha precedenza assoluta

#### 5. Heuristics update (`app/engine/heuristics.py`)
- `calculate_segment_delay()` — 2 parametri opzionali:
  - `special_element_weather_multiplier: float = 1.0`
  - `special_element_time_multiplier: float = 1.0`
- Backward compatible: default 1.0 = nessun cambiamento

#### 6. Pipeline integration (`app/services/prediction.py`)
- Step 3.5: `get_special_elements(route.polyline)` con try/except → fallback vuoto
- Nel loop segmenti: `find_elements_for_segment()` → `compute_special_element_modifier()` → passare multipliers a `calculate_segment_delay()`
- Stessa logica anche in `_compute_route_delay()` (pipeline leggera per alternative)

### Test
181 test totali, tutti passano (+22 nuovi `test_special_elements.py`):
- `TestSpecialElementModifiers` (10): tunnel annulla meteo, bridge amplifica vento, bridge no effetto su pioggia, bridge amplifica neve, mountain pass amplifica neve, mountain pass no effetto senza neve, urban center amplifica tempo, tunnel override altri elementi, multipli si combinano, nessun elemento → no modifica
- `TestFindElementsForSegment` (2): elemento vicino incluso, elemento lontano escluso
- `TestOverpassService` (6): bbox computation, parse tunnel+bridge, parse mountain pass+urban, fallback su errore, success, cache hit
- `TestHeuristicsWithSpecialElements` (4): delay con tunnel = 0, delay con bridge aumentato, default params invariati, urban center time multiplier

### Budget impatto
| Scenario | Costi Overpass | Nota |
|---|---|---|
| `enable_special_elements=true` | 1 query per rotta (cached 7d) | Bounding box dell'intera rotta |
| `enable_special_elements=false` | 0 | Feature disabilitata |
| Overpass down | 0 | Graceful degradation → predizioni invariate |

### Decisioni tecniche
| Decisione | Motivazione |
|---|---|
| Overpass API (non altro) | Gratuita, no API key, menzionata nel FRD 3.1 |
| Cache 7 giorni | Infrastruttura stradale cambia raramente |
| Fog zone skip | Nessun tag OSM diretto per zone nebbia — rimandato a versione futura |
| Proximity 5km | Bilancia precisione e tolleranza GPS/routing |
| Tunnel < 500m filtrati | Sotto 500m il tunnel non offre protezione significativa dal meteo |
| Tunnel ha precedenza assoluta | Se sei in galleria, non importa che ci sia anche un ponte |

### Struttura file aggiornata
```
app/
  main.py              ← + setup_logging() + middleware
  config.py            ← + enable_special_elements, cache_ttl_osm, overpass_base_url
  logging_config.py    ← NUOVO: ContextVars + JSON formatter
  metrics.py           ← NUOVO: MetricsCollector in-memory
  middleware.py        ← NUOVO: RequestId + Timing middleware
  auth.py              ← + org_id_var.set()
  models/
    schemas.py         ← + SpecialElementType, SpecialElement, SpecialElementFactor
  engine/
    heuristics.py      ← + special_element_weather/time_multiplier params
    special_elements.py ← NUOVO: multipliers + proximity filter + modifier computation
  routes/
    health.py          ← Arricchito con dependencies + metrics
  services/
    cache.py           ← + cache hit/miss metrics
    overpass.py        ← NUOVO: Overpass API client
    prediction.py      ← + step 3.5 special elements integration
    weather.py         ← Coordinate privacy nei log
tests/
  test_monitoring.py   ← NUOVO: 18 test
  test_special_elements.py ← NUOVO: 22 test
```
