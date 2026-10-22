"""Cache Erlang import/export declarations for a single parse operation."""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from tree_sitter import Node


@dataclass(frozen=True)
class Symbols:
    exports: frozenset[str]
    imports: dict[str, str]


_CURRENT: ContextVar[Symbols | None] = ContextVar("_ERLANG_SYMBOLS", default=None)


@contextmanager
def module_symbols(root: Node) -> Generator[None]:
    exports: set[str] = set()
    imports = {}
    for form in root.named_children:
        if form.type == "export_attribute":
            exports.update(function_arity(fa) for fa in form.children_by_field_name("funs"))
        elif form.type == "import_attribute":
            module = form.child_by_field_name("module")
            if module is not None and module.text:
                for fa in form.children_by_field_name("funs"):
                    name = function_arity(fa)
                    imports[name] = f"{module.text.decode()}:{name}"
    token = _CURRENT.set(Symbols(frozenset(exports), imports))
    try:
        yield
    finally:
        _CURRENT.reset(token)


def function_arity(node: Node) -> str:
    function = node.child_by_field_name("fun")
    arity = node.child_by_field_name("arity")
    if function is None or arity is None:
        return ""
    return f"{(function.text or b'').decode()}/{(arity.text or b'').decode().removeprefix('/')}"


def exported(name: str) -> bool:
    symbols = _CURRENT.get()
    return symbols is not None and name in symbols.exports


def imported(name: str) -> str | None:
    symbols = _CURRENT.get()
    return symbols.imports.get(name) if symbols is not None else None
