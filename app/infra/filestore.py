"""Local document storage with safe run identifiers and atomic writes."""

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class FileStore:
    """Store generated .docx files beneath one directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def ensure(self) -> None:
        """Create the storage directory."""
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, run_id: str) -> Path:
        """Resolve one document path, rejecting traversal and unsafe names."""
        if not _RUN_ID.fullmatch(run_id):
            raise ValueError("Invalid run id")
        return self.root / f"{run_id}.docx"

    async def save(self, run_id: str, content: bytes) -> Path:
        """Atomically persist rendered document bytes."""
        target = self.path_for(run_id)
        await asyncio.to_thread(self._write, target, content)
        return target

    async def save_named(self, run_id: str, content: bytes, filename: str) -> Path:
        """Persist a document and its human-readable download filename."""
        target = await self.save(run_id, content)
        await asyncio.to_thread(self._write_filename, run_id, filename)
        return target

    async def read(self, run_id: str) -> bytes:
        """Read one rendered document."""
        target = self.path_for(run_id)
        return await asyncio.to_thread(target.read_bytes)

    async def filename_for(self, run_id: str) -> str:
        """Return the display filename recorded for a run, or a safe fallback."""
        await asyncio.to_thread(self.ensure)
        metadata = self.root / f"{run_id}.json"
        try:
            payload = await asyncio.to_thread(metadata.read_text, encoding="utf-8")
            value = json.loads(payload).get("filename")
            if isinstance(value, str) and value.endswith(".docx") and "/" not in value:
                return value
        except (OSError, ValueError, TypeError):
            pass
        return f"{run_id}.docx"

    def _write(self, target: Path, content: bytes) -> None:
        self.ensure()
        descriptor, temporary = tempfile.mkstemp(
            dir=target.parent, prefix=f".{target.stem}-", suffix=".tmp"
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, target)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _write_filename(self, run_id: str, filename: str) -> None:
        if not filename.endswith(".docx") or "/" in filename or "\\" in filename:
            raise ValueError("Invalid document filename")
        metadata = self.root / f"{run_id}.json"
        metadata.write_text(json.dumps({"filename": filename}), encoding="utf-8")


LocalFileStore = FileStore
