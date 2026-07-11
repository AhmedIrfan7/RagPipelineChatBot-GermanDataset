# Deployment

The API image contains **only application code**. The confidential data tree
(`data/` — verified prices, knowledge base, source PDFs) and secrets are supplied at
runtime, never baked into the image or committed. So a deploy has three inputs:

1. the **code image** (built from this repo),
2. the **data volume** (`data/processed`, `data/interim`, `data/raw` produced by the
   preprocessing pipeline — see below),
3. the **`.env`** secrets (`OPENAI_API_KEY`, `HANDOFF_EMAIL`, `SCHOOL_NAME`).

## Prepare the data (one-time, offline)

Run the pipeline locally to produce the confidential artifacts the API serves:

```bash
python scripts/01_build_manifest.py     # data/interim/manifest.json
python scripts/03_extract_prices.py     # data/processed/prices/*.json  (+ human sign-off)
python scripts/05_extract_knowledge.py  # data/processed/knowledge/*
python scripts/04_build_store.py        # data/processed/fahrschule.sqlite
```

Place the client PDFs in `data/raw/` first. Keep this `data/` tree private.

## Run locally (Docker Compose)

```bash
cp .env.example .env      # fill in the values
docker compose up --build
# open http://localhost:8123/
```

`docker-compose.yml` mounts `./data` read-only and loads `./.env`.

## Run the image directly

```bash
docker build -t fahrschule-chatbot .
docker run --rm -p 8123:8123 \
  --env-file .env \
  -v "$(pwd)/data:/app/data:ro" \
  fahrschule-chatbot
```

## Hosting

- **VPS / Fly.io / Render / Railway (recommended):** a long-running container with a
  **persistent disk** for `data/` and platform **secrets** for the env vars. Attach the
  data disk at `/app/data`; set `OPENAI_API_KEY` / `HANDOFF_EMAIL` / `SCHOOL_NAME` as
  secrets. Point the platform health check at `/api/health`.
- **Vercel / serverless:** not recommended — the app is a long-running server that needs
  a persistent, private data volume, which serverless platforms don't provide well.
- **Embedding the widget:** the site can iframe `/` or call the JSON API
  (`/api/session`, `/api/message`, `/api/document/{variant}`) directly. CORS is open by
  default — restrict `allow_origins` in `api.py` to the client's domain in production.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET  | `/` | Embeddable web chat widget |
| POST | `/api/session?language=de\|en` | Start a session, returns greeting |
| POST | `/api/message` | `{session_id, text?, option_key?, language?}` → next reply |
| GET  | `/api/document/{variant_key}` | Download the official price-sheet PDF (current only) |
| GET  | `/api/health` | Liveness + number of price sheets loaded |
