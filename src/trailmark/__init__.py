"""Trailmark: Parse source code into queryable graphs for security analysis."""

from trailmark.parse import (
    detect_languages,
    parse_directory,
    parse_file,
    parse_roots,
    supported_languages,
)
from trailmark.source_policy import SourcePolicy, SourceRoot

__all__ = [
    "detect_languages",
    "parse_directory",
    "parse_file",
    "parse_roots",
    "SourcePolicy",
    "SourceRoot",
    "supported_languages",
]
__version__ = "0.7.0"
