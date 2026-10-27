"""Trailmark CLI entry point."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, TextIO

from trailmark import __version__
from trailmark.diagram import add_diagram_arguments, build_engine, render_diagram
from trailmark.models.binary import BinaryImportOptions, BinaryLimits, BinarySelection
from trailmark.models.graph import CodeGraph
from trailmark.query.api import QueryEngine
from trailmark.source_policy import SourcePolicy, SourceRoot

_VERSION_STRING = f"trailmark {__version__}"


def _complexity_threshold(value: str) -> int:
    """Parse a nonnegative complexity threshold for the CLI."""
    threshold = int(value)
    if threshold < 0:
        msg = "complexity threshold must be nonnegative"
        raise argparse.ArgumentTypeError(msg)
    return threshold


def _binary_range(value: str) -> tuple[int, int]:
    try:
        left, right = value.split(":")
        selection = BinarySelection(int(left, 0), int(right, 0))
    except (ValueError, TypeError) as error:
        raise argparse.ArgumentTypeError("expected START:END with 0 <= START < END") from error
    return selection.start, selection.end


def _path_map(value: str) -> tuple[str, str]:
    old, separator, new = value.partition("=")
    if not separator or not old or not new:
        raise argparse.ArgumentTypeError("expected a nonempty OLD=NEW path prefix mapping")
    return old, new


def _add_binary_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--backend", choices=("auto", "llvm", "gnu"), default="auto")
    parser.add_argument("--objdump", help="Path to the trusted host GNU or LLVM objdump")
    parser.add_argument("--symbolizer", help="Path to llvm-symbolizer or GNU addr2line")
    parser.add_argument("--debug-file", help="Identity-checked local ELF debug file, dSYM, or PDB")
    parser.add_argument("--mapping-file", help="SHA-256-bound explicit source/range mappings JSON")
    parser.add_argument(
        "--path-map", action="append", type=_path_map, default=[], metavar="OLD=NEW"
    )
    parser.add_argument(
        "--slice", dest="slice_index", type=int, help="Select one universal-image slice index"
    )
    for flag in ("range", "rva-range", "file-range"):
        parser.add_argument(
            f"--{flag}", action="append", type=_binary_range, default=[], metavar="START:END"
        )
    parser.add_argument(
        "--mode", choices=("arm", "thumb"), help="Instruction mode for explicit ARM ranges"
    )
    parser.add_argument("--tool-timeout", type=float, default=30.0)
    parser.add_argument("--import-timeout", type=float, default=300.0)
    parser.add_argument("--max-tool-output-bytes", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--max-scan-instructions", type=int, default=1_000_000)


def _binary_options(args: argparse.Namespace) -> BinaryImportOptions:
    ranges = tuple(
        BinarySelection(start, end, kind, args.mode)
        for key, kind in (("range", "va"), ("rva_range", "rva"), ("file_range", "file_offset"))
        for start, end in getattr(args, key)
    )
    if args.mode is not None and not ranges:
        raise ValueError("--mode requires an explicit --range, --rva-range, or --file-range")
    return BinaryImportOptions(
        backend=args.backend,
        objdump=args.objdump,
        symbolizer=args.symbolizer,
        debug_file=args.debug_file,
        mapping_file=args.mapping_file,
        path_mappings=tuple(args.path_map),
        slice_index=args.slice_index,
        ranges=ranges,
        strict=args.strict if args.command == "binary" else args.binary_strict,
        limits=BinaryLimits(
            args.tool_timeout,
            args.import_timeout,
            args.max_tool_output_bytes,
            args.max_scan_instructions,
        ),
    )


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
    evidence = entrypoints.add_mutually_exclusive_group()
    evidence.add_argument(
        "--evidence", action="store_true", help="Emit all entrypoint evidence as JSON"
    )
    evidence.add_argument(
        "--candidates",
        action="store_true",
        help="Emit candidates outside the attack surface as JSON",
    )
    for command in (analyze, entrypoints):
        command.add_argument(
            "--source-root",
            action="append",
            default=[],
            metavar="NAME=PATH",
            help="Add an explicitly selected source root (repeatable)",
        )
        command.add_argument(
            "--include",
            action="append",
            default=[],
            metavar="GLOB",
            help="Include files despite exclusions, relative to each root (repeatable)",
        )
        command.add_argument(
            "--exclude",
            action="append",
            default=[],
            metavar="GLOB",
            help="Exclude files relative to each root (repeatable)",
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
        "--binary", action="append", default=[], help="Linked native image (repeatable)"
    )
    augment.add_argument(
        "--binary-strict", action="store_true", help="Reject incomplete native evidence"
    )
    _add_binary_options(augment)
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

    binary = subparsers.add_parser(
        "binary", help="Inspect a linked native image and connect source ranges"
    )
    binary.add_argument("artifact", help="Linked ELF, Mach-O, or PE image")
    binary.add_argument("--source-root", help="Optional source directory to connect")
    binary.add_argument(
        "--language", "-l", default="auto", help="Source language selection (default: auto)"
    )
    binary.add_argument(
        "--strict", action="store_true", help="Reject incomplete source or binary evidence"
    )
    binary.add_argument("--allow-empty", action="store_true")
    binary.add_argument(
        "--function", help="Source/binary function whose ranges should be disassembled"
    )
    binary.add_argument(
        "--disassemble", action="store_true", help="Include bounded instruction evidence"
    )
    binary.add_argument(
        "--max-instructions",
        type=int,
        default=256,
        help="Maximum returned instructions across ranges",
    )
    binary.add_argument(
        "--json", action="store_true", help="Emit graph, report, and optional disassembly as JSON"
    )
    _add_binary_options(binary)

    for command in (analyze, diff_cmd, entrypoints, augment):
        command.add_argument(
            "--strict", action="store_true", help="Reject source analysis with parse diagnostics"
        )
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
        elif args.command == "binary":
            _run_binary(args)
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
        strict=args.strict,
        source_policy=_source_policy(args),
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
    print(f"Parse diagnostics: {len(summary['parse_diagnostics'])}")


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
    if args.debug_file and len(args.binary) != 1:
        raise ValueError("--debug-file requires exactly one --binary image")
    options = _binary_options(args)
    engine = QueryEngine.from_directory(
        args.path,
        language=args.language,
        allow_empty=args.allow_empty,
        strict=args.strict,
    )
    summary_stream = sys.stderr if args.json else sys.stdout

    for index, sarif_path in enumerate(args.sarif):
        result = engine.augment_sarif(sarif_path, replace_existing=index == 0)
        _print_augment_result("SARIF", sarif_path, result, file=summary_stream)

    for index, weaudit_path in enumerate(args.weaudit):
        result = engine.augment_weaudit(weaudit_path, replace_existing=index == 0)
        _print_augment_result("weAudit", weaudit_path, result, file=summary_stream)

    complete = True
    for binary_path in args.binary:
        result = engine.augment_binary_file(binary_path, options=options)
        complete = complete and bool(result["complete"])
        print(
            f"Binary {binary_path}: {result['binary_nodes']} nodes, "
            f"{result['correspondences']} correspondences, complete={result['complete']}",
            file=summary_stream,
        )

    if args.json:
        print(engine.to_json())
    if not complete:
        sys.exit(3)


def _run_binary(args: argparse.Namespace) -> None:
    options = _binary_options(args)
    if args.max_instructions <= 0:
        raise ValueError("--max-instructions must be positive")
    engine = (
        QueryEngine.from_directory(
            args.source_root,
            language=args.language,
            strict=args.strict,
            allow_empty=args.allow_empty,
        )
        if args.source_root
        else QueryEngine.from_graph(CodeGraph())
    )
    report = engine.augment_binary_file(args.artifact, options=options)
    output: dict[str, object] = {"graph": json.loads(engine.to_json()), "report": report}
    complete = report["complete"]
    if args.disassemble:
        if args.function:
            ranges = engine.binary_ranges(args.function)
            if not ranges:
                raise ValueError(f"No compiled ranges match function: {args.function}")
        else:
            ranges = [asdict(row) for row in engine._store._graph.binary_ranges.values()]  # noqa: SLF001
        remaining = args.max_instructions
        disassembly = []
        for index, row in enumerate(ranges):
            if remaining <= 0:
                break
            result = engine.disassemble_binary(
                row["artifact_id"], row["start"], row["end"], max_instructions=remaining
            )
            disassembly.append(result)
            remaining -= len(result["instructions"])
            complete = complete and result["complete"]
            if remaining <= 0 and index + 1 < len(ranges):
                result["truncated"] = True
                result["complete"] = False
                result["diagnostics"].append(
                    {"code": "instruction_limit", "message": "Further ranges omitted"}
                )
                complete = False
        output["disassembly"] = disassembly
    if args.json:
        print(json.dumps(output))
    else:
        print(
            f"Binary {args.artifact}: {report['binary_nodes']} nodes, {report['ranges']} ranges, "
            f"{report['correspondences']} correspondences, complete={report['complete']}"
        )
        for diagnostic in report["diagnostics"]:
            print(f"  {diagnostic['code']}: {diagnostic['message']}")
        if args.disassemble:
            print(json.dumps(output["disassembly"], indent=2))
    if not complete:
        sys.exit(3)


def _run_diagram(args: argparse.Namespace) -> None:
    """Execute the diagram subcommand."""
    engine = build_engine(
        args.target, args.language, allow_empty=args.allow_empty, strict=args.strict
    )
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
        args.path,
        language=args.language,
        allow_empty=args.allow_empty,
        strict=args.strict,
        source_policy=_source_policy(args),
    )
    if args.evidence or args.candidates:
        facts = engine.entrypoint_candidates() if args.candidates else engine.entrypoint_evidence()
        print(json.dumps(facts, indent=2))
        return
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


def _source_policy(args: argparse.Namespace) -> SourcePolicy | None:
    if not (args.source_root or args.include or args.exclude):
        return None
    roots = [SourceRoot(args.path, "root")]
    for specification in args.source_root:
        name, separator, path = specification.partition("=")
        if not separator or not path:
            raise ValueError("--source-root requires NAME=PATH")
        roots.append(SourceRoot(path, name))
    return SourcePolicy(tuple(roots), exclude=tuple(args.exclude), include=tuple(args.include))


def _run_diff(args: argparse.Namespace) -> None:
    """Execute the diff subcommand."""
    from trailmark.analysis.diff import format_diff

    before_engine = _load_diff_side(
        args.before, args.language, args.repo, allow_empty=args.allow_empty, strict=args.strict
    )
    after_engine = _load_diff_side(
        args.after, args.language, args.repo, allow_empty=args.allow_empty, strict=args.strict
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
    strict: bool = False,
) -> QueryEngine:
    """Build a QueryEngine for a path or git ref."""
    from trailmark.analysis.diff import git_worktree

    candidate = Path(side)
    if candidate.exists() or candidate.is_absolute() or side.endswith("/"):
        return QueryEngine.from_directory(
            str(candidate), language=language, allow_empty=allow_empty, strict=strict
        )

    repo_path = Path(repo).resolve()
    with git_worktree(repo_path, side) as worktree:
        return QueryEngine.from_directory(
            str(worktree), language=language, allow_empty=allow_empty, strict=strict
        )
