import json

import pytest

from app.core.config import get_settings
from app.modules.agents.code.schema import CodeFile
from app.modules.projects.models import Project
from app.modules.sandbox import docker, service
from app.modules.sandbox.spec import SandboxService, SandboxSpec
from tests.factories import make_user

pytestmark = pytest.mark.docker

BACKEND = """from http.server import BaseHTTPRequestHandler, HTTPServer


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")


HTTPServer(("0.0.0.0", 8000), H).serve_forever()
"""
FRONTEND = """require("http").createServer((req, res) => res.end("<h1>hi</h1>")).listen(3000);
"""


async def test_two_service_fixture_healthy(db):
    user = await make_user(db)
    p = Project(owner_id=user.id, name="P")
    await db.projects.insert_one(p.to_doc())
    files = [
        CodeFile(path="backend/main.py", content=BACKEND),
        CodeFile(path="frontend/server.js", content=FRONTEND),
        CodeFile(
            path="frontend/package.json", content=json.dumps({"name": "fe", "version": "1.0.0"})
        ),
    ]
    spec = SandboxSpec(
        services=[
            SandboxService(
                name="backend",
                dir="backend",
                runtime="python3.12",
                install=["python", "-c", "print('ready')"],
                start=["python", "main.py"],
                port=8000,
            ),
            SandboxService(
                name="frontend",
                dir="frontend",
                runtime="node22",
                install=["npm", "install", "--no-audit", "--no-fund"],
                start=["node", "server.js"],
                port=3000,
                health="/",
            ),
        ]
    )
    try:
        state = await service.start(db, p, files, spec)
        assert state.status == "healthy", state
        assert all(s.healthy and s.url.startswith("http://127.0.0.1:") for s in state.services)
        assert (await service.status(p.id)).status == "healthy"
        assert "ready" in await service.logs(p.id, "backend")
        # npm wrote its lockfile: the work dir is writable by uid 1000.
        workdir = get_settings().sandbox_root / str(p.id)
        assert (workdir / "frontend" / "package-lock.json").exists()
    finally:
        await service.stop(p.id)
    rc, out = await docker.exec("docker", "ps", "-aq", "--filter", f"label=ampera.project={p.id}")
    assert rc == 0 and out.strip() == ""
    rc, out = await docker.exec(
        "docker", "network", "ls", "-q", "--filter", f"label=ampera.project={p.id}"
    )
    assert rc == 0 and out.strip() == ""
    assert (await service.status(p.id)).status == "stopped"
