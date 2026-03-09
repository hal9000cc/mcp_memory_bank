"""Document storage: Markdown files with YAML frontmatter + SQLite index."""

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import frontmatter
from platformdirs import user_data_dir

from .models import Document

logger = logging.getLogger("memory-bank.storage")

DOCUMENTS_DIR = "documents"
INDEX_DB = "index.db"


def _project_slug(project_id: str) -> str:
    """Convert project_id to a safe directory name: <last_component>_<8-char-hash>."""
    last_part = Path(project_id).name or "default"
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in last_part)
    hash_suffix = hashlib.sha256(project_id.encode()).hexdigest()[:8]
    return f"{safe_name}_{hash_suffix}"


def resolve_storage_path(project_id: str, base_dir: Optional[Path], project_local: bool) -> Path:
    """Return the storage directory for a given project_id.

    Global mode (project_local=False):
        <base_dir>/projects/<slug>/
        base_dir defaults to the OS user data directory if not provided.

    Local mode (project_local=True):
        <project_id>/.memory_bank/
        project_id must be an absolute path to the project root.
    """
    if project_local:
        return Path(project_id) / ".memory_bank"
    if base_dir is None:
        base_dir = Path(user_data_dir("mcp-memory-bank"))
    slug = _project_slug(project_id)
    return base_dir / "projects" / slug


class MemoryBankStorage:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.docs_dir = base_dir / DOCUMENTS_DIR
        self.db_path = base_dir / INDEX_DB

        self.docs_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("storage dir: %s", self.docs_dir)
        self._init_db()
        self._sync_index()

    # ------------------------------------------------------------------
    # Database setup
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    name          TEXT PRIMARY KEY,
                    tags          TEXT NOT NULL,
                    core          INTEGER NOT NULL DEFAULT 0,
                    last_modified TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS document_tags (
                    document_name TEXT NOT NULL,
                    tag           TEXT NOT NULL,
                    PRIMARY KEY (document_name, tag),
                    FOREIGN KEY (document_name) REFERENCES documents(name) ON DELETE CASCADE
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # ------------------------------------------------------------------
    # Index sync
    # ------------------------------------------------------------------

    def _sync_index(self) -> None:
        """Rebuild SQLite index from MD files on startup."""
        md_files = {p.name for p in self.docs_dir.glob("*.md")}

        with self._connect() as conn:
            indexed = {row["name"] for row in conn.execute("SELECT name FROM documents")}

            stale = indexed - md_files
            if stale:
                logger.debug("removing stale index entries: %s", stale)
            for name in stale:
                conn.execute("DELETE FROM documents WHERE name = ?", (name,))

            new_files = md_files - indexed
            for filename in md_files:
                doc = self._read_md(filename)
                if doc:
                    self._upsert_index(conn, doc)

        logger.info("index synced: %d documents (%d new)", len(md_files), len(new_files))

    def _upsert_index(self, conn: sqlite3.Connection, doc: Document) -> None:
        conn.execute(
            """
            INSERT INTO documents (name, tags, core, last_modified)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                tags = excluded.tags,
                core = excluded.core,
                last_modified = excluded.last_modified
            """,
            (doc.name, json.dumps(doc.tags), int(doc.core), doc.last_modified),
        )
        conn.execute("DELETE FROM document_tags WHERE document_name = ?", (doc.name,))
        conn.executemany(
            "INSERT OR IGNORE INTO document_tags (document_name, tag) VALUES (?, ?)",
            [(doc.name, tag) for tag in doc.tags],
        )

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------

    def _md_path(self, name: str) -> Path:
        return self.docs_dir / name

    def _read_md(self, name: str) -> Optional[Document]:
        path = self._md_path(name)
        if not path.exists():
            logger.debug("document not found: %s", name)
            return None
        post = frontmatter.load(str(path))
        tags = post.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        return Document(
            name=name,
            tags=list(tags),
            core=bool(post.get("core", False)),
            last_modified=str(post.get("lastModified", _now_iso())),
            content=post.content or None,
        )

    def _write_md(self, doc: Document) -> None:
        metadata = {
            "tags": doc.tags,
            "core": doc.core,
            "lastModified": doc.last_modified,
        }
        post = frontmatter.Post(doc.content or "", **metadata)
        path = self._md_path(doc.name)
        path.write_text(frontmatter.dumps(post), encoding="utf-8")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read_all_metadata(self) -> list[Document]:
        """Return metadata for all documents (no content)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name, tags, core, last_modified FROM documents ORDER BY name"
            ).fetchall()
        return [
            Document(
                name=row["name"],
                tags=json.loads(row["tags"]),
                core=bool(row["core"]),
                last_modified=row["last_modified"],
                content=None,
            )
            for row in rows
        ]

    def read_documents(self, names: list[str]) -> list[Optional[Document]]:
        """Return documents with content by names. None if not found."""
        return [self._read_md(name) for name in names]

    def write_document(
        self,
        name: str,
        content: str,
        tags: list[str],
        core: bool = False,
    ) -> Document:
        """Create or overwrite a document. Returns the saved Document."""
        now = _now_iso()
        doc = Document(name=name, tags=tags, core=core, last_modified=now, content=content)
        self._write_md(doc)
        logger.debug("written to disk: %s", name)
        with self._connect() as conn:
            self._upsert_index(conn, doc)
        return doc

    def search_by_tags(self, tags: list[str]) -> list[Document]:
        """Return documents that have ALL specified tags (metadata only)."""
        if not tags:
            return []

        placeholders = ",".join("?" * len(tags))
        query = f"""
            SELECT d.name, d.tags, d.core, d.last_modified
            FROM documents d
            WHERE (
                SELECT COUNT(DISTINCT dt.tag)
                FROM document_tags dt
                WHERE dt.document_name = d.name
                  AND dt.tag IN ({placeholders})
            ) = ?
            ORDER BY d.name
        """
        with self._connect() as conn:
            rows = conn.execute(query, (*tags, len(tags))).fetchall()

        return [
            Document(
                name=row["name"],
                tags=json.loads(row["tags"]),
                core=bool(row["core"]),
                last_modified=row["last_modified"],
                content=None,
            )
            for row in rows
        ]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
