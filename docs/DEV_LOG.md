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

### Struttura file attuale
```
app/
  main.py              ← Entry point, router wiring
  config.py            ← Settings da env vars
  errors.py            ← Eccezioni custom + handler
  models/
    schemas.py         ← Tutti i modelli Pydantic
  engine/
    heuristics.py      ← Regole meteo + moltiplicatori
    confidence.py      ← Calcolo confidence score
    sampler.py         ← Campionamento percorso
  routes/
    health.py          ← GET /v1/health
    predictions.py     ← CRUD predictions + feedback
  services/            ← (vuoto, Blocco 2)
tests/
  test_heuristics.py
  test_confidence.py
  test_sampler.py
  test_api.py
```
