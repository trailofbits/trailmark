"""Recognize application exits during collection without hiding harness errors."""

import os
from pathlib import Path

import pytest
from pytest_gremlins import plugin
from pytest_gremlins.instrumentation.spawn_hook import export_spawn_hook


def record_source_collection_exits(directory: Path) -> None:
    """Extend the pinned bootstrap's load recorder, retaining upstream confirmation."""
    path = directory / "gremlin_bootstrap.py"
    source = path.read_text()
    replacements = {
        "            self.suite_failed_to_load = False\n": (
            "            self.suite_failed_to_load = False\n"
            "            self.source_collection_exit = False\n"
        ),
        "        def pytest_collection(self, session):\n            outcome = yield\n": (
            "        def pytest_collection(self, session):\n"
            "            outcome = yield\n"
            "            if outcome.excinfo is not None and issubclass(\n"
            "                outcome.excinfo[0], SystemExit\n"
            "            ):\n"
            "                self.source_collection_exit = any(\n"
            "                    normalize_origin(frame.f_code.co_filename) "
            "in instrumented_sources\n"
            "                    for frame, _ in traceback.walk_tb(outcome.excinfo[2])\n"
            "                )\n"
            "                if self.source_collection_exit:\n"
            "                    self.suite_failed_to_load = True\n"
        ),
        "    load_failure_exit = exit_code in (pytest.ExitCode.USAGE_ERROR, "
        "pytest.ExitCode.INTERRUPTED)\n": (
            "    load_failure_exit = (\n"
            "        exit_code in (pytest.ExitCode.USAGE_ERROR, pytest.ExitCode.INTERRUPTED)\n"
            "        or (exit_code == pytest.ExitCode.INTERNAL_ERROR "
            "and recorder.source_collection_exit)\n"
            "    )\n"
        ),
    }
    for original, replacement in replacements.items():
        if source.count(original) != 1:
            raise pytest.UsageError("Revalidate the pinned mutation bootstrap adapter")
        source = source.replace(original, replacement)
    path.write_text(source)


def test_command(directory: Path, session: plugin.GremlinSession) -> list[str]:
    return plugin._build_test_command(
        directory,
        session.preserved_addopts,
        xdist_loaded=session.xdist_loaded,
        xdist_loader_options=session.xdist_loader_options,
        doctest_ini_overrides=session.doctest_ini_overrides,
        config_file_options=session.config_file_options,
        rerun_options=session.rerun_options,
        pytest_cov_options=session.pytest_cov_options,
    )


def baseline_environment(directory: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("ACTIVE_GREMLIN", None)
    env["PYTEST_GREMLINS_SOURCES_FILE"] = str(directory / "sources.json")
    env["GREMLIN_ROOTDIR"] = str(Path.cwd())
    export_spawn_hook(env)
    return env


def record_test_execution(directory: Path) -> None:
    """Record executed test prefixes and stop neutral checks after their target."""
    path = directory / "gremlin_bootstrap.py"
    source = path.read_text()
    original = (
        "    recorder = SuiteLoadRecorder()\n"
        "    exit_code = pytest.main(pytest_args, plugins=[FinderFronter(), recorder])\n"
    )
    replacement = """    class ExecutionRecorder:
        def __init__(self):
            self.started = []
            self.failed = False
            self.failures = []
            self.stopped = False
            self.limit = int(os.environ.get('TRAILMARK_PREFIX_LIMIT', '0'))

        def pytest_runtest_logstart(self, nodeid):
            self.started.append(nodeid)

        def pytest_runtest_logreport(self, report):
            if report.failed:
                self.failed = True
                self.failures.append({'test': report.nodeid, 'phase': report.when})

        @pytest.hookimpl(wrapper=True, tryfirst=True)
        def pytest_runtest_protocol(self, item):
            outcome = yield
            if self.limit and len(self.started) == self.limit:
                self.stopped = True
                pytest.exit('Unmutated failure prefix completed', returncode=0)
            return outcome

    recorder = SuiteLoadRecorder()
    execution = ExecutionRecorder()
    exit_code = pytest.main(pytest_args, plugins=[FinderFronter(), recorder, execution])
    active = os.environ.get('ACTIVE_GREMLIN')
    destination = os.environ.get('TRAILMARK_PREFIX_REPORT')
    if active:
        import hashlib
        destination = os.path.join(os.path.dirname(sources_file),
            'execution-' + hashlib.sha256(active.encode()).hexdigest() + '.json')
    if destination:
        with open(destination + '.tmp', 'w') as output:
            json.dump({'tests': execution.started, 'failed': execution.failed,
                'failures': execution.failures,
                'stopped': execution.stopped, 'exit_code': int(exit_code)}, output)
        os.replace(destination + '.tmp', destination)
"""
    if source.count(original) != 1:
        raise pytest.UsageError("Revalidate the pinned failure-prefix bootstrap adapter")
    path.write_text(source.replace(original, replacement))
