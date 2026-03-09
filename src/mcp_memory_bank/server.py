"""MCP server implementation for Memory Bank."""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .storage import MemoryBankStorage

logger = logging.getLogger("memory-bank")

_storage: MemoryBankStorage | None = None
_response_delay_ms: int = 0

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
    import time
    t0 = time.monotonic()
    logger.debug(">>> call_tool: name=%s, args=%s", name, arguments)
    try:
        storage = _get_storage()
        result = _dispatch_tool(name, arguments, storage)
        if _response_delay_ms > 0:
            logger.debug("call_tool: sleeping %dms before response", _response_delay_ms)
            await asyncio.sleep(_response_delay_ms / 1000)
        elapsed = time.monotonic() - t0
        total_len = sum(len(c.text) for c in result)
        logger.debug("<<< call_tool: name=%s, elapsed=%.3fs (delay=%dms), response_bytes=%d", name, elapsed, _response_delay_ms, total_len)
        return result
    except Exception:
        elapsed = time.monotonic() - t0
        logger.exception("!!! call_tool exception: name=%s, elapsed=%.3fs", name, elapsed)
        return [TextContent(type="text", text=json.dumps({"error": "Internal server error"}))]


def _dispatch_tool(name: str, arguments: dict, storage: MemoryBankStorage) -> list[TextContent]:
    if name == "memory_bank_read_context":
        logger.debug("tool: read_context")
        return _handle_read_context(storage)

    if name == "memory_bank_read_documents":
        names = arguments.get("names", [])
        logger.debug("tool: read_documents, names=%s", names)
        return _handle_read_documents(storage, names)

    if name == "memory_bank_write_document":
        logger.debug(
            "tool: write_document, name=%s, tags=%s, core=%s",
            arguments.get("name"),
            arguments.get("tags"),
            arguments.get("core", False),
        )
        return _handle_write_document(storage, arguments)

    if name == "memory_bank_search_by_tags":
        tags = arguments.get("tags", [])
        logger.debug("tool: search_by_tags, tags=%s", tags)
        return _handle_search_by_tags(storage, tags)

    logger.warning("unknown tool requested: %s", name)
    return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]


def _handle_read_context(storage: MemoryBankStorage) -> list[TextContent]:
    logger.debug("read_context: reading all metadata")
    all_docs = storage.read_all_metadata()
    logger.debug("read_context: total documents=%d", len(all_docs))

    core_names = [d.name for d in all_docs if d.core]
    logger.debug("read_context: core documents=%s", core_names)

    core_with_content = storage.read_documents(core_names)
    core_map = {d.name: d for d in core_with_content if d is not None}
    logger.debug("read_context: loaded core content for=%s", list(core_map.keys()))

    result = []
    for doc in all_docs:
        if doc.core and doc.name in core_map:
            entry = core_map[doc.name].to_dict(include_content=True)
            content_len = len(entry.get("content") or "")
            logger.debug("read_context: core doc '%s' content_len=%d", doc.name, content_len)
            result.append(entry)
        else:
            result.append(doc.to_dict(include_content=True))  # content=None already

    payload = json.dumps({"documents": result}, ensure_ascii=False)
    logger.debug("read_context: response JSON size=%d bytes", len(payload))
    return [TextContent(type="text", text=payload)]


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
        logger.error("write_document called without name")
        return [TextContent(type="text", text=json.dumps({"error": "name is required"}))]
    if len(tags) < 2:
        logger.error("write_document: too few tags for '%s': %s", name, tags)
        return [TextContent(type="text", text=json.dumps({"error": "at least 2 tags required"}))]

    doc = storage.write_document(name=name, content=content, tags=tags, core=core)
    logger.info("document saved: %s (core=%s, tags=%s)", doc.name, doc.core, doc.tags)
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

async def _run(base_dir: Path, response_delay_ms: int = 0) -> None:
    global _storage, _response_delay_ms
    _response_delay_ms = response_delay_ms
    logger.info("starting memory-bank server, storage: %s, response_delay=%dms", base_dir, response_delay_ms)
    _storage = MemoryBankStorage(base_dir)

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

    logger.info("server stopped")


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
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Path to log file. If not specified, logs go to stderr.",
    )
    parser.add_argument(
        "--response-delay",
        type=int,
        default=0,
        metavar="MS",
        help="Delay in milliseconds before sending each tool response (default: 0). Useful for debugging.",
    )
    args = parser.parse_args()

    handler: logging.Handler
    if args.log_file:
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(args.log_file, encoding="utf-8")
    else:
        handler = logging.StreamHandler(sys.stderr)

    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    # Configure only our namespace to avoid noise from mcp SDK
    root_logger = logging.getLogger("memory-bank")
    root_logger.setLevel(args.log_level)
    root_logger.addHandler(handler)
    root_logger.propagate = False

    try:
        asyncio.run(_run(args.dir, response_delay_ms=args.response_delay))
    except KeyboardInterrupt:
        sys.exit(0)
