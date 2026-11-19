"""Central configuration for the Sprints AI Career & Assessment Orchestrator.

Every threshold and limit that the project brief, the kickoff deck or (later) the discovery call
fixes lives here, so a number that changes is changed in ONE place.

Curriculum:
    Module 15 (Capstone and Career Readiness): the placement-readiness thresholds below decide
    when a learner may be put in front of a human advisor for a placement approval.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CSV_PATH = DATA_DIR / "sprints_learner_db.csv"
OPENINGS_CSV_PATH = DATA_DIR / "partner_openings.csv"
DB_PATH = DATA_DIR / "sprints_learner.db"
CURRICULUM_DIR = DATA_DIR / "curriculum_docs"

# The 15-module Advanced Agentic AI bootcamp. completed_modules in the learner database counts
# these modules (the sample data goes up to 15). Docstrings across the project reference them.
CURRICULUM_MODULES = {
    1: "Python Foundations",
    2: "Python Functions, Testing and Debugging",
    3: "LLM APIs and Prompt Engineering",
    4: "Structured Outputs with Pydantic",
    5: "LangChain Chains and LCEL",
    6: "Document Ingestion and Embeddings",
    7: "Vector Stores and Retrieval-Augmented Generation",
    8: "Tools and Function Calling",
    9: "ReAct Agents",
    10: "SQL and Data Tools",
    11: "LangGraph State Machines",
    12: "Memory and Checkpointing",
    13: "Evaluator-Optimizer and Reflection Patterns",
    14: "Human-in-the-Loop and Safety",
    15: "Capstone and Career Readiness",
}
TOTAL_MODULES = len(CURRICULUM_MODULES)

# Brief, acceptance criteria
MAX_REFINEMENT_ITERATIONS = 3      # evaluator-optimizer loop must halt here
RETRIEVAL_BUDGET_MS = 1000         # ChromaDB retrieval must finish within this
UNABLE_TO_VERIFY_PREFIX = "Unable to verify"

# ASSUMPTIONS (the discovery-call transcript was not available; confirm with the client).
# They match the sample data: every "Ready for Placement" learner has >= 12 modules and >= 85.
PLACEMENT_MIN_MODULES = 12
PLACEMENT_MIN_SCORE = 85
PASSING_SCORE = 70                 # assignment score treated as a pass in summaries

READINESS_STATUSES = ("Ready for Placement", "In Progress", "Needs Review", "Dropped")
OPENING_STATUSES = ("Open", "Filled", "On Hold")
CHROMA_DIR = DATA_DIR / "chroma_db"
