# Evaluation Results (Native n8n Multi-Agent Workflow)

Generated from a live run against the fully native 22-node n8n workflow (Chat
Trigger, Text Classifier, Vector Store, Retriever, Retrieval QA Chain, Merge,
Aggregate, Basic LLM Chain), using the same golden fixtures the Python system
(`src/fahrschule/`) is tested against. Every number below is measured, not
estimated, including the ones that are not perfect.

## Price accuracy

The workflow was asked for the exact total of all 42 current price variants,
the same set the Python system matches 44/44 (100%) against source PDFs.

**41/42 exact matches (97.6%), clean run with no transient errors.**

| Variant | Result |
| --- | --- |
| `A` | wrong: returned the price of a different variant (`A_VorbA1`) |
| all other 41 variants | exact match against the golden fixture |

Exact euro figures are omitted here since they are the client's confidential
pricing data (see the repo's confidentiality policy); the golden fixture and
raw run output with full figures stay in the gitignored `data/golden/` and
`n8n/eval_results_raw.json`.

### The one error: `A`, and why it is worth trusting as a real finding

Asking for variant `A` consistently returns the price of `A_VorbA1` (a
preparation course for A1) instead of Class A's own price. This is the same
error, on the same variant, with the same wrong value, previously found and
confirmed reproducible (3/3 retries) when this workflow was built
with n8n's HTTP Request nodes calling Qdrant and OpenAI directly. Reproducing
it independently through a completely different orchestration mechanism (real
Vector Store / Retriever / Retrieval QA Chain nodes instead of raw HTTP calls),
against the same underlying Qdrant collection and embeddings, is strong
confirmation that this is a genuine property of the embedded data (the `A` and
`A_VorbA1` chunks are similar enough in embedding space that retrieval
sometimes prefers the wrong one), not an artifact of either implementation.

## A real, reproducible n8n integration bug found and fixed

Before any query could succeed, every request failed at the Intent Classifier
with an output-parsing error. The OpenAI Chat Model was returning its answer
as a "content parts" array (OpenAI's newer Responses API format) wrapped in
markdown code fences, instead of the plain JSON string the classifier's parser
expected.

Root cause, confirmed by reading the node's parameter schema directly: the
`lmChatOpenAi` node in this n8n version defaults to `responsesApiEnabled:
true`. Fixed by explicitly setting `responsesApiEnabled: false` on the shared
OpenAI Chat Model node (see `n8n/scripts/04_build_native_workflow.py`), which
forces the classic Chat Completions API and resolved the issue completely.
This is documented in `n8n/docs/ARCHITECTURE.md` so it is not re-discovered by
trial and error.

## Multi-category parallel execution: verified via the execution log, not just the reply text

Asking "Was kostet Klasse C1 und wie sind eure Öffnungszeiten in Kleve?" (price
plus hours in one message) produced a correct, complete answer for both parts
(C1's price matched the golden fixture exactly; the opening hours for Kleve
were also correct). This was
independently confirmed against n8n's own execution log, not just by reading
the reply text: the Intent Classifier's `multiClass` output correctly
activated exactly two branches (Pricing and Timings), the other three branches
(Documents, Courses, Other) did not execute at all, and the Merge node
received exactly 2 items. This is real conditional parallel execution through
n8n's native per-branch execution engine, not a script simulating it.

## The "Other" fallback branch: works, with an honest limitation noted

A clearly out-of-scope company-values question ("Was sind eure Werte und
wofür steht euer Unternehmen?") correctly triggered the `Other` branch and
retrieved the right content (the company's actual stated values and mission
text).

A company-history question ("Erzähl mir etwas über die Geschichte der
Fahrschule") was, however, routed to the `Courses` branch instead of `Other`,
confirmed via the execution log. The classifier is an LLM making a judgment
call between categories it was given, not a deterministic rule, and it
apparently considered a history question loosely course-related rather than
falling back. The Courses branch then correctly said it did not know the
answer rather than guessing, which is the right failure mode, but the routing
itself was not what a person would have chosen. This is noted as an honest,
observed limitation of relying on an LLM's category judgment.

## Behavioral difference from the Python system: no disambiguation

Asking the plain, ambiguous question "What does class B cost?" (Class B has 18
priced variants) does not trigger a follow-up question, unlike the Python
system. It picked one specific variant (`B_Wiedererteilung`), with its line
items summing correctly to that variant's real golden total, and answered as
if that were the only option, with no indication that 17 other priced variants
exist. This is the same "ambiguity is not a stop"
behavior found in the earlier build, now confirmed under the fully native node
implementation as well, since it is a property of building Pricing as
retrieval-plus-generation rather than a deterministic class-resolution step,
not specific to how the retrieval calls happen to be wired.

## Query cases: client examples and adversarial probes

| Group | Result |
| --- | --- |
| Strict FAQ (hours, documents, validity) | 3/3 correct; one response added an unnecessary "I am not sure" hedge after already giving the correct 2-year validity answer, a minor phrasing issue, not a factual error |
| Semantic FAQ (simulator, online theory, funding, registration) | 4/4 answered correctly |
| Pricing (direct questions) | 3/3 returned a real, correctly-dated price; see the "no disambiguation" note above for the ambiguous-class case |
| Adversarial (unknown class, fake discount, nonsense) | 3/3 correctly refused: no fabricated price, no invented discount, nonsense correctly deflected to a human handoff offer |

Adversarial resistance matches the Python system's behavior exactly in this
test run: no invented price or discount in any adversarial case.

## Bottom line

| | Python system (`src/fahrschule/`) | n8n native multi-agent workflow |
| --- | --- | --- |
| Price accuracy (measured) | 44/44 (100%) | 41/42 (97.6%), same single error reproduced across two independent implementations |
| Ambiguous class handling | Forces a follow-up question, never guesses | Answers immediately with one possibility, no follow-up |
| Category routing | Rule-based, deterministic | LLM judgment call; mostly correct, one observed misroute on an edge-case query |
| Adversarial resistance | Never fabricates | Never fabricated in this test run |
| Architecture | Deterministic store, no LLM in the price path | Real n8n nodes throughout: Text Classifier, Vector Store, Retriever, Retrieval QA Chain, Merge, Aggregate, Basic LLM Chain |

This workflow satisfies the requested Master-Slave multi-agent architecture,
built entirely from n8n's own node palette, and performs well across
categories. For pricing specifically, the accuracy gap versus the
deterministic Python system is real, measured, and reproduces consistently
regardless of how the retrieval calls are implemented underneath, which is
useful evidence that the gap is inherent to the RAG-for-pricing approach
itself, not a fixable implementation detail.
