"""
web.py - web interface (Starlette / ASGI)
=========================================
    pip install ".[web]"
    library-robot serve                 # http://127.0.0.1:8000
    library-robot serve --host 0.0.0.0  # also reachable from a phone on the same Wi-Fi

All decisions are made in service.py (reasoning engine). This file only turns HTTP
requests into service calls and back to JSON.

Note: there are no accounts yet. Anyone who can open the page can lend and return books,
so keep it on a trusted network.
"""
import logging
import os
from functools import wraps

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from .service import MAX_IMAGE_BYTES, LibraryError, LibraryService

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
SAMPLE_JSON = os.path.join(HERE, "library_data.json")
log = logging.getLogger("library_robot.web")

CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp


def api(fn):
    """Turn LibraryError into a JSON error with the right status code."""
    @wraps(fn)
    async def wrapper(request):
        try:
            return JSONResponse(await fn(request))
        except LibraryError as e:
            return JSONResponse({"error": str(e)}, status_code=e.status)
        except Exception:                                  # never leak internals
            log.exception("unexpected error in %s", fn.__name__)
            return JSONResponse({"error": "Something went wrong on the server."}, status_code=500)
    return wrapper


async def _json(request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise LibraryError("Send a JSON body.")
    if not isinstance(body, dict):
        raise LibraryError("Send a JSON object.")
    return body


def _str(body: dict, key: str) -> str:
    val = body.get(key)
    if not isinstance(val, str) or not val.strip():
        raise LibraryError(f"'{key}' is required.")
    return val.strip()


def create_app(db_path: str = None) -> Starlette:
    db_path = db_path or os.environ.get("LIBRARY_DB", "library.db")
    svc = LibraryService(db_path, sample_json=SAMPLE_JSON)

    async def index(request):
        return FileResponse(os.path.join(STATIC, "index.html"), headers={"Cache-Control": "no-cache"})

    @api
    async def overview(request):
        return await run_in_threadpool(svc.overview)

    @api
    async def books(request):
        return await run_in_threadpool(svc.list_books)

    @api
    async def book(request):
        return await run_in_threadpool(svc.book_detail, request.path_params["book"])

    @api
    async def add_book(request):
        b = await _json(request)
        return await run_in_threadpool(svc.add_book, _str(b, "id"), _str(b, "title"),
                                       _str(b, "shelf"), b.get("copies", 1))

    @api
    async def users(request):
        return await run_in_threadpool(svc.list_users)

    @api
    async def add_user(request):
        return await run_in_threadpool(svc.add_user, _str(await _json(request), "name"))

    @api
    async def shelves(request):
        return await run_in_threadpool(svc.list_shelves)

    @api
    async def add_shelf(request):
        b = await _json(request)
        return await run_in_threadpool(svc.add_shelf, _str(b, "id"), _str(b, "room"), _str(b, "row"))

    @api
    async def loans(request):
        return await run_in_threadpool(svc.loans)

    @api
    async def events(request):
        return await run_in_threadpool(svc.events, request.query_params.get("n", 30))

    @api
    async def rules(request):
        return svc.rules()

    @api
    async def checkout(request):
        b = await _json(request)
        return await run_in_threadpool(svc.checkout, _str(b, "user"), _str(b, "book"))

    @api
    async def checkin(request):
        b = await _json(request)
        return await run_in_threadpool(svc.checkin, _str(b, "user"), _str(b, "book"))

    @api
    async def reserve(request):
        b = await _json(request)
        return await run_in_threadpool(svc.reserve, _str(b, "user"), _str(b, "book"))

    @api
    async def ask(request):
        b = await _json(request)
        given = b.get("given") or []
        if not isinstance(given, list) or not all(isinstance(g, str) for g in given):
            raise LibraryError("'given' must be a list of fact strings.")
        return await run_in_threadpool(svc.ask, _str(b, "query"), given[:10])

    @api
    async def scan(request):
        size = request.headers.get("content-length")
        if size and size.isdigit() and int(size) > MAX_IMAGE_BYTES:
            raise LibraryError("Image is too large (max 8 MB).")
        data = await request.body()
        return await run_in_threadpool(svc.scan_image, data)

    async def qr(request):
        try:
            png = await run_in_threadpool(svc.qr_png, request.path_params["book"])
        except LibraryError as e:
            return JSONResponse({"error": str(e)}, status_code=e.status)
        return Response(png, media_type="image/png")

    routes = [
        Route("/", index),
        Route("/api/overview", overview),
        Route("/api/books", books),
        Route("/api/books", add_book, methods=["POST"]),
        Route("/api/books/{book}", book),
        Route("/api/users", users),
        Route("/api/users", add_user, methods=["POST"]),
        Route("/api/shelves", shelves),
        Route("/api/shelves", add_shelf, methods=["POST"]),
        Route("/api/loans", loans),
        Route("/api/events", events),
        Route("/api/rules", rules),
        Route("/api/checkout", checkout, methods=["POST"]),
        Route("/api/return", checkin, methods=["POST"]),
        Route("/api/reserve", reserve, methods=["POST"]),
        Route("/api/ask", ask, methods=["POST"]),
        Route("/api/scan", scan, methods=["POST"]),
        Route("/api/qr/{book}.png", qr),
    ]
    app = Starlette(routes=routes)
    app.add_middleware(SecurityHeaders)
    app.state.service = svc
    return app


def run(host="127.0.0.1", port=8000, db_path=None):
    import uvicorn
    uvicorn.run(create_app(db_path), host=host, port=port)
