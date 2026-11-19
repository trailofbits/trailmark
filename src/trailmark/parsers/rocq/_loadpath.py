"""Resolve explicit -Q/-R mappings without guessing logical module prefixes."""

import shlex
from pathlib import Path


def logical_module(path: str, fallback: str) -> str:
    source = Path(path).resolve()
    for directory in source.parents:
        project = directory / "_CoqProject"
        if project.is_file():
            return _mapped(source, directory, project.read_text(), fallback)
    return fallback


def _mapped(source: Path, directory: Path, text: str, fallback: str) -> str:
    try:
        tokens = shlex.split(text, comments=True)
    except ValueError:
        return fallback
    mappings = []
    for index, token in enumerate(tokens[:-2]):
        if token not in {"-Q", "-R"}:
            continue
        physical = (directory / tokens[index + 1]).resolve()
        if source.is_relative_to(physical):
            relative = source.relative_to(physical).with_suffix("")
            logical = ".".join(filter(None, (tokens[index + 2], *relative.parts)))
            mappings.append((len(physical.parts), logical))
    return max(mappings, default=(0, fallback))[1]
