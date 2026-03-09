"""Tests for MCP server tool handlers."""

import asyncio
import json
import pathlib

import pytest

import mcp_memory_bank.server as server_module
from mcp_memory_bank.server import (
    _handle_read_context,
    _handle_read_documents,
    _handle_search_by_tags,
    _handle_write_document,
    list_tools,
)
from mcp_memory_bank.storage import MemoryBankStorage

TEST_PROJECT_ID = "/home/test/my_project"


@pytest.fixture(autouse=True)
def inject_storage(tmp_path: pathlib.Path):
    """Inject a fresh storage instance into the server module for every test."""
    storage = MemoryBankStorage(tmp_path / ".memory_bank")
    server_module._storages.clear()
    server_module._storages[TEST_PROJECT_ID] = storage
    yield storage
    server_module._storages.clear()


def _json(text_contents) -> dict:
    """Extract and parse JSON from the first TextContent item."""
    return json.loads(text_contents[0].text)


# ------------------------------------------------------------------
# list_tools
# ------------------------------------------------------------------

def test_list_tools_returns_four():
    tools = asyncio.run(list_tools())
    assert len(tools) == 4


def test_list_tools_names():
    tools = asyncio.run(list_tools())
    names = {t.name for t in tools}
    assert names == {
        "memory_bank_read_context",
        "memory_bank_read_documents",
        "memory_bank_write_document",
        "memory_bank_search_by_tags",
    }


def test_list_tools_all_have_project_id():
    tools = asyncio.run(list_tools())
    for tool in tools:
        props = tool.inputSchema.get("properties", {})
        assert "project_id" in props, f"Tool {tool.name} missing project_id"
        assert "project_id" in tool.inputSchema.get("required", [])


# ------------------------------------------------------------------
# memory_bank_read_context
# ------------------------------------------------------------------

def test_read_context_empty(inject_storage):
    result = _json(_handle_read_context(inject_storage))
    assert result["documents"] == []


def test_read_context_core_docs_have_content(inject_storage):
    inject_storage.write_document("ctx.md", "# Ctx", ["context", "global"], core=True)
    inject_storage.write_document("arch.md", "# Arch", ["decision", "architecture"], core=False)

    result = _json(_handle_read_context(inject_storage))
    docs = {d["name"]: d for d in result["documents"]}

    assert docs["ctx.md"]["content"] == "# Ctx"
    assert docs["arch.md"]["content"] is None


def test_read_context_non_core_docs_no_content(inject_storage):
    inject_storage.write_document("nc.md", "body", ["a", "b"], core=False)
    result = _json(_handle_read_context(inject_storage))
    assert result["documents"][0]["content"] is None


# ------------------------------------------------------------------
# memory_bank_read_documents
# ------------------------------------------------------------------

def test_read_documents_returns_content(inject_storage):
    inject_storage.write_document("doc.md", "# Content here", ["a", "b"])
    result = _json(_handle_read_documents(inject_storage, ["doc.md"]))
    assert result["documents"][0]["content"] == "# Content here"


def test_read_documents_missing_returns_null(inject_storage):
    result = _json(_handle_read_documents(inject_storage, ["ghost.md"]))
    assert result["documents"][0] is None


def test_read_documents_mixed(inject_storage):
    inject_storage.write_document("exists.md", "hi", ["a", "b"])
    result = _json(_handle_read_documents(inject_storage, ["exists.md", "missing.md"]))
    assert result["documents"][0]["name"] == "exists.md"
    assert result["documents"][1] is None


# ------------------------------------------------------------------
# memory_bank_write_document
# ------------------------------------------------------------------

def test_write_document_success(inject_storage):
    result = _json(_handle_write_document(inject_storage, {
        "name": "new.md",
        "content": "# New",
        "tags": ["tag1", "tag2"],
        "core": False,
    }))
    assert result["success"] is True
    assert result["name"] == "new.md"
    assert "lastModified" in result


def test_write_document_missing_name(inject_storage):
    result = _json(_handle_write_document(inject_storage, {
        "name": "",
        "content": "x",
        "tags": ["a", "b"],
    }))
    assert "error" in result


def test_write_document_too_few_tags(inject_storage):
    result = _json(_handle_write_document(inject_storage, {
        "name": "doc.md",
        "content": "x",
        "tags": ["only_one"],
    }))
    assert "error" in result


def test_write_document_persisted(inject_storage):
    _handle_write_document(inject_storage, {
        "name": "saved.md",
        "content": "Saved content",
        "tags": ["a", "b"],
    })
    loaded = inject_storage.read_documents(["saved.md"])[0]
    assert loaded is not None
    assert loaded.content == "Saved content"


# ------------------------------------------------------------------
# memory_bank_search_by_tags
# ------------------------------------------------------------------

def test_search_by_tags_finds_match(inject_storage):
    inject_storage.write_document("found.md", "x", ["search", "test"])
    inject_storage.write_document("other.md", "y", ["unrelated", "doc"])
    result = _json(_handle_search_by_tags(inject_storage, ["search"]))
    assert len(result["documents"]) == 1
    assert result["documents"][0]["name"] == "found.md"


def test_search_by_tags_no_match(inject_storage):
    inject_storage.write_document("doc.md", "x", ["a", "b"])
    result = _json(_handle_search_by_tags(inject_storage, ["nonexistent"]))
    assert result["documents"] == []


def test_search_by_tags_no_content_returned(inject_storage):
    inject_storage.write_document("doc.md", "body text", ["a", "b"])
    result = _json(_handle_search_by_tags(inject_storage, ["a"]))
    assert "content" not in result["documents"][0]


# ------------------------------------------------------------------
# call_tool routing
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_tool_routing_write(inject_storage):
    from mcp_memory_bank.server import call_tool
    result = _json(await call_tool("memory_bank_write_document", {
        "project_id": TEST_PROJECT_ID,
        "name": "routed.md",
        "content": "# Routed",
        "tags": ["route", "test"],
    }))
    assert result["success"] is True


@pytest.mark.asyncio
async def test_call_tool_missing_project_id(inject_storage):
    from mcp_memory_bank.server import call_tool
    result = _json(await call_tool("memory_bank_read_context", {}))
    assert "error" in result


@pytest.mark.asyncio
async def test_call_tool_unknown_tool(inject_storage):
    from mcp_memory_bank.server import call_tool
    result = _json(await call_tool("nonexistent_tool", {"project_id": TEST_PROJECT_ID}))
    assert "error" in result
