from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import runpy
import subprocess
import sys
import threading
import urllib.request

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "_check_studio_assets.py"


@pytest.fixture
def asset_server():
    routes: dict[str, tuple[int, dict[str, str], str]] = {}
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            status, headers, body = routes.get(self.path, (404, {}, "not found"))
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", routes, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def run_checker(url: str):
    return subprocess.run(
        [sys.executable, str(SCRIPT), url],
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_resolves_assets_against_redirected_page(asset_server):
    base, routes, requests = asset_server
    routes["/studio"] = (302, {"Location": "/studio/"}, "")
    routes["/studio/"] = (
        200,
        {},
        f"""<script src='./static/app.js?v=1&amp;x=2'></script>
        <link href="/static/app.css" rel="stylesheet">
        <script src="{base}/absolute.js"></script>""",
    )
    routes["/studio/static/app.js?v=1&x=2"] = (200, {}, "console.log('ok')")
    routes["/static/app.css"] = (200, {}, "body {}")
    routes["/absolute.js"] = (200, {}, "console.log('absolute')")

    result = run_checker(base + "/studio")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "accessible 3/3" in result.stdout
    assert requests == [
        "/studio", "/studio/", "/studio/static/app.js?v=1&x=2",
        "/static/app.css", "/absolute.js",
    ]


def test_failure_after_eighth_asset_returns_nonzero(asset_server):
    base, routes, requests = asset_server
    routes["/"] = (200, {}, "".join(f'<script src="a{i}.js"></script>' for i in range(9)))
    for i in range(8):
        routes[f"/a{i}.js"] = (200, {}, "ok")

    result = run_checker(base)

    assert result.returncode != 0
    assert "accessible 8/9" in result.stdout
    assert "/a8.js" in requests


@pytest.mark.parametrize("status, body", [(404, "not found"), (200, "<html></html>")])
def test_missing_page_or_assets_returns_nonzero(asset_server, status, body):
    base, routes, _ = asset_server
    routes["/"] = (status, {}, body)

    result = run_checker(base)

    assert result.returncode != 0
    assert "FAIL" in result.stdout
    assert "Traceback" not in result.stderr


def test_import_does_not_make_requests(monkeypatch):
    def unexpected_request(*args, **kwargs):
        pytest.fail("importing the checker must not send HTTP requests")

    monkeypatch.setattr(urllib.request, "urlopen", unexpected_request)
    runpy.run_path(str(SCRIPT))
