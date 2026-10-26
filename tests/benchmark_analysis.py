"""Measure full directory analysis and fingerprint its complete graph output.

Run on the same unchanged corpus in both checkouts, without concurrent tests:
``uv run python -m tests.benchmark_analysis /path/to/corpus --language python``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import time

from trailmark.query import QueryEngine


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    parser.add_argument("--language", default="auto")
    parser.add_argument("--repeat", type=int, default=7)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")

    elapsed = []
    digests = set()
    # Warm up grammar loading and filesystem caches before timing.
    QueryEngine.from_directory(args.directory, language=args.language)
    for _ in range(args.repeat):
        start = time.perf_counter()
        engine = QueryEngine.from_directory(args.directory, language=args.language)
        elapsed.append(time.perf_counter() - start)
        graph = json.loads(engine.to_json())
        serialized = json.dumps(graph, sort_keys=True, separators=(",", ":"))
        digests.add(hashlib.sha256(serialized.encode()).hexdigest())
        del engine
    if len(digests) != 1:
        raise RuntimeError("Graph output changed between benchmark repetitions")
    print(
        json.dumps(
            {
                "directory": args.directory,
                "language": args.language,
                "python": platform.python_version(),
                "platform": platform.platform(),
                "seconds": elapsed,
                "median_seconds": statistics.median(elapsed),
                "graph_sha256": digests.pop(),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
