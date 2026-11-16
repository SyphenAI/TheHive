"""Context ingestion endpoints: URL fetch, text upload, scholarly search. All return text blocks the
client appends to the session context. Nothing is stored server-side (stateless)."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.deps import Principal, require_scope
from app.services import ratelimit, scholar
from app.services.context import MAX_BYTES, UnsafeURL, fetch_url, source_block

router = APIRouter(prefix="/api/v1/context", tags=["context"])

TEXT_EXT = (
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".log",
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".h",
    ".cpp",
    ".cs",
    ".sh",
    ".ps1",
    ".toml",
    ".ini",
    ".cfg",
    ".xml",
    ".html",
    ".sql",
)


class UrlIn(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class ScholarIn(BaseModel):
    query: str = Field(min_length=3, max_length=300)
    limit: int = Field(default=8, ge=1, le=20)
    sources: list[str] | None = None
    year_from: int | None = Field(default=None, ge=1900, le=2100)
    year_to: int | None = Field(default=None, ge=1900, le=2100)


class ScholarFormatIn(BaseModel):
    query: str = ""
    items: list[dict]


@router.post("/url")
def ingest_url(body: UrlIn, p: Principal = Depends(require_scope("run"))) -> dict:
    ratelimit.check("context.url", p.name, limit=20, window_s=60)
    try:
        r = fetch_url(body.url)
    except UnsafeURL as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Remote returned {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Fetch failed: {type(exc).__name__}") from exc
    label = r["title"] or r["url"]
    return {
        "label": label,
        "url": r["url"],
        "chars": r["chars"],
        "block": source_block(f"{label} <{r['url']}>", r["text"], "url"),
    }


@router.post("/upload")
async def ingest_upload(file: UploadFile = File(...), p: Principal = Depends(require_scope("run"))) -> dict:
    ratelimit.check("context.upload", p.name, limit=30, window_s=60)
    name = (file.filename or "upload").lower()
    if not name.endswith(TEXT_EXT):
        raise HTTPException(
            status_code=400,
            detail=f"Text-like files only ({', '.join(TEXT_EXT[:8])}, …). PDFs: paste the text for now.",
        )
    raw = await file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"File larger than {MAX_BYTES // 1000} KB")
    text = raw.decode("utf-8", errors="replace")
    return {"label": file.filename, "chars": len(text), "block": source_block(file.filename or "upload", text, "file")}


@router.post("/scholar")
def scholar_search(body: ScholarIn, p: Principal = Depends(require_scope("read"))) -> dict:
    ratelimit.check("context.scholar", p.name, limit=30, window_s=60)
    items = scholar.search(
        body.query, limit=body.limit, sources=body.sources, year_from=body.year_from, year_to=body.year_to
    )
    return {"query": body.query, "count": len(items), "sources": sorted(scholar.SOURCES), "items": items}


@router.post("/scholar/block")
def scholar_block(body: ScholarFormatIn, _: Principal = Depends(require_scope("read"))) -> dict:
    items = [dict(it, n=i) for i, it in enumerate(body.items, 1)]
    for it in items:
        it.setdefault("author", ", ".join((it.get("authors") or [])[:4]))
    return {"block": scholar.evidence_block(items, body.query), "count": len(items)}
