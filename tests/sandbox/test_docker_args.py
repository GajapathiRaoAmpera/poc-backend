import uuid
from pathlib import Path

from app.modules.sandbox.docker import run_args
from app.modules.sandbox.spec import SandboxService

PID = str(uuid.uuid4())


def make(**kw):
    base = dict(
        name="backend",
        dir="backend",
        runtime="python3.12",
        install=["pip", "install", "-r", "requirements.txt"],
        start=["uvicorn", "main:app", "--port", "8000"],
        port=8000,
    )
    return SandboxService(**{**base, **kw})


def test_run_args_flags():
    args = run_args(PID, make(), Path("/srv/sb") / PID)
    joined = " ".join(args)
    for flag in [
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--memory 1g",
        "--cpus 1",
        "--pids-limit 256",
        "--user 1000:1000",
        "-p 127.0.0.1::8000",
        "-w /app",
        f"--name ampera-{PID}-backend",
        f"--network ampera-{PID}",
        f"--label ampera.project={PID}",
    ]:
        assert flag in joined, flag
    assert "/var/run/docker.sock" not in joined
    assert args.count("-v") == 1
    mount = args[args.index("-v") + 1]
    assert mount == f"/srv/sb/{PID}/backend:/app"
    assert args[:3] == ["docker", "run", "-d"]
    assert "python:3.12-slim" in args
    assert args[-3:-1] == ["sh", "-c"]
    assert "--privileged" not in args


def test_shell_metachar_in_install_is_quoted():
    args = run_args(PID, make(install=["pip", "install", "x; rm -rf /"]), Path("/srv/sb"))
    cmd = args[-1]
    assert "'x; rm -rf /'" in cmd
    assert cmd.endswith(" && exec uvicorn main:app --port 8000")
