"""Pinned public pilot projects and source worktree preparation."""

from __future__ import annotations

import concurrent.futures
import shutil
import subprocess
from pathlib import Path
from typing import Any

from tests.graph_eval.util import command, digest, source_inventory, write_json

BASELINE = "455f6554aa1b6c138c62a7d2a114ae4aba111af7"
SEED = 20261119
PROJECTS = (
    ("python", "pallets/flask", "49943b5a94413a530761350f04e896f76e8d620e"),
    ("python", "psf/requests", "3c7baaa05ab4aa49cc819ce3a819d260de19c157"),
    ("typescript", "honojs/hono", "75ff8e3ea18485589816881c6278bc9783e56602"),
    ("typescript", "trpc/trpc", "596d03d93295f8b4876909cfce86f94a1e6afe51"),
    ("rust", "tokio-rs/axum", "8d60d69eb195dad2f983926ab11fc91a79b98e06"),
    ("rust", "rustls/rustls", "836e411eccaf7331f4c289aabef0de32474de3b8"),
    ("solidity", "OpenZeppelin/openzeppelin-contracts", "d98b8f438d6c2b9d368d1220448b8519f733d371"),
    ("solidity", "Uniswap/v4-core", "bea7f6d1c11cd5dc4f0d5151ffa37a1c239feee6"),
    ("fstar", "hacl-star/hacl-star", "f557259007db9933442dde10aa995c1b00bd3446"),
    ("fstar", "FStarLang/FStar", "fc1776afe592ed8eef352c6610614a70d77b3b66"),
    ("tlaplus", "tlaplus/Examples", "334c3983a2f44ac78fce89d1d20aa79ceb3e6987"),
    ("tlaplus", "tlaplus/CommunityModules", "cbf7a667423a3768aa3c87fe622faf7db1baa2e7"),
    ("graphql", "octokit/graphql-schema", "82ff2d4780080e6929ebb95608cefa22dfa05ac7"),
    ("graphql", "saleor/saleor", "4974a295ddd33b1a34ebac1b1d6c0730d7edc8ee"),
    ("sql", "pgmq/pgmq", "51468f6ba2afeb4a4a9a67e9a532f37b590a0929"),
    ("sql", "pgpartman/pg_partman", "e0a6c384a6c77846d2560f9251089339c9637814"),
)


def slug(repo: str) -> str:
    return repo.replace("/", "--")


def checkout_project(campaign: Path, project: tuple[str, str, str]) -> dict[str, Any]:
    language, repo, revision = project
    name = slug(repo)
    bare = campaign / "repos" / f"{name}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    log = campaign / "logs" / f"prepare-{name}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    if not bare.exists():
        with log.open("w") as stream:
            subprocess.run(  # noqa: S603
                ["gh", "repo", "clone", repo, str(bare), "--", "--bare", "--depth=1"],  # noqa: S607
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=1200,
            )
    try:
        command(["git", "--git-dir", str(bare), "cat-file", "-e", f"{revision}^{{commit}}"])
    except subprocess.CalledProcessError:
        command(
            ["git", "--git-dir", str(bare), "fetch", "--depth=1", "origin", revision], timeout=1200
        )
    roots = {}
    for role in ("sources", "measurement-sources"):
        root = campaign / role / name
        root.parent.mkdir(parents=True, exist_ok=True)
        if not root.exists():
            command(
                ["git", "--git-dir", str(bare), "worktree", "add", "--detach", str(root), revision],
                timeout=1200,
            )
        if command(["git", "-C", str(root), "rev-parse", "HEAD"]) != revision:
            raise ValueError(f"Worktree revision changed: {root}")
        if command(["git", "-C", str(root), "status", "--porcelain"]):
            raise ValueError(f"Source worktree is dirty: {root}")
        roots[role] = str(root)
    inventory = source_inventory(Path(roots["sources"]))
    measured = source_inventory(Path(roots["measurement-sources"]))
    if inventory != measured:
        raise ValueError(f"Role source snapshots differ for {repo}")
    write_json(campaign / "inventories" / f"{name}.json", inventory)
    return {
        "id": name,
        "repo": repo,
        "revision": revision,
        "language": language,
        "roots": roots,
        "source_hash": digest(inventory),
        "tracked_files": len(inventory),
    }


def checkout_projects(campaign: Path, workers: int = 4) -> list[dict[str, Any]]:
    campaign.mkdir(parents=True, exist_ok=True)
    if not shutil.which("gh"):
        raise RuntimeError("Authenticated gh CLI is required to prepare the public corpus")
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda project: checkout_project(campaign, project), PROJECTS))
    write_json(campaign / "projects.json", results)
    return results
