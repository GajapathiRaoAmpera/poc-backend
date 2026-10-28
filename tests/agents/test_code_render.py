import os
import shutil
import subprocess

import pytest

from app.modules.agents.code import check
from app.modules.agents.code.render import DELIMITER, render
from app.modules.agents.code.schema import CodeAgentOutput, CodeFile
from app.modules.agents.code.validate import normalise


def manifest(**kw) -> CodeAgentOutput:
    base = dict(
        summary="s",
        project_slug="demo-app",
        files=[
            CodeFile(path="web/src/index.ts", content="console.log('$HOME `x`');\n"),
            CodeFile(path="README.md", content="# Demo\n"),
            CodeFile(
                path="scripts/dev.sh", content="#!/usr/bin/env bash\necho hi\n", executable=True
            ),
        ],  # fmt: skip
        next_steps=["cd web && npm install", "it's done"],
    )
    return normalise(CodeAgentOutput(**(base | kw)))


def script(m: CodeAgentOutput) -> str:
    return render(m, {"plan": 1, "architecture": 3})


def test_deterministic():
    m = manifest()
    assert script(m) == script(m)
    s = script(m)
    assert s.startswith("#!/usr/bin/env bash\n")
    assert s.index("'README.md'") < s.index("'scripts/dev.sh'") < s.index("'web/src/index.ts'")
    assert "# Sources: architecture r3, plan r1." in s


def test_next_steps_quote_escaping():
    assert "'  it'\\''s done'" in script(manifest())


def _run(s: str, tmp_path, target):
    path = tmp_path / "scaffold.sh"
    path.write_text(s)
    return subprocess.run(  # noqa: S603 - test-only, trusted fixture
        [shutil.which("bash"), str(path), str(target)], capture_output=True, text=True, timeout=20
    )


def test_delimiter_collision_roundtrip(tmp_path):
    body = f"a\n{DELIMITER}\n{DELIMITER}_1\nb\n"
    m = manifest(files=[CodeFile(path="t.txt", content=body)])
    s = script(m)
    assert f"<<'{DELIMITER}_2'" in s
    assert _run(s, tmp_path, tmp_path / "out").returncode == 0
    assert (tmp_path / "out" / "t.txt").read_text() == body


async def test_renderer_output_is_shellcheck_clean():
    report = await check.check(script(manifest()), [])
    assert report.shellcheck == "ok", report.findings
    assert report.bash_n == "ok"


def test_script_run_creates_files_and_refuses_rerun(tmp_path):
    m = manifest()
    s = script(m)
    first = _run(s, tmp_path, tmp_path / "out")
    assert first.returncode == 0, first.stderr
    assert "Next steps:" in first.stdout
    for f in m.files:
        assert (tmp_path / "out" / f.path).read_text() == f.content
    assert os.access(tmp_path / "out" / "scripts/dev.sh", os.X_OK)
    assert not os.access(tmp_path / "out" / "README.md", os.X_OK)
    again = _run(s, tmp_path, tmp_path / "out")
    assert again.returncode == 1
    assert "Refusing" in again.stderr


@pytest.mark.parametrize("bad", ["../x", "a'b"])
def test_rendered_paths_come_from_validated_manifest(bad):
    from app.modules.agents.code.validate import manifest_issues

    assert manifest_issues(manifest(files=[CodeFile(path=bad, content="")]))
