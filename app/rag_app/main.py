"""검사 대상 RAG 앱. TEI로 임베딩하고 Qdrant에서 찾은 문서를 llama-server에 넘긴다.

LEAK_PROBE_URL은 회귀 게이트가 새 외부 호출을 잡는지 보이려고 일부러 넣는 누출 장치다.
값이 있으면 기동할 때 그 주소로 GET을 한 번 보낸다. 기본값은 비어 있다.
"""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

LLM_URL = os.environ.get("LLM_URL", "http://llm:8080")
EMBED_URL = os.environ.get("EMBED_URL", "http://embed:80")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
COLLECTION = os.environ.get("COLLECTION", "docs")
LEAK_PROBE_URL = os.environ.get("LEAK_PROBE_URL", "")
TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "300"))

log = logging.getLogger("rag_app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if LEAK_PROBE_URL:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.get(LEAK_PROBE_URL)
        except httpx.HTTPError as exc:
            log.warning("leak probe failed: %s", exc)
    yield


app = FastAPI(lifespan=lifespan)
qdrant = QdrantClient(url=QDRANT_URL, timeout=int(TIMEOUT))


class Doc(BaseModel):
    id: str
    text: str


class IngestRequest(BaseModel):
    docs: list[Doc]


class AskRequest(BaseModel):
    question: str
    top_k: int = 3
    max_tokens: int = 128


def embed(texts: list[str]) -> list[list[float]]:
    resp = httpx.post(f"{EMBED_URL}/embed", json={"inputs": texts}, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/ingest")
def ingest(req: IngestRequest) -> dict:
    # multilingual-e5 계열은 문서에 "passage: ", 질문에 "query: " 접두어를 붙인다.
    vectors = embed([f"passage: {d.text}" for d in req.docs])
    if not qdrant.collection_exists(COLLECTION):
        qdrant.create_collection(
            COLLECTION, vectors_config=VectorParams(size=len(vectors[0]), distance=Distance.COSINE)
        )
    points = [
        PointStruct(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, d.id)),
            vector=v,
            payload={"doc_id": d.id, "text": d.text},
        )
        for d, v in zip(req.docs, vectors)
    ]
    qdrant.upsert(COLLECTION, points=points)
    return {"ingested": len(points)}


@app.post("/ask")
def ask(req: AskRequest) -> dict:
    vector = embed([f"query: {req.question}"])[0]
    hits = qdrant.query_points(COLLECTION, query=vector, limit=req.top_k).points
    context = "\n".join(f"- {h.payload['text']}" for h in hits)
    body = {
        "messages": [
            {"role": "system", "content": "아래 문서만 근거로 짧게 답하세요.\n" + context},
            {"role": "user", "content": req.question},
        ],
        "max_tokens": req.max_tokens,
        "temperature": 0,
    }
    resp = httpx.post(f"{LLM_URL}/v1/chat/completions", json=body, timeout=TIMEOUT)
    resp.raise_for_status()
    answer = resp.json()["choices"][0]["message"]["content"]
    return {"answer": answer, "sources": [h.payload["doc_id"] for h in hits]}
