# n8n Multi-Agent Orchestration Layer

This document explains the n8n-based orchestration layer built to match the
Master-Slave multi-agent architecture requested for this project: an Intent
Classifier Agent routes each customer message to one or more category-specific
RAG sub-agents (Pricing, Timings, Documents, Courses, and a General fallback),
which run in parallel, get merged, and are polished by a Synthesizer Agent
before the final reply goes back to the customer.

This is a second implementation of the chatbot, alongside the existing Python
system in `src/fahrschule/`. The Python system stays as-is and is untouched by
this work. See "Relationship to the Python system" below for why both exist and
what changed.

**This workflow is built entirely from n8n's own node palette.** No custom code
calls OpenAI or Qdrant directly; every step is a real n8n node (Chat Trigger,
Text Classifier, Vector Store, Retriever, Retrieval QA Chain, Merge, Aggregate,
Basic LLM Chain) wired together on the canvas, the way a production n8n
workflow is actually built.

## Architecture

```
Chat Trigger
  -> Intent Classifier (Text Classifier node, multiClass=true, fallback="Other")
       [Pricing]   -> Pricing Vector Store   -> Pricing Retriever   -> Pricing RAG Chain   -+
       [Timings]   -> Timings Vector Store   -> Timings Retriever   -> Timings RAG Chain   -+
       [Documents] -> Documents Vector Store -> Documents Retriever -> Documents RAG Chain -+--> Merge Answers -> Aggregate Answers -> Synthesizer -> (chat response)
       [Courses]   -> Courses Vector Store   -> Courses Retriever   -> Courses RAG Chain   -+
       [Other]     -> Other Vector Store     -> Other Retriever     -> Other RAG Chain     -+

Shared model nodes (one instance each, fanned out to every consumer):
  OpenAI Chat Model  -> ai_languageModel -> Intent Classifier, all 5 RAG Chains, Synthesizer
  OpenAI Embeddings  -> ai_embedding     -> all 5 Vector Store nodes
```

22 nodes total: 1 trigger, 2 shared model nodes, 1 classifier, 5 x (Vector Store
+ Retriever + RAG Chain), 1 Merge, 1 Aggregate, 1 Synthesizer.

## Why this node combination, specifically

n8n exposes several ways to build a "RAG agent." Two were considered:

- **`chainRetrievalQa` per category (chosen).** This chain node always retrieves
  from its connected retriever before answering, has no autonomous tool-use
  decision to get wrong, and maps directly onto "RAG Pipeline per category"
  from the brief. Each branch needs exactly three nodes (Vector Store,
  Retriever, Chain).
- **`agent` with a `toolVectorStore` tool (rejected).** A full AI Agent node
  that decides, per message, whether to call a retrieval tool at all. This adds
  an autonomous decision this workflow does not need (a matched category should
  always retrieve), and more connection types to wire correctly, for no
  benefit here.

Two connection-type details that are easy to get wrong and are documented here
so they are not re-discovered by trial and error:

- **`vectorStoreQdrant` (mode `retrieve`) has no `main` input at all.** The
  actual message never flows through the vector store or retriever nodes, they
  only provide retrieval capability to the chain via the `ai_vectorStore` /
  `ai_retriever` connection types. The real per-item message flow is
  `Chat Trigger -> Intent Classifier -> (per matched category) -> RAG Chain`
  directly.
- **`chainRetrievalQa` does not accept a vector store directly.** The chain is
  `vectorStoreQdrant` (`ai_vectorStore` output) -> `retrieverVectorStore`
  (converts vector store to `ai_retriever`) -> `chainRetrievalQa`
  (`ai_retriever` input). Connecting the vector store straight to the chain is
  a type mismatch.

## A real, reproducible integration issue found and fixed while building this

Requests initially failed at the Intent Classifier with an output-parsing
error: the OpenAI Chat Model returned its answer as a "content parts" array
(`[{"type":"text","text":"..."}]`, OpenAI's newer Responses API format)
wrapped in markdown code fences, instead of a plain JSON string, and the
classifier's structured-output parser rejected it.

Root cause: `lmChatOpenAi` (this n8n version) defaults to
`responsesApiEnabled: true`. Fixed by explicitly setting
`responsesApiEnabled: false` on the OpenAI Chat Model node, forcing the
classic Chat Completions API, which returns a plain string the classifier can
parse. This is set explicitly in `n8n/scripts/04_build_native_workflow.py`
rather than left to the node's default.

## Multi-category routing and the "Other" fallback

The Text Classifier's `options.multiClass` is set to `true`, so a single
message can be routed to more than one category output at once (verified live:
"Was kostet Klasse C1 und wie sind eure Öffnungszeiten in Kleve?" correctly
fired both the Pricing and Timings branches, and only those two, confirmed via
the execution log; `Merge Answers` received exactly 2 items). `options.fallback`
is set to `"other"`, which adds an extra output branch used whenever the
classifier has no confident category match; this is wired to its own
Vector Store / Retriever / RAG Chain against the `general` collection, exactly
matching the requested "General Agent... for a query that does not clearly
belong to any category."

The classifier's category judgment is not perfect: a company-history question
("Erzähl mir etwas über die Geschichte der Fahrschule") was routed to `Courses`
rather than falling back to `Other`, apparently because the model considered it
loosely course-related. It answered honestly that it did not know rather than
guessing, which is the correct failure mode, but it is worth noting as a real,
observed limitation of relying on an LLM's category judgment rather than a
deterministic rule. A more clearly out-of-scope question ("Was sind eure Werte
und wofür steht euer Unternehmen?") did correctly trigger the `Other` branch and
retrieved the right company-values content.

## Multi-turn follow-up questions for Pricing

The first version of the Pricing branch used the same pattern as the other four
categories, a single-shot Retrieval QA Chain: retrieve, answer, done. Testing it
against a genuinely ambiguous question ("Was kostet Klasse B?", which has about
18 differently priced variants) showed the real problem with that pattern: it
retrieved several variants by similarity and described all of them in one
answer, rather than asking which one the customer meant.

This was fixed by rebuilding the Pricing branch as a conversational **Agent**
node instead of a QA Chain, with two things a QA Chain does not have:

- **Session memory** (`memoryBufferWindow`, keyed to the Chat Trigger's session
  ID), so the agent remembers what the customer already answered earlier in the
  conversation.
- **The price collection as a callable tool** (`vectorStoreQdrant` in
  `retrieve-as-tool` mode), rather than an automatic retrieval step, so the
  agent only searches once it has narrowed the question down to one specific
  variant, instead of retrieving broadly and describing whatever comes back.

Its system prompt encodes the same disambiguation order the proven Python
system uses: new license or special case, then alone or combined with another
class, then manual or automatic transmission, then standard or intensive or
simulator course, asking one question at a time and never stating a price until
exactly one variant is unambiguous.

**A second problem surfaced once memory was added**: the Text Classifier has no
memory of its own, so a short follow-up reply like "Neuerwerb" or "Automatik"
was sometimes classified as a fresh question about a different topic (observed
live: "Neuerwerb, ein neuer Führerschein" was misrouted to the Documents branch,
answering with registration paperwork instead of continuing the price
conversation, silently dropping the disambiguation in progress). Since the
branches only run when the classifier selects them, a misroute here means the
Pricing Agent, and its memory, never even executes for that turn.

The fix is a small `Session Router` step (a Code node) placed between the Chat
Trigger and everything else, backed by workflow static data (a plain key/value
store that persists across separate webhook calls, keyed by session ID, no
database needed for this). It flags a session as "mid pricing disambiguation"
whenever the Pricing Agent's last reply did not contain a stated total, and an
`IF` node reads that flag every turn: if set, the message is sent straight to
the Pricing Agent, bypassing the classifier entirely for that turn; otherwise
classification proceeds as normal. A matching `Update Pricing State` step after
the agent clears the flag once a reply contains a real euro total (matched with
a simple regular expression against the retrieved price text, not inferred by
the model), so the next unrelated message in the same session goes through
normal classification again.

Verified live, full transcript:

```
Was kostet Klasse B?
  -> Ist das ein Neuerwerb oder ein Sonderfall?
Neuerwerb, ein neuer Führerschein
  -> Nur Pkw oder kombiniert mit einer anderen Klasse?
Nur Auto, keine Kombination
  -> Schaltung oder Automatik (B197)?
Automatik
  -> Gesamtbetrag 2.696,21 EUR, Preisstand 01.01.2025, externe Gebühren ca. 280 EUR,
     Preisblatt zum Download.
Wie sind eure Öffnungszeiten?      (same session, unrelated question)
  -> correctly answered by the Timings branch, not stuck in pricing mode
```

The stated total, 2696.21 EUR, is exactly the golden value for variant B197.
A second run in a fresh session with an unrelated ambiguous class ("Was kostet
Klasse C?") also correctly began its own disambiguation from the first
question, confirming state is isolated per session.

This is a deliberately lightweight fix, not a full dialogue-state machine: it
tracks one boolean per session (mid-disambiguation or not), not which question
was last asked, and relies on the agent's own memory for that finer detail. A
customer who abandons a pricing disambiguation mid-way and asks something
unrelated will be held in the Pricing branch for one extra turn before the
off-topic safety net in the agent's own prompt catches it, this is a known,
acceptable simplification, not a fixed limitation of the pattern.

## The five categories, and what content is in each

Reused as-is from the already-populated Qdrant collections (79 chunks total,
built by the Python data-prep scripts, unchanged by this rebuild):

| Text Classifier branch | Qdrant collection | Content |
| --- | --- | --- |
| Pricing | `pricing` | 42 current official price sheets + pricing-adjacent FAQ/consultation snippets (49 chunks) |
| Timings | `location_hours` | Both locations, addresses, phone, opening hours, team reachability (3 chunks) |
| Documents | `registration_docs` | Enrollment process, required documents, exam registration, document-validity FAQs (12 chunks) |
| Courses | `courses_offerings` | Full course catalog, training start process, exam/curriculum content, course-related FAQs (12 chunks) |
| Other (fallback) | `general` | Company background, policies/AGB, team description (3 chunks) |

See `n8n/scripts/01_prepare_pricing_chunks.py` and
`n8n/scripts/02_prepare_knowledge_chunks.py` for how these were built, including
the category-boundary reasoning and a gap that was found and fixed (a
consultation document that had been extracted but never made it into the
original retrieval corpus).

## Relationship to the Python system, and the accuracy tradeoff

The existing Python system (`src/fahrschule/`) guarantees 100% price accuracy by
construction: prices are extracted once, arithmetic-verified against their
source PDF, stored deterministically, and never touched by a language model.
Asking about an ambiguous class (for example, plain "Klasse B", which has 18
priced variants) is treated as a hard stop that triggers a follow-up question,
never a guess.

This n8n workflow's Pricing sub-agent is a real retrieval-plus-generation
pipeline, per the requested architecture, and does not have that guarantee.
Measured, honest accuracy numbers against the same golden price set the Python
system was tested against are in `EVAL_RESULTS.md`, generated directly from a
live run, not estimated.

Mitigations applied within the RAG approach, to keep this as accurate as
reasonably possible without abandoning the requested pattern:
- The Pricing collection is built from the same clean, verified structured
  records the Python system uses, not raw PDF text.
- Each RAG Chain's system prompt (set via `chainRetrievalQa`'s
  `systemPromptTemplate` option) is category-specific. The Pricing branch's
  prompt explicitly forbids estimating, rounding, or calculating a discount,
  and instructs it to state uncertainty and offer a handoff when the retrieved
  context does not clearly answer.
- The Synthesizer is explicitly instructed to never alter a number, price,
  date, or fact from the sub-agent drafts, only to merge and reformat them.

## Running it

See `n8n/README.md` for full setup. In short:

```
docker compose -f n8n/docker-compose.yml up -d
.venv/Scripts/python.exe n8n/scripts/01_prepare_pricing_chunks.py
.venv/Scripts/python.exe n8n/scripts/02_prepare_knowledge_chunks.py
.venv/Scripts/python.exe n8n/scripts/03_embed_and_upsert.py
.venv/Scripts/python.exe n8n/scripts/04_build_native_workflow.py
curl -X POST http://localhost:5678/webhook/fahrschule-native-chat/chat \
     -H "Content-Type: application/json" \
     -d '{"chatInput":"Was kostet Klasse B197?","sessionId":"demo"}'
```
