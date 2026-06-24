"""M-3 真远程下载验证(不 mock fetch / 不 monkeypatch extract)。

造一个真实 tar.gz 虚拟包(含 pyproject.toml + unilabos_registry/),用本地 HTTP server
托管,构造带 download_url + 真 sha256 的 PairBundle,跑 make_downloader 的默认 fetch,
真正走 community_packages._download_and_extract_package 的 下载→sha256 校验→解压→挂载 全链路。
"""

import functools
import hashlib
import http.server
import socketserver
import tarfile
import threading
from pathlib import Path

from unilabos.sim.pairs.bundle import parse_bundle
from unilabos.sim.pairs.download import make_downloader


def _build_pkg_targz(dst: Path) -> Path:
    """造一个最小合法虚拟包(pyproject + unilabos_registry/),打成 tar.gz。"""
    src = dst / "pkgsrc"
    (src / "unilabos_registry" / "devices").mkdir(parents=True)
    (src / "pyproject.toml").write_text(
        "[project]\nname = \"m3-virtual-pkg\"\nversion = \"0.1.0\"\n\n"
        "[tool.unilabos.registry]\npaths = [\"unilabos_registry\"]\n",
        encoding="utf-8",
    )
    (src / "unilabos_registry" / "devices" / "v.yaml").write_text(
        "m3.virtual.demo:\n  class:\n    module: x:Y\n    type: python\n", encoding="utf-8"
    )
    archive = dst / "m3-virtual-pkg-0.1.0.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(src, arcname="m3-virtual-pkg-0.1.0")
    return archive


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, port


def _bundle(download_url: str, sha256: str) -> dict:
    return {
        "bundle_version": "2026-06-24T00:00:00Z",
        "engine": "custom",
        "pairs": [
            {
                "real": "m3.real.demo",
                "engine": "custom",
                "virtual": "community.m3.virtual.demo",
                "missing_sim_policy": "stub",
                "is_default": True,
                "priority": 100,
                "twin_capability": {"enabled": False},
                "virtual_package": {
                    "normalized_name": "m3-virtual-pkg",
                    "version": "0.1.0",
                    "download_url": download_url,
                    "sha256": sha256,
                    "class_namespace": "community.m3",
                },
            }
        ],
        "warnings": [],
    }


def test_m3_real_http_download_and_extract(tmp_path):
    archive = _build_pkg_targz(tmp_path)
    sha = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
    httpd, port = _serve(tmp_path)
    try:
        url = f"http://127.0.0.1:{port}/{archive.name}"
        bundle = parse_bundle(_bundle(url, sha))
        work = tmp_path / "work"
        work.mkdir()
        out = make_downloader(http_client=None, working_dir=str(work))(bundle)

        assert len(out) == 1, out
        pkg_dir = Path(out[0])
        assert pkg_dir.is_dir()
        assert (pkg_dir / "pyproject.toml").is_file()
        assert (pkg_dir / "unilabos_registry" / "devices" / "v.yaml").is_file()
    finally:
        httpd.shutdown()


def test_m3_real_sha256_mismatch_is_isolated(tmp_path):
    archive = _build_pkg_targz(tmp_path)
    httpd, port = _serve(tmp_path)
    try:
        url = f"http://127.0.0.1:{port}/{archive.name}"
        bundle = parse_bundle(_bundle(url, "sha256:deadbeef"))  # 故意错的 sha256
        work = tmp_path / "work"
        work.mkdir()
        out = make_downloader(http_client=None, working_dir=str(work))(bundle)
        # sha256 不匹配 → 该包失败被隔离,返回空(不抛)
        assert out == []
    finally:
        httpd.shutdown()
