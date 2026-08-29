import pickle


def save_graph(graph, path):
    """Persists the evidence graph to disk via pickle -- preserves the exact
    node/edge attribute types (numpy floats, etc.) without lossy coercion."""
    with open(path, 'wb') as f:
        pickle.dump(graph, f)


def load_graph(path):
    """Loads a previously persisted evidence graph from disk."""
    with open(path, 'rb') as f:
        return pickle.load(f)
