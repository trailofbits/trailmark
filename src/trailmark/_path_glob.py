"""Shared segment-aware source path globs."""

import re
from functools import lru_cache


@lru_cache(maxsize=256)
def compile_path_glob(pattern: str, *, suffix: bool = False) -> re.Pattern[str]:
    """Translate a shell-style glob with ``**`` support into a regex.

    - ``**`` (with surrounding slashes) matches zero or more path segments.
    - ``*`` matches any characters except ``/``.
    - ``?`` matches one character other than ``/``.
    - Other regex metacharacters are escaped.

    Matches start at path-segment boundaries and extend to the end of the path.
    The caller supplies configuration-root-relative paths with Windows and
    POSIX separators normalized to ``/``.
    """
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*" and i + 1 < n and pattern[i + 1] == "*":
            # `/**/` expands to `(?:/.*)?/` — zero or more segments.
            if i > 0 and pattern[i - 1] == "/" and i + 2 < n and pattern[i + 2] == "/":
                # Rewrite the trailing `/` of the preceding segment as
                # part of the globstar expansion.
                out[-1] = "(?:/.*)?/"
                i += 3
                continue
            # Leading `**/` or trailing `/**`.
            if i + 2 < n and pattern[i + 2] == "/":
                out.append("(?:.*/)?")
                i += 3
                continue
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c in r".+^$()[]{}|\\":
            out.append("\\" + c)
            i += 1
        else:
            out.append(c)
            i += 1
    return re.compile((r"(?:^|/)" if suffix else r"\A") + "".join(out) + r"\Z")
