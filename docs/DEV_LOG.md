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
