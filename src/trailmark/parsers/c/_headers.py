"""Root-bounded header reads shared by one C directory parse."""

from pathlib import Path

from tree_sitter import Node, Parser, Tree

from trailmark.parsers._walk import walk_owned


class HeaderSources:
    """Cache headers only for the current operation, including failed lookups."""

    def __init__(self, root: Path, parser: Parser) -> None:
        self.root = root.resolve()
        self.parser = parser
        self._headers: dict[Path, Tree | None] = {}
        self._directives: dict[int, tuple[Node, ...]] = {}
        self._conditions: dict[int, dict[int, bool]] = {}

    def conditions(self, root: Node, path: Path) -> dict[int, bool]:
        """Cache ancestry only while its header tree is retained by this operation."""
        if path.suffix.lower() != ".h":
            return {}
        return self._conditions.setdefault(root.id, {})

    def get(self, path: Path) -> Node | None:
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError):
            return None
        if not resolved.is_relative_to(self.root):
            return None
        if resolved not in self._headers:
            self._headers[resolved] = self._read(resolved)
        tree = self._headers[resolved]
        return tree.root_node if tree is not None else None

    def parse_file(self, path: Path) -> tuple[bytes, Tree]:
        resolved = path.resolve()
        cached = self._headers.get(resolved)
        if cached is not None:
            return cached.root_node.text or b"", cached
        source = path.read_bytes()
        tree = self.parser.parse(source)
        if path.suffix.lower() == ".h":
            self._headers[resolved] = tree
        return source, tree

    def _read(self, path: Path) -> Tree | None:
        try:
            if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
                return None
            source = path.read_bytes()
        except OSError:
            return None
        return self.parser.parse(source)

    def directives(self, root: Node, path: Path) -> tuple[Node, ...]:
        if root.id not in self._directives:
            nodes = tuple(
                node
                for node in walk_owned(root)
                if node.type
                in {"preproc_def", "preproc_function_def", "preproc_call", "preproc_include"}
            )
            if path.suffix.lower() != ".h":
                return nodes
            self._directives[root.id] = nodes
        return self._directives[root.id]
