"""Document storage: Markdown files with YAML frontmatter + shared SQLite index."""

import hashlib
import json
import logging
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import frontmatter
import yaml
from platformdirs import user_data_dir

from .models import Document

logger = logging.getLogger("memory-bank.storage")

DOCUMENTS_DIR = "documents"
COMMON_STORAGE_DIR = "common_storage"
PROJECTS_DIR = "projects"
INDEX_DB = "index.db"
SCHEMA_VERSION = 2


@dataclass(frozen=True)
class IndexedFileState:
    project_id: str
    name: str
    last_modified: str
    size: int


def _project_slug(project_id: str) -> str:
    """Convert project_id to a safe directory name: <last_component>_<8-char-hash>."""
    last_part = Path(project_id).name or "default"
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in last_part)
    hash_suffix = hashlib.sha256(project_id.encode()).hexdigest()[:8]
    return f"{safe_name}_{hash_suffix}"


def get_storage_root(base_dir: Optional[Path]) -> Path:
    """Return the shared storage root directory."""
    return base_dir or Path(user_data_dir("mcp-memory-bank"))


def resolve_storage_path(project_id: str, base_dir: Optional[Path], project_local: bool) -> Path:
    """Return the document storage directory for a given project_id."""
    if project_id == "":
        return get_storage_root(base_dir) / COMMON_STORAGE_DIR
    if project_local:
        return Path(project_id) / ".memory_bank"
    root = get_storage_root(base_dir)
    slug = _project_slug(project_id)
    return root / PROJECTS_DIR / slug


class MemoryBankStorage:
    def __init__(self, base_dir: Optional[Path], project_local: bool = False) -> None:
        self.root_dir = get_storage_root(base_dir)
        self.project_local = project_local
        self.db_path = self.root_dir / INDEX_DB
        self._synced_projects: set[str] = set()

        self.root_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("storage root: %s", self.root_dir)
        is_fresh_db = not self.db_path.exists()
        if is_fresh_db:
            self._cleanup_legacy_indexes()
        self._init_db()
        self._sync_common_storage()

    @property
    def base_dir(self) -> Path:
        return self.root_dir

    @property
    def docs_dir(self) -> Path:
        return self.get_project_docs_dir("")

    # ------------------------------------------------------------------
    # Database setup
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    project_id    TEXT NOT NULL,
                    name          TEXT NOT NULL,
                    tags          TEXT NOT NULL,
                    core          INTEGER NOT NULL DEFAULT 0,
                    last_modified TEXT NOT NULL,
                    size          INTEGER NOT NULL,
                    PRIMARY KEY (project_id, name)
                );

                CREATE TABLE IF NOT EXISTS document_tags (
                    project_id    TEXT NOT NULL,
                    document_name TEXT NOT NULL,
                    tag           TEXT NOT NULL,
                    PRIMARY KEY (project_id, document_name, tag),
                    FOREIGN KEY (project_id, document_name)
                        REFERENCES documents(project_id, name)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_document_tags_tag
                    ON document_tags(project_id, tag);

                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER NOT NULL
                );
            """)
            row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if row is None:
                conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _cleanup_legacy_indexes(self) -> None:
        projects_root = self.root_dir / PROJECTS_DIR
        if projects_root.exists():
            for legacy_db in projects_root.glob(f"*/{INDEX_DB}"):
                if legacy_db.exists():
                    legacy_db.unlink()
                    logger.info("removed legacy index database: %s", legacy_db)

        if self.project_local and self.db_path.exists():
            self.db_path.unlink()
            logger.info("removed legacy index database: %s", self.db_path)

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def get_project_storage_dir(self, project_id: str) -> Path:
        return resolve_storage_path(project_id, self.root_dir, self.project_local)

    def get_project_docs_dir(self, project_id: str) -> Path:
        docs_dir = self.get_project_storage_dir(project_id) / DOCUMENTS_DIR
        docs_dir.mkdir(parents=True, exist_ok=True)
        return docs_dir

    def _md_path(self, project_id: str, name: str) -> Path:
        return self.get_project_docs_dir(project_id) / name

    # ------------------------------------------------------------------
    # Index sync
    # ------------------------------------------------------------------

    def _iter_known_project_ids(self) -> set[str]:
        # Currently unused. Kept for possible future full-index rebuild flows.
        project_ids: set[str] = {""}

        if self.project_local:
            with self._connect() as conn:
                rows = conn.execute("SELECT DISTINCT project_id FROM documents").fetchall()
            project_ids.update(row["project_id"] for row in rows if row["project_id"])
            return project_ids

        projects_root = self.root_dir / PROJECTS_DIR
        if projects_root.exists():
            for path in projects_root.iterdir():
                if path.is_dir():
                    slug_file = path / ".project_id"
                    if slug_file.exists():
                        project_ids.add(slug_file.read_text(encoding="utf-8").strip())

        with self._connect() as conn:
            rows = conn.execute("SELECT DISTINCT project_id FROM documents").fetchall()
        project_ids.update(row["project_id"] for row in rows)
        return project_ids

    def _scan_documents(self) -> dict[tuple[str, str], IndexedFileState]:
        # Currently unused. Kept for possible future full-index rebuild flows.
        scanned: dict[tuple[str, str], IndexedFileState] = {}

        for project_id in self._iter_known_project_ids():
            docs_dir = self.get_project_docs_dir(project_id)
            if not docs_dir.exists():
                continue

            for path in docs_dir.glob("*.md"):
                stat = path.stat()
                scanned[(project_id, path.name)] = IndexedFileState(
                    project_id=project_id,
                    name=path.name,
                    last_modified=_stat_mtime_iso(stat),
                    size=stat.st_size,
                )

        return scanned

    def _sync_index(self) -> None:
        """Incrementally synchronize SQLite index for all known projects.

        Currently unused. Kept for possible future full-index rebuild flows.
        """
        scanned = self._scan_documents()

        with self._connect() as conn:
            self._sync_index_from_scan(conn, scanned)

        logger.info("index synced for all known projects: scanned=%d", len(scanned))

    def _sync_index_from_scan(
        self,
        conn: sqlite3.Connection,
        scanned: dict[tuple[str, str], IndexedFileState],
    ) -> tuple[int, int]:
        indexed_rows = conn.execute(
            "SELECT project_id, name, last_modified, size FROM documents"
        ).fetchall()
        indexed = {
            (row["project_id"], row["name"]): IndexedFileState(
                project_id=row["project_id"],
                name=row["name"],
                last_modified=row["last_modified"],
                size=row["size"],
            )
            for row in indexed_rows
        }

        stale = set(indexed) - set(scanned)
        for project_id, name in stale:
            if not self._md_path(project_id, name).exists():
                conn.execute(
                    "DELETE FROM documents WHERE project_id = ? AND name = ?",
                    (project_id, name),
                )

        to_reindex: list[IndexedFileState] = []
        for key, scanned_state in scanned.items():
            indexed_state = indexed.get(key)
            if indexed_state is None:
                to_reindex.append(scanned_state)
                continue
            if (
                indexed_state.last_modified != scanned_state.last_modified
                or indexed_state.size != scanned_state.size
            ):
                to_reindex.append(scanned_state)

        for state in to_reindex:
            doc = self._read_md(state.project_id, state.name)
            if doc is not None:
                self._upsert_index(conn, doc)

        return len(to_reindex), len(stale)

    def _scan_project_documents(self, project_id: str) -> dict[tuple[str, str], IndexedFileState]:
        scanned: dict[tuple[str, str], IndexedFileState] = {}
        docs_dir = self.get_project_docs_dir(project_id)
        if not docs_dir.exists():
            return scanned

        for path in docs_dir.glob("*.md"):
            stat = path.stat()
            scanned[(project_id, path.name)] = IndexedFileState(
                project_id=project_id,
                name=path.name,
                last_modified=_stat_mtime_iso(stat),
                size=stat.st_size,
            )

        return scanned

    def _sync_common_storage(self) -> None:
        with self._connect() as conn:
            reindexed, stale = self._sync_project_index(conn, "")
        self._synced_projects.add("")
        logger.info(
            "common storage synced incrementally: reindexed=%d, stale_removed=%d",
            reindexed,
            stale,
        )

    def _sync_project_index(self, conn: sqlite3.Connection, project_id: str) -> tuple[int, int]:
        scanned = self._scan_project_documents(project_id)
        indexed_rows = conn.execute(
            "SELECT project_id, name, last_modified, size FROM documents WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        indexed = {
            (row["project_id"], row["name"]): IndexedFileState(
                project_id=row["project_id"],
                name=row["name"],
                last_modified=row["last_modified"],
                size=row["size"],
            )
            for row in indexed_rows
        }

        stale = set(indexed) - set(scanned)
        for stale_project_id, name in stale:
            conn.execute(
                "DELETE FROM documents WHERE project_id = ? AND name = ?",
                (stale_project_id, name),
            )

        to_reindex: list[IndexedFileState] = []
        for key, scanned_state in scanned.items():
            indexed_state = indexed.get(key)
            if indexed_state is None:
                to_reindex.append(scanned_state)
                continue
            if (
                indexed_state.last_modified != scanned_state.last_modified
                or indexed_state.size != scanned_state.size
            ):
                to_reindex.append(scanned_state)

        for state in to_reindex:
            doc = self._read_md(state.project_id, state.name)
            if doc is not None:
                self._upsert_index(conn, doc)

        return len(to_reindex), len(stale)

    def _ensure_project_synced(self, project_id: str) -> None:
        if project_id in self._synced_projects:
            return

        with self._connect() as conn:
            reindexed, stale = self._sync_project_index(conn, project_id)

        self._synced_projects.add(project_id)
        logger.info(
            "project synced incrementally: project_id=%s, reindexed=%d, stale_removed=%d",
            project_id,
            reindexed,
            stale,
        )

    def _upsert_index(self, conn: sqlite3.Connection, doc: Document) -> None:
        conn.execute(
            """
            INSERT INTO documents (project_id, name, tags, core, last_modified, size)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, name) DO UPDATE SET
                tags = excluded.tags,
                core = excluded.core,
                last_modified = excluded.last_modified,
                size = excluded.size
            """,
            (
                doc.project_id,
                doc.name,
                json.dumps(doc.tags),
                int(doc.core),
                doc.last_modified,
                doc.size,
            ),
        )
        conn.execute(
            "DELETE FROM document_tags WHERE project_id = ? AND document_name = ?",
            (doc.project_id, doc.name),
        )
        conn.executemany(
            "INSERT OR IGNORE INTO document_tags (project_id, document_name, tag) VALUES (?, ?, ?)",
            [(doc.project_id, doc.name, tag) for tag in doc.tags],
        )

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------

    def _read_md(self, project_id: str, name: str) -> Optional[Document]:
        path = self._md_path(project_id, name)
        if not path.exists():
            logger.debug("document not found: %s/%s", project_id, name)
            return None

        try:
            post = frontmatter.load(str(path))
        except (yaml.YAMLError, ValueError, OSError) as exc:
            logger.warning(
                "failed to parse markdown document %s/%s: %s",
                project_id,
                name,
                exc,
            )
            return None

        raw_tags = post.get("tags", [])
        if isinstance(raw_tags, str):
            tags = [raw_tags]
        elif isinstance(raw_tags, list):
            tags = [str(tag) for tag in raw_tags if isinstance(tag, (str, int, float, bool))]
            invalid_count = len(raw_tags) - len(tags)
            if invalid_count > 0:
                logger.warning(
                    "skipping %d non-scalar tags in %s/%s",
                    invalid_count,
                    project_id,
                    name,
                )
        else:
            logger.warning(
                "invalid tags type in %s/%s: %s; using empty tags",
                project_id,
                name,
                type(raw_tags).__name__,
            )
            tags = []

        stat = path.stat()
        return Document(
            project_id=project_id,
            name=name,
            tags=tags,
            core=bool(post.get("core", False)),
            last_modified=_stat_mtime_iso(stat),
            size=stat.st_size,
            content=post.content or None,
        )

    def _write_md(self, doc: Document) -> None:
        metadata = {
            "tags": doc.tags,
            "core": doc.core,
            "lastModified": doc.last_modified,
        }
        post = frontmatter.Post(doc.content or "", **metadata)
        path = self._md_path(doc.project_id, doc.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        content = frontmatter.dumps(post)

        temp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f"{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_file.write(content)
                temp_file.flush()
                os.fsync(temp_file.fileno())
                temp_path = Path(temp_file.name)

            os.replace(temp_path, path)
        except BaseException:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()
            raise

        if not self.project_local and doc.project_id:
            slug_file = self.get_project_storage_dir(doc.project_id) / ".project_id"
            slug_file.write_text(doc.project_id, encoding="utf-8")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read_all_metadata(self, project_id: str) -> list[Document]:
        """Return metadata for all documents in a project (no content)."""
        self._ensure_project_synced(project_id)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT project_id, name, tags, core, last_modified, size
                FROM documents
                WHERE project_id = ?
                ORDER BY name
                """,
                (project_id,),
            ).fetchall()
        return [
            Document(
                project_id=row["project_id"],
                name=row["name"],
                tags=json.loads(row["tags"]),
                core=bool(row["core"]),
                last_modified=row["last_modified"],
                size=row["size"],
                content=None,
            )
            for row in rows
        ]

    def read_all_metadata_legacy(self) -> list[Document]:
        return self.read_all_metadata("")

    def read_documents(self, project_id: str, names: list[str]) -> list[Optional[Document]]:
        """Return documents with content by names. None if not found."""
        self._ensure_project_synced(project_id)
        return [self._read_md(project_id, name) for name in names]

    def write_document(
        self,
        project_id: str,
        name: str,
        content: str,
        tags: list[str],
        core: bool = False,
    ) -> Document:
        """Create or overwrite a document. Returns the saved Document."""
        initial = Document(
            project_id=project_id,
            name=name,
            tags=tags,
            core=core,
            last_modified=_now_iso(),
            size=0,
            content=content,
        )
        self._write_md(initial)
        doc = self._read_md(project_id, name)
        assert doc is not None
        logger.debug("written to disk: %s/%s", project_id, name)
        with self._connect() as conn:
            self._upsert_index(conn, doc)
        return doc

    def delete_document(self, project_id: str, name: str) -> bool:
        """Delete a document file and its index entry. Raises FileNotFoundError if missing."""
        path = self._md_path(project_id, name)
        if not path.exists():
            raise FileNotFoundError(name)

        path.unlink()
        logger.debug("deleted from disk: %s/%s", project_id, name)
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM documents WHERE project_id = ? AND name = ?",
                (project_id, name),
            )
        return True

    def append_content(
        self,
        project_id: str,
        name: str,
        content: str,
        tags: Optional[list[str]] = None,
        core: bool = False,
    ) -> tuple[Document, int]:
        """Append content to an existing document without reading it into the caller's context."""
        existing = self._read_md(project_id, name)
        if existing is not None:
            base = existing.content or ""
            new_content = base + "\n\n" + content if base else content
            doc = self.write_document(
                project_id=project_id,
                name=name,
                content=new_content,
                tags=existing.tags,
                core=existing.core,
            )
        else:
            if not tags or len(tags) < 2:
                raise ValueError("tags (minimum 2) are required when creating a new document")
            doc = self.write_document(
                project_id=project_id,
                name=name,
                content=content,
                tags=tags,
                core=core,
            )

        return doc, len(doc.content or "")

    def search_by_tags(self, project_id: str, tags: list[str]) -> list[Document]:
        """Return documents that have ALL specified tags (metadata only)."""
        if not tags:
            return []

        self._ensure_project_synced(project_id)
        if project_id != "":
            self._ensure_project_synced("")

        project_ids = [project_id] if project_id == "" else [project_id, ""]
        project_placeholders = ",".join("?" * len(project_ids))
        placeholders = ",".join("?" * len(tags))
        query = f"""
            SELECT d.project_id, d.name, d.tags, d.core, d.last_modified, d.size
            FROM document_tags dt
            JOIN documents d
              ON d.project_id = dt.project_id
             AND d.name = dt.document_name
            WHERE dt.project_id IN ({project_placeholders})
              AND dt.tag IN ({placeholders})
            GROUP BY d.project_id, d.name
            HAVING COUNT(DISTINCT dt.tag) = ?
            ORDER BY d.project_id, d.name
        """
        with self._connect() as conn:
            rows = conn.execute(query, (*project_ids, *tags, len(tags))).fetchall()

        return [
            Document(
                project_id=row["project_id"],
                name=row["name"],
                tags=json.loads(row["tags"]),
                core=bool(row["core"]),
                last_modified=row["last_modified"],
                size=row["size"],
                content=None,
            )
            for row in rows
        ]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stat_mtime_iso(stat: os.stat_result) -> str:
    return datetime.fromtimestamp(stat.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")