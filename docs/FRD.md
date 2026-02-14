# LOGINTEL API - Functional Requirements Document

**Versione 1.0 — Febbraio 2026**

*Documento interno per il team di sviluppo*

## 1. Executive Summary

### 1.1 Scopo del documento

Il presente documento costituisce il Functional Requirements Document (FRD) per la prima release di Logintel API, un servizio di predizione dei ritardi per il trasporto merci su strada basato su condizioni meteorologiche e caratteristiche del percorso.

Il documento è destinato al team di sviluppo interno e ha l'obiettivo di:

- Definire in modo chiaro e dettagliato le funzionalità da implementare
- Documentare le scelte architetturali e le relative motivazioni
- Stabilire i criteri di accettazione e le metriche di successo
- Fungere da riferimento durante tutte le fasi di sviluppo e testing

Il documento non copre aspetti di project management, pianificazione temporale o allocazione risorse, che sono trattati in documentazione separata.

### 1.2 Filosofia del prodotto

Logintel API nasce con un principio guida: offrire il massimo valore con il minimo costo operativo. Il prodotto è progettato per essere vendibile e sostenibile fin dalla prima release, senza dipendere da investimenti significativi in infrastruttura o licenze dati.

L'architettura si fonda su quattro pilastri:

**Smart Heuristics** — Il sistema non utilizza semplici regole if/else, ma funzioni multifattoriali che combinano diverse variabili con pesi configurabili. Una condizione meteorologica viene valutata in relazione al tipo di strada, all'altitudine, all'orario, alla stagione e ad altri fattori contestuali.

**Confidence scoring** — Ogni predizione è accompagnata da un punteggio di confidenza che indica l'affidabilità della stima. Il cliente non riceve solo un numero di minuti di ritardo previsto, ma anche un'indicazione di quanto il sistema sia sicuro di quella predizione.

**Sistema di calibrazione** — I coefficienti delle heuristics non sono statici. Il sistema raccoglie feedback sui ritardi effettivi e utilizza questi dati per affinare progressivamente i propri parametri.

**Indipendenza economica** — La prima release utilizza esclusivamente data sources gratuiti o a costo marginale. Questa scelta permette di validare il product-market fit e generare revenue senza bruciare capitale.

### 1.3 Definizione del perimetro di prodotto

**Logintel API è:**

- Un servizio di predizione dei ritardi basato su condizioni meteorologiche previste lungo il percorso
- Un sistema che considera fattori contestuali (tipo strada, altitudine, orario, stagione) per affinare le stime
- Una piattaforma che apprende e migliora le proprie predizioni attraverso il feedback dei clienti
- Un prodotto B2B accessibile via REST API, integrabile nei sistemi esistenti dei clienti
- Un servizio operativo e vendibile, con gestione errori, logging e monitoring adeguati a un ambiente di produzione

**Logintel API non è:**

- Un sistema di "satellite intelligence" con analisi di immagini satellitari (questa è la visione a lungo termine)
- Un prodotto basato su machine learning o deep learning
- Un sistema di tracking veicoli o fleet management
- Un servizio di previsioni meteo generico
- Un sistema che integra dati di traffico real-time (previsto per release successive)

**Copertura geografica:** La prima release copre il territorio italiano ed europeo.

**Orizzonte temporale:** Il sistema fornisce predizioni per partenze nelle successive 72 ore.

---

## 2. Decisioni architetturali

### 2.1 Approccio adottato: Smart Heuristics con calibrazione

Il sistema di predizione di Logintel si basa su un approccio denominato "Smart Heuristics con calibrazione", che combina regole euristiche sofisticate con un meccanismo di apprendimento continuo.

#### Struttura delle heuristics

A differenza di regole semplici che associano una condizione a un valore fisso, le Smart Heuristics sono funzioni multifattoriali. La formula generale per il calcolo del ritardo su un segmento di percorso è:

```
ritardo = impatto_base × severità × F_strada × F_altitudine × F_temporale × C_calibrazione
```

| Componente | Descrizione | Range tipico |
|---|---|---|
| impatto_base | Ritardo base associato alla condizione meteo (min/100km) | 0 - 60 |
| severità | Moltiplicatore legato all'intensità del fenomeno | 0.5 - 2.0 |
| F_strada | Fattore legato al tipo di strada | 0.8 - 1.8 |
| F_altitudine | Fattore legato alla quota del segmento | 1.0 - 2.0 |
| F_temporale | Fattore legato a orario, giorno, stagione | 0.7 - 1.6 |
| C_calibrazione | Coefficiente appreso dal sistema | 0.5 - 2.0 |

#### Meccanismo di calibrazione

I coefficienti di calibrazione partono da valori iniziali basati su letteratura di settore e stime ragionate. Man mano che il sistema viene utilizzato e i clienti forniscono feedback sui ritardi effettivi, un algoritmo di calibrazione confronta le predizioni con la realtà e aggiusta gradualmente i coefficienti.

Il risultato è un sistema che:

- Funziona in modo ragionevole fin dal primo giorno
- Migliora progressivamente con l'uso
- Si adatta alle specificità operative di ciascun cliente
- Beneficia dei dati aggregati di tutti i clienti (effetti di rete)

### 2.2 Approcci valutati e non adottati

#### Machine Learning su dati storici

L'approccio ideale prevederebbe l'addestramento di modelli ML su dataset storici che correlano condizioni meteorologiche a ritardi effettivi.

**Motivazioni per la non adozione:**

- Non esistono dataset pubblici di correlazione meteo-ritardi logistici
- Problema di cold start: senza dati non c'è modello, senza modello non ci sono clienti
- Complessità MLOps non giustificata in assenza di dati di qualità

#### Ensemble di fonti meteorologiche

È stato valutato l'utilizzo combinato di multiple API meteorologiche.

**Motivazioni per la non adozione:**

- Open-Meteo già implementa internamente un approccio ensemble
- Complessità di integrazione non giustifica il beneficio marginale
- Le API gratuite hanno limiti di utilizzo stringenti

#### Dati di traffico real-time

È stata valutata l'integrazione di API di traffico (Google Maps, HERE, TomTom).

**Motivazioni per la non adozione nella prima release:**

- Costi significativi ($5-10 per 1000 richieste)
- Dati real-time hanno valore predittivo limitato per partenze future
- Diluirebbe il focus sul core value proposition

---

## 3. Data sources

### 3.1 Fonti dati primarie

La prima release utilizza esclusivamente fonti dati gratuite o a costo zero.

| Fonte | Utilizzo | Costo | Note |
|---|---|---|---|
| Open-Meteo | Previsioni meteo orarie 7gg | Gratuito | No rate limit, fair use |
| OpenRouteService | Calcolo percorsi e durate | Gratuito | 2000 req/day free tier |
| Open-Elevation | Dati altitudine percorso | Gratuito | Self-hosting consigliato |
| OSM / Overpass | Caratteristiche strade | Gratuito | Fair use policy |

### 3.2 Fonti valutate e non adottate

| Fonte | Costo | Motivo esclusione |
|---|---|---|
| Google Maps API | $5-10 per 1000 req | Costi proibitivi, ORS equivalente per routing |
| HERE Traffic | $0.50-5 per 1000 req | Traffico real-time non predittivo, fase 2 |
| Satellite imagery | $500-5000/mese | Richiede ML/CV, non per MVP |
| TomTom Traffic | Enterprise pricing | Overkill per prima release |

---

## 4. Architettura del sistema

### 4.1 Overview dei componenti

| Layer | Responsabilità | Tecnologie |
|---|---|---|
| **API Layer** | REST endpoints, auth, rate limiting | FastAPI, JWT, OpenAPI |
| **Data Layer** | Fetch esterni, caching, normalizzazione | Redis, HTTP clients |
| **Prediction Engine** | Calcolo delay, confidence, alternatives | Python, NumPy |
| **Learning Layer** | Logging, feedback, calibrazione | PostgreSQL, background jobs |

### 4.2 Stack tecnologico

| Componente | Tecnologia | Motivazione |
|---|---|---|
| Linguaggio | Python 3.11+ | Ecosistema maturo, facilità manutenzione |
| Framework API | FastAPI | Performance, OpenAPI automatica |
| Piattaforma DB/Auth | Supabase | PostgreSQL gestito, auth integrata |
| Cache | Redis (Upstash) | Performance, TTL nativo |
| Hosting | Railway | Deploy semplice, scaling |

### 4.3 Infrastruttura e deployment

| Servizio | Funzione | Free tier | Tier pagamento |
|---|---|---|---|
| Railway | Hosting applicazione | 500 ore/mese | $5/mese + uso |
| Supabase | Database, auth | 500MB DB | $25/mese |
| Upstash | Redis gestito | 10k cmd/day | Pay per use |
| Cloudflare | DNS, SSL, DDoS | Illimitato base | Opzionale |

### 4.4 Flusso di elaborazione di una predizione

1. Ricezione e validazione della richiesta
2. Calcolo del percorso (OpenRouteService)
3. Campionamento del percorso (ogni 50 km)
4. Raccolta dati meteorologici (Open-Meteo)
5. Arricchimento contestuale (altitudine, tipo strada)
6. Applicazione delle heuristics
7. Calcolo confidence score
8. Calcolo percorsi alternativi (se richiesti)
9. Logging e risposta

**Tempi di risposta target:** < 1.500 ms (p95) con cache popolata.

### 4.5 Gestione degli errori e resilienza

Il sistema implementa strategie di resilienza per ogni servizio esterno:

- **Retry con backoff:** Fino a 3 tentativi con attesa esponenziale
- **Circuit breaker:** Apertura dopo 5 errori consecutivi, timeout 30s
- **Fallback:** Degradazione graceful per dati non critici
- **Timeout:** Configurati per ogni servizio (3-10 secondi)

### 4.6 Sicurezza e autenticazione

L'autenticazione è gestita tramite Supabase Auth con supporto per:

- JWT Bearer token per applicazioni web
- API Key per integrazioni server-to-server

Tutte le comunicazioni sono cifrate (TLS 1.2+). I dati sono isolati per organizzazione.

---

## 5. Requisiti non funzionali

### 5.1 Performance

| Metrica | Target | Condizione |
|---|---|---|
| Latenza p50 | < 800 ms | Cache popolata |
| Latenza p95 | < 1.500 ms | Cache popolata |
| Latenza p99 | < 3.000 ms | Cache popolata |
| Throughput | > 10 req/s | Sustained |
| Cache hit rate | > 60% | Dopo 1 settimana |

### 5.2 Availability e uptime

| Metrica | Target | Note |
|---|---|---|
| Uptime mensile | 99.5% | Max 3.6 ore downtime/mese |
| MTTR | < 1 ora | Mean Time To Recovery |
| Incident P1/mese | < 1 | Incidenti critici |

### 5.3 Scalabilità

La prima release è dimensionata per:

- Fino a 50 clienti attivi
- Fino a 5.000 predizioni giornaliere
- 12 mesi di retention dati

L'architettura supporta scaling orizzontale per volumi superiori.

### 5.4 Data retention e privacy

| Tipo dato | Retention | Motivazione |
|---|---|---|
| Predizioni | 12 mesi | Calibrazione e analytics |
| Feedback | Permanente | Valore per calibrazione |
| Log applicativi | 30 giorni | Troubleshooting |
| Log di audit | 90 giorni | Requisiti sicurezza |

Tutti i dati sono conservati in data center europei (conformità GDPR).

---

## 6. Prediction Engine

### 6.1 Formula generale di calcolo

Il ritardo di un singolo segmento è calcolato come:

```
ritardo_segmento = impatto_base × severità × F_strada × F_altitudine × F_temporale × C_calibrazione
```

Il ritardo effettivo è proporzionale alla lunghezza del segmento:

```
ritardo_effettivo = ritardo_segmento × (lunghezza_km / 100)
```

Il ritardo totale è la somma dei ritardi di tutti i segmenti.

### 6.2 Regole di impatto meteorologico

#### Precipitazioni — Pioggia

| Intensità | Soglia | Impatto base | Note |
|---|---|---|---|
| Leggera | 0.1 - 2.5 mm/h | +3 min/100km | Visibilità minima ridotta |
| Moderata | 2.5 - 7.5 mm/h | +8 min/100km | Aquaplaning possibile |
| Forte | 7.5 - 15 mm/h | +15 min/100km | Rallentamenti significativi |
| Molto forte | > 15 mm/h | +25 min/100km | Possibili fermi temporanei |

#### Precipitazioni — Neve

| Intensità | Soglia | Impatto base | Note |
|---|---|---|---|
| Leggera | 0.1 - 1 cm/h | +10 min/100km | Strade trattate |
| Moderata | 1 - 3 cm/h | +25 min/100km | Catene consigliate |
| Forte | 3 - 5 cm/h | +45 min/100km | Catene necessarie |
| Molto forte | > 5 cm/h | +70 min/100km | Chiusure possibili |

#### Vento e visibilità

| Condizione | Soglia | Impatto base |
|---|---|---|
| Vento forte | 40-60 km/h | +6 min/100km |
| Vento molto forte | 60-80 km/h | +12 min/100km |
| Tempesta | > 80 km/h | +25 min/100km |
| Nebbia leggera | 200-500m vis | +8 min/100km |
| Nebbia moderata | 50-200m vis | +18 min/100km |
| Nebbia fitta | < 50m vis | +35 min/100km |

### 6.3 Fattori di contesto (moltiplicatori)

#### Tipo di strada

| Tipo strada | Fattore | Motivazione |
|---|---|---|
| Autostrada | 0.8 | Manutenzione prioritaria, drenaggio efficiente |
| Strada statale | 1.0 | Baseline di riferimento |
| Strada provinciale | 1.3 | Manutenzione variabile, curve |
| Strada montana | 1.8 | Pendenze, tornanti, microclimi |

#### Altitudine

| Fascia | Fattore | Motivazione |
|---|---|---|
| 0 - 300 m | 1.0 | Baseline, condizioni pianeggianti |
| 300 - 600 m | 1.1 | Leggero incremento rischio ghiaccio |
| 600 - 1000 m | 1.3 | Neve frequente in inverno |
| 1000 - 1500 m | 1.6 | Neve probabile ottobre-aprile |
| > 1500 m | 2.0 | Condizioni severe frequenti |

#### Fattore temporale

| Periodo | Fattore | Motivazione |
|---|---|---|
| Notte (22-06) | 0.7 | Traffico minimo |
| Rush hour (7-9, 17-19) | 1.4 | Meteo amplifica congestione |
| Weekend | 0.85 | Meno traffico commerciale |
| Esodo estivo | 1.5 | Traffico turistico estremo |

### 6.4 Elementi speciali del percorso

| Elemento | Impatto | Modifica | Implementazione |
|---|---|---|---|
| Tunnel > 500m | Tutti i fattori meteo | = 0 per lunghezza tunnel | `weather_multiplier = 0.0` |
| Ponte/viadotto | Vento, neve | × 1.3 (midpoint range 1.2-1.5) | Solo se condizioni vento o neve presenti |
| Valico | Neve | × 1.9 (midpoint range 1.8-2.0) | Solo se condizioni neve presenti |
| Zona nebbia | Nebbia | × 1.2 - 1.4 | Skip per MVP (no tag OSM diretto) |
| Centro urbano | F_temporale | × 1.3 | `time_multiplier = 1.3` |

**Fonte dati:** Overpass API (OpenStreetMap) — gratuita, no API key. Query: `tunnel=yes`, `bridge=yes`, `mountain_pass=yes`, `place=city|town`. Cache 7 giorni (infrastruttura statica).

**Precedenza tunnel:** Se un segmento attraversa un tunnel > 500m, il weather_multiplier è 0.0 indipendentemente da altri elementi (ponte, valico). Il time_multiplier di urban center si applica sempre.

**Graceful degradation:** Se Overpass API è down, gli elementi speciali vengono ignorati (lista vuota) e le predizioni restano invariate.

### 6.6 Rotte alternative

Il sistema può suggerire percorsi alternativi quando il ritardo previsto sulla rotta principale supera una soglia configurabile.

**Principi di design:**

- **Soglia di attivazione:** Le alternative vengono calcolate solo se il ritardo previsto sulla rotta principale supera 20 minuti. Sotto questa soglia, il valore aggiunto delle alternative è marginale.
- **Stessa chiamata ORS:** Le rotte alternative sono richieste nella stessa chiamata API a OpenRouteService (parametro `alternative_routes`), quindi non consumano quota aggiuntiva.
- **Max 2 alternative:** ORS restituisce fino a 2 percorsi alternativi oltre alla rotta principale.
- **Pipeline leggera:** Le alternative utilizzano la stessa pipeline di calcolo della rotta principale (sampling, elevazione, meteo, heuristics) ma senza costruire il dettaglio per segmento. Si calcola solo il ritardo totale.

**Parametri ORS per alternative:**

| Parametro | Valore | Descrizione |
|---|---|---|
| target_count | 2 | Numero di alternative richieste |
| share_factor | 0.6 | Massima sovrapposizione con la rotta principale (60%) |
| weight_factor | 1.4 | Preferenza per rotte significativamente diverse |

**Risposta API:**

Quando `include_alternatives: true` e il ritardo supera la soglia, la risposta include un array `alternatives` con:

| Campo | Descrizione |
|---|---|
| route_index | Indice dell'alternativa (1, 2) |
| total_delay_minutes | Ritardo previsto sull'alternativa |
| duration_minutes | Tempo di percorrenza base (senza ritardo) |
| distance_km | Distanza totale dell'alternativa |
| delay_savings_minutes | Risparmio di ritardo (main_delay - alt_delay) |
| summary | Descrizione leggibile |

Se il ritardo è sotto soglia o non ci sono alternative valide, l'array è vuoto (`[]`).

### 6.5 Calcolo del confidence score

Il confidence score è calcolato come media ponderata di quattro componenti:

```
confidence = (C_orizzonte × 0.40) + (C_stabilità × 0.30) + (C_storico × 0.20) + (C_dati × 0.10)
```

| Componente | Peso | Logica |
|---|---|---|
| Orizzonte temporale | 40% | <6h: 95%, 6-24h: 80%, 24-48h: 65%, >48h: 50% |
| Stabilità meteo | 30% | Varianza previsioni ore successive |
| Accuratezza storica | 20% | Performance su condizioni simili |
| Completezza dati | 10% | % dati disponibili |

**Interpretazione:** 85-100% alta, 70-84% buona, 55-69% moderata, <55% bassa affidabilità.

---

## 7. Calibration System

### 7.1 Architettura del sistema di apprendimento

Il Calibration System permette a Logintel di migliorare le predizioni nel tempo. I componenti principali sono:

- **Prediction Logger:** Salva ogni predizione con tutti i parametri di input
- **Feedback Collector:** Raccoglie i ritardi reali riportati dai clienti
- **Coefficient Tuner:** Analizza i dati e calcola nuovi coefficienti

### 7.2 Logging delle predizioni

Ogni predizione registra: coordinate, condizioni meteo per segmento, fattori applicati, ritardo predetto, confidence, versione coefficienti.

Volume stimato: ~2 KB per predizione, ~100 MB/mese con volumi target.

### 7.3 Raccolta feedback

Il feedback è raccolto tramite API dedicata. La prima release supporta solo feedback manuale. Validazioni:

- Feedback entro 7 giorni dalla departure_time
- actual_delay_minutes tra -60 e 1440
- Una sola submission per predizione

**Target feedback rate:** > 30% delle predizioni.

### 7.4 Algoritmo di calibrazione dei coefficienti

La calibrazione avviene settimanalmente con algoritmo conservativo:

```
nuovo_coeff = vecchio_coeff + 0.15 × (fattore_correzione - 1.0) × vecchio_coeff
```

**Prerequisiti:** ≥ 20 feedback, ≥ 14 giorni di dati, ≥ 3 valori distinti di condizioni.

**Bounds:** Coefficienti limitati a [0.5, 2.0] per evitare derive.

**Versioning:** Ogni calibrazione crea nuova versione, rollback possibile.

---

## 8. Specifiche API

### 8.1 Informazioni generali

| Aspetto | Specifica |
|---|---|
| Base URL | https://api.logintel.io/v1 |
| Autenticazione | Bearer token (JWT) o API Key (X-API-Key header) |
| Formato | JSON (Content-Type: application/json) |
| Encoding | UTF-8 |
| Date/time | ISO 8601 con timezone |
| Versioning | URL path (/v1/) |

### 8.2 Endpoints principali

| Metodo | Endpoint | Descrizione |
|---|---|---|
| POST | /v1/predictions | Crea una nuova predizione |
| GET | /v1/predictions/{id} | Recupera una predizione |
| GET | /v1/predictions | Lista predizioni |
| POST | /v1/predictions/{id}/feedback | Invia feedback |
| GET | /v1/analytics/accuracy | Metriche accuratezza |
| GET | /v1/health | Stato servizio |

### 8.3 Gestione degli errori

Tutte le risposte di errore seguono un formato consistente con codice, messaggio e dettagli.

| HTTP | Codice errore | Descrizione |
|---|---|---|
| 400 | INVALID_REQUEST | Parametri mancanti o non validi |
| 401 | UNAUTHORIZED | Token mancante o invalido |
| 404 | NOT_FOUND | Risorsa non trovata |
| 429 | RATE_LIMIT_EXCEEDED | Quota superata |
| 502 | SERVICE_UNAVAILABLE | Servizio esterno non disponibile |

### 8.4 Rate limiting e quote

| Tier | Richieste/ora | Predizioni/mese |
|---|---|---|
| Free | 50 | 1.000 |
| Starter | 200 | 10.000 |
| Professional | 1.000 | 50.000 |
| Enterprise | Personalizzato | Personalizzato |

---

## 9. Logging e monitoring

### 9.1 Strategia di logging

Tutti i log sono in formato JSON strutturato con: timestamp, level, message, request_id, organization_id.

Livelli: ERROR (attenzione richiesta), WARN (anomalie gestite), INFO (eventi operativi), DEBUG (troubleshooting).

Dati sensibili (token, password, coordinate esatte) non sono mai loggati.

### 9.2 Metriche e alerting

**Alert critici (notifica immediata):**

- Error rate > 10% per 2 minuti
- Health check fallito 3 volte consecutive
- Circuit breaker OPEN > 5 minuti

**Alert warning (orario lavorativo):**

- Latenza p95 > 3.000ms per 10 minuti
- Memory usage > 75% per 15 minuti
- Cache hit rate < 30% per 1 ora

### 9.3 Gestione dei limiti delle API esterne

OpenRouteService ha il limite più stringente (2.000 req/giorno). Strategie di mitigazione:

- Cache aggressiva (TTL 24h per percorsi): risparmio 60-70%
- Route hash per coordinate vicine: risparmio 10-15%
- Alternative solo se delay > 20 minuti: risparmio 20-30%

Con cache efficace, il sistema supporta ~4.400 predizioni/giorno con il free tier.

---

## 10. Documentazione per i clienti

### 10.1 Struttura della documentazione API

La documentazione è generata automaticamente dalla specifica OpenAPI e include:

- Guida introduttiva e quick start
- API Reference completa con esempi
- Guide pratiche per integrazione
- SDK e librerie (Python, JavaScript, cURL)
- FAQ e troubleshooting

### 10.2 Esempi di integrazione

Sono forniti esempi funzionanti in Python, JavaScript/Node.js, cURL e PHP.

È disponibile un ambiente sandbox (sandbox.logintel.io) per testing con risposte deterministiche.

### 10.3 FAQ e troubleshooting

La documentazione include risposte alle domande frequenti su:

- Funzionamento generale e accuratezza
- Autenticazione e gestione credenziali
- Utilizzo API e limiti
- Feedback e calibrazione
- Troubleshooting errori comuni

---

## 11. Metriche di successo

### 11.1 KPI tecnici

| Metrica | Target 3 mesi | Target 6 mesi | Target 12 mesi |
|---|---|---|---|
| MAE (minuti) | < 15 | < 12 | < 10 |
| Within 10 min | > 50% | > 60% | > 70% |
| Within 20 min | > 75% | > 80% | > 85% |
| Latenza p95 | < 1.500 ms | < 1.500 ms | < 1.500 ms |
| Error rate | < 0.5% | < 0.5% | < 0.5% |
| Uptime | > 99.5% | > 99.5% | > 99.5% |

### 11.2 KPI di business

| Metrica | Target 6 mesi | Target 12 mesi |
|---|---|---|
| Organizzazioni attive | 30 | 80 |
| Predizioni/mese | 5.000 | 15.000 |
| Feedback rate | > 30% | > 30% |
| MRR | €2.000 | €8.000 |
| Clienti paganti | 10 | 35 |
| Retention 90 giorni | > 50% | > 50% |

---

## 12. Appendice

### 12.1 Glossario

| Termine | Definizione |
|---|---|
| API | Application Programming Interface |
| Calibrazione | Processo di aggiustamento coefficienti basato su feedback |
| Circuit breaker | Pattern che interrompe chiamate a servizi non disponibili |
| Confidence score | Punteggio (0-100%) che indica affidabilità predizione |
| ETA | Estimated Time of Arrival |
| Heuristics | Regole basate su esperienza per calcolare ritardi |
| MAE | Mean Absolute Error — media errori assoluti |
| MAPE | Mean Absolute Percentage Error |
| MRR | Monthly Recurring Revenue |
| Smart Heuristics | Approccio Logintel: regole multifattoriali + calibrazione |

### 12.2 Riferimenti

**Documentazione API esterne:**

- Open-Meteo: https://open-meteo.com/en/docs
- OpenRouteService: https://openrouteservice.org/dev/
- Open-Elevation: https://open-elevation.com/
- Overpass API: https://wiki.openstreetmap.org/wiki/Overpass_API

**Stack tecnologico:**

- FastAPI: https://fastapi.tiangolo.com/
- Supabase: https://supabase.com/docs
- Railway: https://docs.railway.app/

**Standard:**

- OpenAPI 3.0: https://spec.openapis.org/oas/v3.0.3
- ISO 8601: formato date e orari
- RFC 7519: JSON Web Token (JWT)
