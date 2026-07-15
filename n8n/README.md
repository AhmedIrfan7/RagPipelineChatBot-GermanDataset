# n8n Multi-Agent Orchestration Layer

A second implementation of the Fahrschule chatbot's brain, built to match a
Master-Slave multi-agent architecture in n8n: an Intent Classifier Agent routes
each message to one or more category-specific RAG sub-agents, which run in
parallel, get merged, and are polished by a Synthesizer Agent. Built entirely
from n8n's own nodes (Chat Trigger, Text Classifier, Vector Store, Retriever,
Retrieval QA Chain, Merge, Aggregate, Basic LLM Chain), no custom code calls
OpenAI or Qdrant directly.

Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) first for the full design,
including the accuracy tradeoff versus the deterministic Python system in
`src/fahrschule/` (which is untouched by this work and remains the
recommended production path for pricing).

## Prerequisites

- Docker Desktop running.
- `OPENAI_API_KEY` set in the repo-root `.env` (already required by the Python
  system, reused here).
- The Python venv at the repo root with `requirements.txt` installed (these
  scripts reuse `fahrschule.store.Store` and `fahrschule.llm.LLMClient`).
- The client's price sheets and knowledge base already extracted, i.e.
  `data/processed/prices/*.json` and `data/processed/knowledge/*` exist (see the
  main repo README's "Getting started" section).

## Setup, in order

**1. Start n8n and Qdrant**
```bash
cd n8n
docker compose up -d
```
n8n: http://localhost:5678 . Qdrant: http://localhost:6333 .

**2. Prepare the data** (from the repo root, not `n8n/`)
```bash
.venv/Scripts/python.exe n8n/scripts/01_prepare_pricing_chunks.py
.venv/Scripts/python.exe n8n/scripts/02_prepare_knowledge_chunks.py
```
Writes confidential chunk files to `n8n/data/` (gitignored).

**3. Embed and upsert into Qdrant**
```bash
.venv/Scripts/python.exe n8n/scripts/03_embed_and_upsert.py
```

**4. Create the n8n owner account** (first run only)

Open http://localhost:5678 and complete the one-time owner setup in the UI, or
do it via the API:
```bash
curl -X POST http://localhost:5678/rest/owner/setup -H "Content-Type: application/json" \
  -d '{"email":"admin@fahrschule.local","firstName":"Fahrschule","lastName":"Admin","password":"<choose one>"}'
```
Then generate an API key (Settings → API in the UI, or `POST /rest/api-keys`)
and add to your `.env`:
```
N8N_URL=http://localhost:5678
N8N_API_KEY=<the generated key>
```

**5. Create the OpenAI and Qdrant credentials, note their IDs**

In the n8n UI: Credentials → New → OpenAI, paste your API key, save, and copy
the credential ID from the URL. Then Credentials → New → QdrantApi, set the
URL to `http://qdrant:6333` (the Docker Compose service hostname, not
`localhost`, since n8n reaches it over the compose network), no API key needed.
Add both IDs to `.env`:
```
N8N_OPENAI_CRED_ID=<the openai credential id>
N8N_QDRANT_CRED_ID=<the qdrant credential id>
```

**6. Build and activate the workflow**
```bash
.venv/Scripts/python.exe n8n/scripts/04_build_native_workflow.py
```

**7. Try it**
```bash
curl -X POST http://localhost:5678/webhook/fahrschule-native-chat/chat \
     -H "Content-Type: application/json" \
     -d '{"chatInput":"Was kostet Klasse B197?","sessionId":"demo"}'
```

## Evaluating it

```bash
.venv/Scripts/python.exe n8n/scripts/05_evaluate.py
```
Runs the client's example questions, adversarial probes, and a full price-accuracy
sweep against the same golden fixtures the Python system is tested against.
Results (including the honest, measured accuracy number) are written to
`n8n/docs/EVAL_RESULTS.md`.

## Repository layout

```
n8n/
├── docker-compose.yml       n8n + Qdrant, both with persistent volumes
├── workflow.json            exported workflow (importable), no inline secrets
├── data/                    LOCAL ONLY, git-ignored: prepared chunks (real client text/prices)
├── scripts/
│   ├── 01_prepare_pricing_chunks.py     verified price records -> clean text chunks
│   ├── 02_prepare_knowledge_chunks.py   knowledge base -> 5-category taxonomy
│   ├── 03_embed_and_upsert.py           embed chunks, upsert into Qdrant
│   ├── 04_build_native_workflow.py      build + activate the 22-node native workflow via the n8n API
│   └── 05_evaluate.py                   golden + adversarial + price-accuracy eval
└── docs/
    ├── ARCHITECTURE.md      full design explanation (node graph, why chainRetrievalQa, issues found)
    └── EVAL_RESULTS.md      measured results (generated, not hand-written)
```

## Confidentiality

Same rule as the rest of this repo: everything under `n8n/data/` is real client
content (prices, business text) and is git-ignored. Only the workflow structure,
scripts, and documentation are public. The n8n owner password and the OpenAI/n8n
API keys used here are local-only credentials for a container running on your own
machine; they are never committed and are read from the repo-root `.env`.
