"""Trailmark CLI entry point."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, TextIO

from trailmark import __version__
from trailmark.diagram import add_diagram_arguments, build_engine, render_diagram
from trailmark.query.api import QueryEngine

_VERSION_STRING = f"trailmark {__version__}"


def _complexity_threshold(value: str) -> int:
    """Parse a nonnegative complexity threshold for the CLI."""
    threshold = int(value)
    if threshold < 0:
        msg = "complexity threshold must be nonnegative"
        raise argparse.ArgumentTypeError(msg)
    return threshold


def build_parser() -> argparse.ArgumentParser:
    """Construct the Trailmark CLI's argparse tree."""
    parser = argparse.ArgumentParser(
        prog="trailmark",
        description=f"Parse source code into queryable graphs (v{__version__})",
    )
    parser.add_argument(
        "--version",
        "-V",
        action="version",
        version=_VERSION_STRING,
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser(
        "version",
        help="Print the Trailmark version and exit",
    )

    analyze = subparsers.add_parser(
        "analyze",
        help="Analyze a directory and output the code graph",
    )
    analyze.add_argument("path", help="Directory to analyze")
    analyze.add_argument(
        "--language",
        "-l",
        default="python",
        help="Source language (default: python)",
    )
    output = analyze.add_mutually_exclusive_group()
    output.add_argument(
        "--summary",
        "-s",
        action="store_true",
        help="Print summary instead of full graph",
    )
    output.add_argument(
        "--complexity",
        "-c",
        type=_complexity_threshold,
        default=None,
        help="Show functions with complexity >= threshold (nonnegative)",
    )
    output.add_argument(
        "--resolution",
        action="store_true",
        help="Report extracted call-resolution counts and unresolved callee groups as JSON",
    )
    analyze.add_argument(
        "--json",
        action="store_true",
        help="Print the summary as JSON (graph and resolution output already use JSON)",
    )

    diff_cmd = subparsers.add_parser(
        "diff",
        help="Structural diff between two code graphs",
    )
    diff_cmd.add_argument(
        "before",
        help="Path or git ref for the 'before' snapshot",
    )
    diff_cmd.add_argument(
        "after",
        help="Path or git ref for the 'after' snapshot",
    )
    diff_cmd.add_argument(
        "--language",
        "-l",
        default="python",
        help="Source language (default: python)",
    )
    diff_cmd.add_argument(
        "--repo",
        default=".",
        help=(
            "Repository root used to resolve git refs (default: cwd). "
            "Ignored when BEFORE and AFTER are directory paths."
        ),
    )
    diff_cmd.add_argument(
        "--json",
        action="store_true",
        help="Emit the structured diff as JSON instead of a report",
    )

    entrypoints = subparsers.add_parser(
        "entrypoints",
        help="List detected entrypoints and their trust classification",
    )
    entrypoints.add_argument("path", help="Directory to analyze")
    entrypoints.add_argument(
        "--language",
        "-l",
        default="python",
        help="Source language (default: python)",
    )
    entrypoints.add_argument(
        "--json",
        action="store_true",
        help="Emit entrypoints as JSON instead of a human-readable list",
    )

    augment = subparsers.add_parser(
        "augment",
        help="Augment a code graph with SARIF or weAudit findings",
    )
    augment.add_argument("path", help="Directory to analyze")
    augment.add_argument(
        "--language",
        "-l",
        default="python",
        help="Source language (default: python)",
    )
    augment.add_argument(
        "--sarif",
        action="append",
        default=[],
        help="SARIF file(s) to augment with (repeatable)",
    )
    augment.add_argument(
        "--weaudit",
        action="append",
        default=[],
        help="weAudit file(s) to augment with (repeatable)",
    )
    augment.add_argument(
        "--json",
        action="store_true",
        help="Export graph topology and selected node metadata as JSON (omits finding details)",
    )

    diagram = subparsers.add_parser(
        "diagram",
        help="Generate a Mermaid diagram from the code graph",
    )
    add_diagram_arguments(diagram)

    for command in (analyze, diff_cmd, entrypoints, augment):
        command.add_argument(
            "--allow-empty",
            action="store_true",
            help="Allow an existing directory with zero matching source files",
        )

    return parser


def main() -> None:
    """Run the Trailmark CLI."""
    parser = build_parser()
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "version":
        print(_VERSION_STRING)
        return
    try:
        if args.command == "analyze":
            _run_analyze(args)
        elif args.command == "augment":
            _run_augment(args)
        elif args.command == "entrypoints":
            _run_entrypoints(args)
        elif args.command == "diff":
            _run_diff(args)
        elif args.command == "diagram":
            _run_diagram(args)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"trailmark: error: {exc}\n")
    except subprocess.CalledProcessError as exc:
        parser.exit(2, f"trailmark: error: {exc.stderr.decode().strip()}\n")


def _run_analyze(args: argparse.Namespace) -> None:
    """Execute the analyze subcommand."""
    if args.json and args.complexity is not None:
        raise ValueError("--json cannot be combined with --complexity")
    engine = QueryEngine.from_directory(
        args.path,
        language=args.language,
        allow_empty=args.allow_empty,
    )

    if args.resolution:
        print(json.dumps(engine.call_resolution(), indent=2))
    elif args.summary:
        if args.json:
            print(json.dumps(engine.summary(), indent=2))
        else:
            _print_summary(engine)
    elif args.complexity is not None:
        _print_complexity(engine, args.complexity)
    else:
        print(engine.to_json())


def _print_summary(engine: QueryEngine) -> None:
    """Print a graph summary."""
    summary = engine.summary()
    print(f"Nodes: {summary['total_nodes']}")
    print(f"  Functions: {summary['functions']}")
    print(f"  Classes: {summary['classes']}")
    print(f"  Proxies: {summary['proxies']}")
    print(f"Call edges: {summary['call_edges']}")
    print(f"Dependencies: {', '.join(summary['dependencies'])}")
    print(f"Entrypoints: {summary['entrypoints']}")
    print(f"Languages: {', '.join(summary['languages'])}")
    print(f"Source files: {summary['source_files']}")


def _print_complexity(engine: QueryEngine, threshold: int) -> None:
    """Print complexity hotspots."""
    hotspots = engine.complexity_hotspots(threshold)
    if not hotspots:
        print(f"No functions with complexity >= {threshold}")
        return
    for h in hotspots:
        loc = h["location"]
        print(
            f"  {h['id']}  "
            f"complexity={h['cyclomatic_complexity']}  "
            f"{loc['file_path']}:{loc['start_line']}",
        )


def _run_augment(args: argparse.Namespace) -> None:
    """Execute the augment subcommand."""
    engine = QueryEngine.from_directory(
        args.path,
        language=args.language,
        allow_empty=args.allow_empty,
    )
    summary_stream = sys.stderr if args.json else sys.stdout

    for index, sarif_path in enumerate(args.sarif):
        result = engine.augment_sarif(sarif_path, replace_existing=index == 0)
        _print_augment_result("SARIF", sarif_path, result, file=summary_stream)

    for index, weaudit_path in enumerate(args.weaudit):
        result = engine.augment_weaudit(weaudit_path, replace_existing=index == 0)
        _print_augment_result("weAudit", weaudit_path, result, file=summary_stream)

    if args.json:
        print(engine.to_json())


def _run_diagram(args: argparse.Namespace) -> None:
    """Execute the diagram subcommand."""
    engine = build_engine(args.target, args.language, allow_empty=args.allow_empty)
    print(render_diagram(engine, args))


def _print_augment_result(
    label: str,
    path: str,
    result: dict[str, Any],
    *,
    file: TextIO | None = None,
) -> None:
    """Print a summary of an augmentation result."""
    print(f"{label}: {path}", file=file)
    print(f"  Matched: {result['matched_findings']}", file=file)
    print(f"  Unmatched: {result['unmatched_findings']}", file=file)
    subgraphs = result.get("subgraphs_created", [])
    if subgraphs:
        print(f"  Subgraphs: {', '.join(str(s) for s in subgraphs)}", file=file)


def _run_entrypoints(args: argparse.Namespace) -> None:
    """Execute the entrypoints subcommand."""
    engine = QueryEngine.from_directory(
        args.path, language=args.language, allow_empty=args.allow_empty
    )
    surface = engine.attack_surface()

    if args.json:
        print(json.dumps(surface, indent=2))
        return

    if not surface:
        print("No entrypoints detected.")
        print("Hint: declare entrypoints manually in .trailmark/entrypoints.toml")
        return

    print(f"{len(surface)} entrypoint(s) detected:")
    for ep in surface:
        print(
            f"  {ep['node_id']}  "
            f"kind={ep['kind']}  "
            f"trust={ep['trust_level']}  "
            f"asset={ep['asset_value']}",
        )
        if ep.get("description"):
            print(f"    {ep['description']}")


def _run_diff(args: argparse.Namespace) -> None:
    """Execute the diff subcommand."""
    from trailmark.analysis.diff import format_diff

    before_engine = _load_diff_side(
        args.before, args.language, args.repo, allow_empty=args.allow_empty
    )
    after_engine = _load_diff_side(
        args.after, args.language, args.repo, allow_empty=args.allow_empty
    )
    diff = after_engine.diff_against(before_engine)

    if args.json:
        print(json.dumps(diff, indent=2))
        return
    print(format_diff(diff))


def _load_diff_side(
    side: str,
    language: str,
    repo: str,
    *,
    allow_empty: bool = False,
) -> QueryEngine:
    """Build a QueryEngine for a path or git ref."""
    from trailmark.analysis.diff import git_worktree

    candidate = Path(side)
    if candidate.exists() or candidate.is_absolute() or side.endswith("/"):
        return QueryEngine.from_directory(
            str(candidate), language=language, allow_empty=allow_empty
        )

    repo_path = Path(repo).resolve()
    with git_worktree(repo_path, side) as worktree:
        return QueryEngine.from_directory(str(worktree), language=language, allow_empty=allow_empty)
