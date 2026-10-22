"""Bounded, cached SARIF artifact and URI-base resolution."""

from __future__ import annotations

from typing import Any, TypeGuard
from urllib.parse import unquote, urljoin, urlparse

_LIMIT = 32


class SarifUris:
    """Keep artifact indices and URI-base identities in separate namespaces."""

    def __init__(self, run: dict[str, Any]) -> None:
        self._artifacts = run.get("artifacts", [])
        self._bases = run.get("originalUriBaseIds", {})
        self._cache: dict[str, str | None] = {}

    def resolve(self, artifact: dict[str, Any]) -> str | None:
        location = self._location(artifact)
        if location is None:
            return None
        uri, base = location
        if urlparse(uri).scheme or base is None:
            return uri
        if not isinstance(base, str) or not isinstance(self._bases, dict):
            return None
        if base not in self._bases:
            return uri
        return _join(self._base(base), uri)

    def _location(self, artifact: object) -> tuple[str, object] | None:
        seen: set[int] = set()
        for _ in range(_LIMIT):
            if not isinstance(artifact, dict):
                break
            uri = artifact.get("uri")
            if uri is not None:
                return (uri, artifact.get("uriBaseId")) if isinstance(uri, str) else None
            index = artifact.get("index")
            if not _valid_index(index, self._artifacts, seen):
                break
            seen.add(index)
            record = self._artifacts[index]
            artifact = record.get("location") if isinstance(record, dict) else None
        return None

    def _base(self, name: str) -> str | None:
        chain: list[tuple[str, str]] = []
        seen: set[str] = set()
        current = name
        value = None
        for _ in range(_LIMIT):
            if current in seen:
                break
            if current in self._cache:
                value = self._cache[current]
                break
            seen.add(current)
            location = self._location(self._bases[current])
            if location is None:
                break
            uri, parent = location
            if _terminal_base(uri, parent, self._bases):
                value = uri if _directory_uri(uri) else None
                self._cache[current] = value
                break
            chain.append((current, uri))
            if not isinstance(parent, str):
                break
            current = parent
        for base, suffix in reversed(chain):
            value = _join(value, suffix)
            self._cache[base] = value
        self._cache.setdefault(name, value)
        return self._cache[name]


def _valid_index(index: object, artifacts: object, seen: set[int]) -> TypeGuard[int]:
    return (
        type(index) is int
        and isinstance(artifacts, list)
        and 0 <= index < len(artifacts)
        and index not in seen
    )


def _terminal_base(uri: str, parent: object, bases: dict[str, Any]) -> bool:
    return (
        bool(urlparse(uri).scheme)
        or parent is None
        or isinstance(parent, str)
        and parent not in bases
    )


def _directory_uri(uri: str) -> bool:
    parsed = urlparse(uri)
    return (
        uri.endswith("/")
        and parsed.scheme in {"", "file"}
        and parsed.netloc in {"", "localhost"}
        and not parsed.query
        and not parsed.fragment
    )


def _join(base: str | None, uri: str) -> str | None:
    if base is None or not _directory_uri(base) or not _relative_within_base(uri):
        return None
    return urljoin(base, uri)


def _relative_within_base(uri: str) -> bool:
    parsed = urlparse(uri)
    path = unquote(parsed.path).replace("\\", "/")
    if parsed.scheme or parsed.netloc or path.startswith("/"):
        return False
    depth = 0
    for component in path.split("/"):
        if component == "..":
            if depth == 0:
                return False
            depth -= 1
        elif component not in {"", "."}:
            depth += 1
    return True
