"""Conservative class-name hints for Python raises, using the lexical binding index."""

from __future__ import annotations

from functools import cached_property

from tree_sitter import Node

from trailmark.analysis._python_decorators import (
    _Analyzer,
    _Binding,
    _is_decorated,
    _Scope,
    _select,
)

# Public builtin exceptions across supported Python versions. Keep classification
# independent of the interpreter running Trailmark; target code is never imported.
_BUILTIN_EXCEPTIONS = frozenset(
    [
        "ArithmeticError",
        "AssertionError",
        "AttributeError",
        "BaseException",
        "BaseExceptionGroup",
        "BlockingIOError",
        "BrokenPipeError",
        "BufferError",
        "BytesWarning",
        "ChildProcessError",
        "ConnectionAbortedError",
        "ConnectionError",
        "ConnectionRefusedError",
        "ConnectionResetError",
        "DeprecationWarning",
        "EOFError",
        "EncodingWarning",
        "EnvironmentError",
        "Exception",
        "ExceptionGroup",
        "FileExistsError",
        "FileNotFoundError",
        "FloatingPointError",
        "FutureWarning",
        "GeneratorExit",
        "IOError",
        "ImportCycleError",
        "ImportError",
        "ImportWarning",
        "IndentationError",
        "IndexError",
        "InterruptedError",
        "IsADirectoryError",
        "KeyError",
        "KeyboardInterrupt",
        "LookupError",
        "MemoryError",
        "ModuleNotFoundError",
        "NameError",
        "NotADirectoryError",
        "NotImplementedError",
        "OSError",
        "OverflowError",
        "PendingDeprecationWarning",
        "PermissionError",
        "ProcessLookupError",
        "PythonFinalizationError",
        "RecursionError",
        "ReferenceError",
        "ResourceWarning",
        "RuntimeError",
        "RuntimeWarning",
        "StopAsyncIteration",
        "StopIteration",
        "SyntaxError",
        "SyntaxWarning",
        "SystemError",
        "SystemExit",
        "TabError",
        "TimeoutError",
        "TypeError",
        "UnboundLocalError",
        "UnicodeDecodeError",
        "UnicodeEncodeError",
        "UnicodeError",
        "UnicodeTranslateError",
        "UnicodeWarning",
        "UserWarning",
        "ValueError",
        "Warning",
        "WindowsError",
        "ZeroDivisionError",
    ]
)


class PythonRaisedNames:
    """Build scope evidence once per source file and preserve source spellings.

    Only builtin exceptions and explicit, undecorated class definitions qualify.
    Assignments, parameters, conditional definitions and unknown imports stay unknown.
    """

    def __init__(self, root: Node) -> None:
        self._root = root

    @cached_property
    def _bindings(self) -> _Analyzer:
        return _Analyzer("", self._root)

    def type_name(self, operand: Node) -> str | None:
        parts = _parts(operand)
        if not parts or self._bindings._writes[1]:
            return None
        scope = self._bindings._owner(operand)
        value = self._identifier(parts[0], scope, operand.start_byte)
        for member in parts[1:]:
            value = self._member(value, member)
        if isinstance(value, Node) or isinstance(value, str) and value.startswith("builtins."):
            return _text(operand)
        return None

    def _identifier(self, name: str, scope: _Scope, position: int) -> Node | str | None:
        binding, _ = self._bindings._lookup(name, scope, position)
        if binding is None:
            return self._builtin(name)
        target = binding.target
        if binding.kind == "import_module" and target == "builtins":
            return "builtins"
        if binding.kind == "import_name" and isinstance(target, str):
            module, _, member = target.rpartition(".")
            return self._builtin(member) if module == "builtins" else None
        return _class_definition(binding)

    def _builtin(self, name: str) -> str | None:
        written, _ = self._bindings._writes
        return f"builtins.{name}" if name in _BUILTIN_EXCEPTIONS and name not in written else None

    def _member(self, value: Node | str | None, name: str) -> Node | str | None:
        if value == "builtins":
            return self._builtin(name)
        if not isinstance(value, Node):
            return None
        written, dynamic = self._bindings._writes
        if name in written or dynamic:
            return None
        scope = self._bindings._scopes.get(value.id)
        found = scope.bindings.get(name, []) if scope is not None else []
        return _class_definition(_select(found, None))


def _class_definition(binding: _Binding) -> Node | None:
    target = binding.target
    if binding.kind != "definition" or not isinstance(target, Node):
        return None
    return target if target.type == "class_definition" and not _is_decorated(target) else None


def _parts(node: Node) -> list[str]:
    parts = []
    while node.type == "attribute":
        attribute, value = node.child_by_field_name("attribute"), node.child_by_field_name("object")
        if attribute is None or value is None:
            return []
        parts.append(_text(attribute))
        node = value
    return [_text(node), *reversed(parts)] if node.type == "identifier" else []


def _text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")
