"""Graph algorithms for Task Graph: cycle detection and topological analysis."""

from collections import defaultdict, deque


def detect_cycle(edges: list[tuple[int, int]]) -> list[int] | None:
    """Return the first detected cycle as a list of node IDs, or None if acyclic.

    edges: list of (from_id, depends_on_id) pairs.
    """
    adj: dict[int, list[int]] = defaultdict(list)
    nodes: set[int] = set()
    for u, v in edges:
        adj[u].append(v)
        nodes.add(u)
        nodes.add(v)

    WHITE, GRAY, BLACK = 0, 1, 2  # noqa: N806 — standard DFS colors
    color: dict[int, int] = {n: WHITE for n in nodes}
    parent: dict[int, int | None] = {n: None for n in nodes}

    def dfs(u: int) -> list[int] | None:
        color[u] = GRAY
        for v in adj.get(u, []):
            if color[v] == GRAY:
                # cycle found — reconstruct
                cycle = [v, u]
                while parent[u] is not None and parent[u] != v:
                    u = parent[u]
                    cycle.append(u)
                cycle.append(v)
                cycle.reverse()
                return cycle
            if color[v] == WHITE:
                parent[v] = u
                result = dfs(v)
                if result is not None:
                    return result
        color[u] = BLACK
        return None

    for n in list(nodes):
        if color[n] == WHITE:
            result = dfs(n)
            if result is not None:
                return result
    return None


def topological_order(edges: list[tuple[int, int]]) -> list[int]:
    """Return nodes in topological order.  Raises ValueError on cycle."""
    adj: dict[int, list[int]] = defaultdict(list)
    in_degree: dict[int, int] = defaultdict(int)
    nodes: set[int] = set()

    for u, v in edges:
        adj[u].append(v)
        in_degree[v] += 1
        nodes.add(u)
        nodes.add(v)

    for n in nodes:
        in_degree.setdefault(n, 0)

    queue = deque(n for n in nodes if in_degree[n] == 0)
    result: list[int] = []

    while queue:
        u = queue.popleft()
        result.append(u)
        for v in adj.get(u, []):
            in_degree[v] -= 1
            if in_degree[v] == 0:
                queue.append(v)

    if len(result) != len(nodes):
        raise ValueError("Cycle detected — topological order impossible")
    return result
