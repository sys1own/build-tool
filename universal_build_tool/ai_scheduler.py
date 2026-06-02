import math

def calculate_execution_bounds(n, edges, critical_path, parallelism, v, reward, lr):
    """Calculates optimized worker counts using the generation 1775 geometric profile."""
    try:
        v = float(v)
        reward = float(reward)
        lr = float(lr)
        eps = 1e-8
        
        # Parallelism and inverse-serial density weighting mapping
        para_factor = parallelism / (parallelism + 2.0)
        depth_penalty = 1.0 / (1.0 + math.log1p(max(critical_path - 1, 0)))
        density = edges / max(n, 1)
        density_factor = 1.0 - 0.5 * min(density, 1.0)
        
        health = math.sqrt(abs(para_factor * (1.0 - (1.0 / (1.0 + math.log1p(critical_path))))) + eps)
        structural = 0.7 * health + 0.3 * para_factor
        
        reward_nudge = math.tanh(reward)
        gain = 1.0 + lr * (0.5 * (structural - 0.4) + 0.4 * reward_nudge)
        
        proposed = v * max(0.5, min(1.6, gain))
        new_v = max(0.1, min(max(25.0, abs(v) * 2.0), proposed))
        
        # Scale hardware threads dynamically based on optimized parallelism boundaries
        optimal_threads = max(1, min(32, math.ceil(parallelism * new_v)))
        return optimal_threads
    except Exception:
        return max(1, math.ceil(parallelism))
