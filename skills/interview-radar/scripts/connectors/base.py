from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from scripts.models import RawPost


@dataclass
class SearchResult:
    posts: list[RawPost] = field(default_factory=list)
    status: str = "ok"  # ok | degraded | error
    message: str = ""

    @classmethod
    def degraded(cls, source: str, message: str) -> "SearchResult":
        return cls(posts=[], status="degraded", message=f"[{source}] {message}")


class Connector(ABC):
    name: str = "base"

    @abstractmethod
    def search(self, queries: list[str]) -> SearchResult:
        """Run queries against the source and return normalized RawPosts."""
        raise NotImplementedError
