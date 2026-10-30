"""A tiny ASGI test client (no httpx needed): call(app, 'POST', '/api/x', json={...})."""
import asyncio
import json as jsonlib


def call(app, method, path, json=None, data=None, headers=None):
    body = jsonlib.dumps(json).encode() if json is not None else (data or b"")
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if json is not None:
        hdrs.append((b"content-type", b"application/json"))
    hdrs += [(b"host", b"testserver"), (b"content-length", str(len(body)).encode())]
    path_only, _, query = path.partition("?")
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path_only, "raw_path": path_only.encode(),
             "query_string": query.encode(), "root_path": "", "headers": hdrs,
             "client": ("127.0.0.1", 1234), "server": ("testserver", 80)}
    out = {"status": None, "headers": {}, "body": b""}

    async def run():
        sent = False

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.sleep(3600)

        async def send(msg):
            if msg["type"] == "http.response.start":
                out["status"] = msg["status"]
                out["headers"] = {k.decode().lower(): v.decode() for k, v in msg["headers"]}
            elif msg["type"] == "http.response.body":
                out["body"] += msg.get("body", b"")

        await app(scope, receive, send)

    asyncio.run(run())
    ctype = out["headers"].get("content-type", "")
    out["json"] = jsonlib.loads(out["body"]) if ctype.startswith("application/json") else None
    return out
