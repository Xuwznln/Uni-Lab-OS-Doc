"""为已构建动态库生成匹配字节摘要的交付目录及插件清单。"""

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DESCRIBE_TIMEOUT_SECONDS = 10


def _inspect_manifest(library: Path, metadata: Path, python: str | Path, engine_id: str, timeout: float) -> dict:
    """在指定目标解释器的子进程中验证实际库，原生错误不得终止打包进程。"""
    bootstrap = """
import hashlib, sys
from pathlib import Path
from uuid import uuid4
sys.path.insert(0, sys.argv[1])
if sys.argv[5]:
    # PEP 517 的声明构建依赖可能位于隔离 overlay，而不在基础解释器目录。
    sys.path.insert(1, sys.argv[5])
from unilabos.scheduling.plugin import verify_contract
from unilabos.scheduling.plugin_worker import NativeLibrary
from unilabos.scheduling.protocol import (
    CONTRACT_DIR, CONTRACT_SHA256, PROTOCOL, SCHEMA_VERSION,
    EngineError, decode_message, encode_message, require, validate_message, validator,
)
contract = verify_contract(CONTRACT_DIR, CONTRACT_SHA256)
request = {
    "protocol": PROTOCOL, "schema_version": SCHEMA_VERSION, "operation": "describe",
    "request_id": str(uuid4()), "timeout_ms": 10000,
    "body": {"host_version": "unilab.scheduler.r3", "supported_schema_versions": [SCHEMA_VERSION],
             "required_capabilities": ["core.v1"]},
}
response = decode_message(NativeLibrary(Path(sys.argv[2])).call(encode_message(request), timeout_ms=10000))
validate_message(response, "DescribeResponse")
require(all(response[key] == request[key] for key in ("protocol", "schema_version", "operation", "request_id")),
        "INVALID_REQUEST", "动态库 describe 响应与打包请求不匹配")
if not response["ok"]:
    raise EngineError(**response["error"])
description = response["result"]
require(description["engine_id"] == sys.argv[4], "ENGINE_UNAVAILABLE", "待打包库与预期引擎身份不符")
require(SCHEMA_VERSION in description["supported_schema_versions"], "UNSUPPORTED_SCHEMA", "没有共同协议")
require("core.v1" in description["capabilities"], "UNSUPPORTED_CAPABILITY", "动态库缺少核心能力")
manifest = {
    "format": "unilab.scheduler.plugin-manifest.v1",
    **{key: description[key] for key in ("engine_id", "engine_version", "build_id")},
    "contract": {key: contract[key] for key in ("schema_version", "release_state", "draft_revision", "contract_sha256")},
    "artifact": {"kind": "native", "path": Path(sys.argv[2]).name,
                 "sha256": hashlib.sha256(Path(sys.argv[2]).read_bytes()).hexdigest()},
    "abi": {"major": 1, "minor": 0},
}
require(validator(manifest=True).is_valid(manifest), "INVALID_REQUEST", "动态库不能生成合法的发布清单")
Path(sys.argv[3]).write_bytes(encode_message(manifest))
"""
    root = Path(__file__).resolve().parents[2]
    dependencies = ""
    if Path(python).resolve() == Path(sys.executable).resolve():
        schema = importlib.util.find_spec("jsonschema")
        if schema is not None and schema.origin is not None:
            dependencies = str(Path(schema.origin).resolve().parents[1])
    try:
        subprocess.run(
            [str(python), "-I", "-c", bootstrap, str(root), str(library), str(metadata), engine_id, dependencies],
            check=True, timeout=timeout, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("动态库 describe 超时，未生成发布产物") from error
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"动态库身份检查失败，未生成发布产物：{error.stderr.decode(errors='replace')[-2000:]}") from error
    return json.loads(metadata.read_bytes())


def package(library: Path, destination: Path, *, engine_id: str, python: str | Path = sys.executable, timeout: float = DESCRIBE_TIMEOUT_SECONDS, notices: Path | None = None) -> Path:
    destination = destination.absolute()
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".unilab-" + engine_id.removeprefix("unilab.").replace("_", "-") + "-package-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        target = stage / library.name
        shutil.copyfile(library, target)
        metadata_path = stage / "description.json"
        manifest = _inspect_manifest(target, metadata_path, python, engine_id, timeout)
        metadata_path.unlink()
        license_text = Path(__file__).with_name("THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8")
        if notices is not None:
            license_text += "\n\n" + notices.read_text(encoding="utf-8")
        (stage / "THIRD_PARTY_NOTICES.txt").write_text(license_text, encoding="utf-8")
        (stage / "plugin.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        # 先排他保留目标名称，再用完整目录替换本次创建的空目录；不覆盖已有发布。
        destination.mkdir(exist_ok=False)
        try:
            stage.replace(destination)
        except OSError:
            destination.rmdir()
            raise
    return destination / "plugin.json"


def main(engine_id: str) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("library", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--python", default=sys.executable, help="与动态库匹配的目标解释器")
    args = parser.parse_args()
    print(package(args.library, args.destination, engine_id=engine_id, python=args.python))
