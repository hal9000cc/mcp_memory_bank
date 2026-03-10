"""MCP server implementation for Memory Bank."""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .storage import MemoryBankStorage, get_storage_root

logger = logging.getLogger("memory-bank")

# Shared storage instance
_storage: Optional[MemoryBankStorage] = None
_base_dir: Optional[Path] = None
_project_local: bool = False
_response_delay_ms: int = 0

app = Server("memory-bank")

PROJECT_ID_SCHEMA = {
    "project_id": {
        "type": "string",
        "description": (
            "Unique project identifier. Use the absolute path to the project root "
            "(Current Working Directory) if available."
        ),
    }
}


def _get_storage(project_id: str) -> MemoryBankStorage:
    del project_id
    global _storage
    if _storage is None:
        root = get_storage_root(_base_dir)
        logger.info("initializing shared storage at %s", root)
        _storage = MemoryBankStorage(root, project_local=_project_local)
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
                "properties": PROJECT_ID_SCHEMA,
                "required": ["project_id"],
            },
        ),
        Tool(
            name="memory_bank_read_documents",
            description="Read one or more documents by name and return their full content.",
            inputSchema={
                "type": "object",
                "properties": {
                    **PROJECT_ID_SCHEMA,
                    "names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of document names to read (e.g. ['architecture.md'])",
                        "minItems": 1,
                    },
                },
                "required": ["project_id", "names"],
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
                    **PROJECT_ID_SCHEMA,
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
                "required": ["project_id", "name", "content", "tags"],
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
                    **PROJECT_ID_SCHEMA,
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags to search for (document must have all of them)",
                        "minItems": 1,
                    },
                },
                "required": ["project_id", "tags"],
            },
        ),
        Tool(
            name="memory_bank_append_content",
            description=(
                "Append text to the end of an existing document without reading its content. "
                "The server reads the file internally, appends the new content with a blank line "
                "separator, and saves it back. This is the preferred way to update log-style "
                "documents (e.g. progress.md) as it avoids loading the full document into context. "
                "If the document does not exist, it will be created (tags are required in that case)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **PROJECT_ID_SCHEMA,
                    "name": {
                        "type": "string",
                        "description": "Document filename, e.g. 'progress.md'",
                    },
                    "content": {
                        "type": "string",
                        "description": "Markdown text to append at the end of the document",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Tags for the document. Required only when creating a new document "
                            "(minimum 2, lowercase English). Ignored if the document already exists."
                        ),
                        "minItems": 2,
                    },
                    "core": {
                        "type": "boolean",
                        "description": (
                            "Load automatically at session start. "
                            "Used only when creating a new document (default: false)."
                        ),
                        "default": False,
                    },
                },
                "required": ["project_id", "name", "content"],
            },
        ),
        Tool(
            name="memory_bank_delete_document",
            description="Delete an existing document by name. Returns an error if the document is not found.",
            inputSchema={
                "type": "object",
                "properties": {
                    **PROJECT_ID_SCHEMA,
                    "name": {
                        "type": "string",
                        "description": "Document filename, e.g. 'activeTask.md'",
                    },
                },
                "required": ["project_id", "name"],
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
        if "project_id" not in arguments:
            return [TextContent(type="text", text=json.dumps({"error": "project_id is required"}))]
        project_id = arguments["project_id"]
        storage = _get_storage(project_id)
        result = _dispatch_tool(name, arguments, storage, project_id)
        if _response_delay_ms > 0:
            logger.debug("call_tool: sleeping %dms before response", _response_delay_ms)
            await asyncio.sleep(_response_delay_ms / 1000)
        elapsed = time.monotonic() - t0
        total_len = sum(len(c.text) for c in result)
        logger.debug(
            "<<< call_tool: name=%s, project=%s, elapsed=%.3fs (delay=%dms), response_bytes=%d",
            name, project_id, elapsed, _response_delay_ms, total_len,
        )
        return result
    except Exception:
        elapsed = time.monotonic() - t0
        logger.exception("!!! call_tool exception: name=%s, elapsed=%.3fs", name, elapsed)
        return [TextContent(type="text", text=json.dumps({"error": "Internal server error"}))]


def _dispatch_tool(
    name: str,
    arguments: dict,
    storage: MemoryBankStorage,
    project_id: str,
) -> list[TextContent]:
    if name == "memory_bank_read_context":
        logger.debug("tool: read_context")
        return _handle_read_context(storage, project_id)

    if name == "memory_bank_read_documents":
        names = arguments.get("names", [])
        logger.debug("tool: read_documents, names=%s", names)
        return _handle_read_documents(storage, project_id, names)

    if name == "memory_bank_write_document":
        logger.debug(
            "tool: write_document, name=%s, tags=%s, core=%s",
            arguments.get("name"),
            arguments.get("tags"),
            arguments.get("core", False),
        )
        return _handle_write_document(storage, project_id, arguments)

    if name == "memory_bank_search_by_tags":
        tags = arguments.get("tags", [])
        logger.debug("tool: search_by_tags, tags=%s", tags)
        return _handle_search_by_tags(storage, project_id, tags)

    if name == "memory_bank_append_content":
        logger.debug(
            "tool: append_content, name=%s, tags=%s, core=%s",
            arguments.get("name"),
            arguments.get("tags"),
            arguments.get("core", False),
        )
        return _handle_append_content(storage, project_id, arguments)

    if name == "memory_bank_delete_document":
        logger.debug("tool: delete_document, name=%s", arguments.get("name"))
        return _handle_delete_document(storage, project_id, arguments)

    logger.warning("unknown tool requested: %s", name)
    return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]


def _handle_read_context(storage: MemoryBankStorage, project_id: str) -> list[TextContent]:
    logger.debug("read_context: reading all metadata")
    all_docs = storage.read_all_metadata(project_id)
    logger.debug("read_context: total documents=%d", len(all_docs))

    core_names = [d.name for d in all_docs if d.core]
    logger.debug("read_context: core documents=%s", core_names)

    core_with_content = storage.read_documents(project_id, core_names)
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


def _handle_read_documents(
    storage: MemoryBankStorage,
    project_id: str,
    names: list[str],
) -> list[TextContent]:
    docs = storage.read_documents(project_id, names)
    result = []
    for doc in docs:
        if doc is None:
            result.append(None)
        else:
            result.append(doc.to_dict(include_content=True))
    return [TextContent(type="text", text=json.dumps({"documents": result}, ensure_ascii=False))]


def _handle_write_document(
    storage: MemoryBankStorage,
    project_id: str,
    arguments: dict,
) -> list[TextContent]:
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

    doc = storage.write_document(
        project_id=project_id,
        name=name,
        content=content,
        tags=tags,
        core=core,
    )
    logger.info("document saved: %s (core=%s, tags=%s)", doc.name, doc.core, doc.tags)
    return [TextContent(
        type="text",
        text=json.dumps({
            "success": True,
            "name": doc.name,
            "lastModified": doc.last_modified,
        }),
    )]


def _handle_search_by_tags(
    storage: MemoryBankStorage,
    project_id: str,
    tags: list[str],
) -> list[TextContent]:
    docs = storage.search_by_tags(project_id, tags)
    result = [doc.to_dict(include_content=False) for doc in docs]
    return [TextContent(type="text", text=json.dumps({"documents": result}, ensure_ascii=False))]


def _handle_append_content(
    storage: MemoryBankStorage,
    project_id: str,
    arguments: dict,
) -> list[TextContent]:
    name = arguments.get("name", "")
    content = arguments.get("content", "")
    tags = arguments.get("tags") or None
    core = bool(arguments.get("core", False))

    if not name:
        logger.error("append_content called without name")
        return [TextContent(type="text", text=json.dumps({"error": "name is required"}))]
    if not content:
        logger.error("append_content called without content for '%s'", name)
        return [TextContent(type="text", text=json.dumps({"error": "content is required"}))]

    try:
        doc, content_length = storage.append_content(
            project_id=project_id, name=name, content=content, tags=tags, core=core
        )
    except ValueError as exc:
        logger.error("append_content validation error for '%s': %s", name, exc)
        return [TextContent(type="text", text=json.dumps({"error": str(exc)}))]

    logger.info("content appended: %s (content_length=%d)", doc.name, content_length)
    return [TextContent(
        type="text",
        text=json.dumps({
            "success": True,
            "name": doc.name,
            "lastModified": doc.last_modified,
            "contentLength": content_length,
        }),
    )]


def _handle_delete_document(
    storage: MemoryBankStorage,
    project_id: str,
    arguments: dict,
) -> list[TextContent]:
    name = arguments.get("name", "")

    if not name:
        logger.error("delete_document called without name")
        return [TextContent(type="text", text=json.dumps({"error": "name is required"}))]

    try:
        storage.delete_document(project_id=project_id, name=name)
    except FileNotFoundError:
        logger.error("delete_document: document not found '%s'", name)
        return [TextContent(type="text", text=json.dumps({"error": f"Document not found: {name}"}))]

    logger.info("document deleted: %s", name)
    return [TextContent(
        type="text",
        text=json.dumps({
            "success": True,
            "name": name,
        }),
    )]


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

async def _run(
    base_dir: Optional[Path],
    project_local: bool = False,
    response_delay_ms: int = 0,
) -> None:
    global _base_dir, _project_local, _response_delay_ms
    _base_dir = base_dir
    _project_local = project_local
    _response_delay_ms = response_delay_ms

    mode = "project-local" if project_local else f"global ({base_dir or 'platformdirs default'})"
    logger.info("starting memory-bank server, mode=%s, response_delay=%dms", mode, response_delay_ms)

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

    logger.info("server stopped")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MCP Memory Bank server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  mcp-memory-bank                        # global storage, auto path\n"
            "  mcp-memory-bank --dir /data/membank    # global storage, custom root\n"
            "  mcp-memory-bank --project-local        # store inside each project\n"
        ),
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help=(
            "Root directory for the global storage "
            "(default: OS user data dir via platformdirs). "
            "Ignored when --project-local is set."
        ),
    )
    parser.add_argument(
        "--project-local",
        action="store_true",
        default=False,
        help=(
            "Store memory bank inside each project directory "
            "(<project_id>/.memory_bank/). "
            "project_id must be an absolute path to the project root."
        ),
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
        help="Delay in milliseconds before sending each tool response (default: 0).",
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

    root_logger = logging.getLogger("memory-bank")
    root_logger.setLevel(args.log_level)
    root_logger.addHandler(handler)
    root_logger.propagate = False

    try:
        asyncio.run(_run(
            base_dir=args.dir,
            project_local=args.project_local,
            response_delay_ms=args.response_delay,
        ))
    except KeyboardInterrupt:
        sys.exit(0)
