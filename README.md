# 🗳️ Election Process Assistant

A **production-ready** async REST API built with **FastAPI** and deployed on **Google Cloud Run** that wraps the [Google Civic Information API v2](https://developers.google.com/civic-information). It provides polling locations, ballot contests, election metadata, and representative information for any US civic address.

---

## Table of Contents

- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Local Development Setup](#local-development-setup)
- [Environment Variables](#environment-variables)
- [Running Locally](#running-locally)
- [API Reference](#api-reference)
- [Running Tests](#running-tests)
- [Docker Build](#docker-build)
- [Deploying to Cloud Run](#deploying-to-cloud-run)
- [Security Notes](#security-notes)

---

## Architecture

```
Client → Cloud Run (FastAPI / Uvicorn)
              │
              ├── app/api/endpoints.py        ← Controllers (route handlers)
              ├── app/services/election_logic.py  ← Service layer (business logic)
              ├── app/services/civic_api_client.py ← httpx async client + retry
              └── app/models/schemas.py       ← Pydantic request/response models
```

**Design principles:**
- **Stateless** – No in-memory session state; safe for horizontal Cloud Run scaling.
- **Async I/O** – All Civic API calls use `httpx.AsyncClient` to avoid blocking.
- **Fail-fast** – Missing `CIVIC_API_KEY` aborts startup (no silent failures).
- **Structured logging** – JSON logs are natively parsed by Google Cloud Logging.
- **Exponential backoff** – Transient Civic API errors trigger up to 3 retries with full-jitter backoff.

---

## Project Structure

```
ElectionApp-PromptWar/
├── app/
│   ├── api/
│   │   ├── __init__.py
│   │   └── endpoints.py        # Route controllers
│   ├── models/
│   │   ├── __init__.py
│   │   └── schemas.py          # Pydantic request/response models
│   ├── services/
│   │   ├── __init__.py
│   │   ├── civic_api_client.py # Async Google Civic API wrapper
│   │   └── election_logic.py   # Business logic / data mapping
│   └── main.py                 # FastAPI app factory & entry point
├── tests/
│   └── test_main.py            # pytest test suite
├── .dockerignore
├── Dockerfile                  # Multi-stage build
├── pytest.ini
├── requirements.txt            # Pinned production + test deps
└── README.md
```

---

## Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.12+ |
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
python3.12 -m venv .venv
source .venv/bin/activate

# 3. Install all dependencies (including test deps)
pip install -r requirements.txt

# 4. Configure your API key
cp .env.example .env          # Then edit .env and add your key
# OR export directly:
export CIVIC_API_KEY="your-google-civic-api-key-here"
```

Create a `.env` file (never commit this to Git):

```ini
CIVIC_API_KEY=your-google-civic-api-key-here
LOG_LEVEL=INFO
ALLOWED_ORIGINS=*
```

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CIVIC_API_KEY` | **Yes** | – | Google Civic Information API key |
| `PORT` | No | `8080` | Port the server listens on |
| `LOG_LEVEL` | No | `INFO` | Python log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `ALLOWED_ORIGINS` | No | `*` | Comma-separated CORS allowed origins |
| `ENV` | No | `production` | Set to `development` to enable Uvicorn hot-reload |

---

## Running Locally

```bash
# With python-dotenv loading .env automatically:
python -m app.main

# Or directly via uvicorn:
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

Visit the interactive API docs at: **http://localhost:8080/docs**

---

## API Reference

### `GET /api/v1/health`
Service health check. Used by Cloud Run liveness/readiness probes.

**Response `200`:**
```json
{ "status": "ok", "version": "1.0.0" }
```

---

### `GET /api/v1/elections/list`
Returns all elections available in the Civic API.

**Response `200`:**
```json
{
  "elections": [
    {
      "id": "2000",
      "name": "VIP Test Election",
      "election_day": "2021-06-06",
      "ocd_division_id": "ocd-division/country:us"
    }
  ]
}
```

---

### `POST /api/v1/elections/info`
Returns voter information (polling locations, contests) for a civic address.

**Request body:**
```json
{
  "address": "1600 Amphitheatre Pkwy, Mountain View, CA 94043",
  "election_id": 2000
}
```

**Response `200`:** Election metadata, normalized address, polling locations, contests.

**Error codes:**

| HTTP | Code | Cause |
|------|------|-------|
| 422 | `VALIDATION_ERROR` | Invalid/disallowed characters in address |
| 400 | `UPSTREAM_CLIENT_ERROR` | Civic API rejected the request |
| 429 | `RATE_LIMITED` | Civic API rate limit hit |
| 502 | `UPSTREAM_SERVER_ERROR` | Civic API server error |
| 504 | `UPSTREAM_TIMEOUT` | Civic API timed out |
| 500 | `MISSING_API_KEY` | `CIVIC_API_KEY` env var not set |

---

### `POST /api/v1/representatives`
Returns elected officials for a civic address.

**Request body:**
```json
{
  "address": "1600 Pennsylvania Ave NW, Washington, DC 20500",
  "include_offices": true
}
```

**Response `200`:** Normalized address, offices, and officials.

---

## Running Tests

```bash
# Install dependencies (if not already done)
pip install -r requirements.txt

# Run all tests
pytest

# Run with coverage report
pip install pytest-cov
pytest --cov=app --cov-report=term-missing
```

The test suite covers:
- ✅ Health endpoint smoke test
- ✅ Successful election list response
- ✅ Successful voter info response
- ✅ Successful representatives response
- ✅ Malformed address validation (SQL injection, XSS, path traversal)
- ✅ API timeout simulation → 504
- ✅ API rate-limit (429) simulation → 429
- ✅ API server error (500) simulation → 502
- ✅ Missing API key → 500
- ✅ Representatives malformed address → 422

---

## Docker Build

```bash
# Build the multi-stage image
docker build -t election-assistant:latest .

# Inspect the final image size (target: < 100 MB)
docker image inspect election-assistant:latest --format='{{.Size}}' | \
    awk '{printf "Image size: %.1f MB\n", $1/1024/1024}'

# Run the container locally
docker run --rm \
  -e CIVIC_API_KEY="your-api-key" \
  -e LOG_LEVEL=DEBUG \
  -p 8080:8080 \
  election-assistant:latest
```

---

## Deploying to Cloud Run

### 1. Authenticate with Google Cloud

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
```

### 2. Enable required APIs

```bash
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com
```

### 3. Store the API key in Secret Manager (recommended)

```bash
echo -n "your-google-civic-api-key" | \
  gcloud secrets create civic-api-key --data-file=-
```

### 4. Build and push to Artifact Registry

```bash
# Create a repository (one-time)
gcloud artifacts repositories create election-app \
  --repository-format=docker \
  --location=us-central1

# Configure Docker auth
gcloud auth configure-docker us-central1-docker.pkg.dev

# Build and push
IMAGE="us-central1-docker.pkg.dev/YOUR_PROJECT_ID/election-app/election-assistant:latest"
docker build -t "$IMAGE" .
docker push "$IMAGE"
```

### 5. Deploy to Cloud Run

```bash
gcloud run deploy election-assistant \
  --image "$IMAGE" \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-secrets="CIVIC_API_KEY=civic-api-key:latest" \
  --set-env-vars="LOG_LEVEL=INFO,ALLOWED_ORIGINS=https://yourdomain.com" \
  --memory 256Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 10 \
  --concurrency 80 \
  --timeout 30
```

### 6. Verify the deployment

```bash
SERVICE_URL=$(gcloud run services describe election-assistant \
  --platform managed --region us-central1 \
  --format 'value(status.url)')

curl "$SERVICE_URL/api/v1/health"
```

---

## Security Notes

- **API keys**: Always use Google Secret Manager or Cloud Run secret injection. Never bake keys into the image or source code.
- **Input sanitization**: All addresses are validated with a strict regex allowlist (`[A-Za-z0-9 ,.\-'#]`) before being forwarded to the Civic API.
- **Non-root user**: The Docker image runs as `appuser` (no root privileges).
- **CORS**: Set `ALLOWED_ORIGINS` to your specific frontend domain in production (not `*`).
- **Rate limits**: The service forwards 429 responses from the Civic API so clients can implement their own back-off logic.

---

## License

MIT © 2024 Election Process Assistant Contributors
