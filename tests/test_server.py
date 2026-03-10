"""Tests for MCP server tool handlers."""

import asyncio
import json
import pathlib

import pytest

import mcp_memory_bank.server as server_module
from mcp_memory_bank.server import (
    _handle_delete_document,
    _handle_append_content,
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
    server_module._storage = None
    server_module._storage = storage
    yield storage
    server_module._storage = None


def _json(text_contents) -> dict:
    """Extract and parse JSON from the first TextContent item."""
    return json.loads(text_contents[0].text)


# ------------------------------------------------------------------
# list_tools
# ------------------------------------------------------------------

def test_list_tools_returns_six():
    tools = asyncio.run(list_tools())
    assert len(tools) == 6


def test_list_tools_names():
    tools = asyncio.run(list_tools())
    names = {t.name for t in tools}
    assert names == {
        "memory_bank_read_context",
        "memory_bank_read_documents",
        "memory_bank_write_document",
        "memory_bank_search_by_tags",
        "memory_bank_append_content",
        "memory_bank_delete_document",
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
    result = _json(_handle_read_context(inject_storage, TEST_PROJECT_ID))
    assert result["documents"] == []


def test_read_context_core_docs_have_content(inject_storage):
    inject_storage.write_document(TEST_PROJECT_ID, "ctx.md", "# Ctx", ["context", "global"], core=True)
    inject_storage.write_document(TEST_PROJECT_ID, "arch.md", "# Arch", ["decision", "architecture"], core=False)

    result = _json(_handle_read_context(inject_storage, TEST_PROJECT_ID))
    docs = {d["name"]: d for d in result["documents"]}

    assert docs["ctx.md"]["content"] == "# Ctx"
    assert docs["arch.md"]["content"] is None


def test_read_context_non_core_docs_no_content(inject_storage):
    inject_storage.write_document(TEST_PROJECT_ID, "nc.md", "body", ["a", "b"], core=False)
    result = _json(_handle_read_context(inject_storage, TEST_PROJECT_ID))
    assert result["documents"][0]["content"] is None


def test_read_context_indexes_existing_project_local_files(tmp_path):
    project_id = str(tmp_path / "project")
    docs_dir = pathlib.Path(project_id) / ".memory_bank" / "documents"
    docs_dir.mkdir(parents=True)
    docs_dir.joinpath("context.md").write_text(
        "---\ntags:\n- context\n- global\ncore: true\nlastModified: '2026-03-09T13:11:56Z'\n---\n\n# Context",
        encoding="utf-8",
    )

    storage = MemoryBankStorage(tmp_path / "shared_root", project_local=True)

    result = _json(_handle_read_context(storage, project_id))

    assert [doc["name"] for doc in result["documents"]] == ["context.md"]
    assert result["documents"][0]["content"] == "# Context"


# ------------------------------------------------------------------
# memory_bank_read_documents
# ------------------------------------------------------------------

def test_read_documents_returns_content(inject_storage):
    inject_storage.write_document(TEST_PROJECT_ID, "doc.md", "# Content here", ["a", "b"])
    result = _json(_handle_read_documents(inject_storage, TEST_PROJECT_ID, ["doc.md"]))
    assert result["documents"][0]["content"] == "# Content here"


def test_read_documents_missing_returns_null(inject_storage):
    result = _json(_handle_read_documents(inject_storage, TEST_PROJECT_ID, ["ghost.md"]))
    assert result["documents"][0] is None


def test_read_documents_mixed(inject_storage):
    inject_storage.write_document(TEST_PROJECT_ID, "exists.md", "hi", ["a", "b"])
    result = _json(_handle_read_documents(inject_storage, TEST_PROJECT_ID, ["exists.md", "missing.md"]))
    assert result["documents"][0]["name"] == "exists.md"
    assert result["documents"][1] is None


# ------------------------------------------------------------------
# memory_bank_write_document
# ------------------------------------------------------------------

def test_write_document_success(inject_storage):
    result = _json(_handle_write_document(inject_storage, TEST_PROJECT_ID, {
        "name": "new.md",
        "content": "# New",
        "tags": ["tag1", "tag2"],
        "core": False,
    }))
    assert result["success"] is True
    assert result["name"] == "new.md"
    assert "lastModified" in result


def test_write_document_missing_name(inject_storage):
    result = _json(_handle_write_document(inject_storage, TEST_PROJECT_ID, {
        "name": "",
        "content": "x",
        "tags": ["a", "b"],
    }))
    assert "error" in result


def test_write_document_too_few_tags(inject_storage):
    result = _json(_handle_write_document(inject_storage, TEST_PROJECT_ID, {
        "name": "doc.md",
        "content": "x",
        "tags": ["only_one"],
    }))
    assert "error" in result


def test_write_document_persisted(inject_storage):
    _handle_write_document(inject_storage, TEST_PROJECT_ID, {
        "name": "saved.md",
        "content": "Saved content",
        "tags": ["a", "b"],
    })
    loaded = inject_storage.read_documents(TEST_PROJECT_ID, ["saved.md"])[0]
    assert loaded is not None
    assert loaded.content == "Saved content"


# ------------------------------------------------------------------
# memory_bank_search_by_tags
# ------------------------------------------------------------------

def test_search_by_tags_finds_match(inject_storage):
    inject_storage.write_document(TEST_PROJECT_ID, "found.md", "x", ["search", "test"])
    inject_storage.write_document(TEST_PROJECT_ID, "other.md", "y", ["unrelated", "doc"])
    result = _json(_handle_search_by_tags(inject_storage, TEST_PROJECT_ID, ["search"]))
    assert len(result["documents"]) == 1
    assert result["documents"][0]["name"] == "found.md"


def test_search_by_tags_no_match(inject_storage):
    inject_storage.write_document(TEST_PROJECT_ID, "doc.md", "x", ["a", "b"])
    result = _json(_handle_search_by_tags(inject_storage, TEST_PROJECT_ID, ["nonexistent"]))
    assert result["documents"] == []


def test_search_by_tags_no_content_returned(inject_storage):
    inject_storage.write_document(TEST_PROJECT_ID, "doc.md", "body text", ["a", "b"])
    result = _json(_handle_search_by_tags(inject_storage, TEST_PROJECT_ID, ["a"]))
    assert "content" not in result["documents"][0]


def test_search_by_tags_includes_common_storage(inject_storage):
    inject_storage.write_document("", "shared.md", "shared body", ["search", "shared"])
    inject_storage.write_document(TEST_PROJECT_ID, "project.md", "project body", ["search", "shared"])

    result = _json(_handle_search_by_tags(inject_storage, TEST_PROJECT_ID, ["search", "shared"]))

    assert [(doc["name"], doc["common"]) for doc in result["documents"]] == [
        ("shared.md", True),
        ("project.md", False),
    ]


# ------------------------------------------------------------------
# memory_bank_append_content
# ------------------------------------------------------------------

def test_append_content_success(inject_storage):
    inject_storage.write_document(TEST_PROJECT_ID, "log.md", "# Log", ["log", "progress"])
    result = _json(_handle_append_content(inject_storage, TEST_PROJECT_ID, {
        "name": "log.md",
        "content": "## New entry",
    }))
    assert result["success"] is True
    assert result["name"] == "log.md"
    assert "lastModified" in result
    assert result["contentLength"] > len("# Log")


def test_append_content_creates_new_document(inject_storage):
    result = _json(_handle_append_content(inject_storage, TEST_PROJECT_ID, {
        "name": "new_log.md",
        "content": "## First entry",
        "tags": ["log", "progress"],
    }))
    assert result["success"] is True
    assert result["contentLength"] == len("## First entry")
    loaded = inject_storage.read_documents(TEST_PROJECT_ID, ["new_log.md"])[0]
    assert loaded is not None


def test_append_content_missing_name(inject_storage):
    result = _json(_handle_append_content(inject_storage, TEST_PROJECT_ID, {
        "name": "",
        "content": "some text",
    }))
    assert "error" in result


def test_append_content_missing_content(inject_storage):
    result = _json(_handle_append_content(inject_storage, TEST_PROJECT_ID, {
        "name": "doc.md",
        "content": "",
    }))
    assert "error" in result


def test_append_content_new_doc_no_tags_error(inject_storage):
    result = _json(_handle_append_content(inject_storage, TEST_PROJECT_ID, {
        "name": "ghost.md",
        "content": "data",
    }))
    assert "error" in result


def test_append_content_persisted(inject_storage):
    inject_storage.write_document(TEST_PROJECT_ID, "doc.md", "original", ["a", "b"])
    _handle_append_content(inject_storage, TEST_PROJECT_ID, {
        "name": "doc.md",
        "content": "appended",
    })
    loaded = inject_storage.read_documents(TEST_PROJECT_ID, ["doc.md"])[0]
    assert "original" in loaded.content
    assert "appended" in loaded.content


# ------------------------------------------------------------------
# memory_bank_delete_document
# ------------------------------------------------------------------

def test_delete_document_success(inject_storage):
    inject_storage.write_document(TEST_PROJECT_ID, "delete.md", "bye", ["a", "b"])

    result = _json(_handle_delete_document(inject_storage, TEST_PROJECT_ID, {
        "name": "delete.md",
    }))

    assert result["success"] is True
    assert result["name"] == "delete.md"
    assert inject_storage.read_documents(TEST_PROJECT_ID, ["delete.md"])[0] is None


def test_delete_document_missing_name(inject_storage):
    result = _json(_handle_delete_document(inject_storage, TEST_PROJECT_ID, {
        "name": "",
    }))
    assert "error" in result


def test_delete_document_not_found(inject_storage):
    result = _json(_handle_delete_document(inject_storage, TEST_PROJECT_ID, {
        "name": "ghost.md",
    }))
    assert result["error"] == "Document not found: ghost.md"


def test_delete_document_from_common_storage(inject_storage):
    inject_storage.write_document("", "shared.md", "shared body", ["shared", "test"])

    result = _json(_handle_delete_document(inject_storage, "", {
        "name": "shared.md",
    }))

    assert result["success"] is True
    assert inject_storage.read_documents("", ["shared.md"])[0] is None


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
async def test_call_tool_common_storage_write(inject_storage):
    from mcp_memory_bank.server import call_tool
    result = _json(await call_tool("memory_bank_write_document", {
        "project_id": "",
        "name": "shared.md",
        "content": "# Shared",
        "tags": ["shared", "release"],
    }))
    assert result["success"] is True


@pytest.mark.asyncio
async def test_call_tool_routing_append(inject_storage):
    from mcp_memory_bank.server import call_tool
    inject_storage.write_document(TEST_PROJECT_ID, "routed_log.md", "entry1", ["log", "test"])
    result = _json(await call_tool("memory_bank_append_content", {
        "project_id": TEST_PROJECT_ID,
        "name": "routed_log.md",
        "content": "entry2",
    }))
    assert result["success"] is True
    assert "contentLength" in result


@pytest.mark.asyncio
async def test_call_tool_routing_delete(inject_storage):
    from mcp_memory_bank.server import call_tool
    inject_storage.write_document(TEST_PROJECT_ID, "routed_delete.md", "bye", ["route", "test"])

    result = _json(await call_tool("memory_bank_delete_document", {
        "project_id": TEST_PROJECT_ID,
        "name": "routed_delete.md",
    }))

    assert result["success"] is True


@pytest.mark.asyncio
async def test_call_tool_delete_not_found(inject_storage):
    from mcp_memory_bank.server import call_tool
    result = _json(await call_tool("memory_bank_delete_document", {
        "project_id": TEST_PROJECT_ID,
        "name": "ghost.md",
    }))

    assert result["error"] == "Document not found: ghost.md"


def test_search_by_tags_returns_size(inject_storage):
    inject_storage.write_document(TEST_PROJECT_ID, "doc.md", "body text", ["a", "b"])
    result = _json(_handle_search_by_tags(inject_storage, TEST_PROJECT_ID, ["a"]))
    assert result["documents"][0]["size"] > 0


@pytest.mark.asyncio
async def test_call_tool_unknown_tool(inject_storage):
    from mcp_memory_bank.server import call_tool
    result = _json(await call_tool("nonexistent_tool", {"project_id": TEST_PROJECT_ID}))
    assert "error" in result
