from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.csrf import CsrfMiddleware
from app.observability import RequestLogMiddleware
from app.routers import (
    analysis,
    ask,
    auth,
    automations,
    conversations,
    documents,
    folders,
    invitations,
    knowledge_products,
    management,
    teams,
)

settings = get_settings()

app = FastAPI(title=settings.app_name)

app.add_middleware(RequestLogMiddleware)
app.add_middleware(CsrfMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(invitations.router)
app.include_router(teams.router)
app.include_router(documents.router)
app.include_router(ask.router)
app.include_router(conversations.router)
app.include_router(analysis.router)
app.include_router(automations.router)
app.include_router(folders.router)
app.include_router(knowledge_products.router)
app.include_router(management.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
