# Build Plan — German Fahrschule Chatbot (88 steps)

Accuracy-first RAG + structured-store chatbot. Built and validated one step at a
time; each step is committed and pushed after triple validation.

## The one decision that makes it "never wrong"

The LLM does **not** generate prices. The system is split:

- **Structured price store** (JSON/SQLite) = single source of truth. Prices are
  returned verbatim by a lookup function, never written by the model.
- **RAG (vector store)** = only descriptive/policy text.
- **LLM = router + clarifier + translator only.** It picks the class, asks
  follow-ups, formats, and translates DE/EN. It never invents a euro amount.
- Every number in output is validated against the store before send.

---

### Phase 0 — Discovery & scope (1–7)
1. Confirm client channels (web widget vs WhatsApp/Telegram/email).
2. Confirm languages: DE primary, EN secondary.
3. Confirm handoff target (phone/email/booking link).
4. Confirm price-doc delivery (document + downloadable link).
5. Confirm current price generation (2025_01) is authoritative.
6. Define "wrong answer" in writing (wrong price / class / outdated all fail).
7. Agree success metric: 100% price accuracy on a golden test set.

### Phase 1 — Data audit (8–15)
8. Inventory all files → manifest (filename → class → variant → date → type).
9. Classify each file (price / info / consultation / truck-display).
10. Detect duplicates & version conflicts; mark newest canonical.
11. Extract license-class taxonomy (full A/B/C tree + every variant).
12. Map each variant to plain-language meaning.
13. Mine consultation docs for the client's real follow-up sequence.
14. Catalog German fee terminology.
15. Flag OCR-risky tables for manual handling.

### Phase 2 — Extraction & preprocessing (16–30)
16. Build table-aware extraction pipeline.
17. Extract each price sheet into a strict schema.
18. Normalize German numbers (49,90 € → 49.90, EUR).
19. Normalize umlauts/encoding; add EN label mapping.
20. Second-pass LLM structured-output extraction as cross-check.
21. **Human verification gate** — every sheet diffed vs source, signed off.
22. Store verified structured data as versioned JSON per class-variant.
23. Separate descriptive text (→ RAG) from numbers (→ store).
24. Preprocess business-info doc into clean FAQ KB.
25. Convert consultation scripts into a disambiguation decision tree.
26. Semantic chunking of descriptive text.
27. Attach rich metadata to every chunk.
28. Build bilingual glossary/synonym table.
29. Map each class-variant → downloadable PDF link.
30. Version the preprocessed dataset (DVC/git).

### Phase 3 — Structured price store (31–38)
31. Load verified JSON into SQLite/Postgres.
32. Enforce one canonical current row per class-variant.
33. `get_price(class, variant)` — deterministic exact lookup.
34. `list_variants(class)` — feeds follow-up questions.
35. `get_document_link(class, variant)`.
36. `resolve_class(user_text)` — free text → canonical code or ambiguous+candidates.
37. Unit-test every lookup against source PDFs.
38. Expose lookups as agent tools/functions.

### Phase 4 — RAG for descriptive content (39–46)
39. Pick German-strong embedding model.
40. Embed only descriptive chunks + FAQ.
41. Vector DB with metadata filters.
42. Metadata pre-filtering (class + current date).
43. Hybrid search (BM25 + vector).
44. Cross-encoder reranker.
45. Retrieval confidence threshold.
46. Test retrieval recall on German query set.

### Phase 5 — Orchestration brain, LangGraph (47–62)
47. Design agent as a graph.
48. Language-detect node (DE/EN).
49. Query-normalize node (glossary).
50. Intent-classify node.
51. Class-resolution node.
52. Disambiguation node (decision tree).
53. Price-fetch node (deterministic).
54. Info-answer node (grounded RAG).
55. Document-delivery node (PDF link).
56. Response-compose node (prices injected as literals).
57. Out-of-scope / handoff node.
58. Short-term memory (carry resolved class/variant/language).
59. Slot-based memory (ask only missing slots).
60. Memory summarization for long chats.
61. Persist session state (Redis).
62. Per-turn audit trace.

### Phase 6 — "Never wrong" guardrails (63–72)
63. Numeric grounding check (every € asserted against store).
64. Source-binding (class+variant+date+file on every price reply).
65. Ambiguity gate (refuse to price if unresolved).
66. Freshness guard (serve current only; state date).
67. Grounding/faithfulness judge pass.
68. Confidence-based refusal.
69. No-extrapolation rule.
70. Hallucination honeypots in eval set.
71. Human-in-the-loop escalation.
72. Log refusals & escalations.

### Phase 7 — Frontend, channels, delivery (73–80)
73. FastAPI wrapping the LangGraph agent.
74. Web chat widget (React/Next.js), DE/EN toggle.
75. Itemized pricing card + Download-PDF button.
76. Serve PDFs via downloadable links.
77. Always show class/variant/date of a price.
78. Optional multichannel adapters (n8n glue layer).
79. Lead capture → client CRM/email.
80. Accessibility + mobile + streaming.

### Phase 8 — Eval, test, deploy, monitor (81–88)
81. Golden test set (every class-variant → exact expected prices).
82. Automated price-accuracy regression on every change.
83. Adversarial DE/EN test set.
84. Human UAT with client (native German).
85. Load test + latency budget + caching.
86. Deploy (Docker; Vercel/Render/VPS; secrets; PDF storage).
87. Monitoring + low-confidence/refusal dashboard.
88. Price-update runbook (new sheets → pipeline → verify → version bump).

---

## n8n verdict

**Not for the brain. Optionally for the plumbing.** The accuracy-critical core
(class resolution, price lookup, numeric validation) is code (Python /
LangGraph / FastAPI) — versionable, testable, regression-gated. n8n is optional
glue for channels (WhatsApp/Telegram/email), document sending, and lead routing
to the client's CRM. Rule: **n8n calls the bot; the bot is not built in n8n.**
