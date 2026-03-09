"""Data models for Memory Bank documents."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Document:
    name: str
    tags: list[str]
    core: bool
    last_modified: str
    content: Optional[str] = None

    def to_dict(self, include_content: bool = True) -> dict:
        result = {
            "name": self.name,
            "tags": self.tags,
            "core": self.core,
            "lastModified": self.last_modified,
        }
        if include_content:
            result["content"] = self.content
        return result
