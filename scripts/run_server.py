"""Real (non-demo) backend entry point for local development on Windows.

Not part of the application itself — never imported by src/ or tests/.

Why this exists instead of the bare `uvicorn api.main:app` CLI: `psycopg`'s
async mode cannot run on asyncio's default Windows event loop
(`ProactorEventLoop`) — every async database call raises
`psycopg.InterfaceError` without this fix.

This is NOT simply `asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())`
before calling `uvicorn.run(...)` — that looked like it worked once, then
failed on a second run. Root cause, confirmed directly against this
project's installed uvicorn (>=0.36): `uvicorn.run()` no longer consults
the global event loop policy at all. `Config.get_loop_factory()` decides
the loop class itself and hands it to `asyncio.run(..., loop_factory=...)`
internally, and for `loop="asyncio"`/`"auto"` on Windows that factory
always returns `ProactorEventLoop`, silently overriding any policy set
beforehand. There is no public `uvicorn` CLI flag or `Config` option to
change this. The only reliable fix is to not go through `Server.run()`
(the method that does that internal `asyncio.run(..., loop_factory=...)`
call) at all: build the `Server` ourselves, create a real
`SelectorEventLoop` explicitly, and drive `Server.serve()` — the actual
coroutine — on it directly.

Usage (from the repo root, real Postgres/Kafka/Ollama already running —
see docker-compose.yml and README instructions):

    python scripts/run_server.py
"""

from __future__ import annotations

import asyncio
import sys

import uvicorn


async def _serve() -> None:
    config = uvicorn.Config("api.main:app", host="0.0.0.0", port=8000, reload=False)
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_serve())
        finally:
            loop.close()
    else:
        asyncio.run(_serve())
