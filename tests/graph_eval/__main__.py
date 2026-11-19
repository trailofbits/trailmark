"""Opt-in graph evaluation; normal pytest never runs live agents."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tests.graph_eval import campaign
from tests.graph_eval.worker import DEFAULT_CONTEXT, DEFAULT_IMAGE, build_image, preflight


def _shard(value: str) -> tuple[int, int]:
    try:
        index, total = map(int, value.split("/"))
        if 1 <= index <= total:
            return index, total
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("Use one-based INDEX/TOTAL")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Pin source scopes, runtime and isolated worker")
    prepare.add_argument("campaign", type=Path)
    prepare.add_argument("--tool-python", required=True, type=Path)
    prepare.add_argument("--validator-python", type=Path)
    prepare.add_argument("--image", default=DEFAULT_IMAGE)
    prepare.add_argument("--context", default=DEFAULT_CONTEXT)
    prepare.add_argument("--workers", type=int, default=4)
    prepare.add_argument("--build-image", action="store_true")
    prepare.add_argument("--live-preflight", action="store_true")
    run = commands.add_parser("run", help="Run or resume unchanged frozen jobs")
    run.add_argument("campaign", type=Path)
    run.add_argument("--phase", choices=("all", "readers", "measure", "judge"), default="all")
    run.add_argument("--workers", type=int)
    run.add_argument("--language", action="append", dest="languages")
    run.add_argument("--case", action="append", dest="case_ids")
    run.add_argument("--shard", type=_shard, default=(1, 1))
    report = commands.add_parser("report", help="Summarize all pilot and unevaluated frontends")
    report.add_argument("campaign", type=Path)
    replay = commands.add_parser("replay", help="Repeat frozen measurement and comparisons")
    replay.add_argument("campaign", type=Path)
    replay.add_argument("--case", required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            image = args.image
            if args.build_image:
                image = build_image(args.campaign / "worker-build", context=args.context)["image"]
            probe_dir = args.campaign / "preflight" / campaign.now().replace(":", "-")
            probe = preflight(
                probe_dir,
                context=args.context,
                image=image,
                live=args.live_preflight,
            )
            if probe["status"] != "ok":
                raise ValueError(f"Worker preflight failed; inspect {probe_dir}")
            result = campaign.prepare(
                args.campaign,
                tool_python=args.tool_python,
                validator_python=args.validator_python,
                image=probe["image"],
                workers=args.workers,
                context=args.context,
            )
            print(
                json.dumps(
                    {"manifest_sha256": result["manifest_sha256"], "cases": len(result["cases"])}
                )
            )
        elif args.command == "run":
            campaign.run(
                args.campaign,
                phase=args.phase,
                workers=args.workers,
                languages=args.languages,
                case_ids=args.case_ids,
                shard=args.shard,
            )
        elif args.command == "report":
            print(campaign.report(args.campaign))
        elif args.command == "replay":
            print(json.dumps(campaign.replay(args.campaign, args.case), indent=2))
    except (ValueError, OSError) as error:
        parser.exit(2, f"graph-eval: {error}\n")


if __name__ == "__main__":
    main()
