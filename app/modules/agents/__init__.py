def load_all() -> None:
    """Register agents, artifact kinds and mock fixtures (called by the worker and the API)."""
    from app.modules import diagrams
    from app.modules.agents import architecture, requirements

    requirements.register()
    diagrams.register()
    architecture.register()
