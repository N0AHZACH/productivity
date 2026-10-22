"""Filesystem safety tests for generated document storage."""

from pathlib import Path

import pytest

from app.infra.filestore import FileStore


@pytest.mark.asyncio
async def test_store_round_trip_and_rejects_unsafe_ids(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "documents")
    path = await store.save("run_01", b"document bytes")

    assert path == tmp_path / "documents" / "run_01.docx"
    assert await store.read("run_01") == b"document bytes"

    with pytest.raises(ValueError):
        await store.read("../outside")
    with pytest.raises(ValueError):
        await store.save("bad/id", b"x")
