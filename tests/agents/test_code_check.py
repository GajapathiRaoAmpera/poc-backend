from app.modules.agents.code import check
from app.modules.agents.code.schema import CodeFile

OK = "#!/usr/bin/env bash\nset -euo pipefail\necho ok\n"


async def test_bash_n_syntax_error():
    report = await check.check("#!/usr/bin/env bash\nif true; then\n", [])
    assert report.bash_n == "failed"
    assert report.findings[0].tool == "bash-n"
    assert report.findings[0].file == check.SCRIPT


async def test_shellcheck_warning_is_error():
    bad = CodeFile(path="scripts/dev.sh", content='#!/usr/bin/env bash\ncd $1\necho "x"\n')
    report = await check.check(OK, [bad])
    assert report.bash_n == "ok"
    assert report.shellcheck == "failed"
    found = [f for f in report.findings if f.file == "scripts/dev.sh"]
    assert found and all(f.level == "error" and f.line == 2 for f in found)
    assert {f.code for f in found} & {"SC2164", "SC2086"}
    assert report.shellcheck_version


async def test_dialect_from_shebang():
    assert check.dialect(CodeFile(path="run", content="#!/bin/sh\necho\n")) == "sh"
    assert check.dialect(CodeFile(path="run", content="#!/usr/bin/env bash\n")) == "bash"
    assert check.dialect(CodeFile(path="x.sh", content="echo\n")) == "bash"
    assert check.dialect(CodeFile(path="app.py", content="print(1)\n")) is None
    # `[[` is a bashism: an sh file is checked as sh.
    sh = CodeFile(path="run", content='#!/bin/sh\nif [[ -n "$1" ]]; then echo; fi\n')
    report = await check.check(OK, [sh])
    assert any(f.file == "run" for f in report.findings)


async def test_non_shell_files_are_not_linted():
    report = await check.check(OK, [CodeFile(path="app.py", content="cd $x\n")])
    assert report.findings == []


async def test_unavailable_when_missing(monkeypatch):
    monkeypatch.setattr(check, "shellcheck_path", lambda: None)
    report = await check.check(OK, [])
    assert report.shellcheck == "unavailable"
    assert [f.code for f in report.findings] == ["VALIDATOR_UNAVAILABLE"]


async def test_unavailable_on_timeout(monkeypatch):
    async def slow(*a, **k):
        raise TimeoutError

    monkeypatch.setattr(check, "_shellcheck", slow)
    report = await check.check(OK, [])
    assert report.shellcheck == "unavailable"
    assert report.findings[-1].code == "VALIDATOR_UNAVAILABLE"
