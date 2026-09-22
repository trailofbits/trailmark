"""Run instrumented modules through real pytest, preserving package metadata.

Compatibility with pytest-gremlins 1.9.0: its lightweight runner bypasses
fixtures/parametrization, and its import hook drops __file__ and package paths.
This script replaces both generated runner scripts; mutation generation is
still performed entirely by pytest-gremlins.
"""

from __future__ import annotations

import json
import os
import sys
from importlib.abc import MetaPathFinder
from importlib.machinery import ModuleSpec, PathFinder, SourceFileLoader
from pathlib import Path
from types import ModuleType
from typing import Any


class InstrumentedLoader(SourceFileLoader):
    def __init__(self, name: str, path: str, source: str) -> None:
        super().__init__(name, path)
        self.source = source

    def exec_module(self, module: ModuleType) -> None:
        exec(compile(self.source, self.path, "exec"), module.__dict__)  # noqa: S102


class InstrumentedFinder(MetaPathFinder):
    def __init__(self, sources: dict[str, str]) -> None:
        self.sources = {name.removesuffix(".__init__"): code for name, code in sources.items()}

    def find_spec(
        self, fullname: str, path: Any = None, target: ModuleType | None = None
    ) -> ModuleSpec | None:
        if fullname not in self.sources:
            return None
        spec = PathFinder.find_spec(fullname, path)
        if spec is None or spec.origin is None:
            raise ImportError(f"Cannot locate instrumented module {fullname}")
        spec.loader = InstrumentedLoader(fullname, spec.origin, self.sources[fullname])
        return spec


def main() -> int:
    root = Path(os.environ.get("GREMLIN_ROOTDIR", Path.cwd()))
    sys.path[:0] = [str(root), str(root / "src")]
    sources = json.loads(Path(os.environ["PYTEST_GREMLINS_SOURCES_FILE"]).read_text())
    sys.meta_path.insert(0, InstrumentedFinder(sources))
    import pytest

    return pytest.main(["-x", "-q", "--tb=short", "-p", "no:gremlins", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
