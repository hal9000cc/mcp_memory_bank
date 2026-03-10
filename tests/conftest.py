"""Shared pytest fixtures."""

import pathlib
import pytest

from mcp_memory_bank.storage import MemoryBankStorage


@pytest.fixture
def tmp_storage(tmp_path: pathlib.Path) -> MemoryBankStorage:
    """Fresh MemoryBankStorage in a temporary directory."""
    return MemoryBankStorage(tmp_path / ".memory_bank")


@pytest.fixture
def populated_storage(tmp_storage: MemoryBankStorage) -> MemoryBankStorage:
    """Storage pre-filled with three documents for convenience."""
    tmp_storage.write_document(
        "",
        "context.md",
        "# Project context\n\nTest project.",
        ["context", "global"],
        core=True,
    )
    tmp_storage.write_document(
        "",
        "activeTask.md",
        "# Active task\n\nDoing stuff.",
        ["task", "active"],
        core=True,
    )
    tmp_storage.write_document(
        "",
        "architecture.md",
        "# Architecture\n\nUsing SQLite.",
        ["decision", "architecture", "database"],
        core=False,
    )
    return tmp_storage
