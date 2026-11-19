"""Run the warmed-up ChromaDB latency acceptance benchmark.

Curriculum:
    Module 7 (Vector Stores and Retrieval-Augmented Generation): measure retrieval against 1000 ms.
    Module 13 (Evaluator-Optimizer and Reflection Patterns): report p95 and maximum evidence.
"""
import json
from src.tools.rag_retriever import benchmark_retrieval

def main():
    """Print cold-start and warmed retrieval latency metrics as machine-readable JSON.

    Curriculum:
        Module 7 (Vector Stores and Retrieval-Augmented Generation): validate the retrieval budget.
        Module 13 (Evaluator-Optimizer and Reflection Patterns): report p95 and maximum timings.
    """
    queries = ["What is LangGraph state and conditional routing?", "How are Python assignments graded?",
              "What are the placement approval rules?", "Explain ChromaDB and embeddings."]
    print(json.dumps(benchmark_retrieval(queries, repeats=5), indent=2))

if __name__ == "__main__":
    main()
