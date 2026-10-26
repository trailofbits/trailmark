"""Controls against false kills and false survivors in the mutation harness."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pytest_gremlins.instrumentation.transformer import transform_source

from tests.mutation_gate import check
from tests.run_mutations import RUNNER, VERSION, parse_shard, select_shard

pytestmark = pytest.mark.mutation_infrastructure


@pytest.mark.parametrize("value", ["0/4", "5/4", "1/0", "-1/4", "1", "1/2/3", "one/4"])
def test_rejects_invalid_shard(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="INDEX/TOTAL"):
        parse_shard(value)


def test_shards_are_disjoint_complete_and_independent_of_file_discovery_order() -> None:
    source = "def checked(value):\n    return value > 0\n"
    first, _ = transform_source(source, "first.py")
    second, _ = transform_source(source, "second.py")
    inventory = first + second
    partitions = [select_shard(inventory, (index, 3)) for index in range(1, 4)]
    ids = [g.gremlin_id for partition in partitions for g in partition]
    assert len(ids) == len(set(ids)) == len(inventory)
    assert set(ids) == {g.gremlin_id for g in inventory}
    for index, partition in enumerate(partitions, 1):
        assert partition == select_shard(second + first, (index, 3))
    assert max(map(len, partitions)) - min(map(len, partitions)) <= 2


def test_sharded_campaigns_match_complete_campaign(tmp_path: Path) -> None:
    source = "def checked(value):\n    return value > 0\n"
    for name in ("first", "second"):
        (tmp_path / f"{name}.py").write_text(source)
    (tmp_path / "test_targets.py").write_text(
        "from first import checked as first\nfrom second import checked as second\n"
        "def test_boundaries():\n"
        "    for checked in (first, second):\n"
        "        assert checked(0) is False\n        assert checked(1) is True\n"
    )
    baseline = {
        "pytest_gremlins": VERSION,
        "scope": {"first.py": "*", "second.py": "*"},
        "exceptions": [],
    }
    runner = Path(__file__).with_name("run_mutations.py")
    complete = set()
    partitioned = []
    for shard in ((1, 1), (1, 3), (2, 3), (3, 3), (99, 99)):
        result = subprocess.run(  # noqa: S603
            [
                sys.executable,
                str(runner),
                "--targets=first.py,second.py",
                "--workers=2",
                f"--shard={shard[0]}/{shard[1]}",
                "test_targets.py",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        report = json.loads((tmp_path / "coverage/gremlins/gremlins.json").read_text())
        run = json.loads((tmp_path / "coverage/gremlins/run.json").read_text())
        assert run["shard"] == list(shard)
        assert check(tmp_path, report, run, baseline, shard=shard) == []
        assert all(row["status"] == "zapped" for row in report["results"])
        assert all(
            row["selected_tests"] == ["test_targets.py::test_boundaries"]
            for row in report["results"]
        )
        ids = {row["gremlin_id"] for row in report["results"]}
        if shard == (1, 1):
            complete = ids
        elif shard == (99, 99):
            assert not ids
        else:
            partitioned.extend(ids)
    assert complete
    assert len(partitioned) == len(set(partitioned)) == len(complete)
    assert set(partitioned) == complete


def test_runner_executes_fixtures_parameters_and_package_mutations(tmp_path: Path) -> None:
    package = tmp_path / "toy"
    package.mkdir()
    (package / "value.txt").write_text("known resource")
    (package / "helper.py").write_text("VALUE = 7\n")
    (package / "__init__.py").write_text(
        "from pathlib import Path\nfrom .helper import VALUE\n"
        'RESOURCE = Path(__file__).with_name("value.txt").read_text()\n'
        "def checked(value):\n    return value > 0\n"
        "def ignored(value):\n    return value > 0\n"
        "def delegated(value):\n    return value > 0\n"
    )
    (tmp_path / "test_toy.py").write_text("""\
import pytest
from toy import checked, ignored, RESOURCE, VALUE

@pytest.mark.parametrize(("value", "answer"), [(0, False), (1, True)], ids=["ZERO", "ONE"])
def test_strong(tmp_path, value, answer):
    assert tmp_path.is_dir()
    assert RESOURCE == "known resource" and VALUE == 7
    assert checked(value) is answer

@pytest.mark.parametrize("value", [0, 1])
def test_weak(tmp_path, value):
    assert tmp_path.is_dir()
    ignored(value)
""")
    (tmp_path / "test_integration.py").write_text(
        "from toy import delegated\n"
        "def test_integration(tmp_path):\n"
        "    assert tmp_path.is_dir()\n"
        "    assert delegated(0) is False\n"
        "    assert delegated(1) is True\n"
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"ACTIVE_GREMLIN", "PYTEST_GREMLINS_SOURCES_FILE", "PYTEST_ADDOPTS"}
    }
    runner = Path(__file__).with_name("run_mutations.py")
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(runner),
            "--targets=toy/__init__.py",
            "--workers=2",
            "test_toy.py",
            "test_integration.py",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    run = json.loads((tmp_path / "coverage/gremlins/run.json").read_text())
    assert run["pytest_gremlins"] == VERSION
    assert run["runner"] == RUNNER
    assert run["test_order"] == "component"
    assert run["tests"] == ["test_toy.py", "test_integration.py"]
    assert set(run["sources"]) == {"toy/__init__.py"}
    report = json.loads((tmp_path / "coverage/gremlins/gremlins.json").read_text())
    results = report["results"]
    strong = [r for r in results if r["line_number"] == 5]
    weak = [r for r in results if r["line_number"] == 7]
    delegated = [r for r in results if r["line_number"] == 9]
    assert len(strong) >= 4 and len(weak) == len(strong)
    assert len(delegated) == len(strong)
    assert {r["status"] for r in strong} == {"zapped"}
    assert {r["status"] for r in delegated} == {"zapped"}
    assert {r["status"] for r in weak} == {"survived"}
    assert all(len(r["selected_tests"]) == 5 for r in results)
    assert all(
        "test_toy.py::test_strong[ZERO]" in r["selected_tests"]
        and "test_toy.py::test_strong[ONE]" in r["selected_tests"]
        and "test_integration.py::test_integration" in r["selected_tests"]
        for r in results
    )


@pytest.mark.parametrize("shard", ["1/1", "99/99"])
def test_runner_rejects_order_dependent_baseline(tmp_path: Path, shard: str) -> None:
    (tmp_path / "target.py").write_text("def checked(value):\n    return value > 0\n")
    (tmp_path / "state.py").write_text("ready = False\n")
    (tmp_path / "test_a_setup.py").write_text(
        "import state\ndef test_initialize():\n    state.ready = True\n"
    )
    (tmp_path / "test_target.py").write_text(
        "import state\nfrom target import checked\n"
        "def test_checked():\n    assert state.ready\n    assert checked(1)\n"
    )
    runner = Path(__file__).with_name("run_mutations.py")
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(runner), "--targets=target.py", f"--shard={shard}", "."],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "Instrumented baseline failed" in result.stdout + result.stderr
    assert not (tmp_path / "coverage/gremlins/run.json").exists()


def test_runner_instruments_spawned_children(tmp_path: Path) -> None:
    (tmp_path / "target.py").write_text("def checked():\n    return True\n")
    (tmp_path / "child_worker.py").write_text(
        "def evaluate(queue):\n    from target import checked\n    queue.put(checked())\n"
    )
    (tmp_path / "test_spawn.py").write_text("""\
from multiprocessing import get_context
from child_worker import evaluate

def test_checked_only_in_child(tmp_path):
    assert tmp_path.is_dir()
    context = get_context("spawn")
    queue = context.Queue()
    child = context.Process(target=evaluate, args=(queue,))
    child.start()
    try:
        answer = queue.get(timeout=10)
        child.join(timeout=10)
        assert child.exitcode == 0
        assert answer is True
    finally:
        if child.is_alive():
            child.terminate()
            child.join()
        queue.close()
""")
    runner = Path(__file__).with_name("run_mutations.py")
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(runner), "--targets=target.py", "--workers=2", "test_spawn.py"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "coverage/gremlins/gremlins.json").read_text())
    assert len(report["results"]) == 2
    assert {row["operator"] for row in report["results"]} == {"boolean", "return"}
    assert {row["status"] for row in report["results"]} == {"zapped"}
    assert all(
        row["selected_tests"] == ["test_spawn.py::test_checked_only_in_child"]
        for row in report["results"]
    )


@pytest.mark.parametrize("shard", ["1/1", "99/99"])
def test_runner_rejects_skipped_instrumentation(tmp_path: Path, shard: str) -> None:
    (tmp_path / "target.py").write_text("def checked(value):\n    return value > 0\n")
    (tmp_path / "test_target.py").write_text(
        "from target import checked\ndef test_checked():\n    assert checked(1)\n"
    )
    runner = Path(__file__).with_name("run_mutations.py")
    launcher = tmp_path / "launch.py"
    launcher.write_text(
        "import runpy\nfrom unittest.mock import patch\n"
        "with patch('pytest_gremlins.plugin.transform_source', "
        "side_effect=ValueError('controlled instrumentation failure')):\n"
        f"    runpy.run_path({str(runner)!r}, run_name='__main__')\n"
    )
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(launcher),
            "--targets=target.py",
            f"--shard={shard}",
            "test_target.py",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode != 0
    assert "Mutation instrumentation skipped files" in result.stdout + result.stderr
    assert "controlled instrumentation failure" in result.stdout + result.stderr
    assert not (tmp_path / "coverage/gremlins/run.json").exists()
