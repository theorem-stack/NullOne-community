from pathlib import Path

import httpx
import pytest

from nullone.sources.eia_wngsr import WNGSRPoller


def _redirecting_eia_transport() -> httpx.MockTransport:
    csv_bytes = Path("tests/fixtures/wngsr_sample.csv").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ngs/wngsr.json":
            return httpx.Response(503, request=request)
        if request.url.path == "/ngs/wngsr.csv":
            return httpx.Response(
                302,
                headers={"Location": "https://ir.eia.gov/secure/ngs/wngsr.csv?token=test"},
                request=request,
            )
        if request.url.path == "/secure/ngs/wngsr.csv":
            return httpx.Response(200, content=csv_bytes, request=request)
        return httpx.Response(404, request=request)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_fetch_once_follows_eia_csv_redirect_with_injected_client():
    async with httpx.AsyncClient(transport=_redirecting_eia_transport()) as client:
        async with WNGSRPoller(client=client) as poller:
            result = await poller.fetch_once()

    assert result.source == "csv"
    assert result.reading.net_change_bcf == 101


@pytest.mark.asyncio
async def test_stream_fetch_follows_eia_csv_redirect_with_injected_client():
    async with httpx.AsyncClient(transport=_redirecting_eia_transport()) as client:
        async with WNGSRPoller(client=client) as poller:
            result = await poller.fetch_csv_stream_once()

    assert result.source == "csv-stream"
    assert result.reading.net_change_bcf == 101
