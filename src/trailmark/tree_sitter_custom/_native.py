"""Content-addressed, atomic compilation for bundled tree-sitter grammars."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
import sysconfig
import tempfile
from dataclasses import dataclass
from pathlib import Path


def extension_suffix() -> str:
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    if not isinstance(suffix, str) or not suffix:
        raise ImportError("Python does not provide a native extension suffix")
    return suffix


@dataclass(frozen=True)
class NativeGrammar:
    """Compile and load a grammar for the current platform and Python ABI."""

    directory: Path
    sources: tuple[str, ...]

    def compiler_flags(self) -> list[str]:
        flags = ["cc", "-shared", "-fPIC", "-O2", "-std=c11"]
        if sys.platform == "darwin":
            flags.extend(["-undefined", "dynamic_lookup"])
        flags.extend([f"-I{sysconfig.get_path('include')}", f"-I{self.directory / 'src'}"])
        return flags

    def binding_path(self) -> Path:
        digest = hashlib.sha256()
        configuration = (
            self.compiler_flags(),
            sysconfig.get_config_var("SOABI"),
            sysconfig.get_platform(),
        )
        digest.update(repr(configuration).encode())
        for relative in self.sources:
            digest.update(relative.encode())
            digest.update(hashlib.sha256((self.directory / relative).read_bytes()).digest())
        return self.directory / f"_binding_{digest.hexdigest()}{extension_suffix()}"

    def build(self, output_path: Path) -> None:
        """Publish a complete binding; preserve existing output on failure."""
        with tempfile.NamedTemporaryFile(
            dir=output_path.parent, prefix=".grammar-", suffix=extension_suffix(), delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
        try:
            cmd = self.compiler_flags()
            cmd.extend(str(self.directory / path) for path in self.sources if path.endswith(".c"))
            cmd.extend(["-o", str(temporary_path)])
            self._compile(cmd)
            os.replace(temporary_path, output_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _compile(cmd: list[str]) -> None:
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)  # noqa: S603
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "no compiler diagnostic").strip()
            msg = f"Grammar compilation failed (exit {exc.returncode}): {detail}"
            raise ImportError(msg) from exc

    def language(self) -> object:
        """Return a tree-sitter Language capsule, compiling changed inputs on demand."""
        binding_path = self.binding_path()
        if not binding_path.exists():
            self.build(binding_path)
        spec = importlib.util.spec_from_file_location("_binding", str(binding_path))
        if spec is None or spec.loader is None:
            raise ImportError(f"Failed to load compiled grammar from {binding_path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        entrypoint = getattr(mod, "language", None)
        if not callable(entrypoint):
            raise ImportError(
                f"Grammar binding has no callable language entrypoint: {binding_path}"
            )
        return entrypoint()
