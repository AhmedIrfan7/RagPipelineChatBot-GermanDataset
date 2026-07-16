"""n8n workflow build (fully native n8n nodes, no custom HTTP/Code calls to
OpenAI or Qdrant): the Master-Slave multi-agent orchestration workflow built
entirely from n8n's own Chat Trigger, Text Classifier, Vector Store, Retriever,
Retrieval QA Chain, Merge, Aggregate, and Basic LLM Chain nodes.

Architecture:
  Chat Trigger
    -> Session Router (Code node; reads workflow static data to check whether this
       session is mid pricing-disambiguation, so a short follow-up reply like
       "Neuerwerb" or "Automatik" is not lost to a classifier that has no memory
       of the previous turn)
       -> Route Check (IF): mid-disambiguation -> straight to Pricing Agent,
          bypassing the classifier for this turn
                          -> otherwise -> Intent Classifier (Text Classifier node;
          multiClass; categories Pricing, Timings, Documents, Courses; unmatched
          -> "Other" fallback branch)
             -> per matched category: Retrieval QA Chain (grounded in that
                category's Qdrant collection via a Vector Store + Retriever pair),
                except Pricing, which is a conversational Agent (session memory +
                the price collection as a tool) so it can ask clarifying
                questions across multiple turns before ever stating a price
    -> (Pricing path only) Update Pricing State: marks the session as resolved
       once the agent's reply contains a final stated total, otherwise leaves it
       marked as still disambiguating for the Session Router to pick up next turn
    -> Merge (collects whichever branches fired)
    -> Aggregate (combines the per-branch answers into one item)
    -> Synthesizer (Basic LLM Chain; merges + polishes into one final reply,
       never alters a fact from the drafts)

Shared sub-nodes (one instance each, fanned out to every consumer):
  OpenAI Chat Model  -> ai_languageModel -> Classifier, all 5 RAG Chains, Synthesizer
  OpenAI Embeddings  -> ai_embedding     -> all 5 Vector Store nodes

Requires an existing OpenAI credential (N8N_OPENAI_CRED_ID) and Qdrant credential
(N8N_QDRANT_CRED_ID) in the repo-root .env.

Run:  .venv/Scripts/python.exe n8n/scripts/04_build_native_workflow.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Text Classifier branch label -> (Qdrant collection, category description for the
# classifier, grounded-answer system prompt for that branch's Retrieval QA Chain).
BRANCHES = {
    "Pricing": (
        "pricing",
        "Questions about course prices, costs, fees, discounts, or payment. Also "
        "includes short follow-up answers that continue an ongoing pricing "
        "conversation, such as: new license vs special case (conversion, "
        "re-issuance, school change); car only vs combined with another class; "
        "manual vs automatic transmission; standard vs intensive vs simulator "
        "course. Treat these short disambiguation replies as Pricing even without "
        "the word 'price' in them.",
        "You answer PRICING questions for a German driving school using ONLY the "
        "retrieved context. Never estimate, round, calculate, or invent a discount "
        "or total. If the context does not clearly answer, say you are not certain "
        "and offer to connect the customer with the team. Always state the price's "
        "effective date if it is given in the context.",
    ),
    "Timings": (
        "location_hours",
        "Questions about addresses, opening hours, contact info, or locations.",
        "You answer LOCATION AND OPENING HOURS questions using ONLY the retrieved "
        "context. If the context does not clearly answer, say you are not certain "
        "and offer to connect the customer with the team.",
    ),
    "Documents": (
        "registration_docs",
        "Questions about how to register/enroll, required documents, exam "
        "registration, or document validity.",
        "You answer REGISTRATION AND REQUIRED-DOCUMENTS questions using ONLY the "
        "retrieved context. If the context does not clearly answer, say you are "
        "not certain and offer to connect the customer with the team.",
    ),
    "Courses": (
        "courses_offerings",
        "Questions about which courses or license classes are offered, course "
        "content, exam procedure, or simulator/VR training.",
        "You answer COURSE AND OFFERING questions using ONLY the retrieved "
        "context. If the context does not clearly answer, say you are not certain "
        "and offer to connect the customer with the team.",
    ),
}
PRICING_AGENT_PROMPT = (
    "You are the Pricing specialist for a German driving school. You have a tool "
    "that searches the official, verified price sheets. Some license classes "
    "have only one priced variant (for example Mofa, AM, A1, A2, L, T, or a "
    "variant the customer already named exactly, like BE or B197). Others have "
    "several differently priced variants, most notably Class B, which has about "
    "18, so you must NEVER guess or average a price across variants.\n\n"
    "Follow this exact process on every pricing question:\n"
    "1. First decide whether the class actually needs disambiguation. If the "
    "customer named a class that only has one variant, or already named a "
    "specific variant exactly, skip straight to step 2, do not invent "
    "clarifying questions that do not apply to that class.\n"
    "2. If, and only if, the class genuinely has multiple priced variants and "
    "it is not yet clear which one, ask ONE short clarifying question at a "
    "time, in this order, skipping any step already answered by the customer:\n"
    "   a. Is this a new license, or a special case (converting a foreign "
    "license, re-issuance after withdrawal, switching driving schools, or "
    "removing an automatic-only restriction)?\n"
    "   b. If new: do they want the class alone, or combined with another class "
    "(for example a trailer, another vehicle class, or a tractor)?\n"
    "   c. If the class alone (for Class B specifically): manual or automatic "
    "transmission? (Automatic keeps manual entitlement too, this is the B197 "
    "variant, and fully answers this question, no course-type question needed.)\n"
    "   d. If manual: standard course, intensive course, or with simulator?\n"
    "3. Once exactly ONE specific variant is clear (whether immediately, from "
    "step 1, or after disambiguating in step 2), use the search tool to "
    "retrieve its real price and state the exact total and the effective date, "
    "and mention the official price sheet is available as a download.\n"
    "4. Never state a price for a class that still has multiple possible "
    "variants. Never invent, estimate, round, or calculate a discount. If the "
    "tool does not return a clear answer, say you are not certain and offer to "
    "connect the customer with the team.\n"
    "5. Reply in the same language the customer is using (German or English), "
    "and remember answers the customer already gave earlier in this "
    "conversation, do not ask the same question twice.\n"
    "6. You may be invoked directly for a message that is not actually about "
    "pricing (this can happen when the routing defaults to you mid-conversation). "
    "If the customer's message is clearly unrelated to any driving license "
    "topic at all (for example a completely different subject), say briefly "
    "that you handle pricing questions and ask them to restate what they need. "
    "A question naming a license class or course is on-topic even if you are "
    "not yet sure exactly what it needs, in that case follow steps 1 to 3 "
    "instead of treating it as off-topic."
)

FALLBACK_LABEL = "Other"
FALLBACK_COLLECTION = "general"
FALLBACK_PROMPT = (
    "You answer general company questions using ONLY the retrieved context. If "
    "nothing relevant was retrieved, say you are not certain and offer to "
    "connect the customer with the team."
)

SYNTHESIZER_PROMPT = (
    "=You are the final response formatter for a German driving school chatbot. "
    "You receive one or more draft answers from specialist agents, plus the "
    "customer's original message. Combine them into ONE clean, professional, "
    "well-formatted reply in the same language as the customer's message (German "
    "or English). Do not add, remove, or alter any number, price, date, or fact "
    "from the draft answers, only merge, reformat, and improve the phrasing. If a "
    "draft says it is not certain, keep that uncertainty and the offer to "
    "connect with the team in the final reply.\n\n"
    "Customer's message: {{ $('Chat Trigger').item.json.chatInput }}\n\n"
    "Draft answers:\n{{ ($json.response || []).join('\\n\\n') }}"
)


def env() -> dict:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
    return {
        "url": os.environ["N8N_URL"],
        "key": os.environ["N8N_API_KEY"],
        "openai_cred": os.environ["N8N_OPENAI_CRED_ID"],
        "qdrant_cred": os.environ["N8N_QDRANT_CRED_ID"],
    }


def http(url: str, key: str, method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{url}{path}", data=data, method=method,
        headers={"Content-Type": "application/json", "X-N8N-API-KEY": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"__error__": e.code, "__body__": e.read().decode("utf-8")}


def rag_branch_nodes(label: str, collection: str, system_prompt: str, x: float, y: float,
                      qdrant_cred: str) -> tuple[list[dict], str, str, str]:
    """Vector Store -> Retriever -> Retrieval QA Chain for one category. Returns
    (nodes, vector_store_name, retriever_name, chain_name)."""
    vs_name = f"{label} Vector Store"
    ret_name = f"{label} Retriever"
    chain_name = f"{label} RAG Chain"
    nodes = [
        {
            "parameters": {
                "mode": "retrieve",
                "qdrantCollection": {"__rl": True, "mode": "id", "value": collection},
                "options": {"contentPayloadKey": "text"},
            },
            "type": "@n8n/n8n-nodes-langchain.vectorStoreQdrant", "typeVersion": 1.3,
            "position": [x, y], "name": vs_name,
            "credentials": {"qdrantApi": {"id": qdrant_cred, "name": "Fahrschule Qdrant"}},
        },
        {
            "parameters": {},
            "type": "@n8n/n8n-nodes-langchain.retrieverVectorStore", "typeVersion": 1,
            "position": [x + 150, y], "name": ret_name,
        },
        {
            "parameters": {
                "promptType": "define",
                "text": "={{ $json.chatInput }}",
                "options": {
                    "systemPromptTemplate": system_prompt + "\n----------------\nContext: {context}",
                },
            },
            "type": "@n8n/n8n-nodes-langchain.chainRetrievalQa", "typeVersion": 1.7,
            "position": [x + 300, y], "name": chain_name,
        },
    ]
    return nodes, vs_name, ret_name, chain_name


UPDATE_STATE_CODE = r"""
const store = $getWorkflowStaticData('global');
if (!store.pricingSessions) store.pricingSessions = {};
if (!store.lastDocument) store.lastDocument = {};
const sessionId = $('Chat Trigger').item.json.sessionId;
const answer = $json.output || '';

// Extract the source PDF filename from the actual retrieved tool content
// (returnIntermediateSteps=true on the Agent node), not from the model's own
// prose, so this does not depend on the LLM remembering to cite it correctly
// every time. The pricing chunk text always contains this exact line (see
// scripts/01_prepare_pricing_chunks.py), so a match here is deterministic.
const filenameRe = /Quelldokument \(offizielles Preisblatt\):\s*([^\n]+?\.pdf)/i;
let pdfFile = null;
const steps = $json.intermediateSteps;
if (Array.isArray(steps)) {
  for (const step of steps) {
    const observation = (step && (step.observation || step.output)) || '';
    const text = typeof observation === 'string' ? observation : JSON.stringify(observation);
    const m = text.match(filenameRe);
    if (m) pdfFile = m[1].trim();  // keep the last (most recent) match if several
  }
}

// A final priced answer states a concrete euro total; a clarifying question does not.
const looksResolved = /\d[\d.]*,\d{2}\s*(€|EUR)/.test(answer);
store.pricingSessions[sessionId] = !looksResolved;
store.lastDocument[sessionId] = looksResolved ? pdfFile : null;

return $input.all();
""".strip()

ROUTER_CODE = r"""
const store = $getWorkflowStaticData('global');
const sessions = store.pricingSessions || {};
const sessionId = $json.sessionId;
const midPricing = sessions[sessionId] === true;
return [{ json: { ...$json, _forcePricing: midPricing } }];
""".strip()

ATTACH_DOCUMENT_CODE = r"""
const store = $getWorkflowStaticData('global');
const sessionId = $('Chat Trigger').item.json.sessionId;
const docs = store.lastDocument || {};
const documentFile = docs[sessionId] || null;
// consume once so it doesn't reattach to a later, unrelated reply in the same session
if (sessionId in docs) delete docs[sessionId];

const item = $input.all()[0];
return [{ json: { ...item.json, documentFile } }];
""".strip()


def pricing_agent_nodes(x: float, y: float, qdrant_cred: str) -> tuple[list[dict], str, str, str]:
    """Vector Store (as a tool) + a conversational Agent with memory, replacing the
    single-shot Retrieval QA Chain for Pricing so it can ask clarifying questions
    across multiple turns before ever stating a price. A small state-update step
    follows, recording (in workflow static data) whether this session is now
    resolved (a real price was stated) or still mid disambiguation, so the Session
    Router can force-route the next turn back here even though the Text Classifier
    has no memory of its own. Returns (nodes, vector_store_name, agent_name,
    normalize_name)."""
    vs_name = "Pricing Vector Store"
    agent_name = "Pricing Agent"
    state_name = "Update Pricing State"
    norm_name = "Pricing Answer (normalized)"
    nodes = [
        {
            "parameters": {
                "mode": "retrieve-as-tool",
                "toolName": "search_price_sheets",
                "toolDescription": (
                    "Search the official, verified price sheets for German driving "
                    "license courses. Input a specific class/variant description "
                    "(for example 'Klasse B197' or 'Klasse BE'). Returns the exact "
                    "line items, total price, and effective date for matching "
                    "sheets. Only call this once the specific variant is known, "
                    "not for a still-ambiguous class."
                ),
                "qdrantCollection": {"__rl": True, "mode": "id", "value": "pricing"},
                "options": {"contentPayloadKey": "text"},
            },
            "type": "@n8n/n8n-nodes-langchain.vectorStoreQdrant", "typeVersion": 1.3,
            "position": [x, y], "name": vs_name,
            "credentials": {"qdrantApi": {"id": qdrant_cred, "name": "Fahrschule Qdrant"}},
        },
        {
            "parameters": {
                "promptType": "define",
                "text": "={{ $json.chatInput }}",
                "hasOutputParser": False,
                "options": {
                    "systemMessage": PRICING_AGENT_PROMPT,
                    "returnIntermediateSteps": True,
                },
            },
            "type": "@n8n/n8n-nodes-langchain.agent", "typeVersion": 3.1,
            "position": [x + 200, y], "name": agent_name,
        },
        {
            "parameters": {"mode": "runOnceForAllItems", "jsCode": UPDATE_STATE_CODE},
            "type": "n8n-nodes-base.code", "typeVersion": 2,
            "position": [x + 350, y], "name": state_name,
        },
        {
            "parameters": {
                "mode": "manual",
                "assignments": {"assignments": [{
                    "id": "1", "name": "response", "type": "string",
                    "value": "={{ $json.output }}",
                }]},
                "options": {},
            },
            "type": "n8n-nodes-base.set", "typeVersion": 3.4,
            "position": [x + 500, y], "name": norm_name,
        },
    ]
    return nodes, vs_name, agent_name, state_name, norm_name


def build_workflow(e: dict) -> dict:
    nodes = []
    nodes.append({
        "parameters": {"public": True, "mode": "webhook", "options": {}},
        "type": "@n8n/n8n-nodes-langchain.chatTrigger", "typeVersion": 1.4,
        "position": [-2000, 500], "name": "Chat Trigger", "webhookId": "fahrschule-native-chat",
    })
    nodes.append({
        "parameters": {"model": {"__rl": True, "mode": "id", "value": "gpt-4o-mini"},
                       "responsesApiEnabled": False, "options": {}},
        "type": "@n8n/n8n-nodes-langchain.lmChatOpenAi", "typeVersion": 1.3,
        "position": [-1900, 900], "name": "OpenAI Chat Model",
        "credentials": {"openAiApi": {"id": e["openai_cred"], "name": "Fahrschule OpenAI"}},
    })
    nodes.append({
        "parameters": {"model": "text-embedding-3-small", "options": {}},
        "type": "@n8n/n8n-nodes-langchain.embeddingsOpenAi", "typeVersion": 1.2,
        "position": [-1900, 1100], "name": "OpenAI Embeddings",
        "credentials": {"openAiApi": {"id": e["openai_cred"], "name": "Fahrschule OpenAI"}},
    })

    categories = {"categories": [
        {"category": label, "description": desc} for label, (_, desc, _) in BRANCHES.items()
    ]}
    nodes.append({
        "parameters": {
            "inputText": "={{ $json.chatInput }}",
            "categories": categories,
            "options": {"multiClass": True, "fallback": "other"},
        },
        "type": "@n8n/n8n-nodes-langchain.textClassifier", "typeVersion": 1.1,
        "position": [-1700, 500], "name": "Intent Classifier",
    })
    nodes.append({
        "parameters": {
            "sessionIdType": "customKey",
            "sessionKey": "={{ $('Chat Trigger').item.json.sessionId }}",
            "contextWindowLength": 12,
        },
        "type": "@n8n/n8n-nodes-langchain.memoryBufferWindow", "typeVersion": 1.3,
        "position": [-1500, 300], "name": "Session Memory",
    })
    nodes.append({
        "parameters": {"mode": "runOnceForAllItems", "jsCode": ROUTER_CODE},
        "type": "n8n-nodes-base.code", "typeVersion": 2,
        "position": [-1900, 500], "name": "Session Router",
    })
    nodes.append({
        "parameters": {"conditions": {
            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
            "combinator": "and",
            "conditions": [{
                "id": "1", "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                "leftValue": "={{ $json._forcePricing }}", "rightValue": "",
            }],
        }},
        "type": "n8n-nodes-base.if", "typeVersion": 2.3,
        "position": [-1800, 500], "name": "Route Check",
    })

    connections: dict = {
        "Chat Trigger": {"main": [[{"node": "Session Router", "type": "main", "index": 0}]]},
        "Session Router": {"main": [[{"node": "Route Check", "type": "main", "index": 0}]]},
        "Route Check": {"main": [
            [{"node": "Pricing Agent", "type": "main", "index": 0}],       # true: mid-disambiguation
            [{"node": "Intent Classifier", "type": "main", "index": 0}],   # false: normal classification
        ]},
        "OpenAI Chat Model": {"ai_languageModel": [[
            {"node": "Intent Classifier", "type": "ai_languageModel", "index": 0},
        ]]},
        "OpenAI Embeddings": {"ai_embedding": [[]]},
        "Session Memory": {"ai_memory": [[{"node": "Pricing Agent", "type": "ai_memory", "index": 0}]]},
    }

    branch_labels = list(BRANCHES.keys()) + [FALLBACK_LABEL]
    classifier_main: list = []
    merge_inputs = len(branch_labels)
    y0 = 100
    for i, label in enumerate(branch_labels):
        if label == "Pricing":
            branch_nodes, vs, endpoint, state, norm = pricing_agent_nodes(
                -1400, y0 + i * 220, e["qdrant_cred"])
            nodes.extend(branch_nodes)
            # reachable two ways: directly from Route Check (forced) and from the
            # classifier's own Pricing output (normal first-touch classification)
            classifier_main.append([{"node": endpoint, "type": "main", "index": 0}])
            connections["OpenAI Chat Model"]["ai_languageModel"][0].append(
                {"node": endpoint, "type": "ai_languageModel", "index": 0})
            connections["OpenAI Embeddings"]["ai_embedding"][0].append(
                {"node": vs, "type": "ai_embedding", "index": 0})
            connections[vs] = {"ai_tool": [[{"node": endpoint, "type": "ai_tool", "index": 0}]]}
            connections[endpoint] = {"main": [[{"node": state, "type": "main", "index": 0}]]}
            connections[state] = {"main": [[{"node": norm, "type": "main", "index": 0}]]}
            connections[norm] = {"main": [[{"node": "Merge Answers", "type": "main", "index": i}]]}
            continue

        if label == FALLBACK_LABEL:
            collection, sys_prompt = FALLBACK_COLLECTION, FALLBACK_PROMPT
        else:
            collection, _, sys_prompt = BRANCHES[label]
        branch_nodes, vs, ret, chain = rag_branch_nodes(
            label, collection, sys_prompt, -1400, y0 + i * 220, e["qdrant_cred"])
        nodes.extend(branch_nodes)

        classifier_main.append([{"node": chain, "type": "main", "index": 0}])
        connections["OpenAI Chat Model"]["ai_languageModel"][0].append(
            {"node": chain, "type": "ai_languageModel", "index": 0})
        connections["OpenAI Embeddings"]["ai_embedding"][0].append(
            {"node": vs, "type": "ai_embedding", "index": 0})
        connections[vs] = {"ai_vectorStore": [[{"node": ret, "type": "ai_vectorStore", "index": 0}]]}
        connections[ret] = {"ai_retriever": [[{"node": chain, "type": "ai_retriever", "index": 0}]]}
        connections[chain] = {"main": [[{"node": "Merge Answers", "type": "main", "index": i}]]}

    connections["Intent Classifier"] = {"main": classifier_main}

    nodes.append({
        "parameters": {"numberInputs": merge_inputs},
        "type": "n8n-nodes-base.merge", "typeVersion": 3.2,
        "position": [-600, 500], "name": "Merge Answers",
    })
    nodes.append({
        "parameters": {
            "aggregate": "aggregateIndividualFields",
            "fieldsToAggregate": {"fieldToAggregate": [{"fieldToAggregate": "response"}]},
        },
        "type": "n8n-nodes-base.aggregate", "typeVersion": 1,
        "position": [-400, 500], "name": "Aggregate Answers",
    })
    nodes.append({
        "parameters": {
            "promptType": "define",
            "text": SYNTHESIZER_PROMPT,
        },
        "type": "@n8n/n8n-nodes-langchain.chainLlm", "typeVersion": 1.9,
        "position": [-200, 500], "name": "Synthesizer",
    })
    nodes.append({
        "parameters": {"mode": "runOnceForAllItems", "jsCode": ATTACH_DOCUMENT_CODE},
        "type": "n8n-nodes-base.code", "typeVersion": 2,
        "position": [0, 500], "name": "Attach Document Link",
    })

    connections["Merge Answers"] = {"main": [[{"node": "Aggregate Answers", "type": "main", "index": 0}]]}
    connections["Aggregate Answers"] = {"main": [[{"node": "Synthesizer", "type": "main", "index": 0}]]}
    connections["Synthesizer"] = {"main": [[{"node": "Attach Document Link", "type": "main", "index": 0}]]}
    connections["OpenAI Chat Model"]["ai_languageModel"][0].append(
        {"node": "Synthesizer", "type": "ai_languageModel", "index": 0})

    return {"name": "Fahrschule Native Multi-Agent Orchestration",
            "nodes": nodes, "connections": connections,
            "settings": {"executionOrder": "v1"}}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    e = env()
    wf = build_workflow(e)

    id_file = REPO_ROOT / "n8n" / "workflow_id.txt"
    existing_id = id_file.read_text(encoding="utf-8").strip() if id_file.exists() else None
    if existing_id:
        check = http(e["url"], e["key"], "GET", f"/api/v1/workflows/{existing_id}")
        if "__error__" in check:
            existing_id = None

    if existing_id:
        update_body = {"name": wf["name"], "nodes": wf["nodes"],
                        "connections": wf["connections"], "settings": wf["settings"]}
        result = http(e["url"], e["key"], "PUT", f"/api/v1/workflows/{existing_id}", update_body)
        if "__error__" in result:
            print(f"UPDATE FAILED ({result['__error__']}): {result['__body__'][:2000]}")
            sys.exit(1)
        wf_id = result["id"]
        print(f"workflow updated in place: id={wf_id}")
    else:
        result = http(e["url"], e["key"], "POST", "/api/v1/workflows", wf)
        if "__error__" in result:
            print(f"FAILED ({result['__error__']}): {result['__body__'][:2000]}")
            sys.exit(1)
        wf_id = result["id"]
        print(f"phase-1 workflow created: id={wf_id}")
        id_file.write_text(wf_id, encoding="utf-8")

    act = http(e["url"], e["key"], "POST", f"/api/v1/workflows/{wf_id}/activate")
    if "__error__" in act:
        print(f"activation FAILED ({act['__error__']}): {act['__body__'][:1000]}")
    else:
        print("activated. webhook path: fahrschule-native-chat")


if __name__ == "__main__":
    main()
