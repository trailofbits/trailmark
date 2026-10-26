"""HTTP claims require lexical evidence for the decorator receiver."""

from pathlib import Path

import pytest

from trailmark.analysis._python_http import python_http_routes
from trailmark.query.api import QueryEngine


@pytest.mark.parametrize(
    "source",
    [
        "from unittest import mock\n@mock.patch('module.send')\ndef handler(): pass\n",
        "import unittest.mock as replace\n@replace.patch('module.send')\ndef handler(): pass\n",
        (
            "from unittest.mock import patch as get\n@get.o"
            "bject(object, 'value')\ndef handler(): pass\n"
        ),
        (
            "class Cache:\n def get(self, value): pass\ncach"
            "e = Cache()\n@cache.get('x')\ndef handler(): pa"
            "ss\n"
        ),
        (
            "class Custom:\n def patch(self, value): pass\no"
            "bj = Custom()\n@obj.patch('x')\ndef handler(): "
            "pass\n"
        ),
        (
            "from flask import Flask\napp = Flask(__name__)"
            "\napp = object()\n@app.get('/')\ndef handler(): "
            "pass\n"
        ),
        (
            "from flask import Flask\napp = Flask(__name__)"
            "\ndef factory(app):\n @app.get('/')\n def handle"
            "r(): pass\n"
        ),
    ],
)
def test_unrelated_receivers_are_not_http(tmp_path: Path, source: str) -> None:
    (tmp_path / "example.py").write_text(source)
    engine = QueryEngine.from_directory(str(tmp_path))
    assert engine.attack_surface() == []
    assert "handler" in engine.to_json()


@pytest.mark.parametrize(
    ("source", "framework", "path"),
    [
        (
            (
                "from flask import Flask as App\napp = App(__na"
                "me__)\n@app.patch('/p')\ndef handler(): pass\n"
            ),
            "flask",
            "/p",
        ),
        (
            (
                "import fastapi as f\nrouter = f.APIRouter(pref"
                "ix='/api')\nalias = router\n@alias.get('/p')\nde"
                "f handler(): pass\n"
            ),
            "fastapi",
            "/api/p",
        ),
        (
            (
                "from aiohttp import web as w\nroutes = w.Route"
                "TableDef()\n@routes.get('/p')\nasync def handle"
                "r(request): pass\n"
            ),
            "aiohttp",
            "/p",
        ),
        (
            (
                "from flask import Flask\ndef create_app():\n ap"
                "p = Flask(__name__)\n @app.get('/p')\n def hand"
                "ler(): pass\n return app\n"
            ),
            "flask",
            "/p",
        ),
        (
            (
                "from fastapi import FastAPI\ndef create_app():"
                "\n return FastAPI()\napp = create_app()\n@app.ge"
                "t('/p')\ndef handler(): pass\n"
            ),
            "fastapi",
            "/p",
        ),
    ],
)
def test_framework_aliases_and_factories(
    tmp_path: Path, source: str, framework: str, path: str
) -> None:
    file = tmp_path / "example.py"
    file.write_text(source)
    routes = python_http_routes(str(file))
    assert len(routes) == 1
    assert routes[0].framework == framework
    assert routes[0].path == path
    engine = QueryEngine.from_directory(str(tmp_path))
    assert len(engine.attack_surface()) == 1


def test_ambiguous_receiver_has_diagnostic(tmp_path: Path) -> None:
    file = tmp_path / "example.py"
    file.write_text("@unknown.patch('/p')\ndef handler(): pass\n")
    assert python_http_routes(str(file))[0].reason == "unresolved_receiver"


def test_dynamic_methods_and_websocket_transport_remain_distinct(tmp_path: Path) -> None:
    file = tmp_path / "routes.py"
    file.write_text(
        "from flask import Flask\napp = Flask(__name__)\n"
        "@app.route('/', methods=configured_methods)\ndef dynamic(): pass\n"
        "from fastapi import FastAPI\napi = FastAPI()\n"
        "@api.websocket('/ws')\nasync def socket(): pass\n"
    )
    records = QueryEngine.from_directory(str(tmp_path)).endpoint_inventory()["registrations"]
    dynamic = next(r for r in records if r["path"] == "/")
    socket = next(r for r in records if r["path"] == "/ws")
    assert dynamic["methods"] == []
    assert socket["transport"] == "websocket"
    assert socket["methods"] == []
