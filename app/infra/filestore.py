"""Local document storage with safe run identifiers and atomic writes."""

import asyncio
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

    async def read(self, run_id: str) -> bytes:
        """Read one rendered document."""
        target = self.path_for(run_id)
        return await asyncio.to_thread(target.read_bytes)

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


LocalFileStore = FileStore
