import math
from functools import lru_cache

def analyze_graph(tasks, graph):
    """Performs single-pass structural analysis with strict memoized cycle guards."""
    n = len(tasks)
    if n == 0:
        return 0, 0, 1, 1.0
        
    valid = set(tasks)
    edges = 0
    for t in tasks:
        deps = graph.get(t, []) or []
        if isinstance(deps, (list, tuple)):
            edges += sum(1 for d in deps if d in valid and d != t)
            
    visiting = set()
    
    @lru_cache(maxsize=None)
    def calculate_depth(node):
        if node in visiting:
            return 0
        deps = graph.get(node, []) or []
        if not isinstance(deps, (list, tuple)) or not deps:
            return 1
        visiting.add(node)
        best = 0
        for d in deps:
            if d in valid and d != node:
                sub_depth = calculate_depth(d)
                if sub_depth > best:
                    best = sub_depth
        visiting.discard(node)
        return best + 1

    critical_path = max((calculate_depth(t) for t in tasks), default=1)
    critical_path = max(critical_path, 1)
    parallelism = n / critical_path
    
    return n, edges, critical_path, parallelism
