from app.llm.fixtures import register_default
from app.modules.agents.base import register_agent
from app.modules.agents.code import agent
from app.modules.agents.code.mock_fixture import code_changes, code_chat_output, code_output
from app.modules.agents.code.schema import CodeContent
from app.modules.agents.code.validate import manifest_issues
from app.modules.artifacts import registry
from app.modules.artifacts.models import ValidationIssue
from app.modules.orchestration import stages


def _approval_issues(content: CodeContent, ctx) -> list[ValidationIssue]:
    return [
        ValidationIssue(
            severity="error",
            code=f.code or f.tool.upper(),
            message=f"{f.file}{f':{f.line}' if f.line else ''}: {f.message}",
            path=f.file,
        )
        for f in content.report.findings
    ]


def register() -> None:
    registry.register(
        registry.KindSpec(
            kind="code",
            schema_version="1.0",
            model=CodeContent,
            validate_save=lambda c, ctx: manifest_issues(c, allow_generated=True),
            validate_approval=_approval_issues,
        )
    )
    register_agent(agent.NAME, agent.run)
    register_default("code_output", code_output)
    register_default("code_changes", code_changes)
    register_default("code_chat_output", code_chat_output)
    stages.INPUT_CHECKS["code"] = lambda c: False
