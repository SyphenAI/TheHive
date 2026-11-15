from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import User  # noqa: F401  (register models)
from app.routers import auth, hive, providers, system
from app.security import hash_password
from app.services.audit import log_event
from app.services.registry import load_registry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("thehive")


def _seed_admin() -> None:
    cfg = get_settings()
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            db.add(
                User(
                    username=cfg.default_admin_username,
                    display_name="Hive Admin",
                    password_hash=hash_password(cfg.default_admin_password),
                    role="admin",
                    must_change_password=True,
                )
            )
            log_event(db, actor="system", action="seed_admin", detail=cfg.default_admin_username)
            db.commit()
            logger.warning("Seeded admin '%s'. Change the password on first login.", cfg.default_admin_username)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    cfg = get_settings()  # raises on bad secrets -> app refuses to start
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    (cfg.storage_dir / "sessions").mkdir(parents=True, exist_ok=True)
    (cfg.storage_dir / "tmp").mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    reg = load_registry()
    logger.info("Registry loaded: %s", ", ".join(sorted(reg.providers)))
    _seed_admin()
    yield


cfg = get_settings()
app = FastAPI(
    title="TheHive",
    description="Multi-model deliberation: independent answers, anonymized critique, revision, judged verdict.",
    version=system.VERSION,
    lifespan=lifespan,
    docs_url="/docs" if cfg.enable_api_docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if cfg.enable_api_docs else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.cors_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["authorization", "content-type"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):  # noqa: ANN001
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Cache-Control", "no-store")
    return resp


app.include_router(system.router)
app.include_router(auth.router)
app.include_router(providers.router)
app.include_router(hive.router)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return FileResponse(STATIC_DIR / "index.html")
else:

    @app.get("/", include_in_schema=False)
    async def root():
        return {"app": "TheHive", "message": "API up; frontend not built. Run `npm run build` in frontend/."}
