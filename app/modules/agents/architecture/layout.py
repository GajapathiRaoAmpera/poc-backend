"""Deterministic layered placement so generated diagrams are readable before client auto-layout."""

LAYERS = [
    {"client", "cdn"},
    {"frontend", "gateway"},
    {"auth", "backend", "service"},
    {"worker", "queue", "cache"},
    {"database", "storage"},
    {"external_api", "monitoring", "deployment"},
]


def layer_of(node_type: str) -> int:
    return next((i for i, layer in enumerate(LAYERS) if node_type in layer), 2)


def layered_positions(nodes: list[dict]) -> list[dict]:
    counts: dict[int, int] = {}
    out = []
    for n in nodes:
        layer = layer_of(n["type"])
        row = counts.get(layer, 0)
        counts[layer] = row + 1
        out.append({**n, "position": {"x": layer * 280, "y": row * 140}})
    return out
