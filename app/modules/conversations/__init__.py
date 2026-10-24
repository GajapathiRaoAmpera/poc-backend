def register() -> None:
    from app.modules.agents.base import register_run_finished
    from app.modules.conversations.hooks import on_run_finished

    register_run_finished(on_run_finished)
