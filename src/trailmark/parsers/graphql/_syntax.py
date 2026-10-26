"""Offset-preserving grammar input for GraphQL string escapes."""

import json
import re

_LEXEME = re.compile(
    rb'#[^\n\r]*|"""(?:\\"""|[\s\S])*?"""|"(?:\\[^\n\r]|[^"\\\n\r])*"',
)
_ESCAPE = re.compile(rb"\\[^\n\r]")
_UNICODE = re.compile(r"\\(?:u\{([0-9a-fA-F]+)\}|.)")


def decode_graphql_string(text: str) -> str | None:
    """Decode the fixed/variable-width escapes in GraphQL September 2025 strings.

    https://spec.graphql.org/September2025/#sec-String-Value
    """

    def expand(match: re.Match[str]) -> str:
        digits = match.group(1)
        if digits is None:
            return match.group()
        digits = digits.lstrip("0") or "0"
        if len(digits) > 6:
            raise ValueError("Invalid Unicode scalar")
        scalar = int(digits, 16)
        if scalar > 0x10FFFF or 0xD800 <= scalar <= 0xDFFF:
            raise ValueError("Invalid Unicode scalar")
        return json.dumps(chr(scalar))[1:-1]

    try:
        value = json.loads(_UNICODE.sub(expand, text), strict=False)
    except ValueError:
        return None
    if not isinstance(value, str) or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        return None
    return value


def mask_graphql_escapes(source: bytes) -> bytes:
    """Mask escapes rejected by the bundled grammar without moving syntax spans.

    The tree supplies structure only. Literal consumers must decode original
    source bytes, never the placeholder text in the returned tree's strings.
    """

    def mask(match: re.Match[bytes]) -> bytes:
        lexeme = match.group()
        if lexeme.startswith(b"#"):
            return lexeme
        if lexeme.startswith(b'"""'):
            return lexeme[:3] + lexeme[3:-3].replace(b'\\"""', b"____") + lexeme[-3:]
        try:
            if decode_graphql_string(lexeme.decode()) is None:
                return lexeme
        except UnicodeDecodeError:
            return lexeme
        return _ESCAPE.sub(lambda item: b"_" * len(item.group()), lexeme)

    return _LEXEME.sub(mask, source)
