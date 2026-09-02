"""
build_graph.py
Builds the evidence graph from data/business_bi.db and persists it to
data/evidence_graph.gpickle, so api_server.py can load it once at startup and
serve per-anomaly subgraphs without rebuilding from scratch every request.

This is invoked automatically by scripts/generate_mock_data.py at seed time;
run it standalone only to rebuild the graph against an already-seeded DB.
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, 'src'))

from analytics.graph_builder import build_graph
from analytics.graph_store import save_graph
from analytics.causal_graph import validate_against_evidence_graph

DB_PATH = os.path.join(BASE_DIR, 'data', 'business_bi.db')
GRAPH_PATH = os.path.join(BASE_DIR, 'data', 'evidence_graph.gpickle')


def main():
    print(f"Building evidence graph from {DB_PATH} ...")
    graph = build_graph(DB_PATH)
    save_graph(graph, GRAPH_PATH)

    kinds = {}
    for _, a in graph.nodes(data=True):
        kinds[a['kind']] = kinds.get(a['kind'], 0) + 1

    print(f"Saved to {GRAPH_PATH}")
    print(f"  {graph.number_of_nodes()} nodes / {graph.number_of_edges()} edges")
    print(f"  PVM mismatches: {graph.graph.get('pvm_mismatches')}")
    for k, v in sorted(kinds.items()):
        print(f"  {k}: {v}")

    # cross-check the hand-authored summary causal graph against evidence-graph co-occurrence
    print("\nSummary causal graph vs evidence-graph co-occurrence:")
    for row in validate_against_evidence_graph(graph):
        u, v = row["edge"]
        mark = "ok  " if row["supported"] else "NONE"
        print(f"  [{mark}] {u} -> {v}  (co-occurrences: {row['cooccurrence_support']})")


if __name__ == '__main__':
    main()
