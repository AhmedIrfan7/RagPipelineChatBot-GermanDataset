"""OpenAI LLM adapter — a bounded assistant to the deterministic core.

The LLM is allowed to do exactly two things, both non-authoritative:
  1. extract_intent  — classify a free-text message into intent + (optionally) a
     license class, so messy phrasing the rule-based resolver missed can still be
     routed. The returned class is validated against the known code list; the LLM
     cannot inject an unknown class, and it NEVER produces a price.
  2. translate       — faithfully translate the client's own FAQ text for EN users,
     with an explicit instruction to preserve every number/price/time/address.

Prices always come from the deterministic store, never from the model. If no API key
is configured or any call fails, every method returns None and the engine falls back
to its deterministic behaviour.
"""

from __future__ import annotations

import json
import os


class LLMClient:
    def __init__(self, api_key: str | None = None, model: str = "gpt-4o-mini"):
        # only read the env when api_key is not passed; an explicit "" means "no key"
        self.api_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
        self.model = model
        self._client = None

    def available(self) -> bool:
        return bool(self.api_key)

    def _client_or_none(self):
        if not self.available():
            return None
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self.api_key)
        return self._client

    def extract_intent(self, text: str, class_codes: list[str]) -> dict | None:
        c = self._client_or_none()
        if c is None:
            return None
        sys_prompt = (
            "You route messages for a German driving-school assistant. "
            "Classify the message and, if a driving-license class is named or clearly "
            "implied, which base class. NEVER invent prices, facts, or classes. "
            'Reply ONLY as JSON: {"intent":"price|faq|other","class":"<code or null>",'
            '"query":"<short German search phrase>"}. '
            "class must be exactly one of [" + ", ".join(class_codes) + "] or null."
        )
        try:
            r = c.chat.completions.create(
                model=self.model, temperature=0, max_tokens=120,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": sys_prompt},
                          {"role": "user", "content": text}],
            )
            data = json.loads(r.choices[0].message.content)
        except Exception:
            return None
        cls = data.get("class")
        if cls not in class_codes:
            cls = None
        intent = data.get("intent")
        if intent not in ("price", "faq", "other"):
            intent = "other"
        return {"intent": intent, "class": cls, "query": (data.get("query") or text)}

    def embed(self, texts: list[str], model: str = "text-embedding-3-small") -> list[list[float]] | None:
        """Embed texts for semantic FAQ retrieval. Returns None on no-key/error.
        Note: this sends the passed text to OpenAI's embedding API."""
        c = self._client_or_none()
        if c is None or not texts:
            return None
        try:
            r = c.embeddings.create(model=model, input=texts)
            return [d.embedding for d in r.data]
        except Exception:
            return None

    def translate(self, text: str, target_language: str = "English") -> str | None:
        c = self._client_or_none()
        if c is None or not text.strip():
            return None
        try:
            r = c.chat.completions.create(
                model=self.model, temperature=0, max_tokens=600,
                messages=[
                    {"role": "system", "content":
                        f"Translate the user's text into {target_language}. Be faithful: "
                        "do not add, remove, or alter any facts, numbers, prices, times, "
                        "dates, or addresses. Output only the translation."},
                    {"role": "user", "content": text}],
            )
            return r.choices[0].message.content.strip()
        except Exception:
            return None
