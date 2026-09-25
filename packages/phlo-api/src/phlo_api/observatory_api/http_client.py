"""Share backend HTTP connections across requests during the API lifespan."""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

import httpx

_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan_client() -> AsyncIterator[None]:
    """Open and close the backend connection pool with the application."""
    global _client
    async with httpx.AsyncClient() as client:
        _client = client
        try:
            yield
        finally:
            _client = None


@asynccontextmanager
async def backend_client(timeout: float) -> AsyncIterator[httpx.AsyncClient]:
    """Borrow the shared client, or create one for standalone helper calls."""
    if _client is not None:
        yield _client
    else:
        async with httpx.AsyncClient(timeout=timeout) as client:
            yield client
