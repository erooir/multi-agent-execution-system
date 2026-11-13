"""The optional real-engine tests exercise isolation rather than mocking container success."""

import base64
import hashlib
import json
import os
import subprocess
import time
from types import SimpleNamespace

import pytest

from backend.app.platform.script_sandbox import (
    _command,
    _decode_files,
    _engine,
    _prefix,
    configuration,
    run_script,
    validate_code,
)
from backend.app.platform.script_tool import ScriptExecute, compact_result


def test_missing_engine_configuration_never_executes_host_code(monkeypatch, tmp_path):
    monkeypatch.setenv("HANGZHI_SANDBOX_ENABLED", "false")
    marker = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="sandbox_unavailable"):
        run_script(f"from pathlib import Path\nPath({str(marker)!r}).touch()", [], work_root=tmp_path)
    assert not marker.exists()


def test_static_validation_is_bounded_and_separate_from_container_boundary():
    assert len(validate_code("print(sum([1, 2]))")) == 64
    for code in ["import socket", "import subprocess", "eval('1+1')"]:
        with pytest.raises(ValueError, match="sandbox_"):
            validate_code(code)
    with pytest.raises(ValueError, match="sandbox_syntax_invalid"):
        validate_code("this is not python code :")


def test_container_command_cannot_enable_network_mount_host_or_pull(tmp_path):
    config = {"distro": "", "executable": "podman", "image": "sha256:" + "a" * 64}
    command = _command(config, "test", tmp_path, 15)
    for flag in (
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--pull=never",
        "--pids-limit=64",
        "--memory=256m",
        "--user=65534:65534",
        "--http-proxy=false",
    ):
        assert flag in command
    assert "target=/inputs,ro=true" in command[command.index("--mount") + 1]
    assert not any("socket" in part or "privileged" in part for part in command)


@pytest.mark.parametrize("name", ["../escape.txt", "/etc/passwd", "C:/x.txt", "unsafe.html", "dir\\x.txt"])
def test_untrusted_output_paths_are_rejected(name):
    with pytest.raises(ValueError, match="sandbox_output_path_denied"):
        _decode_files({"files": [{"name": name, "base64": base64.b64encode(b"x").decode()}]})


def test_outputs_require_valid_json_or_actual_png():
    with pytest.raises(ValueError):
        _decode_files({"files": [{"name": "wrong.json", "base64": base64.b64encode(b"NaN").decode()}]})
    with pytest.raises(OSError):
        _decode_files({"files": [{"name": "wrong.png", "base64": base64.b64encode(b"not a png").decode()}]})


def test_large_multibyte_previews_are_bounded_without_losing_durable_output():
    result = {
        "status": "succeeded",
        "stdout": "日志" * 12000,
        "stderr": "诊断" * 12000,
        "outputs": [
            {
                "name": f"结果{i}.txt",
                "media_type": "text/plain",
                "preview": "数据" * 4000,
                "data": b"full contents",
                "bytes": 13,
                "checksum": "f" * 64,
            }
            for i in range(16)
        ],
    }
    compact = compact_result(result, "script:1", "run", ["source"])
    assert len(json.dumps(compact, ensure_ascii=False).encode()) < 60000
    assert compact["stdout_truncated"] and compact["stderr_truncated"]
    assert len(compact["outputs"]) == 16 and all(row["preview_truncated"] for row in compact["outputs"])
    assert all("data" not in row for row in compact["outputs"])
    assert result["outputs"][0]["data"] == b"full contents" and len(result["outputs"][0]["preview"]) == 8000


@pytest.mark.parametrize("status,grounded", [("failed", False), ("succeeded", True)])
async def test_failed_script_diagnostics_cannot_be_grounded_citations(monkeypatch, status, grounded):
    from backend.app.platform import domain_session

    class Reader:
        def __init__(self, *args, **kwargs):
            self.source_refs = set()

    def dispatch(tool, arguments, reader):
        reader.source_refs.add("source")
        return {"status": status, "stderr": "diagnostic", "outputs": []}

    async def invoke(name, schema, arguments, handler):
        result = await handler(**arguments)
        domain_session._active.get().record = {"id": "call", "status": "completed", "result_checksum": "hash"}
        return {"data": result}

    monkeypatch.setattr(domain_session, "FrozenSourceReader", Reader)
    monkeypatch.setattr(domain_session, "dispatch", dispatch)
    session = object.__new__(domain_session.DomainToolSession)
    session.execution = SimpleNamespace(inputs=[], check=lambda: None)
    session.sources_read, session.derived, session.invoke = set(), [], invoke
    operation = SimpleNamespace(
        id="script.execute", name="script_execute", label="Synthetic", schema=ScriptExecute
    )
    result = await session._binding(operation)(source_ids=["source"], purpose="test", code="print(1)")
    assert result["data"]["stderr"] == "diagnostic"
    assert ("derived_evidence" in result["data"]) is grounded
    assert bool(session.derived) is grounded


@pytest.mark.parametrize(
    "error,expected",
    [
        ("sandbox_syntax_invalid", "sandbox_syntax_invalid"),
        ("sandbox_timeout", "sandbox_timeout"),
        ("sandbox_import_denied", "sandbox_import_denied"),
        ("E:/private/secret.env confidential-sentinel", "tool_input_invalid"),
    ],
)
async def test_tool_session_exposes_only_allowlisted_sandbox_diagnostics(monkeypatch, error, expected):
    from backend.app.platform import tool_runtime

    monkeypatch.setattr(tool_runtime, "validate_frozen", lambda *args: None)
    session = object.__new__(tool_runtime.ToolSession)
    session.execution = SimpleNamespace(
        check=lambda: None,
        current=lambda: {"tool_calls": []},
        task={"id": "task"},
        active_node_id="node",
        repository=None,
    )
    session.agent, session.revision, session.instance_id = {}, 0, "instance"
    session.tools = {"script_execute": {"id": "script.execute", "kind": "tool", "version": 1}}
    session.skills, session.sources_read, session.closed = {}, set(), False
    records = []
    session.save = lambda record, event, **kwargs: records.append(dict(record))

    async def failed(**kwargs):
        raise ValueError(error)

    result = await session.invoke(
        "script_execute",
        ScriptExecute,
        {"code": "print(1)", "source_ids": [], "purpose": "synthetic test"},
        failed,
    )
    assert result["data"]["error_code"] == expected
    assert "confidential-sentinel" not in json.dumps(result) and "private/secret" not in json.dumps(records)
    assert records[-1]["status"] == "failed" and records[-1]["error_code"] == expected


REAL = pytest.mark.skipif(
    os.getenv("HANGZHI_TEST_SANDBOX") != "true", reason="explicit provisioned rootless Podman required"
)


@REAL
def test_real_sandbox_computes_from_readonly_input_and_enforces_limits(monkeypatch):
    monkeypatch.setenv("HANGZHI_HOST_TEST_SECRET", "host-only-sentinel")
    raw = b'[{"n": 2}, {"n": 7}]'
    source = {
        "id": "input",
        "source_version": "v1",
        "media_type": "application/json",
        "checksum": hashlib.sha256(raw).hexdigest(),
        "data": raw,
    }
    code = """import json, os, importlib
from pathlib import Path
manifest = json.loads(Path('/inputs/manifest.json').read_text())
path = Path(manifest['sources'][0]['path'])
rows = json.loads(path.read_text())
checks = {'total':sum(row['n'] for row in rows), 'host_secret_absent': 'HANGZHI_HOST_TEST_SECRET' not in os.environ, 'host_drive_absent':not Path('/mnt/c').exists()}
try:
    path.write_text('mutated')
    checks['input_readonly'] = False
except OSError:
    checks['input_readonly'] = True
try:
    Path('/etc/unallowed').write_text('mutated')
    checks['root_readonly'] = False
except OSError:
    checks['root_readonly'] = True
socket = importlib.import_module('socket')
try:
    socket.create_connection(('1.1.1.1', 80), timeout=1)
    checks['network_blocked'] = False
except OSError:
    checks['network_blocked'] = True
for filename in ['memory.max', 'pids.max', 'cpu.max']:
    checks[filename] = Path('/sys/fs/cgroup/' + filename).read_text().strip()
Path('/outputs/result.json').write_text(json.dumps(checks))
"""
    result = run_script(code, [source])
    assert result["status"] == "succeeded" and result["engine_verification"]["limits_verified"]
    output = json.loads(result["outputs"][0]["data"])
    assert output["total"] == 9
    assert all(
        output[key]
        for key in (
            "input_readonly",
            "root_readonly",
            "network_blocked",
            "host_secret_absent",
            "host_drive_absent",
        )
    )
    assert int(output["memory.max"]) == 268435456 and int(output["pids.max"]) == 64
    quota, period = map(int, output["cpu.max"].split())
    assert quota / period <= 1


@REAL
def test_real_sandbox_rejects_symlink_and_large_output():
    with pytest.raises(ValueError, match="sandbox_output_type_denied"):
        run_script("import os\nos.symlink('/etc/passwd', '/outputs/link.txt')", [])
    with pytest.raises(ValueError, match="sandbox_output_limit"):
        run_script("from pathlib import Path\nPath('/outputs/large.txt').write_text('x'*4100000)", [])


@REAL
def test_real_sandbox_timeout_stops_entire_container():
    with pytest.raises(ValueError, match="sandbox_timeout"):
        run_script("while True: pass", [], timeout_seconds=1)


@REAL
def test_real_sandbox_script_failure_is_not_quality_success():
    result = run_script("raise ValueError('Synthetic calculation failure')", [])
    assert result["status"] == "failed" and result["returncode"] != 0
    assert "Synthetic calculation failure" in result["stderr"]


@REAL
def test_real_sandbox_cancellation_cleans_up_container():
    checks = 0

    def cancelled():
        nonlocal checks
        checks += 1
        if checks > 25:
            raise RuntimeError("synthetic_cancelled")

    with pytest.raises(RuntimeError, match="synthetic_cancelled"):
        run_script("import time\ntime.sleep(50)", [], checkpoint=cancelled)
    result = subprocess.run(
        _engine(
            _prefix(configuration()), ["ps", "--all", "--filter", "name=hangzhi-script-", "--format=json"]
        ),
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout) == []


@REAL
def test_real_script_tool_persists_immutable_code_and_unreviewed_outputs(tmp_path):
    from backend.app.platform import service
    from backend.app.platform.db import Repository
    from backend.app.platform.domain_tool import FrozenSourceReader, dispatch
    from backend.app.platform.runtime import Execution

    repository = Repository(f"sqlite:///{(tmp_path / 'script-test.sqlite3').as_posix()}")
    repository.put(
        "projects", {"id": "p", "name": "Public synthetic fixture", "status": "active", "members": []}
    )
    source = service.create_asset(
        {
            "project_id": "p",
            "name": "Synthetic counts",
            "modality": "structured",
            "media_type": "application/json",
            "content": [{"n": 2}, {"n": 7}],
            "source_id": "synthetic-counts",
            "provenance": "Public synthetic test",
            "classification": "public",
        },
        {"id": "admin", "role": "admin"},
        repository,
    )
    task = repository.put(
        "tasks",
        {
            "id": "task",
            "project_id": "p",
            "status": "running",
            "revision": 0,
            "request": {"mode": "cloud", "deadline_seconds": 180},
            "snapshot": {"inputs": [source]},
            "lease_owner": "worker",
            "lease_token": 1,
            "lease_until": time.time() + 180,
        },
    )
    execution = Execution(repository, task, "worker", 1)
    reader = FrozenSourceReader(execution.inputs, execution.check, public_only=True)
    reader.execution = execution
    result = dispatch(
        "script.execute",
        {
            "code": "import json\nfrom pathlib import Path\nm=json.loads(Path('/inputs/manifest.json').read_text())\nrows=json.loads(Path(m['sources'][0]['path']).read_text())\nPath('/outputs/result.json').write_text(json.dumps({'total':sum(r['n'] for r in rows)}))",
            "source_ids": [source["id"]],
            "purpose": "Sum supplied synthetic counts",
        },
        reader,
    )
    assert result["status"] == "succeeded" and result["quality_status"] == "unreviewed"
    version = repository.get("script_versions", result["script_version_id"])
    run = repository.get("sandbox_runs", result["sandbox_run_id"])
    assert version["scope"] == "task" and version["checksum"] == result["script_checksum"]
    assert run["source_refs"] == [source["id"]] and run["status"] == "succeeded"
    assert json.loads(base64.b64decode(run["outputs"][0]["base64"])) == {"total": 9}
    with pytest.raises(ValueError, match="immutable"):
        repository.put("script_versions", {**version, "code": "print('changed')"})
    assert not repository.list("artifacts")
    assert [event["type"] for event in repository.list("events")] == ["script.started", "script.succeeded"]
    repository.engine.dispose()
