# Logintel API — Analisi del repository

Analisi statica del repository allo stato del commit `ceffb74` (23 commit totali, ultimo del 2026-03-17).
Tutte le affermazioni sono verificabili aprendo i file citati.

---

## 1. COS'È

API HTTP che stima il ritardo dovuto al meteo su un viaggio di trasporto merci su gomma fra due
coordinate, dato un orario di partenza, e propone rotte alternative quando il ritardo previsto supera
una soglia. Il chiamante è un sistema terzo (TMS, gestionale logistico): l'autenticazione è per
organizzazione via JWT Supabase o `X-API-Key` (`app/auth.py:42-62`), non c'è interfaccia utente.

Il calcolo non usa modelli di machine learning: è una formula moltiplicativa su tabelle di
coefficienti scritte a mano (`app/engine/heuristics.py:23-96`), corretta nel tempo da un coefficiente
di calibrazione derivato dal feedback degli utenti (`app/engine/calibration.py`).

Il `README.md` contiene solo le istruzioni di avvio. La descrizione del prodotto è nella `description`
di FastAPI (`app/main.py:34-52`) e in `docs/FRD.md` (624 righe, in italiano).

---

## 2. ARCHITETTURA REALE

Backend Python/FastAPI monolitico, single-process, pensato per un'istanza sola (dichiarato
esplicitamente in `app/rate_limit.py:5` e `app/metrics.py:5`).

### Stratificazione

Quattro livelli, con una separazione che regge quasi ovunque:

| Livello | Directory | Cosa contiene | Fa I/O |
|---|---|---|---|
| HTTP | `app/routes/` | 3 router, validazione, paginazione | no (delega) |
| Orchestrazione | `app/services/prediction.py` | la pipeline completa | sì, indirettamente |
| Dominio | `app/engine/` | euristiche, confidence, calibrazione, sampling | **no** |
| I/O | `app/services/`, `app/stores/` | ORS, Open-Meteo, Open-Elevation, Overpass, Redis, Supabase | sì |

`app/engine/` è composto da funzioni pure: `heuristics.py`, `confidence.py`, `sampler.py` e
`special_elements.py` non importano nulla da `services` o `stores`. `calibration.py` lo dichiara
in testa al file (`app/engine/calibration.py:4-5`) ed è rispettato — importa solo da `models` e la
coppia di costanti `COEFF_LOWER/COEFF_UPPER`.

Il punto in cui il dominio viene contaminato è `app/services/prediction.py`: la funzione
`build_prediction` (437 righe di file, la funzione da sola 60-269) contiene sia l'orchestrazione
I/O sia il loop che assembla i segmenti. È il file più denso del progetto.

### Comunicazione fra i moduli

- I moduli I/O espongono **singleton a livello di modulo** inizializzati nel `lifespan`
  (`app/main.py:18-29`): `_client` in `http_client.py:18`, `_redis` in `cache.py:20`,
  `_base_url/_headers` in `supabase.py:19-21`. Non c'è dependency injection: i moduli si importano
  a vicenda direttamente.
- Gli store sono due variabili di modulo riassegnate a runtime da `init_stores()`
  (`app/stores/__init__.py:17-37`): in memoria se Supabase non è configurato, Supabase altrimenti.
  Non c'è un'interfaccia esplicita (Protocol/ABC), solo due classi che implementano gli stessi metodi.
  I consumatori fanno `import app.stores as stores` e poi `stores.prediction_store.x()` — indirezione
  necessaria perché l'oggetto viene sostituito dopo l'import.
- Tutte le chiamate esterne passano da `request_with_retry` (`app/services/http_client.py:52-121`),
  che applica retry con backoff esponenziale e, se riceve `service_name`, fa da gate al circuit
  breaker corrispondente.

### La pipeline di predizione

`app/services/prediction.py:60-269`, nell'ordine:

1. `get_routes()` → ORS, polyline + durata + tipi strada (cache Redis 24h)
2. `sample_points_from_polyline()` → un punto ogni 50 km, con haversine
3. `estimate_arrival_times()` → distribuzione proporzionale della durata ORS sui punti
4. **in parallelo** (`asyncio.gather`, righe 105-142): elevazione, elementi speciali OSM, meteo
5. loop sui segmenti → `calculate_segment_delay()` per ognuno
6. `compute_confidence()`
7. alternative, **solo se** `include_alternatives` e ritardo > soglia e ORS ha restituito >1 rotta

### Modello dati

Cinque tabelle in `supabase/migrations/`. Le tre che reggono tutto:

- **`predictions`** — `id TEXT` PK, `organization_id TEXT`, `data JSONB` (l'intero
  `PredictionResponse` serializzato) più tre colonne denormalizzate per filtrare/ordinare.
  È un documento, non un modello relazionale: i segmenti, il meteo e le alternative vivono
  dentro il JSONB.
- **`feedback`** — una riga per predizione (`UNIQUE (prediction_id)`, `001:39`), con
  `actual`, `predicted` e `deviation` già calcolati in scrittura.
- **`calibration_versions`** — log append-only: ogni ricalibrazione inserisce una riga con
  l'intero dizionario dei coefficienti in JSONB. Il coefficiente "corrente" è
  `ORDER BY version DESC LIMIT 1` (`app/stores/supabase_store.py:134-143`).

`organizations` e `api_keys` (migration 002) servono solo all'autenticazione.

Nota importante sul modello: **`calibration_versions` non ha una colonna `organization_id`**
(`001_initial_schema.sql:52-57`). Tutti i metodi dello store accettano un parametro `org_id` che
non viene mai usato. La calibrazione è di fatto globale e condivisa fra tutti i tenant.

---

## 3. STACK EFFETTIVO

Non c'è `package.json`, non c'è lockfile, non c'è `pyproject.toml`. Le dipendenze sono due file
`requirements*.txt` con versioni pinnate esatte.

### Produzione — `requirements.txt`

| Pacchetto | Versione | Usato in |
|---|---|---|
| `fastapi` | 0.109.0 | `app/main.py`, tutti i router |
| `uvicorn[standard]` | 0.27.0 | `Procfile` |
| `httpx` | 0.26.0 | `app/services/http_client.py` |
| `redis` | 5.0.1 | `app/services/cache.py` (`redis.asyncio`) |
| `pydantic` | 2.5.3 | `app/models/schemas.py` |
| `pydantic-settings` | 2.1.0 | `app/config.py` |
| `PyJWT` | 2.8.0 | `app/auth.py:14` |
| `python-json-logger` | 2.0.7 | `app/logging_config.py:14` |
| `python-dotenv` | 1.0.0 | **nessun import diretto** — usato indirettamente da `pydantic-settings` (`env_file=".env"`, `app/config.py:6`) |
| `numpy` | 1.26.3 | **mai importato.** `grep -rn "import numpy" app/ tests/` non restituisce nulla. I percentili sono calcolati a mano (`app/metrics.py:91-105`), la varianza pure (`app/engine/confidence.py:61-62`) |

### Sviluppo — `requirements-dev.txt`

`pytest` 7.4.4, `pytest-asyncio` 0.23.3, `respx` 0.20.2 (mock httpx), `fakeredis` 2.21.1.

### Runtime e deploy

- `runtime.txt`: `python-3.12.6`
- `Procfile`: `uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`
- Servizi esterni da `app/config.py:15-26`: OpenRouteService, Open-Meteo, Open-Elevation,
  Overpass API, Supabase PostgREST, Upstash Redis.
- Il client Supabase **non** è la libreria ufficiale: è un wrapper httpx di 85 righe su PostgREST
  (`app/services/supabase.py`), con due sole operazioni, `select` e `insert`. Non esiste `update`
  né `delete`.

### Cosa manca dal toolchain

Nessuna CI (`.github/` non esiste), nessun linter o formatter configurato, nessun `pytest.ini`,
nessun type checker, nessun `LICENSE` — benché `app/main.py:74-76` dichiari `license_info` =
"Proprietary".

---

## 4. TRE FILE DA APRIRE DAL VIVO

### 4.1 — `app/services/ors.py`, righe 91-105 e 254-343

**Cosa fa.** `compute_route_hash` costruisce la chiave di cache arrotondando origine e destinazione
al multiplo di 0.005° (~500 m) prima di hasharle. `get_routes` chiede a ORS fino a 3 rotte in una
sola chiamata, e se ORS rifiuta il parametro `alternative_routes` ricade su una richiesta a rotta
singola.

**La decisione.** OpenRouteService ha 2.000 richieste/giorno sul piano gratuito, ed è il vincolo
dichiarato del progetto. Da questo discendono tre scelte visibili nel file:

1. La cache non è per coordinate esatte ma per **griglia geografica**: due partenze distanti 400 m
   producono la stessa chiave e riusano la stessa rotta (riga 99). Si accetta un errore di
   posizionamento fino a ~500 m in cambio di un hit rate molto più alto.
2. Le alternative si chiedono in **una sola chiamata** con `target_count: 2` (righe 296-300), non
   in tre chiamate separate. Costo: 1 richiesta invece di 3.
3. La chiave delle alternative è separata (`route:{hash}:alt`, riga 271) da quella della rotta
   singola, per non far collidere due payload di forma diversa sotto lo stesso hash.

**L'alternativa possibile.** Si poteva cachare per coordinate esatte (chiave precisa, zero errore
geografico) e calcolare le alternative solo quando servono. Non è stata presa, e il motivo è scritto
nel commit `433f48f`: la griglia è stata *ristretta* da 0.01° a 0.005° "per FRD §9.3", cioè si è
scelto consapevolmente il punto di equilibrio fra precisione e quota, non l'uno o l'altro estremo.

**La domanda che farà un senior.** Il fallback alle righe 307-331 controlla tre condizioni diverse
per intercettare l'errore di ORS: status 400/413, `"error"` nel `content-type`, e `"error"` nel body
con status 200. Il secondo controllo cerca la stringa `"error"` dentro l'header `content-type` — non
è chiaro quale risposta reale di ORS lo attivi. È il punto del file dove il codice sembra scritto
contro un comportamento osservato in produzione piuttosto che documentato.

---

### 4.2 — `app/engine/calibration.py`, righe 46-131

**Cosa fa.** Tre funzioni pure che, dato un insieme di coppie (predizione, feedback), decidono se
ricalibrare e come. `check_prerequisites` (46-70) impone tre cancelli: almeno 20 feedback, che
coprano almeno 14 giorni, su almeno 3 gruppi meteo distinti. `compute_error_factors` (73-105)
raggruppa i feedback per condizione meteo dominante e calcola la media di `actual/predicted`.
`compute_new_coefficients` (108-131) applica `new = old + 0.15 * (error_factor - 1.0) * old` e
clampa il risultato in `[0.5, 2.0]`.

**La decisione.** È un aggiornamento online smorzato, non una regressione. Tre protezioni contro
l'instabilità, tutte esplicite:

- **learning rate 0.15** (riga 31): un feedback che dice "hai sbagliato del doppio" sposta il
  coefficiente del 15% di quella distanza, non del 100%. Servono molte osservazioni concordi per
  spostarlo davvero.
- **clamp [0.5, 2.0]** (`app/stores/calibration_store.py:15-16`): il coefficiente non può mai
  dimezzare oltre o raddoppiare oltre, qualunque cosa dicano i dati.
- **prerequisiti temporali**: 14 giorni di span impediscono che una singola settimana anomala
  (una nevicata) ricalibri l'intero modello.

Il risultato non sovrascrive: viene inserita una nuova riga in `calibration_versions`. Lo storico
è ricostruibile.

**L'alternativa possibile.** Dal codice si capisce che la scelta è fra "correggere i coefficienti
del modello a mano scritto" e "sostituire il modello". La seconda non è stata presa e il motivo è
dichiarato nel progetto (Smart Heuristics, non ML). Quello che **non si capisce dal codice** è
perché il learning rate sia 0.15 e non 0.05 o 0.3: il numero compare come costante senza
giustificazione, e `docs/FRD.md` lo riporta come formula data.

**La domanda che farà un senior — e qui c'è un problema vero.** L'attribuzione dell'errore è grezza.
`_get_dominant_condition` (153-173) cerca in *tutti* i segmenti la condizione con impatto base più
alto e la elegge unica responsabile; poi `compute_error_factors` (righe 91-92) confronta il ritardo
**totale** della predizione con il ritardo **totale** osservato e attribuisce l'intero rapporto a
quella condizione. Un viaggio Milano-Napoli con 8 segmenti di pioggia leggera e 1 di neve forte
attribuisce tutto l'errore a `(snow, heavy)`. Non c'è alcuna scomposizione per segmento, benché i
segmenti siano tutti salvati nel JSONB e quindi disponibili.

---

### 4.3 — `app/services/prediction.py`, righe 105-142 e 245-352

**Cosa fa.** Due decisioni nello stesso file.

**(a) Fan-out con degradazione per singola fonte** (105-142). Elevazione, elementi speciali OSM e
meteo vengono richiesti in parallelo con `asyncio.gather`. Ogni fonte è avvolta in una closure con
il proprio `try/except` che restituisce un fallback: 200 m per l'elevazione, lista vuota per gli
elementi speciali, e il meteo degrada internamente a "cielo sereno"
(`app/services/weather.py:220-228`). Nessuna eccezione arriva a `gather`, quindi la predizione non
fallisce mai per colpa di una dipendenza esterna — degrada e basta. Il flag `elevation_failed`
viene propagato al conteggio dei punti riusciti (riga 174) e finisce nella componente
`data_completeness` del confidence score: il degrado è **visibile nella risposta**, non nascosto.

**(b) Pipeline leggera condizionale per le alternative** (245-256 e 272-352). Le alternative si
calcolano solo se il chiamante le ha chieste **e** il ritardo sulla rotta principale supera la
soglia **e** ORS ha effettivamente restituito più rotte. Quando si calcolano, si usa
`_compute_route_delay`, che rifà sample → elevazione → meteo → euristiche ma restituisce solo il
totale, senza costruire gli oggetti `SegmentDetail`.

**La decisione.** In (a): si è scelto di far degradare ogni fonte indipendentemente invece di far
fallire la richiesta. L'alternativa era `asyncio.gather(..., return_exceptions=True)` con la gestione
centralizzata, oppure un decoratore condiviso. Non è stata presa, e il motivo si legge nel codice:
ogni fonte ha un fallback **diverso e specifico del dominio** (200 m non è un valore neutro, è
un'altitudine plausibile per la pianura italiana), quindi la gestione generica avrebbe comunque
richiesto una tabella di default per fonte.

In (b): il vincolo di quota ORS spiega il gate a tre condizioni. Quello che non spiega è la
duplicazione: `_compute_route_delay` (272-352) è una copia quasi letterale del loop principale
(144-229) con i pezzi di costruzione della risposta rimossi. Le due versioni sono già leggermente
divergenti — la principale traccia `weather_values` e `successful_points` per il confidence, la
leggera no.

**La domanda che farà un senior.** "Perché 80 righe copiate invece di un parametro
`build_details: bool`?" Dal codice **non si capisce**: non c'è commento né traccia nei commit che
giustifichi la duplicazione. Il commit che l'ha introdotta (`3e076f4`, "lightweight pipeline")
descrive il *cosa*, non il *perché non parametrizzato*.

---

## 5. NUMERI VERI

Tutti i conteggi sono stati eseguiti sul working tree, escludendo `.git/` e `__pycache__/`.

### File

| | Conteggio | Come |
|---|---|---|
| File tracciati da git | 73 | `git ls-files \| wc -l` |
| File `.py` in `app/` | 37, di cui **5 vuoti** (`__init__.py`) → 32 con contenuto | `find app -name "*.py"` |
| File `.py` in `tests/` | 17 (15 file di test + `conftest.py` + `__init__.py` vuoto) | `find tests -name "*.py"` |
| File `.sql` | 3 — 2 migration + `docs/schema.sql` | `find . -name "*.sql"` |
| Documentazione `.md` in `docs/` | 4 (FRD 624 righe, FAQ 146, QUICKSTART 167, DEV_LOG 100) | `wc -l docs/*.md` |
| Esempi client | 4 (`curl`, JS, PHP, Python), 340 righe totali | `wc -l docs/examples/*` |

### Righe di codice

| | Righe |
|---|---|
| `app/` — totale grezzo | 4.508 |
| `app/` — escluse righe vuote e commenti a riga intera | **3.476** |
| `tests/` — totale grezzo | 3.297 |
| `tests/` — escluse righe vuote e commenti | 2.652 |

Il conteggio "escluse righe vuote e commenti" è ottenuto con
`grep -v '^\s*$' | grep -v '^\s*#'`. **È una stima per eccesso**: non esclude le docstring, che in
questo repository sono abbondanti (quasi ogni modulo ha un'intestazione di 8-13 righe). Il codice
eseguibile reale è sensibilmente inferiore a 3.476.

I tre file più grandi in `app/`: `services/prediction.py` (437), `models/schemas.py` (366),
`services/ors.py` (345).

### Modelli, tabelle, endpoint

| | Conteggio |
|---|---|
| Modelli Pydantic (`BaseModel`) | 20, in un unico file `app/models/schemas.py` |
| Enum | 5 (`WeatherType`, `Severity`, `RoadType`, `SpecialElementType`, `ConfidenceLevel`) |
| Tabelle PostgreSQL | 5 in `supabase/migrations/` — `predictions`, `feedback`, `calibration_versions`, `organizations`, `api_keys` |
| Endpoint HTTP | **6** — `grep -rn "@router\.(get\|post\|...)" app/routes/` |

I 6 endpoint: `POST /v1/predictions`, `GET /v1/predictions/{id}`, `GET /v1/predictions`,
`POST /v1/predictions/{id}/feedback`, `GET /v1/analytics/accuracy`, `GET /v1/health`.

### Test

**210 funzioni di test** (`pytest --collect-only -q` → "210 tests collected"), distribuite su 15 file.
I tre file più coperti: `test_services.py` (28), `test_special_elements.py` (26),
`test_heuristics.py` (22).

**Esito reale dell'esecuzione** (`python -m pytest -q`, con le dipendenze di
`requirements-dev.txt` installate):

```
9 failed, 201 passed
```

Le 9 fallite sono descritte nella sezione 6 — non sono problemi di ambiente, sono due difetti reali.

Non ho calcolato la copertura: `pytest-cov` non è fra le dipendenze e non l'ho installato.

---

## 6. PUNTI DEBOLI

### 6.1 — Il percorso di persistenza che gira in produzione non è mai testato

`tests/conftest.py:17-29` è una fixture `autouse` che sostituisce gli store con quelli in memoria
**prima di ogni singolo test**. Conseguenza: le 210 prove girano tutte contro
`app/stores/memory.py`. `app/stores/supabase_store.py` (193 righe) e `app/services/supabase.py`
(85 righe) — 278 righe, l'unico codice che parla davvero col database in produzione — non hanno
nemmeno un test. `grep -rn "supabase" tests/*.py` restituisce solo manipolazioni di `settings`
e un `patch` in `test_auth.py:86`.

Il rischio concreto non è teorico. `SupabasePredictionStore.get_prediction`
(`supabase_store.py:47-54`) fa `PredictionResponse.model_validate(row["data"])` su un JSONB scritto
in un momento arbitrario del passato: qualunque campo aggiunto o rinominato negli schemi rompe
la lettura di tutte le predizioni già salvate, e nessun test se ne accorgerebbe.

Allo stesso livello: `clear_all()` è `pass` in entrambe le classi Supabase
(`supabase_store.py:127-128` e `192-193`), con il commento "Not implemented for production store".
Sono gli unici due `pass` non implementati del repository.

### 6.2 — Due difetti reali che i test stanno già segnalando

Non sono ipotesi: sono i 9 fallimenti riproducibili.

**(a) `AlertManager._should_fire` sopprime il primo allarme di ogni regola.**
`app/alerting.py:141-143`:

```python
last = self._cooldowns.get(rule, 0.0)
if now - last < cooldown:
    return False
```

`now` è `time.monotonic()`, che su Linux è il tempo dal boot della macchina. Il default per una
regola mai scattata è `0.0`. Quindi finché `time.monotonic()` è minore del cooldown — 300 s per i
CRITICAL, 900 s per i WARNING — **nessun allarme viene mai emesso**, perché il confronto legge un
cooldown fittizio che non è mai scaduto. Su questa macchina `time.monotonic()` valeva 165.9 s e
8 test su 10 di `test_alerting.py` falliscono con `assert 0 == 1`.

In produzione significa: nei primi 5-15 minuti di vita del container, error rate e latenza possono
sforare le soglie senza che `/v1/health` riporti alcun alert. Esattamente la finestra in cui un
deploy fallisce.

**(b) `tests/test_api.py::test_submit_feedback` è una bomba a orologeria già esplosa.**
`tests/test_api.py:37` fissa `departure_time = datetime(2026, 2, 15, ...)`. La rotta feedback
rifiuta le predizioni con partenza più vecchia di 7 giorni
(`app/routes/predictions.py:185-188`). Il test è passato fino al 22 febbraio 2026 e da allora
restituisce 400. Un test che dipende dalla data di wall-clock senza congelarla non è un test.

### 6.3 — Il multi-tenancy è dichiarato nelle firme ma non esiste nei dati

`org_id` viene passato ovunque — è un parametro di quasi ogni metodo degli store — ma:

- La tabella `calibration_versions` **non ha una colonna `organization_id`**
  (`001_initial_schema.sql:52-57`). `SupabaseCalibrationStore.get_coefficient`
  (`supabase_store.py:134-143`) accetta `org_id` e non lo usa: legge sempre l'ultima versione
  globale. I coefficienti calibrati sui feedback di un cliente si applicano a tutti gli altri.
- `_compute_real_historical_accuracy` (`prediction.py:424-437`) chiama `all_feedback()` **senza
  `org_id`**. Nello store Supabase, `org_id=""` significa "nessun filtro"
  (`supabase_store.py:104-105`): la componente `historical_accuracy` del confidence score di ogni
  cliente è calcolata sui feedback di tutti i clienti. Stessa cosa in
  `_get_segment_calibration_factor` (riga 421).
- Le policy RLS sono `FOR ALL USING (TRUE) WITH CHECK (TRUE)` senza clausola `TO`
  (`001_initial_schema.sql:71-81`), quindi si applicano a `PUBLIC`. Il commento alle righe 64-65
  dice che servono a "proteggere dall'accesso anon/client", ma una policy sempre vera non protegge
  da nulla: la RLS è abilitata e permissiva.

Aggiungo il rovescio: se `SUPABASE_URL` non è valorizzata, `get_current_org` (`app/auth.py:46-49`)
**restituisce un'organizzazione di sviluppo senza guardare gli header**. Una variabile d'ambiente
mancante al deploy non fa fallire l'avvio: apre l'API a chiunque, con `rate_limit_hour=1000`.

### 6.4 — Altri tre che un revisore noterebbe subito

**Query N+1 sul percorso caldo.** `_compute_real_historical_accuracy` viene invocata a **ogni**
`POST /v1/predictions` (`prediction.py:234`) e fa una `get_prediction` per ogni riga di feedback:
con Supabase è 1 + N richieste HTTP prima di poter rispondere. Lo stesso schema in `_try_calibrate`
(`routes/predictions.py:218-221`, dentro la richiesta di feedback) e in
`routes/analytics.py:61-62`. Inoltre `GET /v1/predictions` carica **tutte** le predizioni
dell'organizzazione e pagina in Python (`routes/predictions.py:121-126`), ignorando gli indici
creati apposta in migration 001.

**Perdita di memoria nel metrics collector.** `MetricsCollector.record_request`
(`app/metrics.py:43-48`) fa `append` su `_request_log` e `_latencies` e **non li tronca mai**.
`snapshot()` filtra `_request_log` per finestra (riga 65) ma lascia intatta la lista sottostante, e
ordina l'intera lista di latenze a ogni chiamata (`_percentile`, riga 95). Su un processo
long-running entrambe le liste crescono senza limite e `/v1/health` diventa progressivamente più
lento.

**Documentazione e codice divergenti, in modo verificabile.**
- La soglia delle alternative è `15` in `app/config.py:23` (abbassata nel commit `74c1549`), ma
  "20 min" nella descrizione OpenAPI (`app/main.py:40`), nel docstring dell'endpoint
  (`routes/predictions.py:62`), in `docs/FAQ.md:105`, `docs/QUICKSTART.md:75` e `docs/FRD.md:360`.
  Chi legge la doc pubblica legge il numero sbagliato.
- Il commit `433f48f` si intitola "align FRD coefficients" e porta il vento-tempesta da 25 a 30 e
  la nebbia fitta da 35 a 40, citando "FRD §6.2". Ma `docs/FRD.md:303` e `306` dicono ancora
  +25 e +35. Il commit si è allontanato dal documento che dichiarava di seguire.
- `docs/schema.sql` e `supabase/migrations/` definiscono le **stesse cinque tabelle con tipi
  incompatibili**: `docs/schema.sql:5-30` usa `UUID PRIMARY KEY DEFAULT gen_random_uuid()` e
  chiavi esterne verso `organizations`; le migration usano `TEXT` con `DEFAULT ''` e nessuna FK.
  Due sorgenti di verità per lo schema, e non è indicato quale sia quella buona.
- `docs/DEV_LOG.md` si ferma al Blocco 1 e descrive `services/` come "(vuoto, Blocco 2)"
  (riga 99). Oggi `services/` contiene 8 moduli.

**Codice morto e residui.** `app/stores/prediction_store.py` è un file di 6 righe contenente solo
una docstring che spiega di non usarlo. `WeatherType.WIND: {Severity.LIGHT: 0.0}`
(`heuristics.py:37`) è irraggiungibile: `classify_wind` non restituisce mai `LIGHT`
(soglie a righe 66-70). `_parse_road_types` (`ors.py:108-133`) riceve `total_distance_m` e non lo
usa mai, pur avendo un docstring che promette di convertire indici in percentuali di distanza —
normalizza su indici. `pyflakes` segnala 7 import inutilizzati in `app/` e 25 in `tests/`.

---

## 7. DOMANDE A CUI NON SO RISPONDERE

Formulate come le farebbe qualcuno che apre il repo per la prima volta.

1. **"Un tunnel annulla il meteo su 50 km di segmento?"**
   `special_elements.py:80-90` mette `weather_multiplier = 0.0` se c'è un tunnel nel segmento, e
   `find_elements_for_segment` (36-53) considera "nel segmento" qualunque elemento entro 5 km dal
   **punto medio**. Un tunnel da 600 m azzera quindi l'intero ritardo meteo di un segmento da 50 km.
   Il FRD dice "= 0 per lunghezza tunnel" (`docs/FRD.md:341`), che è un'altra cosa. Serve una
   risposta pronta: è una semplificazione accettata o un difetto?

2. **"I coefficienti di impatto da dove vengono?"**
   `heuristics.py:23-48` contiene 16 numeri (pioggia 3/8/15/25, neve 10/25/45/70, ecc.) che
   determinano l'intero output del prodotto. Il FRD li riporta in tabella ma non cita una fonte,
   uno studio o un dataset. La risposta "sono stime del dominio da calibrare col feedback" regge
   solo se so dire con quanti feedback reali il sistema è stato calibrato finora — e i prerequisiti
   di `check_prerequisites` (20 feedback su 14 giorni) suggeriscono che la risposta possa essere zero.

3. **"Il calcolo della frazione di percorso confronta unità diverse?"**
   `prediction.py:162` calcola `fraction = sum(segment_lengths[:i]) / total_distance` dove
   `segment_lengths` sono distanze haversine fra punti campionati e `total_distance` è la distanza
   stradale reale di ORS. Le due grandezze non coincidono, e il risultato serve a scegliere il tipo
   di strada del segmento (`get_road_type_at_fraction`), che moltiplica il ritardo per un fattore
   fra 0.8 e 1.8. Quanto sbaglia in pratica? Non lo so.

4. **"Perché `way_type = 0` (Unknown) diventa autostrada?"**
   `ors.py:29` mappa lo sconosciuto su `HIGHWAY`, che ha il fattore **più basso** di tutti (0.8,
   `heuristics.py:82`). Il commento dice "default to highway (motorway segments)". Su un tratto
   non classificato il sistema sceglie sistematicamente la stima più ottimistica. È una scelta
   deliberata o un default ereditato?

5. **"Il rate limiter è in-process: cosa succede con più istanze?"**
   `rate_limit.py:5` dichiara "acceptable for MVP single-instance deployment". Con due repliche
   ogni cliente ottiene il doppio della quota, e un restart azzera la finestra. Se qualcuno mi
   chiede il piano per la seconda istanza, dal codice non esiste: né il rate limiter né il
   `MetricsCollector` né la `_mem_cache` di Overpass (`overpass.py:29`) sono condivisi.

6. **"Perché una sola chiamata batch a Open-Meteo per N punti che possono avere date diverse?"**
   `weather.py:171-187` calcola `start_date`/`end_date` come il minimo e il massimo fra tutti gli
   arrivi e li applica a **tutte** le coordinate. Su un viaggio a cavallo di mezzanotte questo
   raddoppia il payload per ogni punto. Funziona, ma non so dire se era la scelta voluta o un
   effetto collaterale del passaggio da N chiamate a una (commit `433f48f`).

7. **"Cosa succede se ORS restituisce meno punti di polyline del previsto?"**
   `prediction.py:94-98` ha un fallback che, se i punti campionati sono meno di 2, sostituisce
   l'intera rotta con origine e destinazione. Il risultato è un segmento unico lungo quanto la
   distanza stradale intera, con un solo campione meteo. La predizione non fallisce e non segnala
   nulla di anomalo al chiamante, se non indirettamente via `data_completeness`. Quando si attiva
   davvero, in pratica?

---

## Nota metodologica

Ho eseguito la suite di test per verificare le affermazioni della sezione 6.2. Questo ha richiesto
di installare `requirements-dev.txt` nell'ambiente (più `cffi`/`cryptography`, mancanti nel
container). **Nessun file del repository è stato modificato**; l'unico file nuovo è questo
`ANALISI.md`, non committato.
