
import asyncio
import pytest
from aiohttp import web

from crypto_dashboard.main import csp_middleware

@pytest.fixture
def cli(aiohttp_client):
    async def handler(request):
        return web.Response(text="Hello")

    app = web.Application(middlewares=[csp_middleware])
    app.router.add_get('/', handler)
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(aiohttp_client(app))

@pytest.mark.asyncio
async def test_csp_middleware(cli):
    resp = await cli.get('/')
    assert resp.status == 200
    assert 'Content-Security-Policy' in resp.headers
    expected_csp = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self';"
    )
    assert resp.headers['Content-Security-Policy'] == expected_csp
