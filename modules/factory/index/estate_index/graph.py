"""Small graph helpers for the repo and file graphs (pure functions, no I/O)."""
from __future__ import annotations

from collections import defaultdict, deque


def tarjan_scc(nodes: list[str], edges: dict[str, set[str]]) -> list[list[str]]:
    """Strongly connected components (iterative Tarjan); only components with a cycle are returned."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on: set[str] = set()
    stack: list[str] = []
    out: list[list[str]] = []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(sorted(edges.get(root, ()))))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on.add(root)
        while work:
            v, it = work[-1]
            advanced = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter(sorted(edges.get(w, ())))))
                    advanced = True
                    break
                if w in on:
                    low[v] = min(low[v], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                if len(comp) > 1 or v in edges.get(v, ()):
                    out.append(sorted(comp))
    return out


def reverse_edges(edges: dict[str, set[str]]) -> dict[str, set[str]]:
    rev: dict[str, set[str]] = defaultdict(set)
    for a, bs in edges.items():
        for b in bs:
            rev[b].add(a)
    return rev


def closure(start: str, edges: dict[str, set[str]], max_depth: int | None = None) -> dict[str, int]:
    """Nodes reachable from start (excluding start) with their BFS depth."""
    seen: dict[str, int] = {}
    q = deque([(start, 0)])
    while q:
        v, d = q.popleft()
        if max_depth is not None and d >= max_depth:
            continue
        for w in sorted(edges.get(v, ())):
            if w != start and w not in seen:
                seen[w] = d + 1
                q.append((w, d + 1))
    return seen


def topo_order(nodes: list[str], edges: dict[str, set[str]]) -> list[list[str]]:
    """Providers before consumers. `edges[a]` = what a depends on. Cycles come out as one group."""
    nodes = sorted(set(nodes))
    sub = {n: {m for m in edges.get(n, ()) if m in nodes and m != n} for n in nodes}
    comp_of: dict[str, int] = {}
    comps: list[list[str]] = []
    for c in tarjan_scc(nodes, sub):
        for n in c:
            comp_of[n] = len(comps)
        comps.append(c)
    for n in nodes:
        if n not in comp_of:
            comp_of[n] = len(comps)
            comps.append([n])
    deps: dict[int, set[int]] = defaultdict(set)
    for a in nodes:
        for b in sub[a]:
            if comp_of[a] != comp_of[b]:
                deps[comp_of[a]].add(comp_of[b])
    order: list[list[str]] = []
    done: set[int] = set()
    remaining = set(range(len(comps)))
    while remaining:
        ready = sorted((c for c in remaining if deps[c] <= done), key=lambda c: comps[c])
        if not ready:          # cannot happen after condensation; guard anyway
            ready = sorted(remaining, key=lambda c: comps[c])
        for c in ready:
            order.append(sorted(comps[c]))
            done.add(c)
            remaining.discard(c)
    return order


def blast(start: str, all_edges: dict[str, set[str]], runtime_edges: dict[str, set[str]]) -> tuple[dict[str, int], list[str]]:
    """(runtime dependents with depth, test/tooling-only dependents) of `start`.

    Runtime dependents propagate transitively. A test/tooling edge does not propagate: a repo is
    test-only affected when its tests (or tooling) import `start` or one of its runtime dependents.
    """
    run = closure(start, reverse_edges(runtime_edges))
    hit = set(run) | {start}
    test_only = sorted({a for a, bs in all_edges.items() for b in bs
                        if b in hit and b not in runtime_edges.get(a, set())} - hit)
    return run, test_only
