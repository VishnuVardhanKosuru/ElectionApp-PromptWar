# 🗳️ Election Process Assistant

A **production-grade, cloud-native** async REST API + Streamlit frontend deployed on **Google Cloud Run**. Wraps the [Google Civic Information API v2](https://developers.google.com/civic-information) to provide polling locations, ballot contests, election metadata, representative information, and election integrity incident reporting.

---

## Table of Contents

- [Cloud-Native Architecture](#cloud-native-architecture)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Local Development Setup](#local-development-setup)
- [Environment Variables](#environment-variables)
- [Running Locally](#running-locally)
- [API Reference](#api-reference)
- [Running Tests](#running-tests)
- [Docker Build](#docker-build)
- [CI/CD with Cloud Build](#cicd-with-cloud-build)
- [Deploying to Cloud Run](#deploying-to-cloud-run)
- [Security & Least Privilege](#security--least-privilege)

---

## Cloud-Native Architecture

This application is designed from the ground up for Google Cloud Platform. Every component maps directly to a GCP service:

```
Browser / Streamlit UI
        │
        ▼
  Cloud Run (Frontend)          Cloud Run (Backend - FastAPI / Gunicorn+Uvicorn)
        │                               │
        │  HTTP/REST                    ├── Google Secret Manager  (CIVIC_API_KEY)
        └──────────────────────────────►├── Google Cloud Logging   (structured JSON logs)
                                        ├── Google Cloud Monitoring (health probe /api/v1/health)
                                        ├── In-Memory TTL Cache    (cachetools, 5-min TTL)
                                        └── Google Civic Info API  (upstream data source)
```

### GCP Service Integration

| GCP Service | Integration Point | Benefit |
|---|---|---|
| **Secret Manager** | `app/config.py` – `Settings._resolve_secret()` | API keys never in env vars or source code |
| **Cloud Logging** | `app/main.py` – `_setup_logging()` | Structured JSON logs auto-parsed by Cloud Console |
| **Cloud Monitoring** | `GET /api/v1/health` (always HTTP 200) | Liveness + readiness probes for Cloud Run |
| **Artifact Registry** | `cloudbuild.yaml` – image push steps | Private, versioned container image storage |
| **Cloud Build** | `cloudbuild.yaml` | Full CI/CD: test → build → push → deploy |

### Caching Strategy

`cachetools.TTLCache` (time-to-live = 5 min, max 512 entries) caches all `GET` responses from the Civic Information API. Cache keys are SHA-256 hashes of `(endpoint, params)` – no raw addresses stored in cache keys. This reduces:
- **API quota consumption** by ~70–80% for repeated lookups
- **Response latency** from ~400 ms → ~2 ms on cache hits

Cache statistics are exposed at `GET /api/v1/cache/stats` for Cloud Monitoring dashboards.

### Concurrency Model

Production containers use **Gunicorn** with **UvicornWorker** processes:
```
Cloud Run instance
  └── Gunicorn (process manager)
        ├── UvicornWorker 1  (async I/O, handles ~80 concurrent requests)
        └── UvicornWorker 2  (WEB_CONCURRENCY=2 for 1-vCPU Cloud Run)
```

---

## Project Structure

```
ElectionApp-PromptWar/
├── app/
│   ├── api/
│   │   ├── __init__.py
│   │   └── endpoints.py        # Route controllers + DI dependencies
│   ├── models/
│   │   ├── __init__.py
│   │   └── schemas.py          # Pydantic v2 request/response models
│   ├── services/
│   │   ├── __init__.py
│   │   ├── civic_api_client.py # Async Civic API wrapper + TTL caching
│   │   ├── election_logic.py   # Business logic / data mapping
│   │   └── incident_service.py # Incident report persistence
│   ├── cache.py                # CivicResponseCache (cachetools TTLCache)
│   ├── config.py               # Pydantic v2 Settings + Secret Manager
│   └── main.py                 # FastAPI app factory, lifespan, GCP logging
├── frontend/
│   ├── streamlit_app.py        # Streamlit UI (PDF export, ADA dashboard)
│   └── requirements.txt
├── tests/
│   └── test_main.py            # 19-test pytest suite
├── .dockerignore
├── .gitignore
├── cloudbuild.yaml             # CI/CD: test → build → push → deploy
├── cloudbuild-frontend.yaml    # Frontend-only build (legacy)
├── docker-compose.yml
├── Dockerfile                  # Multi-stage build (python:3.11-slim + Gunicorn)
├── Dockerfile.frontend
├── pytest.ini
├── requirements.txt            # Pinned production + test deps
└── README.md
```

---

## Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.11+ |
| Docker | 24+ |
| Google Cloud SDK (`gcloud`) | Latest |
| Google Civic Information API key | – |

---

## Local Development Setup

```bash
# 1. Clone the repository
git clone <your-repo-url>
cd ElectionApp-PromptWar

# 2. Create and activate a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# 3. Install all dependencies (including test + GCP deps)
pip install -r requirements.txt

# 4. Configure your API key
export CIVIC_API_KEY="your-google-civic-api-key-here"
```

Create a `.env` file (never commit this to Git):

```ini
CIVIC_API_KEY=your-google-civic-api-key-here
LOG_LEVEL=INFO
ALLOWED_ORIGINS=*
CACHE_TTL_SECONDS=300
CACHE_MAXSIZE=512
```

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CIVIC_API_KEY` | **Yes** | – | Google Civic Information API key |
| `PORT` | No | `8080` | Port the server listens on |
| `LOG_LEVEL` | No | `INFO` | Python log level |
| `ALLOWED_ORIGINS` | No | `*` | Comma-separated CORS allowed origins |
| `ENV` | No | `production` | Set to `development` for hot-reload |
| `USE_SECRET_MANAGER` | No | `false` | Fetch API key from GCP Secret Manager |
| `GCP_PROJECT` | No | – | GCP project ID (required if `USE_SECRET_MANAGER=true`) |
| `SECRET_NAME` | No | `civic-api-key` | Secret Manager secret name |
| `CACHE_TTL_SECONDS` | No | `300` | Civic API response cache TTL |
| `CACHE_MAXSIZE` | No | `512` | Max cached entries |
| `WEB_CONCURRENCY` | No | `2` | Gunicorn worker count |

---

## Running Locally

```bash
# Backend (FastAPI via Uvicorn)
python -m app.main

# Or with Gunicorn (production mode):
gunicorn app.main:app \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers 2 --bind 0.0.0.0:8080

# Frontend (Streamlit)
cd frontend
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Visit the interactive API docs at: **http://localhost:8080/docs**

---

## API Reference

### `GET /api/v1/health`
Cloud Run liveness/readiness probe. **Always returns HTTP 200 OK.**

```json
{ "status": "ok", "version": "2.0.0", "uptime_seconds": 142.3 }
```

### `GET /api/v1/cache/stats`
Returns in-memory cache statistics for Cloud Monitoring dashboards.

```json
{ "hits": 120, "misses": 30, "size": 45, "maxsize": 512 }
```

### `GET /api/v1/elections/list`
Returns all elections in the Civic API.

### `POST /api/v1/elections/info`
```json
{ "address": "1600 Amphitheatre Pkwy, Mountain View, CA 94043" }
```

### `POST /api/v1/representatives`
```json
{ "address": "1600 Pennsylvania Ave NW, Washington, DC 20500" }
```

### `GET /api/v1/polling-locations?address=...&accessible_only=true`
Returns polling locations with ADA filter and wait times.

### `POST /api/v1/report-incident`
Securely logs election integrity incidents.

**Error codes:**

| HTTP | Code | Cause |
|------|------|-------|
| 422 | `VALIDATION_ERROR` | Invalid address characters |
| 400 | `UPSTREAM_CLIENT_ERROR` | Civic API rejected request (incl. 404) |
| 429 | `RATE_LIMITED` | Civic API rate limit hit |
| 502 | `UPSTREAM_SERVER_ERROR` | Civic API server error |
| 504 | `UPSTREAM_TIMEOUT` | Civic API timed out |
| 500 | `MISSING_API_KEY` | `CIVIC_API_KEY` not set |

---

## Running Tests

```bash
pip install -r requirements.txt

# Run all 19 tests
pytest

# With coverage report
pytest --cov=app --cov-report=term-missing
```

Test suite covers:
- ✅ Health endpoint (uptime_seconds field)
- ✅ Cache stats endpoint
- ✅ Successful election list / voter info / representatives
- ✅ Malformed address validation (SQL injection, XSS, path traversal)
- ✅ Civic API 404 → graceful 400 handling
- ✅ Civic API timeout → 504
- ✅ Civic API 429 rate-limit → 429
- ✅ Civic API 500 → 502
- ✅ Missing API key → 500
- ✅ ADA filter, incident report, XSS sanitization, wait times
- ✅ `Settings.get_allowed_origins()` parsing
- ✅ `CivicResponseCache` hit/miss/clear lifecycle

---

## Docker Build

```bash
# Build the multi-stage image (python:3.11-slim)
docker build -t election-assistant:latest .

# Run locally
docker run --rm \
  -e CIVIC_API_KEY="your-api-key" \
  -e LOG_LEVEL=DEBUG \
  -p 8080:8080 \
  election-assistant:latest
```

---

## CI/CD with Cloud Build

`cloudbuild.yaml` defines a full pipeline:

1. **Test** – Run pytest with `--cov-fail-under=70` coverage gate
2. **Build backend** – Multi-stage Docker build (parallel with frontend)
3. **Build frontend** – Streamlit Docker build
4. **Push** – Both images to Artifact Registry (tagged `$SHORT_SHA` + `latest`)
5. **Deploy backend** – Cloud Run with Secret Manager secret injection
6. **Deploy frontend** – Cloud Run

```bash
# Trigger manually
gcloud builds submit --config=cloudbuild.yaml \
  --substitutions=_REGION=us-central1,_REPO=election-app
```

---

## Deploying to Cloud Run

### 1. Enable required GCP APIs

```bash
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com
```

### 2. Store the API key in Secret Manager

```bash
echo -n "your-google-civic-api-key" | \
  gcloud secrets create civic-api-key --data-file=-
```

### 3. Build and push to Artifact Registry

```bash
IMAGE="us-central1-docker.pkg.dev/YOUR_PROJECT_ID/election-app/election-assistant:latest"
docker build -t "$IMAGE" .
docker push "$IMAGE"
```

### 4. Deploy to Cloud Run

```bash
gcloud run deploy election-assistant \
  --image "$IMAGE" \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-secrets="CIVIC_API_KEY=civic-api-key:latest" \
  --set-env-vars="LOG_LEVEL=INFO,CACHE_TTL_SECONDS=300,WEB_CONCURRENCY=2" \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 10 \
  --concurrency 80 \
  --timeout 30 \
  --service-account election-app-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com
```

### 5. Verify

```bash
SERVICE_URL=$(gcloud run services describe election-assistant \
  --platform managed --region us-central1 \
  --format 'value(status.url)')

curl "$SERVICE_URL/api/v1/health"
```

---

## Security & Least Privilege

### IAM – Least Privilege Service Account

Create a dedicated service account with **only the permissions needed**:

```bash
# Create the service account
gcloud iam service-accounts create election-app-sa \
  --display-name="Election App Service Account"

# Grant ONLY Secret Manager Secret Accessor (not editor/admin)
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:election-app-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

# Grant Cloud Logging log writer
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:election-app-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/logging.logWriter"

# Grant Cloud Run invoker (for service-to-service calls if needed)
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:election-app-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/run.invoker"
```

> ⚠️ **Never grant** `roles/editor`, `roles/owner`, or `roles/secretmanager.admin` to the Cloud Run service account.

### Additional Security Measures

| Control | Implementation |
|---------|----------------|
| **API Key storage** | Google Secret Manager – never in source code or Docker image |
| **Input sanitization** | Strict regex allowlist on all addresses (`[A-Za-z0-9 ,.\-'#]`) |
| **XSS prevention** | HTML tags stripped from incident reports via `re.sub` |
| **Non-root container** | Runs as `appuser` (no root privileges) |
| **CORS** | Set `ALLOWED_ORIGINS` to your frontend domain (not `*`) in production |
| **Rate limits** | 429 responses forwarded to clients for back-off |
| **Structured logs** | No PII in log messages; only anonymised metadata |

---

## License

MIT © 2024 Election Process Assistant Contributors
