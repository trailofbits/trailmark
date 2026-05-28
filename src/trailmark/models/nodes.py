"""Core node types for the code graph."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

type AttributeValue = str | int | float | bool | None
type Attribute = tuple[str, AttributeValue]


class NodeKind(Enum):
    """The kind of code unit represented by a node."""

    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"
    MODULE = "module"
    STRUCT = "struct"
    INTERFACE = "interface"
    TRAIT = "trait"
    ENUM = "enum"
    NAMESPACE = "namespace"
    CONTRACT = "contract"
    LIBRARY = "library"
    TEMPLATE = "template"
    PROXY = "proxy"


class NodeOrigin(Enum):
    """Where a node came from."""

    SOURCE = "source"
    PROXY = "proxy"
    BINARY = "binary"
    SYNTHETIC = "synthetic"


@dataclass(frozen=True)
class SourceLocation:
    """A span of source code in a file."""

    file_path: str
    start_line: int
    end_line: int
    start_col: int | None = None
    end_col: int | None = None


@dataclass(frozen=True)
class TypeRef:
    """A reference to a type, possibly with generic arguments."""

    name: str
    module: str | None = None
    generic_args: tuple[TypeRef, ...] = ()


@dataclass(frozen=True)
class TypeParameter:
    """A generic type parameter declared by a code unit."""

    name: str
    constraints: tuple[TypeRef, ...] = ()
    default: TypeRef | None = None
    variance: str | None = None


@dataclass(frozen=True)
class Parameter:
    """A function or method parameter."""

    name: str
    type_ref: TypeRef | None = None
    default: str | None = None


@dataclass(frozen=True)
class BranchInfo:
    """Metadata about a branch within a function."""

    location: SourceLocation
    condition: str
    complexity_contribution: int = 1


@dataclass(frozen=True)
class CodeUnit:
    """A node in the code graph: function, method, class, or module."""

    id: str
    name: str
    kind: NodeKind
    location: SourceLocation
    parameters: tuple[Parameter, ...] = ()
    return_type: TypeRef | None = None
    exception_types: tuple[TypeRef, ...] = ()
    type_parameters: tuple[TypeParameter, ...] = ()
    cyclomatic_complexity: int | None = None
    branches: tuple[BranchInfo, ...] = ()
    docstring: str | None = None
    origin: NodeOrigin = NodeOrigin.SOURCE
    attributes: tuple[Attribute, ...] = ()
