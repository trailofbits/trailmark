"""Small recursive type construction helpers for schema frontends."""

from __future__ import annotations

from trailmark.models.nodes import TypeRef


def schema_type(text: str) -> TypeRef:
    """Preserve wrappers, generic arguments and qualified named leaves."""
    text = text.strip()
    if text.endswith("?"):
        return TypeRef(name=text, generic_args=(schema_type(text[:-1]),))
    opening = text.find("<")
    if opening >= 0 and text.endswith(">"):
        args = []
        start = depth = 0
        content = text[opening + 1 : -1]
        for index, char in enumerate(content):
            if char in "<[({":
                depth += 1
            elif char in ">])}":
                depth -= 1
            elif char == "," and depth == 0:
                args.append(schema_type(content[start:index]))
                start = index + 1
        args.append(schema_type(content[start:]))
        return TypeRef(name=text, generic_args=tuple(args))
    if "." in text:
        module, _, name = text.rpartition(".")
        return TypeRef(name=name, module=module)
    return TypeRef(name=text)
