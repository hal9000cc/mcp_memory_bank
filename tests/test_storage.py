"""Tests for MemoryBankStorage."""

import pathlib

import frontmatter
import pytest

from mcp_memory_bank.storage import MemoryBankStorage


# ------------------------------------------------------------------
# Write & read
# ------------------------------------------------------------------

def test_write_and_read_document(tmp_storage):
    doc = tmp_storage.write_document("test.md", "# Hello", ["a", "b"], core=True)
    assert doc.name == "test.md"
    assert doc.tags == ["a", "b"]
    assert doc.core is True
    assert doc.last_modified

    loaded = tmp_storage.read_documents(["test.md"])[0]
    assert loaded is not None
    assert loaded.content == "# Hello"
    assert loaded.tags == ["a", "b"]
    assert loaded.core is True


def test_write_overwrites_existing(tmp_storage):
    tmp_storage.write_document("doc.md", "v1", ["x", "y"], core=False)
    tmp_storage.write_document("doc.md", "v2 updated", ["x", "y", "z"], core=True)

    loaded = tmp_storage.read_documents(["doc.md"])[0]
    assert loaded.content == "v2 updated"
    assert loaded.tags == ["x", "y", "z"]
    assert loaded.core is True


def test_read_nonexistent_document(tmp_storage):
    result = tmp_storage.read_documents(["missing.md"])[0]
    assert result is None


def test_read_multiple_documents(populated_storage):
    results = populated_storage.read_documents(["context.md", "nonexistent.md", "architecture.md"])
    assert results[0] is not None
    assert results[0].name == "context.md"
    assert results[1] is None
    assert results[2] is not None
    assert results[2].name == "architecture.md"


# ------------------------------------------------------------------
# Metadata
# ------------------------------------------------------------------

def test_read_all_metadata_empty(tmp_storage):
    assert tmp_storage.read_all_metadata() == []


def test_read_all_metadata_count(populated_storage):
    docs = populated_storage.read_all_metadata()
    assert len(docs) == 3


def test_read_all_metadata_no_content(populated_storage):
    for doc in populated_storage.read_all_metadata():
        assert doc.content is None


def test_read_all_metadata_sorted(populated_storage):
    names = [d.name for d in populated_storage.read_all_metadata()]
    assert names == sorted(names)


# ------------------------------------------------------------------
# Search by tags
# ------------------------------------------------------------------

def test_search_single_tag(populated_storage):
    results = populated_storage.search_by_tags(["database"])
    assert len(results) == 1
    assert results[0].name == "architecture.md"


def test_search_multiple_tags_and_logic(populated_storage):
    results = populated_storage.search_by_tags(["decision", "architecture"])
    assert len(results) == 1
    assert results[0].name == "architecture.md"


def test_search_no_match(populated_storage):
    assert populated_storage.search_by_tags(["nonexistent"]) == []


def test_search_empty_tags(populated_storage):
    assert populated_storage.search_by_tags([]) == []


def test_search_returns_no_content(populated_storage):
    for doc in populated_storage.search_by_tags(["context"]):
        assert doc.content is None


def test_search_partial_tag_match_excluded(populated_storage):
    # document has ["decision", "architecture", "database"] — searching for
    # all three plus a fourth tag that doesn't exist must return nothing
    results = populated_storage.search_by_tags(["decision", "architecture", "database", "missing"])
    assert results == []


# ------------------------------------------------------------------
# Core flag
# ------------------------------------------------------------------

def test_core_true_persisted(tmp_storage):
    tmp_storage.write_document("core_doc.md", "x", ["a", "b"], core=True)
    loaded = tmp_storage.read_documents(["core_doc.md"])[0]
    assert loaded.core is True


def test_core_false_persisted(tmp_storage):
    tmp_storage.write_document("normal.md", "x", ["a", "b"], core=False)
    loaded = tmp_storage.read_documents(["normal.md"])[0]
    assert loaded.core is False


# ------------------------------------------------------------------
# MD file format
# ------------------------------------------------------------------

def test_md_file_has_frontmatter(tmp_storage):
    tmp_storage.write_document("check.md", "# Body", ["t1", "t2"], core=True)
    path = tmp_storage.docs_dir / "check.md"
    post = frontmatter.load(str(path))
    assert post.get("tags") == ["t1", "t2"]
    assert post.get("core") is True
    assert "lastModified" in post.metadata


def test_md_file_content_readable(tmp_storage):
    """The Markdown content must be readable without any special parser."""
    body = "# My document\n\nSome text here."
    tmp_storage.write_document("readable.md", body, ["a", "b"])
    raw = (tmp_storage.docs_dir / "readable.md").read_text()
    assert "# My document" in raw
    assert "Some text here." in raw


# ------------------------------------------------------------------
# Index sync on restart
# ------------------------------------------------------------------

def test_sync_on_restart(populated_storage):
    base_dir = populated_storage.base_dir
    restarted = MemoryBankStorage(base_dir)
    docs = restarted.read_all_metadata()
    assert len(docs) == 3
    assert {d.name for d in docs} == {"context.md", "activeTask.md", "architecture.md"}


def test_sync_removes_stale_entries(populated_storage):
    (populated_storage.docs_dir / "architecture.md").unlink()
    restarted = MemoryBankStorage(populated_storage.base_dir)
    names = {d.name for d in restarted.read_all_metadata()}
    assert "architecture.md" not in names
    assert len(names) == 2


def test_sync_picks_up_manually_added_file(tmp_storage):
    """A file created with valid frontmatter must be indexed on restart."""
    import frontmatter as fm
    post = fm.Post("# Manual", tags=["manual", "test"], core=False, lastModified="2026-01-01T00:00:00Z")
    path = tmp_storage.docs_dir / "manual.md"
    path.write_text(fm.dumps(post), encoding="utf-8")

    restarted = MemoryBankStorage(tmp_storage.base_dir)
    names = {d.name for d in restarted.read_all_metadata()}
    assert "manual.md" in names


# ------------------------------------------------------------------
# append_content
# ------------------------------------------------------------------

def test_append_to_existing_document(tmp_storage):
    tmp_storage.write_document("log.md", "# Log\n\n## Day 1", ["log", "progress"])
    doc, length = tmp_storage.append_content("log.md", "## Day 2\n- Done something")
    loaded = tmp_storage.read_documents(["log.md"])[0]
    assert "## Day 1" in loaded.content
    assert "## Day 2" in loaded.content
    assert "\n\n" in loaded.content
    assert length == len(loaded.content)


def test_append_separator_not_doubled_on_empty_content(tmp_storage):
    tmp_storage.write_document("empty.md", "", ["a", "b"])
    doc, _ = tmp_storage.append_content("empty.md", "First entry")
    loaded = tmp_storage.read_documents(["empty.md"])[0]
    assert loaded.content == "First entry"


def test_append_preserves_existing_tags(tmp_storage):
    tmp_storage.write_document("doc.md", "original", ["keep", "these"], core=True)
    doc, _ = tmp_storage.append_content("doc.md", "extra")
    assert doc.tags == ["keep", "these"]
    assert doc.core is True


def test_append_updates_last_modified(tmp_storage):
    doc_before = tmp_storage.write_document("ts.md", "v1", ["a", "b"])
    import time
    time.sleep(0.01)
    doc_after, _ = tmp_storage.append_content("ts.md", "v2")
    assert doc_after.last_modified >= doc_before.last_modified


def test_append_returns_correct_content_length(tmp_storage):
    tmp_storage.write_document("len.md", "hello", ["a", "b"])
    doc, length = tmp_storage.append_content("len.md", "world")
    assert length == len(doc.content)
    assert length > len("hello")


def test_append_creates_new_document_with_tags(tmp_storage):
    doc, length = tmp_storage.append_content(
        "new_log.md", "## Entry 1", tags=["log", "progress"], core=False
    )
    assert doc.name == "new_log.md"
    assert doc.tags == ["log", "progress"]
    assert doc.core is False
    assert length == len("## Entry 1")
    assert tmp_storage.read_documents(["new_log.md"])[0] is not None


def test_append_to_nonexistent_without_tags_raises(tmp_storage):
    with pytest.raises(ValueError, match="tags"):
        tmp_storage.append_content("ghost.md", "content")


def test_append_to_nonexistent_with_one_tag_raises(tmp_storage):
    with pytest.raises(ValueError, match="tags"):
        tmp_storage.append_content("ghost.md", "content", tags=["only_one"])


def test_append_multiple_times(tmp_storage):
    tmp_storage.write_document("multi.md", "line1", ["a", "b"])
    tmp_storage.append_content("multi.md", "line2")
    tmp_storage.append_content("multi.md", "line3")
    loaded = tmp_storage.read_documents(["multi.md"])[0]
    assert "line1" in loaded.content
    assert "line2" in loaded.content
    assert "line3" in loaded.content
