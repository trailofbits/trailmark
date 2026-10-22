"""Root-bounded header reads shared by one C directory parse."""

from collections.abc import Iterator
from pathlib import Path

from tree_sitter import Node, Parser, Tree

_DIRECTIVES = frozenset({"preproc_def", "preproc_function_def", "preproc_call", "preproc_include"})


def _directive_nodes(root: Node) -> Iterator[Node]:
    source = root.text or b""
    offset = root.start_byte
    pending = [root]
    while pending:
        node = pending.pop()
        if node.type in _DIRECTIVES:
            yield node
        else:
            pending.extend(
                child
                for child in reversed(node.named_children)
                if source.find(b"#", child.start_byte - offset, child.end_byte - offset) >= 0
            )


def _include_spelling(node: Node) -> str | None:
    if node.type != "preproc_include":
        return None
    include = node.child_by_field_name("path")
    if include is None or include.type != "string_literal" or include.text is None:
        return None
    return include.text.decode()[1:-1]


class HeaderSources:
    """Cache headers only for the current operation, including failed lookups."""

    def __init__(self, root: Path, parser: Parser) -> None:
        self.root = root.resolve()
        self.parser = parser
        self._headers: dict[Path, Tree | None] = {}
        self._directives: dict[int, tuple[Node, ...]] = {}
        self._conditions: dict[int, dict[int, bool]] = {}
        self._included: dict[str, set[str]] = {}
        self._links: dict[Path, set[Path]] = {}
        self._current_directives: tuple[Node, tuple[Node, ...]] | None = None
        self._resolved: dict[Path, Path | None] = {}

    def resolve(self, path: Path) -> Path | None:
        """Memoize canonical paths only for this analysis operation."""
        if path not in self._resolved:
            try:
                self._resolved[path] = path.resolve()
            except (OSError, RuntimeError):
                self._resolved[path] = None
        return self._resolved[path]

    def included_files(self, source: str) -> set[str]:
        """Resolve bounded, literal quoted include paths for linkage visibility."""
        if source in self._included:
            return self._included[source]
        found: set[str] = set()
        pending = [(Path(source), 0)]
        while pending and len(found) < 128:
            path, depth = pending.pop()
            if depth > 16:
                continue
            for target in self._direct_includes(path):
                if str(target) not in found:
                    found.add(str(target))
                    pending.append((target, depth + 1))
        self._included[source] = found
        return found

    def _direct_includes(self, path: Path) -> set[Path]:
        resolved = self.resolve(path)
        if resolved is None:
            return set()
        path = resolved
        if path not in self._links:
            root = self.get(path)
            self._links[path] = self._include_paths(root, path) if root is not None else set()
        return self._links[path]

    def _include_paths(self, root: Node, path: Path) -> set[Path]:
        return {
            target
            for node in self.directives(root, path)
            if (target := self._quoted_include(node, path)) is not None
        }

    def _quoted_include(self, node: Node, path: Path) -> Path | None:
        spelling = _include_spelling(node)
        if spelling is None:
            return None
        for base in (path.parent, self.root):
            target = self.resolve(base / spelling)
            if (
                target is not None
                and target.is_relative_to(self.root)
                and self.get(target) is not None
            ):
                return target
        return None

    def include(self, node: Node, path: Path) -> tuple[Path, Node] | None:
        """Find a quoted include beside its source, then at the analysis root."""
        target = self._quoted_include(node, path)
        root = self.get(target) if target is not None else None
        return (target, root) if target is not None and root is not None else None

    def conditions(self, root: Node, path: Path) -> dict[int, bool]:
        """Cache ancestry only while its header tree is retained by this operation."""
        if path.suffix.lower() != ".h":
            return {}
        return self._conditions.setdefault(root.id, {})

    def get(self, path: Path) -> Node | None:
        resolved = self.resolve(path)
        if resolved is None or not resolved.is_relative_to(self.root):
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
        self._links[resolved] = self._include_paths(tree.root_node, resolved)
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
        if self._current_directives is not None and self._current_directives[0] == root:
            return self._current_directives[1]
        if root.id not in self._directives:
            nodes = tuple(_directive_nodes(root))
            if path.suffix.lower() != ".h":
                self._current_directives = root, nodes
                return nodes
            self._directives[root.id] = nodes
        return self._directives[root.id]
