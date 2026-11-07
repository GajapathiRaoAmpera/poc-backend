import pytest
from pydantic import ValidationError

from app.modules.agents.code.schema import CodeFile
from app.modules.sandbox.spec import IMAGES, SandboxService, SandboxSpec, missing_dirs


def svc(**kw):
    base = dict(
        name="backend",
        dir="backend",
        runtime="python3.12",
        install=["pip", "install", "-r", "requirements.txt"],
        start=["python", "main.py"],
        port=8000,
    )
    return {**base, **kw}


def test_valid_spec_and_images():
    s = SandboxSpec(services=[SandboxService(**svc())])
    assert s.services[0].health == "/health"
    assert IMAGES == {"python3.12": "python:3.12-slim", "node22": "node:22-slim"}


def test_spec_rejects_unknown_runtime_bad_port_five_services():
    with pytest.raises(ValidationError):
        SandboxService(**svc(runtime="ruby3"))
    for port in (80, 1023, 65536):
        with pytest.raises(ValidationError):
            SandboxService(**svc(port=port))
    five = [svc(name=f"s{i}") for i in range(5)]
    with pytest.raises(ValidationError):
        SandboxSpec.model_validate({"services": five})
    with pytest.raises(ValidationError):
        SandboxSpec.model_validate({"services": []})


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "Backend"},
        {"name": "1x"},
        {"name": "a" * 22},
        {"dir": "../etc"},
        {"dir": "/abs"},
        {"dir": "a:b"},
        {"install": []},
        {"start": []},
        {"health": "http://evil"},
    ],
)
def test_spec_rejects_bad_fields(bad):
    with pytest.raises(ValidationError):
        SandboxService(**svc(**bad))


def test_spec_rejects_duplicate_names():
    with pytest.raises(ValidationError):
        SandboxSpec.model_validate({"services": [svc(), svc(port=9000)]})


def test_dir_must_be_manifest_folder():
    spec = SandboxSpec(
        services=[SandboxService(**svc()), SandboxService(**svc(name="fe", dir="frontend"))]
    )
    files = [CodeFile(path="backend/main.py", content="x"), CodeFile(path="frontend", content="")]
    assert missing_dirs(spec, files) == ["frontend"]


@pytest.mark.parametrize("dirs", [("a", "a"), ("a", "a/b"), ("a/b", "a")])
def test_spec_rejects_overlapping_dirs(dirs):
    services = [svc(name="one", dir=dirs[0]), svc(name="two", dir=dirs[1])]
    with pytest.raises(ValidationError, match="overlap"):
        SandboxSpec.model_validate({"services": services})


def test_spec_allows_sibling_dirs_with_shared_prefix():
    SandboxSpec.model_validate({"services": [svc(name="a", dir="a"), svc(name="b", dir="ab")]})


@pytest.mark.parametrize("bad", [".", "", "a/./b", "a//b"])
def test_spec_rejects_dot_or_empty_dir(bad):
    with pytest.raises(ValidationError):
        SandboxService(**svc(dir=bad))
