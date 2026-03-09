"""Tests for storage path resolution: global and project-local modes."""

import hashlib
import pathlib

import pytest

from mcp_memory_bank.storage import (
    MemoryBankStorage,
    _project_slug,
    resolve_storage_path,
)


# ------------------------------------------------------------------
# _project_slug
# ------------------------------------------------------------------

def test_slug_uses_last_path_component():
    slug = _project_slug("/home/user/projects/my_project")
    assert slug.startswith("my_project_")


def test_slug_sanitizes_special_chars():
    slug = _project_slug("/home/user/my project (v2)")
    # spaces and parens must be replaced with underscores
    first_part = slug.split("_")[0]
    assert all(c.isalnum() or c in "-_" for c in first_part)


def test_slug_hash_is_8_chars():
    slug = _project_slug("/home/user/projects/my_project")
    hash_part = slug.rsplit("_", 1)[-1]
    assert len(hash_part) == 8


def test_slug_different_paths_different_slugs():
    slug_a = _project_slug("/home/user/project_a")
    slug_b = _project_slug("/home/user/project_b")
    assert slug_a != slug_b


def test_slug_same_name_different_paths_differ():
    # Two projects with same folder name but different parent → different slugs
    slug_a = _project_slug("/home/alice/myapp")
    slug_b = _project_slug("/home/bob/myapp")
    assert slug_a != slug_b


def test_slug_is_deterministic():
    assert _project_slug("/some/path") == _project_slug("/some/path")


def test_slug_empty_last_component():
    # Path ending with slash has empty name → falls back to "default"
    slug = _project_slug("/")
    assert slug.startswith("default_")


# ------------------------------------------------------------------
# resolve_storage_path — global mode
# ------------------------------------------------------------------

def test_global_mode_uses_base_dir(tmp_path):
    path = resolve_storage_path("/home/user/project", tmp_path, project_local=False)
    assert path.is_relative_to(tmp_path)
    assert "projects" in path.parts


def test_global_mode_path_contains_slug(tmp_path):
    project_id = "/home/user/my_project"
    expected_slug = _project_slug(project_id)
    path = resolve_storage_path(project_id, tmp_path, project_local=False)
    assert path.name == expected_slug


def test_global_mode_different_projects_different_dirs(tmp_path):
    path_a = resolve_storage_path("/home/user/project_a", tmp_path, project_local=False)
    path_b = resolve_storage_path("/home/user/project_b", tmp_path, project_local=False)
    assert path_a != path_b


def test_global_mode_same_project_same_dir(tmp_path):
    path1 = resolve_storage_path("/home/user/project", tmp_path, project_local=False)
    path2 = resolve_storage_path("/home/user/project", tmp_path, project_local=False)
    assert path1 == path2


def test_global_mode_default_base_dir_is_not_none():
    # Without providing base_dir, must use platformdirs (no error, returns a path)
    path = resolve_storage_path("/some/project", None, project_local=False)
    assert path is not None
    assert "projects" in path.parts


# ------------------------------------------------------------------
# resolve_storage_path — project-local mode
# ------------------------------------------------------------------

def test_local_mode_path_inside_project(tmp_path):
    project_dir = str(tmp_path / "my_project")
    path = resolve_storage_path(project_dir, None, project_local=True)
    assert path == pathlib.Path(project_dir) / ".memory_bank"


def test_local_mode_ignores_base_dir(tmp_path):
    project_dir = str(tmp_path / "my_project")
    custom_base = tmp_path / "custom_base"
    path = resolve_storage_path(project_dir, custom_base, project_local=True)
    # base_dir must be ignored in local mode
    assert not path.is_relative_to(custom_base)
    assert path == pathlib.Path(project_dir) / ".memory_bank"


def test_local_mode_different_projects_different_dirs(tmp_path):
    path_a = resolve_storage_path(str(tmp_path / "project_a"), None, project_local=True)
    path_b = resolve_storage_path(str(tmp_path / "project_b"), None, project_local=True)
    assert path_a != path_b


# ------------------------------------------------------------------
# End-to-end: data isolation between projects
# ------------------------------------------------------------------

def test_global_mode_projects_are_isolated(tmp_path):
    path_a = resolve_storage_path("/proj/alpha", tmp_path, project_local=False)
    path_b = resolve_storage_path("/proj/beta", tmp_path, project_local=False)

    storage_a = MemoryBankStorage(path_a)
    storage_b = MemoryBankStorage(path_b)

    storage_a.write_document("note.md", "Alpha note", ["a", "b"])

    assert len(storage_a.read_all_metadata()) == 1
    assert len(storage_b.read_all_metadata()) == 0


def test_local_mode_projects_are_isolated(tmp_path):
    project_a = str(tmp_path / "proj_alpha")
    project_b = str(tmp_path / "proj_beta")

    path_a = resolve_storage_path(project_a, None, project_local=True)
    path_b = resolve_storage_path(project_b, None, project_local=True)

    storage_a = MemoryBankStorage(path_a)
    storage_b = MemoryBankStorage(path_b)

    storage_a.write_document("note.md", "Alpha note", ["a", "b"])

    assert len(storage_a.read_all_metadata()) == 1
    assert len(storage_b.read_all_metadata()) == 0


def test_local_mode_storage_inside_project_dir(tmp_path):
    project_dir = str(tmp_path / "my_project")
    path = resolve_storage_path(project_dir, None, project_local=True)
    storage = MemoryBankStorage(path)
    storage.write_document("ctx.md", "# Context", ["context", "global"], core=True)

    # Files must be physically inside the project directory
    assert path.is_relative_to(tmp_path / "my_project")
    assert (path / "documents" / "ctx.md").exists()
