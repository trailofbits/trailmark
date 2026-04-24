"""Argparse-surface tests for ``trailmark.cli.build_parser``.

The parser structure — subcommand names, flag long/short forms,
defaults, and action types — is a large mutation target (every
literal in ``build_parser`` is a mutant). Each assertion below locks
in one visible fact about the CLI so a regression or accidental
rename is caught mechanically.
"""

from __future__ import annotations

import argparse

import pytest

from trailmark.cli import build_parser


@pytest.fixture(scope="module")
def parser() -> argparse.ArgumentParser:
    return build_parser()


@pytest.fixture(scope="module")
def subparsers_map(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:  # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return dict(action.choices)
    raise AssertionError("No subparsers action on the CLI parser")


def _option(subparser: argparse.ArgumentParser, flag: str) -> argparse.Action:
    for action in subparser._actions:  # noqa: SLF001
        if flag in action.option_strings:
            return action
    raise AssertionError(f"Option {flag!r} not found on subparser")


def _positional(subparser: argparse.ArgumentParser, name: str) -> argparse.Action:
    for action in subparser._actions:  # noqa: SLF001
        if not action.option_strings and action.dest == name:
            return action
    raise AssertionError(f"Positional {name!r} not found on subparser")


class TestTopLevelParser:
    def test_prog_name(self, parser: argparse.ArgumentParser) -> None:
        assert parser.prog == "trailmark"

    def test_command_dest(self, parser: argparse.ArgumentParser) -> None:
        """``args.command`` carries the subcommand name chosen by the user."""
        for action in parser._actions:  # noqa: SLF001
            if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
                assert action.dest == "command"
                return
        raise AssertionError("No subparsers action")

    def test_all_subcommands_registered(
        self, subparsers_map: dict[str, argparse.ArgumentParser]
    ) -> None:
        assert set(subparsers_map) == {
            "analyze",
            "augment",
            "entrypoints",
            "diff",
            "version",
        }


class TestVersionFlag:
    """Regression for issue #26 — the CLI must expose its version."""

    def test_long_flag_prints_and_exits(
        self, parser: argparse.ArgumentParser, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import trailmark

        with pytest.raises(SystemExit) as excinfo:
            parser.parse_args(["--version"])
        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        assert out.strip() == f"trailmark {trailmark.__version__}"

    def test_short_flag_prints_and_exits(
        self, parser: argparse.ArgumentParser, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import trailmark

        with pytest.raises(SystemExit) as excinfo:
            parser.parse_args(["-V"])
        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        assert out.strip() == f"trailmark {trailmark.__version__}"

    def test_version_subcommand_registered(
        self, subparsers_map: dict[str, argparse.ArgumentParser]
    ) -> None:
        assert "version" in subparsers_map

    def test_version_subcommand_parses(self, parser: argparse.ArgumentParser) -> None:
        args = parser.parse_args(["version"])
        assert args.command == "version"


class TestAnalyzeSubparser:
    def test_path_is_positional(self, subparsers_map: dict[str, argparse.ArgumentParser]) -> None:
        action = _positional(subparsers_map["analyze"], "path")
        assert action.option_strings == []

    def test_language_default_python(
        self, subparsers_map: dict[str, argparse.ArgumentParser]
    ) -> None:
        action = _option(subparsers_map["analyze"], "--language")
        assert action.default == "python"

    def test_language_has_short_flag_l(
        self, subparsers_map: dict[str, argparse.ArgumentParser]
    ) -> None:
        action = _option(subparsers_map["analyze"], "--language")
        assert "-l" in action.option_strings

    def test_summary_is_store_true(
        self, subparsers_map: dict[str, argparse.ArgumentParser]
    ) -> None:
        action = _option(subparsers_map["analyze"], "--summary")
        assert action.const is True
        assert action.default is False

    def test_summary_has_short_flag_s(
        self, subparsers_map: dict[str, argparse.ArgumentParser]
    ) -> None:
        action = _option(subparsers_map["analyze"], "--summary")
        assert "-s" in action.option_strings

    def test_complexity_type_int_default_zero(
        self, subparsers_map: dict[str, argparse.ArgumentParser]
    ) -> None:
        action = _option(subparsers_map["analyze"], "--complexity")
        assert action.type is int
        assert action.default == 0

    def test_complexity_has_short_flag_c(
        self, subparsers_map: dict[str, argparse.ArgumentParser]
    ) -> None:
        action = _option(subparsers_map["analyze"], "--complexity")
        assert "-c" in action.option_strings


class TestDiffSubparser:
    def test_positional_before_and_after(
        self, subparsers_map: dict[str, argparse.ArgumentParser]
    ) -> None:
        assert _positional(subparsers_map["diff"], "before") is not None
        assert _positional(subparsers_map["diff"], "after") is not None

    def test_language_default_python(
        self, subparsers_map: dict[str, argparse.ArgumentParser]
    ) -> None:
        action = _option(subparsers_map["diff"], "--language")
        assert action.default == "python"

    def test_repo_defaults_to_cwd(self, subparsers_map: dict[str, argparse.ArgumentParser]) -> None:
        action = _option(subparsers_map["diff"], "--repo")
        assert action.default == "."

    def test_json_is_store_true(self, subparsers_map: dict[str, argparse.ArgumentParser]) -> None:
        action = _option(subparsers_map["diff"], "--json")
        assert action.const is True
        assert action.default is False


class TestEntrypointsSubparser:
    def test_path_is_positional(self, subparsers_map: dict[str, argparse.ArgumentParser]) -> None:
        action = _positional(subparsers_map["entrypoints"], "path")
        assert action.option_strings == []

    def test_language_default_python(
        self, subparsers_map: dict[str, argparse.ArgumentParser]
    ) -> None:
        action = _option(subparsers_map["entrypoints"], "--language")
        assert action.default == "python"

    def test_json_is_store_true(self, subparsers_map: dict[str, argparse.ArgumentParser]) -> None:
        action = _option(subparsers_map["entrypoints"], "--json")
        assert action.const is True
        assert action.default is False


class TestAugmentSubparser:
    def test_path_is_positional(self, subparsers_map: dict[str, argparse.ArgumentParser]) -> None:
        action = _positional(subparsers_map["augment"], "path")
        assert action.option_strings == []

    def test_language_default_python(
        self, subparsers_map: dict[str, argparse.ArgumentParser]
    ) -> None:
        action = _option(subparsers_map["augment"], "--language")
        assert action.default == "python"

    def test_sarif_is_repeatable(self, subparsers_map: dict[str, argparse.ArgumentParser]) -> None:
        action = _option(subparsers_map["augment"], "--sarif")
        # argparse records `action="append"` by storing a _AppendAction
        # (or equivalent); the empty list default is the tell.
        assert action.default == []

    def test_weaudit_is_repeatable(
        self, subparsers_map: dict[str, argparse.ArgumentParser]
    ) -> None:
        action = _option(subparsers_map["augment"], "--weaudit")
        assert action.default == []

    def test_json_is_store_true(self, subparsers_map: dict[str, argparse.ArgumentParser]) -> None:
        action = _option(subparsers_map["augment"], "--json")
        assert action.const is True
        assert action.default is False


class TestParseBehavior:
    """End-to-end parse smoke tests that assert argparse produced the
    right Namespace fields for a representative command line."""

    def test_analyze_path_summary(self, parser: argparse.ArgumentParser) -> None:
        args = parser.parse_args(["analyze", "scratch-dir", "--summary"])
        assert args.command == "analyze"
        assert args.path == "scratch-dir"
        assert args.summary is True
        assert args.language == "python"
        assert args.complexity == 0

    def test_analyze_short_flags(self, parser: argparse.ArgumentParser) -> None:
        args = parser.parse_args(["analyze", "p", "-l", "rust", "-c", "10"])
        assert args.language == "rust"
        assert args.complexity == 10

    def test_diff_positionals(self, parser: argparse.ArgumentParser) -> None:
        args = parser.parse_args(["diff", "before/", "after/"])
        assert args.command == "diff"
        assert args.before == "before/"
        assert args.after == "after/"
        assert args.repo == "."
        assert args.json is False

    def test_entrypoints_json_flag(self, parser: argparse.ArgumentParser) -> None:
        args = parser.parse_args(["entrypoints", "p", "--json"])
        assert args.json is True

    def test_augment_repeatable_sarif(self, parser: argparse.ArgumentParser) -> None:
        args = parser.parse_args(["augment", "p", "--sarif", "a.sarif", "--sarif", "b.sarif"])
        assert args.sarif == ["a.sarif", "b.sarif"]


class TestVersionSubcommandExecution:
    def test_main_version_prints_version(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trailmark
        from trailmark.cli import main

        monkeypatch.setattr("sys.argv", ["trailmark", "version"])
        main()
        out = capsys.readouterr().out
        assert out.strip() == f"trailmark {trailmark.__version__}"
