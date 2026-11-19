"""Command-line entry point for the Sprints end-to-end demonstration.

Curriculum:
    Module 8 (Tools and Function Calling): exposes retrieval, SQLite and grading tools in a workflow.
    Module 11 (LangGraph State Machines): launches the multi-worker orchestration graph.
    Module 14 (Human-in-the-Loop and Safety): asks a human before final placement approval.
"""
import argparse, json, os
from dotenv import load_dotenv
from src.agents.orchestrator import build_graph, learner_config
from src.state import OrchestratorRequest
from src.config import CHROMA_DIR
from src.tools.rag_retriever import build_index, warm_up

def ensure_curriculum_index():
    """Create the persistent ChromaDB index once, using an explicit offline fallback if needed.

    Curriculum:
        Module 6 (Document Ingestion and Embeddings): builds vectors from the supplied curriculum files.
        Module 7 (Vector Stores and Retrieval-Augmented Generation): persists the ChromaDB collection.
    """
    if not CHROMA_DIR.exists() or not any(CHROMA_DIR.iterdir()):
        try:
            build_index()
        except Exception as exc:
            print(f"Semantic embedding setup unavailable ({type(exc).__name__}); using lexical hashing fallback.")
            os.environ["EMBEDDINGS_PROVIDER"] = "hashing"
            build_index()
    warm_up()

def main():
    """Run one advisor, assessment or placement request from the terminal.

    Curriculum:
        Module 11 (LangGraph State Machines): executes the compiled Sprints agent graph.
        Module 12 (Memory and Checkpointing): uses a learner-specific thread identifier.
        Module 14 (Human-in-the-Loop and Safety): resumes a pending placement only after input.
    """
    load_dotenv()
    parser = argparse.ArgumentParser(description="Sprints AI Career & Assessment Orchestrator")
    parser.add_argument("--intent", choices=["advisor", "assessment", "placement"], default="advisor")
    parser.add_argument("--learner", default="SPR-1022")
    parser.add_argument("--query", default="Summarize my bootcamp progress and explain RAG.")
    parser.add_argument("--code", default="")
    parser.add_argument("--tests", default="[]", help="JSON list of test cases")
    args = parser.parse_args()
    request = OrchestratorRequest(learner_id=args.learner, query=args.query, intent=args.intent,
                                  code=args.code, tests=json.loads(args.tests))
    ensure_curriculum_index()
    graph = build_graph()
    config = learner_config(request.learner_id)
    result = graph.invoke(request.model_dump(), config=config)
    snapshot = graph.get_state(config)
    if snapshot and snapshot.next:
        print(json.dumps(snapshot.tasks[0].interrupts[0].value, ensure_ascii=False, indent=2))
        answer = input("Approve this placement? [y/N]: ").strip().lower()
        reviewer = input("Reviewer name: ").strip() or "demo-reviewer"
        result = graph.invoke(__import__("langgraph.types", fromlist=["Command"]).Command(
            resume={"approved": answer == "y", "reviewer": reviewer}), config=config)
    print(json.dumps(result.get("response", result), ensure_ascii=False, indent=2, default=str))

if __name__ == "__main__":
    main()
