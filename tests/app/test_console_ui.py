"""验证 React 控制台由 Uni-Lab-OS Web 组合根直接下发。"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from unilabos.app.web.console import (
    DEFAULT_CONSOLE_DIRECTORY,
    install_console_security,
    install_console_ui,
)
from unilabos.config.config import BasicConfig


def test_embedded_console_bundle_is_present() -> None:
    assert (DEFAULT_CONSOLE_DIRECTORY / "index.html").is_file()
    assert (DEFAULT_CONSOLE_DIRECTORY / "og.png").is_file()
    assets = DEFAULT_CONSOLE_DIRECTORY / "assets"
    assert any(assets.glob("*.js"))
    assert any(assets.glob("*.css"))


def _console_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "console"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!doctype html><div id="root">Uni-Lab OS Console</div>',
        encoding="utf-8",
    )
    (dist / "og.png").write_bytes(b"console-preview")
    (assets / "app-abc123.js").write_text(
        "window.__UNILAB_CONSOLE__ = true",
        encoding="utf-8",
    )
    return dist


def test_console_serves_spa_without_shadowing_api(tmp_path: Path) -> None:
    app = FastAPI()

    @app.get("/api/v1/readiness")
    def readiness() -> dict[str, str]:
        return {"status": "ready"}

    assert install_console_ui(app, _console_dist(tmp_path))
    client = TestClient(app)

    root = client.get("/?page=tasks", follow_redirects=False)
    assert root.status_code == 307
    assert root.headers["location"] == "/console/?page=tasks"

    index = client.get("/console/?page=tasks")
    assert index.status_code == 200
    assert "Uni-Lab OS Console" in index.text
    assert index.headers["cache-control"] == "no-cache"

    deep_link = client.get("/console/tasks")
    assert deep_link.status_code == 200
    assert "Uni-Lab OS Console" in deep_link.text
    assert deep_link.headers["cache-control"] == "no-cache"

    asset = client.get("/console/assets/app-abc123.js")
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert client.get("/console/assets/missing.js").status_code == 404
    assert client.post("/console/").status_code == 405
    assert client.get("/api/v1/readiness").json() == {"status": "ready"}


def test_console_install_is_idempotent_and_missing_bundle_is_nonfatal(
    tmp_path: Path,
) -> None:
    app = FastAPI()
    dist = _console_dist(tmp_path)

    assert install_console_ui(app, dist)
    assert not install_console_ui(app, dist)
    assert sum(route.name == "unilab-console" for route in app.routes) == 1

    api_only = FastAPI()

    @api_only.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    assert not install_console_ui(api_only, tmp_path / "missing")
    assert TestClient(api_only).get("/api/v1/health").json() == {
        "status": "ok"
    }


def test_console_install_ignores_composition_routes_without_names(
    tmp_path: Path,
) -> None:
    app = FastAPI()
    composition_route = object()
    app.router.routes.append(composition_route)  # type: ignore[arg-type]

    assert install_console_ui(app, _console_dist(tmp_path))
    assert any(
        getattr(route, "name", None) == "unilab-console"
        for route in app.routes
    )


def test_console_security_rejects_cross_site_and_dns_rebinding(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        BasicConfig,
        "frontend_allowed_hosts",
        "localhost,127.0.0.1,::1,edge-http",
    )
    monkeypatch.setattr(BasicConfig, "frontend_same_origin", True)
    app = FastAPI()

    @app.api_route("/api/v1/workflow-tasks", methods=["GET", "POST"])
    def workflow_tasks() -> dict[str, str]:
        return {"status": "accepted"}

    assert install_console_security(app)
    assert not install_console_security(app)
    client = TestClient(app, base_url="http://127.0.0.1:4173")

    same_origin = {
        "origin": "http://127.0.0.1:4173",
        "sec-fetch-site": "same-origin",
    }
    assert client.post(
        "/api/v1/workflow-tasks",
        headers=same_origin,
    ).status_code == 200
    assert client.post("/api/v1/workflow-tasks").status_code == 200

    assert client.get(
        "/api/v1/workflow-tasks",
        headers={
            "origin": "https://evil.example",
            "sec-fetch-site": "cross-site",
        },
    ).status_code == 403
    assert client.get(
        "/api/v1/workflow-tasks",
        headers={
            "host": "evil.example",
            "origin": "http://evil.example",
            "sec-fetch-site": "same-origin",
        },
    ).status_code == 403
    assert client.get(
        "/api/v1/workflow-tasks",
        headers={"origin": "http://127.0.0.1:not-a-port"},
    ).status_code == 403
