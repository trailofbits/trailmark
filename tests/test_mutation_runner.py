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
from pytest_gremlins.parallel.exit_codes import COLLECTION_KILLING_TEST

from tests.mutation_bootstrap import record_source_collection_exits
from tests.mutation_gate import check
from tests.run_mutations import RUNNER, VERSION, parse_shard, select_shard

pytestmark = pytest.mark.mutation_infrastructure


def _run_collection_control(
    tmp_path: Path, source: str, conftest: str = ""
) -> subprocess.CompletedProcess[str]:
    (tmp_path / "target.py").write_text(source)
    (tmp_path / "test_target.py").write_text(
        "from target import checked\ndef test_checked():\n    assert checked() is True\n"
    )
    if conftest:
        (tmp_path / "conftest.py").write_text(conftest)
    return subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(Path(__file__).with_name("run_mutations.py")),
            "--targets=target.py",
            "--workers=2",
            "test_target.py",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )


def test_application_collection_exit_is_confirmed_as_a_kill(tmp_path: Path) -> None:
    result = _run_collection_control(
        tmp_path,
        "import sys\nif __name__ == '__main__':\n    sys.exit(2)\n"
        "def checked():\n    return True\n",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "coverage/gremlins/gremlins.json").read_text())
    assert report["results"]
    assert {row["status"] for row in report["results"]} == {"zapped"}
    assert any(row.get("killing_test") == COLLECTION_KILLING_TEST for row in report["results"])


@pytest.mark.parametrize(
    ("hook", "exception"),
    [
        ("pytest_collection_modifyitems", "SystemExit(2)"),
        ("pytest_collection_modifyitems", "RuntimeError('controlled harness failure')"),
        ("pytest_sessionfinish", "SystemExit(2)"),
    ],
)
def test_harness_failures_remain_errors(tmp_path: Path, hook: str, exception: str) -> None:
    result = _run_collection_control(
        tmp_path,
        "def checked():\n    return True\n",
        "import os\n"
        f"def {hook}():\n"
        "    if os.environ.get('ACTIVE_GREMLIN'):\n"
        f"        raise {exception}\n",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "coverage/gremlins/gremlins.json").read_text())
    assert report["results"]
    assert {row["status"] for row in report["results"]} == {"error"}


def test_unmutated_collection_exit_does_not_produce_scores(tmp_path: Path) -> None:
    result = _run_collection_control(
        tmp_path,
        "import os, sys\n"
        "if os.environ.get('PYTEST_GREMLINS_SOURCES_FILE'):\n    sys.exit(2)\n"
        "def checked():\n    return True\n",
    )
    assert result.returncode != 0
    assert "Instrumented baseline failed" in result.stdout + result.stderr
    assert not (tmp_path / "coverage/gremlins/run.json").exists()
    assert not (tmp_path / "coverage/gremlins/progress.jsonl").exists()


def test_bootstrap_adapter_rejects_an_unrecognized_layout(tmp_path: Path) -> None:
    bootstrap = tmp_path / "gremlin_bootstrap.py"
    bootstrap.write_text("print('unrecognized bootstrap')\n")
    with pytest.raises(pytest.UsageError, match="Revalidate"):
        record_source_collection_exits(tmp_path)
    assert bootstrap.read_text() == "print('unrecognized bootstrap')\n"


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
import coverage
import os
import pytest
from toy import checked, ignored, RESOURCE, VALUE

@pytest.mark.parametrize(("value", "answer"), [(0, False), (1, True)], ids=["ZERO", "ONE"])
def test_strong(tmp_path, value, answer):
    collecting = bool(os.environ.get("TRAILMARK_MUTATION_COVERAGE"))
    assert (coverage.Coverage.current() is not None) == collecting
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
    progress = [
        json.loads(line)
        for line in (tmp_path / "coverage/gremlins/progress.jsonl").read_text().splitlines()
    ]
    assert len(progress) == len(results)
    assert {row["gremlin_id"]: row["status"] for row in progress} == {
        row["gremlin_id"]: row["status"] for row in results
    }
    assert all(0 < row["selected_test_count"] < 5 for row in progress)
    assert all(Path(row["file_path"]).name == "__init__.py" for row in progress)
    strong = [r for r in results if r["line_number"] == 5]
    weak = [r for r in results if r["line_number"] == 7]
    delegated = [r for r in results if r["line_number"] == 9]
    assert len(strong) >= 4 and len(weak) == len(strong)
    assert len(delegated) == len(strong)
    assert {r["status"] for r in strong} == {"zapped"}
    assert {r["status"] for r in delegated} == {"zapped"}
    assert {r["status"] for r in weak} == {"survived"}
    assert all(
        r["selected_tests"] == ["test_toy.py::test_strong[ZERO]", "test_toy.py::test_strong[ONE]"]
        for r in strong
    )
    assert all(r["selected_tests"] == ["test_integration.py::test_integration"] for r in delegated)
    assert all(
        r["selected_tests"] == ["test_toy.py::test_weak[0]", "test_toy.py::test_weak[1]"]
        for r in weak
    )


@pytest.mark.parametrize("partition", ["--shard=1/1", "--shard=99/99", "--baseline-shard=1/1"])
def test_runner_rejects_order_dependent_baseline(tmp_path: Path, partition: str) -> None:
    (tmp_path / "target.py").write_text("def checked(value):\n    return value > 0\n")
    (tmp_path / "state.py").write_text("ready = False\n")
    (tmp_path / "test_a_setup.py").write_text(
        "import state\ndef test_initialize():\n    state.ready = True\n"
    )
    (tmp_path / "test_target.py").write_text(
        "import state\nfrom target import checked\n"
        "def test_checked():\n    assert state.ready\n    assert checked(1)\n"
    )
    progress = tmp_path / "coverage/gremlins/progress.jsonl"
    progress.parent.mkdir(parents=True)
    progress.write_text("stale previous campaign\n")
    runner = Path(__file__).with_name("run_mutations.py")
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(runner),
            "--targets=target.py",
            "--test-selection=full",
            partition,
            ".",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "Instrumented baseline failed" in result.stdout + result.stderr
    assert not (tmp_path / "coverage/gremlins/run.json").exists()
    progress = tmp_path / "coverage/gremlins/progress.jsonl"
    assert not progress.exists() or not progress.read_text()
    assert not list((tmp_path / "coverage/gremlins").glob("baseline-*.json"))


@pytest.mark.parametrize("selection", ["full", "coverage"])
def test_shared_baselines_preserve_mutant_results_and_reject_changed_tests(
    tmp_path: Path, selection: str
) -> None:
    for name in ("first", "second"):
        (tmp_path / f"{name}.py").write_text("def checked(value):\n    return value > 0\n")
        (tmp_path / f"test_{name}.py").write_text(
            f"from {name} import checked\n"
            "def test_boundary():\n    assert not checked(0)\n    assert checked(1)\n"
        )
    runner = Path(__file__).with_name("run_mutations.py")
    reports = tmp_path / "coverage/gremlins"
    shared = reports / "baselines"
    shared.mkdir(parents=True)
    executions = tmp_path.with_suffix(".executions.jsonl")
    (tmp_path / "conftest.py").write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        "def pytest_runtest_call(item):\n"
        f"    with Path({str(executions)!r}).open('a') as stream:\n"
        "        stream.write(json.dumps({'argv': sys.argv, 'test': item.nodeid, "
        "'instrumented': bool(os.environ.get('PYTEST_GREMLINS_SOURCES_FILE')), "
        "'prefix': bool(os.environ.get('TRAILMARK_PREFIX_LIMIT')), "
        "'mutant': bool(os.environ.get('ACTIVE_GREMLIN'))}) + '\\n')\n"
    )

    def run(*options: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            [
                sys.executable,
                str(runner),
                "--targets=first.py,second.py",
                f"--test-selection={selection}",
                "--workers=2",
                *options,
                "test_first.py",
                "test_second.py",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )

    checked = []
    partitions = 1 if selection == "coverage" else 2
    for index in range(1, partitions + 1):
        result = run(f"--baseline-shard={index}/{partitions}")
        assert result.returncode == 0, result.stdout + result.stderr
        receipt = reports / f"baseline-{index}-of-{partitions}.json"
        checked.extend(json.loads(receipt.read_text())["checked"])
        receipt.rename(shared / receipt.name)
        assert not (reports / "run.json").exists()
        assert not (reports / "progress.jsonl").exists()
    assert len(checked) == len(set(checked)) == partitions
    calls = [json.loads(line) for line in executions.read_text().splitlines()]
    assert len(calls) == 2 * partitions
    assert all(call["instrumented"] for call in calls)
    assert not any(call["mutant"] for call in calls)
    if selection == "coverage":
        (reports / "coverage-plan.json").rename(shared / "coverage-plan.json")

    combined = {}
    for index in (1, 2):
        executions.unlink()
        result = run(f"--shard={index}/2", f"--baselines={shared}")
        assert result.returncode == 0, result.stdout + result.stderr
        assert f"Reusing validated instrumented baseline orders: {partitions}" in result.stdout
        assert "Checking instrumented baseline order" not in result.stdout
        calls = [json.loads(line) for line in executions.read_text().splitlines()]
        assert all(call["instrumented"] for call in calls)
        assert not any(Path(call["argv"][0]) == runner for call in calls)
        assert all(call["mutant"] or call["prefix"] for call in calls)
        assert "Checking " not in result.stdout
        report = json.loads((reports / "gremlins.json").read_text())
        combined.update({row["gremlin_id"]: row["status"] for row in report["results"]})
        assert json.loads((reports / "run.json").read_text())["baseline_validation"] == "shared"
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((reports / "gremlins.json").read_text())
    assert combined == {row["gremlin_id"]: row["status"] for row in report["results"]}
    assert set(combined.values()) == {"zapped"}

    with (tmp_path / "test_second.py").open("a") as test:
        test.write("\n# Edited after the baselines were validated.\n")
    result = run("--shard=1/2", f"--baselines={shared}")
    assert result.returncode != 0
    message = (
        "Invalid shared coverage selection"
        if selection == "coverage"
        else "Invalid shared mutation baselines"
    )
    assert message in result.stdout + result.stderr
    assert not (reports / "run.json").exists()
    assert not (reports / "progress.jsonl").exists()


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
        child.join()
        assert child.exitcode == 0
        answer = queue.get()
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
@pytest.mark.parametrize(
    ("instrumentation_step", "error_type"),
    [("transform_source", "ValueError"), ("_instrumented_source_entry", "RecursionError")],
    ids=["transform", "write"],
)
def test_runner_rejects_skipped_instrumentation(
    tmp_path: Path, shard: str, instrumentation_step: str, error_type: str
) -> None:
    (tmp_path / "target.py").write_text("def checked(value):\n    return value > 0\n")
    (tmp_path / "test_target.py").write_text(
        "from target import checked\ndef test_checked():\n    assert checked(1)\n"
    )
    runner = Path(__file__).with_name("run_mutations.py")
    launcher = tmp_path / "launch.py"
    launcher.write_text(
        "import runpy\nfrom unittest.mock import patch\n"
        f"with patch('pytest_gremlins.plugin.{instrumentation_step}', "
        f"side_effect={error_type}('controlled instrumentation failure')):\n"
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
    )
    assert result.returncode != 0
    assert "Mutation instrumentation skipped files" in result.stdout + result.stderr
    assert "controlled instrumentation failure" in result.stdout + result.stderr
    assert not (tmp_path / "coverage/gremlins/run.json").exists()
