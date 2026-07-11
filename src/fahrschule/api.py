"""FastAPI service wrapping the deterministic dialogue engine.

Endpoints:
    GET  /                       -> the embeddable web chat widget
    POST /api/session            -> create a session, returns greeting
    POST /api/message            -> send text or an option key, returns the next reply
    GET  /api/document/{variant} -> download the official price-sheet PDF (current only)
    GET  /api/health             -> liveness

Prices and PDFs are read at runtime from the local (gitignored) data tree; this code
contains no client data. Configure the brand name via SCHOOL_NAME and the escalation
address via HANDOFF_EMAIL environment variables.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from .dialogue import DialogueEngine, Reply, Session
from .disambiguation import Disambiguator
from .knowledge import KnowledgeBase
from .store import Store

REPO_ROOT = Path(__file__).resolve().parents[2]
PRICES_DIR = REPO_ROOT / "data" / "processed" / "prices"
MANIFEST = REPO_ROOT / "data" / "interim" / "manifest.json"
RAW_DIR = REPO_ROOT / "data" / "raw"
KNOWLEDGE_DIR = REPO_ROOT / "data" / "processed" / "knowledge"
WIDGET = Path(__file__).resolve().parent / "web" / "index.html"

app = FastAPI(title="Fahrschule Chatbot", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

_store: Store | None = None
_engine: DialogueEngine | None = None
_sessions: dict[str, Session] = {}


@app.on_event("startup")
def _startup() -> None:
    global _store, _engine
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO_ROOT / ".env")   # load OPENAI_API_KEY / HANDOFF_EMAIL / SCHOOL_NAME
    except Exception:
        pass
    _store = Store.from_dir(PRICES_DIR, MANIFEST)
    kb = KnowledgeBase.from_dir(KNOWLEDGE_DIR) if KNOWLEDGE_DIR.exists() else None
    llm = None
    if os.environ.get("OPENAI_API_KEY"):
        from .llm import LLMClient
        client = LLMClient()
        if client.available():
            llm = client
            # build/load the semantic FAQ index (cached to disk) for better recall
            if kb is not None:
                from .embeddings import SemanticIndex
                cache = KNOWLEDGE_DIR / "embeddings.json"
                index = SemanticIndex.build(kb.docs, client.embed, cache_path=cache)
                if index is not None:
                    kb.attach_semantic(index, lambda q: (client.embed([q]) or [None])[0])
    _engine = DialogueEngine(_store, Disambiguator(), kb=kb, llm=llm)


def _reply_dict(r: Reply) -> dict:
    return {"kind": r.kind, "text": r.text, "options": r.options,
            "price": r.price, "document": r.document}


class MessageIn(BaseModel):
    session_id: str
    text: str | None = None
    option_key: str | None = None
    language: str | None = None


@app.get("/", response_class=HTMLResponse)
def widget() -> str:
    if WIDGET.exists():
        return WIDGET.read_text(encoding="utf-8")
    return "<h1>Fahrschule Chatbot API</h1><p>Widget not found.</p>"


@app.get("/api/health")
def health() -> dict:
    n = len(_store.by_key) if _store else 0
    return {"status": "ok", "sheets_loaded": n}


@app.post("/api/session")
def new_session(language: str = "de") -> dict:
    sid = uuid.uuid4().hex
    s = Session(sid, language=language, explicit_language=True)
    _sessions[sid] = s
    return {"session_id": sid, "reply": _reply_dict(_engine.greet(s))}


@app.post("/api/message")
def message(body: MessageIn) -> dict:
    s = _sessions.get(body.session_id)
    if s is None:
        raise HTTPException(status_code=404, detail="unknown session")
    if body.language:
        s.language = body.language
        s.explicit_language = True
    if body.option_key:
        reply = _engine.handle_option(s, body.option_key)
    elif body.text is not None:
        reply = _engine.handle_text(s, body.text)
    else:
        raise HTTPException(status_code=400, detail="text or option_key required")
    return {"reply": _reply_dict(reply)}


@app.get("/api/document/{variant_key}")
def document(variant_key: str):
    # serve the official PDF only for a current (non-archived) variant
    if _store is None or _store.get_price(variant_key) is None:
        raise HTTPException(status_code=404, detail="no current document for this variant")
    fname = _store.get_document_link(variant_key)
    path = RAW_DIR / fname if fname else None
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="document file not found")
    return FileResponse(str(path), media_type="application/pdf", filename=fname)
