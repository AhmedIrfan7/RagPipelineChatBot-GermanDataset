"""n8n workflow build (fully native n8n nodes, no custom HTTP/Code calls to
OpenAI or Qdrant): the Master-Slave multi-agent orchestration workflow built
entirely from n8n's own Chat Trigger, Text Classifier, Vector Store, Retriever,
Retrieval QA Chain, Merge, Aggregate, and Basic LLM Chain nodes.

Architecture:
  Chat Trigger
    -> Intent Classifier (Text Classifier node; multiClass; categories Pricing,
       Timings, Documents, Courses; unmatched -> "Other" fallback branch)
       -> per matched category: Retrieval QA Chain (grounded in that category's
          Qdrant collection via a Vector Store + Retriever pair)
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
        "Questions about course prices, costs, fees, discounts, or payment.",
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

    connections: dict = {
        "Chat Trigger": {"main": [[{"node": "Intent Classifier", "type": "main", "index": 0}]]},
        "OpenAI Chat Model": {"ai_languageModel": [[
            {"node": "Intent Classifier", "type": "ai_languageModel", "index": 0},
        ]]},
        "OpenAI Embeddings": {"ai_embedding": [[]]},
    }

    branch_labels = list(BRANCHES.keys()) + [FALLBACK_LABEL]
    classifier_main: list = []
    merge_inputs = len(branch_labels)
    y0 = 100
    for i, label in enumerate(branch_labels):
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

    connections["Merge Answers"] = {"main": [[{"node": "Aggregate Answers", "type": "main", "index": 0}]]}
    connections["Aggregate Answers"] = {"main": [[{"node": "Synthesizer", "type": "main", "index": 0}]]}
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
