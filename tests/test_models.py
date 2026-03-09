"""Tests for Document model."""

from mcp_memory_bank.models import Document


def _make_doc(**kwargs) -> Document:
    defaults = dict(
        name="test.md",
        tags=["tag1", "tag2"],
        core=False,
        last_modified="2026-03-09T00:00:00Z",
        content="# Hello",
    )
    defaults.update(kwargs)
    return Document(**defaults)


def test_to_dict_with_content():
    doc = _make_doc(content="# Hello\n\nWorld")
    result = doc.to_dict(include_content=True)
    assert result["content"] == "# Hello\n\nWorld"


def test_to_dict_without_content():
    doc = _make_doc(content="# Hello")
    result = doc.to_dict(include_content=False)
    assert "content" not in result


def test_to_dict_none_content_preserved():
    doc = _make_doc(content=None)
    result = doc.to_dict(include_content=True)
    assert result["content"] is None


def test_to_dict_field_names():
    """last_modified must be serialized as lastModified."""
    doc = _make_doc(last_modified="2026-03-09T01:23:00Z")
    result = doc.to_dict()
    assert "lastModified" in result
    assert "last_modified" not in result
    assert result["lastModified"] == "2026-03-09T01:23:00Z"


def test_to_dict_all_fields_present():
    doc = _make_doc()
    result = doc.to_dict()
    assert set(result.keys()) == {"name", "tags", "core", "lastModified", "content"}


def test_to_dict_tags_preserved():
    doc = _make_doc(tags=["alpha", "beta", "gamma"])
    result = doc.to_dict()
    assert result["tags"] == ["alpha", "beta", "gamma"]


def test_to_dict_core_flag():
    assert _make_doc(core=True).to_dict()["core"] is True
    assert _make_doc(core=False).to_dict()["core"] is False
