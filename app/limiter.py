"""
Shared rate-limiter instance for the entire application.

Importing this module from both main.py and any router guarantees that
all @limiter.limit() decorators reference the SAME Limiter object that
is registered in app.state.limiter — which is what SlowAPIMiddleware
inspects at request time.

Usage in routers:
    from app.limiter import limiter

    @router.post("/some-endpoint")
    @limiter.limit("5/minute")
    async def handler(request: Request, ...):
        ...
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

__all__ = ["limiter"]
