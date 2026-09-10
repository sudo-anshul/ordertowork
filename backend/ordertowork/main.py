from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text

from ordertowork.config import get_settings
from ordertowork.db import session_factory


def create_app() -> FastAPI:
    app = FastAPI(title="OrderToWork", version="0.1.0", docs_url="/api/docs")

    @app.middleware("http")
    async def request_boundaries(request: Request, call_next):
        settings = get_settings()
        origin = request.headers.get("origin")
        app_origin = urlsplit(settings.app_url)
        expected_origin = f"{app_origin.scheme}://{app_origin.netloc}"
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin != expected_origin:
            return JSONResponse(
                {"detail": {"code": "origin_rejected", "message": "This origin is not allowed."}},
                status_code=403,
            )
        response = await call_next(request)
        response.headers["X-Request-ID"] = str(uuid4())
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "no-cache"
        if settings.environment == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; "
                "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
            )
        return response

    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "ordertowork", "version": "0.1.0"}

    @app.get("/api/ready")
    def ready():
        try:
            with session_factory()() as session:
                session.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ready"}

    # Application routers are registered here as each implementation lands.

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        dist = get_settings().frontend_dist.resolve()
        candidate = (dist / path).resolve()
        if candidate.is_relative_to(dist) and candidate.is_file():
            return FileResponse(candidate)
        index = dist / "index.html"
        if index.is_file() and (not Path(path).suffix or path.startswith("customer/")):
            return FileResponse(index)
        return JSONResponse({"detail": "Frontend is not built. Run npm run dev in frontend."}, status_code=404)

    return app


app = create_app()
