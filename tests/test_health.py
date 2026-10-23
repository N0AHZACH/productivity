"""Operational endpoint tests."""

import httpx
import pytest

from app.main import create_app


@pytest.mark.asyncio
async def test_health_and_ready() -> None:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/health")
        ready = await client.get("/ready")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_request_id_is_echoed_and_errors_are_standardized() -> None:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/does-not-exist", headers={"X-Request-ID": "test-request"})

    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == "test-request"
    error = response.json()["error"]
    assert error["code"] == "NOT_FOUND"
    assert error["request_id"] == "test-request"


@pytest.mark.asyncio
async def test_generated_request_id_is_present() -> None:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.headers["X-Request-ID"]


@pytest.mark.asyncio
async def test_demo_ui_is_served() -> None:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        root = await client.get("/")
        demo = await client.get("/demo")

    assert root.status_code == 200
    assert root.headers["content-type"].startswith("text/html")
    assert "Productivity" in root.text
    assert "Generate document" in root.text
    assert demo.status_code == 200
    assert demo.text == root.text
