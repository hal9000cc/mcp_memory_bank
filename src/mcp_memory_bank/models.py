"""Data models for Memory Bank documents."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Document:
    name: str
    tags: list[str]
    core: bool
    last_modified: str
    project_id: str = ""
    size: int = 0
    content: Optional[str] = None

    def to_dict(self, include_content: bool = True) -> dict:
        result = {
            "common": self.project_id == "",
            "name": self.name,
            "tags": self.tags,
            "core": self.core,
            "lastModified": self.last_modified,
            "size": self.size,
        }
        if include_content:
            result["content"] = self.content
        return result
