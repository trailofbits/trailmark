"""Path selection vectors with an independent segment-by-segment oracle."""

from __future__ import annotations

from fnmatch import fnmatchcase

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from trailmark._path_glob import compile_path_glob


@pytest.mark.mutation_fast
@pytest.mark.parametrize(
    "pattern,path,expected",
    [
        ("", "", True),
        ("", "a", False),
        ("*", "", True),
        ("*", "a", True),
        ("*", "a/b", False),
        ("?", "", False),
        ("?", "a", True),
        ("?", "ab", False),
        ("?", "/", False),
        ("a*", "a", True),
        ("a*", "ab", True),
        ("a*", "a/b", False),
        ("**", "", True),
        ("**", "a/b", True),
        ("**/a", "a", True),
        ("**/a", "b/a", True),
        ("**/a", "ba", False),
        ("**/a", "a/b", False),
        ("a/**", "a", False),
        ("a/**", "a/", True),
        ("a/**", "a/b/c", True),
        ("a/**/b", "a/b", True),
        ("a/**/b", "a/x/b", True),
        ("a/**/b", "a/x/y/b", True),
        ("a/**/b", "a/x/b/c", False),
        ("a/**/b", "ab", False),
        ("**/**/b", "b", True),
        ("**/**/**/b", "a/b", True),
        ("a/**/**/b", "a/b", True),
        ("a/**/**/b", "a/x/y/b", True),
        ("a/*/b", "a/b", False),
        ("a/*/b", "a/x/b", True),
        ("a/*/b", "a/x/y/b", False),
        ("a?b", "a/b", False),
        ("a?b", "axb", True),
        ("a**b", "a/x/b", True),
        ("a**b", "ab", True),
        ("a***", "a", True),
        ("a***", "a/b", True),
        ("a", "a\n", False),
        ("a", "ba", False),
        ("*", "a\nb", True),
        ("**", "a\nb", True),
        ("**/a", "b\nc/a", True),
        ("a/**/b", "a/x\ny/b", True),
        ("a/**", "a/x\ny", True),
        ("**/*.py", "a\nb/file.py", True),
    ],
)
def test_path_glob_boundaries(pattern: str, path: str, expected: bool) -> None:
    assert (compile_path_glob(pattern).search(path) is not None) is expected


@pytest.mark.parametrize("literal", list(".+^$()[]{}|\\") + ["é", " "])
def test_path_glob_treats_regex_punctuation_literally(literal: str) -> None:
    pattern = compile_path_glob(f"a{literal}b")
    assert pattern.fullmatch(f"a{literal}b") is not None
    assert pattern.fullmatch("axb") is None


@pytest.mark.parametrize("suffix", [False, True])
@pytest.mark.parametrize(
    "path,anchored,suffixed",
    [
        ("vendor/pkg.py", True, True),
        ("project/vendor/pkg.py", False, True),
        ("notvendor/pkg.py", False, False),
        ("vendor/pkg.py/child", False, False),
        ("project/vendor/pkg.txt", False, False),
    ],
)
def test_path_glob_suffixes_begin_at_segment_boundaries(
    suffix: bool, path: str, anchored: bool, suffixed: bool
) -> None:
    assert (compile_path_glob("vendor/*.py", suffix=suffix).search(path) is not None) is (
        suffixed if suffix else anchored
    )


def _matches_segments(pattern: list[str], path: list[str]) -> bool:
    if not pattern:
        return not path
    if pattern[0] == "**":
        return any(_matches_segments(pattern[1:], path[index:]) for index in range(len(path) + 1))
    return (
        bool(path) and fnmatchcase(path[0], pattern[0]) and _matches_segments(pattern[1:], path[1:])
    )


@pytest.mark.mutation_fast
@given(
    pattern=st.sampled_from(
        ["", "*", "?", "a*", "*.py", "a/*/b", "a/**/b", "**/a", "**/*.py", "**/**/b"]
    ),
    segments=st.lists(
        st.sampled_from(["a", "b", "ab", "a.py", "b.txt", "a+b", "a\nb"]), max_size=4
    ),
    suffix=st.booleans(),
)
@settings(max_examples=200)
def test_path_glob_matches_independent_segment_oracle(
    pattern: str, segments: list[str], suffix: bool
) -> None:
    path = "/".join(segments)
    parts = path.split("/")
    candidates = [parts[index:] for index in range(len(parts))] if suffix else [parts]
    expected = any(_matches_segments(pattern.split("/"), candidate) for candidate in candidates)
    assert (compile_path_glob(pattern, suffix=suffix).search(path) is not None) is expected
