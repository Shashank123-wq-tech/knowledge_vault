import logging
import sqlite3
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import MAX_BODY_BYTES, database_path
from app.database import connect, initialize
from app.middleware import BodyLimitMiddleware
from app.routes import router


def create_app(db_path: str | None = None) -> FastAPI:
    path = db_path or database_path()

    @asynccontextmanager
    async def lifespan(app):
        initialize(path)
        yield

    app = FastAPI(title="KnowledgeVault — Document Search API", version="1.0.0", lifespan=lifespan,
                  description="Private text collections, durable indexing and BM25 keyword search. "
                              "Register, log in, paste your access_token into Authorize, then create a collection. "
                              "Run python worker.py in a second terminal to index documents.")
    app.state.db_path = path
    app.add_middleware(BodyLimitMiddleware, max_bytes=MAX_BODY_BYTES)
    app.include_router(router)

    @app.get("/health", tags=["System"])
    def health():
        with connect(path) as db:
            db.execute("SELECT 1").fetchone()
        # API availability does not imply that a worker process is running.
        return {"status": "ok", "service": "api"}

    @app.middleware("http")
    async def timing(request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - start) * 1000:.2f}"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(sqlite3.IntegrityError)
    async def conflict(request, error):
        return JSONResponse(status_code=409, content={"detail": "Username, collection name or document content already exists"})

    @app.exception_handler(sqlite3.OperationalError)
    async def unavailable(request, error):
        logging.getLogger(__name__).error("Database operation failed", exc_info=error)
        return JSONResponse(status_code=503, content={"detail": "Database unavailable; retry shortly"})

    return app
