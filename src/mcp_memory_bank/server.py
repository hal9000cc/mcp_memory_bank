"""MCP server implementation for Memory Bank."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .storage import MemoryBankStorage

_storage: MemoryBankStorage | None = None

app = Server("memory-bank")


def _get_storage() -> MemoryBankStorage:
    if _storage is None:
        raise RuntimeError("Storage not initialized")
    return _storage


# ------------------------------------------------------------------
# Tool definitions
# ------------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="memory_bank_read_context",
            description=(
                "Start-of-session tool. Returns metadata of ALL documents "
                "and full content of core documents (core=true). "
                "Call this at the very beginning of every session."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="memory_bank_read_documents",
            description="Read one or more documents by name and return their full content.",
            inputSchema={
                "type": "object",
                "properties": {
                    "names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of document names to read (e.g. ['architecture.md'])",
                        "minItems": 1,
                    }
                },
                "required": ["names"],
            },
        ),
        Tool(
            name="memory_bank_write_document",
            description=(
                "Create or fully overwrite a document. "
                "Content must be Markdown. Tags must contain at least 2 items."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Document filename, e.g. 'activeTask.md'",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full Markdown content of the document",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags for search (minimum 2, lowercase English)",
                        "minItems": 2,
                    },
                    "core": {
                        "type": "boolean",
                        "description": "Load automatically at session start (default: false)",
                        "default": False,
                    },
                },
                "required": ["name", "content", "tags"],
            },
        ),
        Tool(
            name="memory_bank_search_by_tags",
            description=(
                "Search documents by tags. Returns only metadata (no content). "
                "A document matches only if it contains ALL specified tags."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags to search for (document must have all of them)",
                        "minItems": 1,
                    }
                },
                "required": ["tags"],
            },
        ),
    ]


# ------------------------------------------------------------------
# Tool handlers
# ------------------------------------------------------------------

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    storage = _get_storage()

    if name == "memory_bank_read_context":
        return _handle_read_context(storage)

    if name == "memory_bank_read_documents":
        names = arguments.get("names", [])
        return _handle_read_documents(storage, names)

    if name == "memory_bank_write_document":
        return _handle_write_document(storage, arguments)

    if name == "memory_bank_search_by_tags":
        tags = arguments.get("tags", [])
        return _handle_search_by_tags(storage, tags)

    return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]


def _handle_read_context(storage: MemoryBankStorage) -> list[TextContent]:
    all_docs = storage.read_all_metadata()

    core_names = [d.name for d in all_docs if d.core]
    core_with_content = storage.read_documents(core_names)
    core_map = {d.name: d for d in core_with_content if d is not None}

    result = []
    for doc in all_docs:
        if doc.core and doc.name in core_map:
            result.append(core_map[doc.name].to_dict(include_content=True))
        else:
            result.append(doc.to_dict(include_content=True))  # content=None already

    return [TextContent(type="text", text=json.dumps({"documents": result}, ensure_ascii=False))]


def _handle_read_documents(storage: MemoryBankStorage, names: list[str]) -> list[TextContent]:
    docs = storage.read_documents(names)
    result = []
    for doc in docs:
        if doc is None:
            result.append(None)
        else:
            result.append(doc.to_dict(include_content=True))
    return [TextContent(type="text", text=json.dumps({"documents": result}, ensure_ascii=False))]


def _handle_write_document(storage: MemoryBankStorage, arguments: dict) -> list[TextContent]:
    name = arguments.get("name", "")
    content = arguments.get("content", "")
    tags = arguments.get("tags", [])
    core = bool(arguments.get("core", False))

    if not name:
        return [TextContent(type="text", text=json.dumps({"error": "name is required"}))]
    if len(tags) < 2:
        return [TextContent(type="text", text=json.dumps({"error": "at least 2 tags required"}))]

    doc = storage.write_document(name=name, content=content, tags=tags, core=core)
    return [TextContent(
        type="text",
        text=json.dumps({
            "success": True,
            "name": doc.name,
            "lastModified": doc.last_modified,
        }),
    )]


def _handle_search_by_tags(storage: MemoryBankStorage, tags: list[str]) -> list[TextContent]:
    docs = storage.search_by_tags(tags)
    result = [doc.to_dict(include_content=False) for doc in docs]
    return [TextContent(type="text", text=json.dumps({"documents": result}, ensure_ascii=False))]


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

async def _run(base_dir: Path) -> None:
    global _storage
    _storage = MemoryBankStorage(base_dir)

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MCP Memory Bank server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  mcp-memory-bank --dir /path/to/project/.memory_bank\n"
            "  mcp-memory-bank  # uses .memory_bank/ in current directory\n"
        ),
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=Path.cwd() / ".memory_bank",
        help="Path to the memory bank storage directory (default: .memory_bank/ in cwd)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(_run(args.dir))
    except KeyboardInterrupt:
        sys.exit(0)
