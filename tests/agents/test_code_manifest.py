import pytest

from app.modules.agents.code.schema import CodeAgentOutput, CodeFile, FileChange
from app.modules.agents.code.validate import apply_changes, manifest_issues, normalise, plan_md


def out(*files: tuple[str, str]) -> CodeAgentOutput:
    return CodeAgentOutput(
        summary="s",
        project_slug="demo",
        files=[CodeFile(path=p, content=c) for p, c in files],
    )


def codes(o: CodeAgentOutput) -> list[str]:
    return [i.code for i in manifest_issues(o)]


@pytest.mark.parametrize("path", ["../x", "/etc/x", "a/../b", ".git/config", "a//b", "a b"])
def test_rejects_traversal_absolute_dotgit(path):
    assert codes(out((path, "x\n"))) == ["PATH"]


def test_rejects_duplicate_case_insensitive_and_dir_collision():
    assert "DUPLICATE" in codes(out(("A.txt", ""), ("a.txt", "")))
    assert codes(out(("a", ""), ("a/b", ""))) == ["PATH"]


@pytest.mark.parametrize("path", [".env", "k.pem", "id_rsa", ".ssh/x", "api/.env"])
def test_rejects_secrets_files(path):
    assert codes(out((path, "x\n"))) == ["FORBIDDEN"]


def test_secret_scan_names_file_and_line():
    issues = manifest_issues(out(("cfg.py", "a = 1\nKEY = 'AKIAABCDEFGHIJKLMNOP'\n")))
    assert [i.code for i in issues] == ["SECRET"]
    assert issues[0].message.startswith("cfg.py:2:")
    assert codes(out((".env.example", "API_KEY=changeme\n"))) == []


def test_size_limits():
    with pytest.raises(ValueError):
        out(("big.txt", "x" * 20001))
    many = [(f"f{i:03}.txt", "x" * 19000) for i in range(16)]
    assert "TOO_LARGE" in codes(out(*many))


def test_llm_cannot_write_plan_md():
    assert codes(out(("PLAN.md", "x\n"))) == ["RESERVED"]


def test_normalise_crlf_and_newline():
    o = normalise(out(("a.txt", "a\r\nb"), ("e.txt", "")))
    assert [f.content for f in o.files] == ["a\nb\n", ""]


def test_apply_changes():
    files = [CodeFile(path="a.txt", content="a\n"), CodeFile(path="b.txt", content="b\n")]
    got = apply_changes(
        files,
        [
            FileChange(op="write", path="a.txt", content="A"),
            FileChange(op="delete", path="b.txt"),
            FileChange(op="write", path="c.sh", content="echo\n", executable=True),
        ],
    )
    assert [(f.path, f.content, f.executable) for f in got] == [
        ("a.txt", "A\n", False),
        ("c.sh", "echo\n", True),
    ]
    with pytest.raises(ValueError, match="no such file"):
        apply_changes(files, [FileChange(op="delete", path="zzz")])
    with pytest.raises(ValueError, match="generated"):
        apply_changes([plan_md({})], [FileChange(op="write", path="PLAN.md", content="x")])


def test_plan_md():
    f = plan_md(
        {
            "milestones": [{"id": "M-01", "title": "Build"}],
            "tasks": [
                {
                    "id": "T-001",
                    "title": "API",
                    "milestone_id": "M-01",
                    "role": "be",
                    "effort_days": 2.0,
                },
            ],
        }  # fmt: skip
    )
    assert f.generated and f.path == "PLAN.md"
    assert "## M-01 Build" in f.content
    assert "- [ ] T-001 API (be, 2d)" in f.content
    with_plan = out(("a.txt", "")).model_copy(update={"files": [f]})
    assert manifest_issues(with_plan, allow_generated=True) == []
