"""Declaration macro collection skips unresolved and previously visited headers."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from tree_sitter import Parser
from tree_sitter_language_pack import get_language

from trailmark.parsers.c._headers import HeaderSources
from trailmark.parsers.c._macros import DeclarationMacros


@pytest.mark.parametrize("resolved", [False, True], ids=["unresolved", "repeated"])
def test_header_collection_does_not_repeat_directive_work(tmp_path: Path, resolved: bool) -> None:
    path = tmp_path / "header.h"
    root = Parser(get_language("c")).parse(b"#define FLAG 1\n").root_node
    headers = Mock(spec=HeaderSources)
    headers.resolve.return_value = path if resolved else None
    headers.conditions.return_value = {}
    headers.directives.return_value = []
    macros = DeclarationMacros(root, str(path), headers)
    macros._collect(root, path, 0, 0)
    macros._collect(root, path, 0, 0)
    assert headers.conditions.call_count == int(resolved)
    assert headers.directives.call_count == int(resolved)
