"""Curriculum advisor worker using grounded retrieval, SQL tools and optional ReAct reasoning.

Curriculum:
    Module 7 (Vector Stores and Retrieval-Augmented Generation): retrieve curriculum evidence.
    Module 9 (ReAct Agents): inspect tool observations before composing an answer.
    Module 10 (SQL and Data Tools): read learner progress and salary from SQLite.
"""
import json
import os
from typing import Any, Dict, List
from src.fallback import is_unable_to_verify, unable_to_verify
from src.tools.db_queries import get_learner_profile, lookup_learner_profile, query_learner_db
from src.tools.rag_retriever import retrieve_curriculum, search_curriculum

def react_advisor(query: str, learner_id: str, max_tool_steps: int = 5) -> Dict[str, Any]:
    """Run a bounded tool-calling ReAct cycle over curriculum and read-only learner tools.

    Curriculum:
        Module 9 (ReAct Agents): model reasoning alternates between tool calls and observations.
        Module 8 (Tools and Function Calling): tools have explicit schemas and structured outputs.
        Module 10 (SQL and Data Tools): learner data is accessed through guarded read-only SQL tools.

    The tool loop is bounded to avoid unbounded agent execution. If model setup or a tool fails,
    the caller uses the deterministic retrieval/profile path and preserves structured failure results.
    """
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
    from langchain_openai import ChatOpenAI
    tools = [search_curriculum, lookup_learner_profile, query_learner_db]
    tool_map = {tool.name: tool for tool in tools}
    model = ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), temperature=0).bind_tools(tools)
    messages = [SystemMessage(content=(
        "You are the Sprints curriculum and learner advisor. Use tools for every factual claim. "
        "Never invent curriculum content, learner status, scores, salaries, employers or openings. "
        "If a tool says Unable to verify, state that and do not answer from model memory. "
        "Cite curriculum module numbers returned by the retrieval tool. Do not make a placement decision."
    )), HumanMessage(content=f"Learner ID: {learner_id}\nQuestion: {query}")]
    trace: List[Dict[str, Any]] = []
    for step in range(max(1, min(max_tool_steps, 5))):
        message = model.invoke(messages)
        messages.append(message)
        calls = getattr(message, "tool_calls", []) or []
        if not calls:
            return {"status": "OK", "answer": message.content, "tool_trace": trace,
                    "reasoning_steps": step + 1}
        for call in calls:
            name, args = call.get("name"), call.get("args", {})
            if name not in tool_map:
                observation = json.dumps(unable_to_verify("react_advisor", f"unknown tool {name}", "UNKNOWN_TOOL"))
            else:
                try:
                    observation = tool_map[name].invoke(args)
                except Exception as exc:
                    observation = json.dumps(unable_to_verify(name, f"tool execution failed ({type(exc).__name__})"))
            trace.append({"tool": name, "observation": observation})
            messages.append(ToolMessage(content=str(observation), tool_call_id=call.get("id", name)))
    return {"status": "STEP_LIMIT", "answer": "Unable to complete the ReAct cycle within the tool-step limit.",
            "tool_trace": trace, "reasoning_steps": len(trace)}

def reflect_retrieval_query(query: str) -> str:
    """Rephrase a low-recall curriculum query once, without generating an answer.

    Curriculum:
        Module 7 (Vector Stores and Retrieval-Augmented Generation): improve retrieval recall by query reformulation.
        Module 13 (Evaluator-Optimizer and Reflection Patterns): inspect retrieval outcome and perform one bounded refinement.
        Module 3 (LLM APIs and Prompt Engineering): constrain the model to return a search query only.
    """
    from langchain_openai import ChatOpenAI
    response = ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), temperature=0).invoke(
        "Rewrite the following learner question as a concise search query for the Sprints Advanced Agentic AI bootcamp curriculum. "
        "Return only the query, do not answer it: " + query
    )
    candidate = str(response.content).strip().strip('"')
    return candidate if candidate else query

def advisor_worker(state: Dict[str, Any]) -> Dict[str, Any]:
    """Answer a curriculum or learner-profile request only from tool observations.

    Curriculum:
        Module 7 (Vector Stores and Retrieval-Augmented Generation): source passages ground answers.
        Module 9 (ReAct Agents): optional tool-calling reasoning reads observations before responding.
        Module 10 (SQL and Data Tools): learner facts are loaded through parameterized SQLite queries.
    """
    query = state.get("query", "").strip()
    learner_id = state.get("learner_id", "")
    try:
        profile = get_learner_profile(learner_id) if learner_id else {"status": "NOT_REQUESTED"}
        retrieval = retrieve_curriculum(query) if query else {"status": "NO_CONTEXT", "passages": []}
        reflected_query = None
        if query and retrieval.get("status") == "NO_CONTEXT" and os.getenv("OPENAI_API_KEY"):
            try:
                reflected_query = reflect_retrieval_query(query)
                retrieval = retrieve_curriculum(reflected_query)
            except Exception:
                reflected_query = None  # preserve the original retrieval outcome
        if is_unable_to_verify(profile):
            result = profile
        elif retrieval.get("status") == "UNABLE_TO_VERIFY":
            result = retrieval
        else:
            passages = retrieval.get("passages", [])
            result = {
                "status": "OK" if passages or profile.get("status") == "OK" else "NO_CONTEXT",
                "message": "Evidence retrieved. Factual fields below are sourced from tool observations." if passages or profile.get("status") == "OK" else "The curriculum does not cover this question; unable to verify an answer.",
                "curriculum": [{"title": p.get("title"), "modules": p.get("modules"), "source": p.get("source"), "text": p.get("text")} for p in passages],
                "profile": profile.get("learner") if profile.get("status") == "OK" else None,
                "retrieval_elapsed_ms": retrieval.get("elapsed_ms"),
                "within_budget": retrieval.get("within_budget"),
                "reflected_query": reflected_query,
            }
            if os.getenv("OPENAI_API_KEY") and query:
                try:
                    result["react"] = react_advisor(query, learner_id)
                except Exception as exc:
                    result["react"] = {"status": "FALLBACK", "message": f"ReAct unavailable ({type(exc).__name__}); deterministic evidence retained."}
        return {"advisor_result": result, "response": result}
    except Exception as exc:
        result = unable_to_verify("advisor_worker", f"advisor could not verify the request ({type(exc).__name__})", "ADVISOR_FAILURE")
        return {"advisor_result": result, "response": result}
