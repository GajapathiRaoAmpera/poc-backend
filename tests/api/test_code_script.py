import hashlib

from app.modules.agents.code.render import render
from app.modules.agents.code.schema import CodeContent
from app.modules.artifacts import service as artifacts
from tests.agents.helpers import run_stage
from tests.agents.test_code_agent import code, ready
from tests.delivery.conftest import scripted
from tests.factories import login, make_user

API = "/api/v1"


async def test_download_script(client, db, make_client):
    owner, project = await ready(db)
    await run_stage(db, project, owner, "code", scripted(code_output=code()))
    await login(client, owner.email)
    url = f"{API}/projects/{project.id}/artifacts/code/revisions/1/script"
    r = await client.get(url)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/x-shellscript")
    assert r.headers["content-disposition"] == (
        'attachment; filename="scaffold-client-portal-r1.sh"'
    )
    rev = await artifacts.get_revision(db, project, "code", 1)
    content = CodeContent.model_validate(rev.content)
    assert r.text == render(content, {s.kind: s.revision for s in rev.sources})
    assert r.headers["x-content-sha256"] == hashlib.sha256(r.content).hexdigest()
    assert r.headers["x-content-sha256"] == content.script_sha256
    assert (await client.get(f"{API}/projects/{project.id}/artifacts/code")).status_code == 200

    other = make_client()
    await make_user(db, email="other@x.com")
    await login(other, "other@x.com")
    assert (await other.get(url)).status_code == 404
    assert (await client.get(url.replace("/1/", "/9/"))).status_code == 404
