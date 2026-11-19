"""Build the Sprints AI Orchestrator deliverables from one source of truth.

Outputs (default folder: docs/):
    technical_report.pdf      written report (reportlab)
    final_presentation.pptx   9-slide stakeholder deck (python-pptx)
    final_presentation.pdf    same deck as PDF (reportlab)

Run from the project root:
    python scripts/build_docs.py            # writes into docs/
    python scripts/build_docs.py --out /tmp/x

Every number below comes from evidence/ (pytest_output*.txt,
benchmark_output.txt, packaging_validation.md). Edit the dictionaries and
lists in this file, never the generated PDFs.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

# --------------------------------------------------------------------------
# Evidence values synchronized from the checked-in evidence/ folder.
# Refresh these values whenever evidence files are regenerated; generated PDFs
# are outputs only and must never be edited as the source of truth.
# --------------------------------------------------------------------------
EV = {
    "tests_314": "111 passed, 0 skipped (Python 3.14)",
    "tests_312": "111 passed in 19.16 s (Python 3.12.16)",
    "cold_ms": "4292.8",
    "mean_ms": "13.5",
    "p50_ms": "12.1",
    "p95_ms": "17.6",
    "max_ms": "26.2",
    "budget_ms": "1000",
    "docker": "Docker daemon unavailable; fail-closed behavior verified (UNABLE_TO_VERIFY, no learner code executed)",
    "learners": "25",
}

TITLE = "Sprints AI Orchestrator"
SUBTITLE = "Multi-agent learner support, grading and placement"

# (slide title, bullets). 9 slides: inside the required 8-10 range.
SLIDES = [
    ("Problem and business context", [
        "Learners wait for answers, feedback and placement decisions.",
        "Advisors spend time on routine questions and manual code review.",
        "Placement needs consistent rules plus a human final decision.",
        "Goal: faster, auditable support without removing human control.",
    ]),
    ("Solution overview", [
        "One LangGraph orchestrator routes each request to a specialist agent.",
        "Advisor: curriculum Q&A (RAG) and learner profile lookup.",
        "Assessor: sandboxed code grading with a capped feedback loop.",
        "Placement: deterministic employer matching, then human approval.",
    ]),
    ("System design", [
        "Single StateGraph with typed state; conditional routing, no linear chain.",
        "Tools: ChromaDB retriever, read-only SQLite queries, Docker code REPL.",
        "Every tool failure returns a structured UNABLE_TO_VERIFY response.",
        "Docstrings map each function to its Sprints curriculum module.",
    ]),
    ("Memory and RAG configuration", [
        "Short-term memory: LangGraph checkpointer, one thread per learner.",
        "Long-term memory: learner profile rows in read-only SQLite.",
        "RAG: ChromaDB, 500-token chunks, fastembed embeddings.",
        f"Warmed retrieval p95 {EV['p95_ms']} ms against a {EV['budget_ms']} ms budget.",
    ]),
    ("Safe code grading", [
        "Designed Docker sandbox: no network, read-only filesystem, non-root user and resource limits.",
        "Docker was unavailable during packaging validation; the fail-closed path was verified and no learner code was executed.",
        "Evaluator-optimizer loop stops after a hard maximum of 3 iterations.",
        "Process-mode unit test: incorrect submission scored 1/2; real Docker execution was not available in the packaging environment.",
    ]),
    ("Human-in-the-loop placement", [
        "Eligibility rules are code, not model judgement (12 modules, score 85).",
        "Eligible learners are matched to live partner openings.",
        "LangGraph interrupt pauses the final decision for a human reviewer.",
        "Non-ready learners are routed to an advisor, no match proposed.",
    ]),
    ("Evidence", []),  # table slide, built separately
    ("Limitations and risks", [
        "Placement thresholds (12 modules, 85) are assumptions from the sample data.",
        f"Demo data is synthetic: {EV['learners']} learners, fixture openings and curriculum.",
        "In-memory checkpointer suits the demo; production needs a persistent store.",
        "Latency figures are environment-dependent and must be re-measured.",
    ]),
    ("Next steps", [
        "Confirm placement rules with the client discovery call recording.",
        "Replace the in-memory checkpointer with a persistent one.",
        "Add learner-facing channel (chat or LMS) in front of the orchestrator.",
        "Pilot with one cohort and track advisor time saved.",
    ]),
]

EVIDENCE_ROWS = [
    ["Check", "Result"],
    ["Full test suite, Python 3.14", EV["tests_314"]],
    ["Full test suite, Python 3.12", EV["tests_312"]],
    ["Retrieval benchmark (n=20, warmed)",
     f"p50 {EV['p50_ms']} ms, p95 {EV['p95_ms']} ms, max {EV['max_ms']} ms: PASS"],
    ["Cold start (reported separately)", f"{EV['cold_ms']} ms"],
    ["Docker runtime", EV["docker"]],
    ["Fail-closed, Docker down", "UNABLE_TO_VERIFY, no learner code executed"],
    ["Non-ready learner SPR-1002", "NOT_READY, routed to human advisor"],
    ["HITL placement", "Interrupt raised, human APPROVED recorded"],
    ["SQLite", f"{EV['learners']} learner rows, integrity_check ok"],
]

REPORT = [
    ("1. Problem and scope", [
        "Sprints learners need quick curriculum answers, objective code "
        "feedback and consistent placement decisions. Advisors are the "
        "bottleneck. This project delivers a multi-agent system that "
        "handles routine requests automatically and escalates the final "
        "placement decision to a human.",
    ]),
    ("2. Agentic architecture", [
        "The system uses one LangGraph StateGraph with typed state. A router "
        "node inspects the request and sends it to the advisor, assessor or "
        "placement worker through conditional edges. LangGraph was chosen "
        "over linear chains because routing depends on the content of each "
        "request and because it provides checkpointing and interrupts.",
        "Tools are isolated in src/tools: a ChromaDB retriever, a read-only "
        "SQLite query layer with a SELECT guard, and a Docker-based Python "
        "REPL. Each tool failure is converted by src/fallback.py into a "
        "structured UNABLE_TO_VERIFY response.",
    ]),
    ("3. Memory strategy", [
        "Short-term memory is the LangGraph checkpointer, keyed by a "
        "per-learner thread, so a conversation and a paused placement "
        "decision can be resumed. Long-term memory is the learner database "
        "(SQLite, opened read-only) holding progress and scores.",
        "The in-memory checkpointer is reproducible for the demo. A "
        "persistent checkpointer is required for production.",
    ]),
    ("4. RAG configuration", [
        "Curriculum documents are split into 500-token chunks and embedded "
        "with fastembed into ChromaDB. The retriever returns context for "
        "the advisor agent. The acceptance criterion is retrieval within "
        "1000 ms; cold start is reported separately from warmed queries.",
    ]),
    ("5. Secure grading and evaluator-optimizer loop", [
        "Learner code is executed in a Docker container with no network "
        "and a read-only filesystem, under a timeout. If Docker is not "
        "available the grader fails closed and reports UNABLE_TO_VERIFY "
        "rather than running code on the host.",
        "The evaluator-optimizer loop (assessor agent) refines feedback "
        "against executable test results and stops after a maximum of 3 "
        "iterations to bound token use and latency.",
    ]),
    ("6. Placement and human approval", [
        "Eligibility and employer matching are deterministic code over "
        "live database rows. When a learner is ready, a LangGraph interrupt "
        "pauses the graph so a human can approve or reject. Non-ready "
        "learners are routed to an advisor and no match is proposed.",
    ]),
    ("7. Results", [
        f"Test suite: {EV['tests_314']}; {EV['tests_312']}.",
        f"Retrieval benchmark (n=20 warmed queries): cold start "
        f"{EV['cold_ms']} ms; mean {EV['mean_ms']} ms; p50 {EV['p50_ms']} ms; "
        f"p95 {EV['p95_ms']} ms; max {EV['max_ms']} ms; 0 over budget "
        f"(budget {EV['budget_ms']} ms).",
        f"Docker runtime: {EV['docker']}. Process-mode unit tests include an "
        "incorrect submission scored 1/2. Real Docker grading remains to be "
        "run on a Docker-enabled machine.",
        "The 3–5 minute end-to-end walkthrough is documented in docs/demo_script.md; "
        "recording and full LangGraph runtime verification remain outstanding.",
    ]),
    ("8. Defect found and fixed during verification", [
        "src/main.py imported CHROMA_DIR from src.config, but the constant "
        "was defined only in src/tools/rag_retriever.py, so the CLI and "
        "demo failed with ImportError. Unit tests did not import main.py. "
        "CHROMA_DIR is now defined once in src/config.py.",
    ]),
    ("9. Trade-offs and limitations", [
        "Placement thresholds (12 modules, score 85) are inferred from the "
        "sample data because the discovery call recording was not "
        "available at build time; they must be confirmed with the client.",
        f"The learner data is a synthetic demo set ({EV['learners']} rows). "
        "Latency results depend on the machine. Verification ran on "
        "Python 3.12.16 and 3.14 under Ubuntu on WSL2. Full LangGraph runtime, "
        "ChromaDB indexing/search and real Docker grading were not verified "
        "in the packaging environment.",
    ]),
]

# --------------------------------------------------------------------------
# PPTX
# --------------------------------------------------------------------------
NAVY = RGBColor(0x14, 0x2B, 0x4D)
TEAL = RGBColor(0x1F, 0x8A, 0x8A)
INK = RGBColor(0x22, 0x2B, 0x38)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)


def _text(slide, x, y, w, h, text, size, color, bold=False):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    run = p.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    return box


def build_pptx(path: Path) -> None:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]

    # Title slide
    s = prs.slides.add_slide(blank)
    s.background.fill.solid()
    s.background.fill.fore_color.rgb = NAVY
    _text(s, 0.9, 2.4, 11.5, 1.2, TITLE, 48, WHITE, True)
    _text(s, 0.9, 3.7, 11.5, 0.8, SUBTITLE, 24, RGBColor(0xBF, 0xE3, 0xE3))
    _text(s, 0.9, 6.3, 11.5, 0.5, "Final presentation for Sprints stakeholders",
          16, RGBColor(0xBF, 0xE3, 0xE3))

    for title, bullets in SLIDES:
        s = prs.slides.add_slide(blank)
        bar = s.shapes.add_shape(1, 0, 0, prs.slide_width, Inches(1.2))
        bar.fill.solid()
        bar.fill.fore_color.rgb = NAVY
        bar.line.fill.background()
        _text(s, 0.7, 0.2, 12, 0.8, title, 32, WHITE, True)

        if title == "Evidence":
            rows = EVIDENCE_ROWS
            tbl = s.shapes.add_table(len(rows), 2, Inches(0.7), Inches(1.6),
                                     Inches(11.9), Inches(0.5 * len(rows))).table
            tbl.columns[0].width = Inches(4.4)
            tbl.columns[1].width = Inches(7.5)
            for r, row in enumerate(rows):
                for c, val in enumerate(row):
                    cell = tbl.cell(r, c)
                    cell.text = val
                    para = cell.text_frame.paragraphs[0]
                    para.runs[0].font.size = Pt(15)
                    para.runs[0].font.bold = r == 0
                    para.runs[0].font.color.rgb = WHITE if r == 0 else INK
                    cell.fill.solid()
                    cell.fill.fore_color.rgb = TEAL if r == 0 else (
                        RGBColor(0xF1, 0xF5, 0xF7) if r % 2 else WHITE)
            continue

        box = s.shapes.add_textbox(Inches(0.9), Inches(1.7), Inches(11.5), Inches(5))
        tf = box.text_frame
        tf.word_wrap = True
        for i, b in enumerate(bullets):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.space_after = Pt(18)
            run = p.add_run()
            run.text = "\u2022  " + b
            run.font.size = Pt(24)
            run.font.color.rgb = INK
    prs.save(path)


# --------------------------------------------------------------------------
# Deck as PDF (same content, landscape pages)
# --------------------------------------------------------------------------
def _wrap(c, text, font, size, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if c.stringWidth(t, font, size) <= max_w:
            cur = t
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def build_deck_pdf(path: Path) -> None:
    W, H = landscape(A4)
    c = canvas.Canvas(str(path), pagesize=(W, H))
    c.setTitle(f"{TITLE} - Final presentation")
    navy = colors.HexColor("#142B4D")
    teal = colors.HexColor("#1F8A8A")

    c.setFillColor(navy)
    c.rect(0, 0, W, H, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 38)
    c.drawString(2 * cm, H / 2 + 20, TITLE)
    c.setFillColor(colors.HexColor("#BFE3E3"))
    c.setFont("Helvetica", 18)
    c.drawString(2 * cm, H / 2 - 20, SUBTITLE)
    c.setFont("Helvetica", 12)
    c.drawString(2 * cm, 2 * cm, "Final presentation for Sprints stakeholders")
    c.showPage()

    for title, bullets in SLIDES:
        c.setFillColor(navy)
        c.rect(0, H - 3 * cm, W, 3 * cm, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 26)
        c.drawString(2 * cm, H - 1.9 * cm, title)
        y = H - 5 * cm
        if title == "Evidence":
            data = [[Paragraph(v, ParagraphStyle("t", fontSize=11, leading=14,
                               textColor=colors.white if r == 0 else colors.black))
                     for v in row] for r, row in enumerate(EVIDENCE_ROWS)]
            t = Table(data, colWidths=[8 * cm, W - 12 * cm])
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), teal),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                 [colors.HexColor("#F1F5F7"), colors.white]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            _, th = t.wrapOn(c, W - 4 * cm, H)
            t.drawOn(c, 2 * cm, y - th)
        else:
            c.setFillColor(colors.HexColor("#222B38"))
            c.setFont("Helvetica", 17)
            for b in bullets:
                lines = _wrap(c, b, "Helvetica", 17, W - 5 * cm)
                c.drawString(2 * cm, y, "\u2022")
                for ln in lines:
                    c.drawString(2.8 * cm, y, ln)
                    y -= 22
                y -= 16
        c.showPage()
    c.save()


# --------------------------------------------------------------------------
# Technical report PDF
# --------------------------------------------------------------------------
def build_report(path: Path) -> None:
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=24, spaceAfter=6)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=colors.HexColor("#142B4D"),
                        spaceBefore=14, spaceAfter=6)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=10.5, leading=15,
                          spaceAfter=6)
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=2.2 * cm,
                            rightMargin=2.2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
                            title=f"{TITLE} - Technical report", author="Sprints AI Orchestrator team")
    story = [Paragraph(TITLE, h1), Paragraph("Technical report: agentic architecture, "
             "memory strategy and RAG configuration", ss["Heading3"]), Spacer(1, 8)]
    for heading, paras in REPORT:
        story.append(Paragraph(heading, h2))
        for p in paras:
            story.append(Paragraph(p, body))
    story += [Paragraph("Appendix: evidence summary", h2)]
    data = [[Paragraph(v, body) for v in row] for row in EVIDENCE_ROWS]
    t = Table(data, colWidths=[6 * cm, 10.4 * cm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE9EA")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9AA7B0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(t)
    doc.build(story)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs", help="output folder")
    out = Path(ap.parse_args().out)
    out.mkdir(parents=True, exist_ok=True)
    build_report(out / "technical_report.pdf")
    build_pptx(out / "final_presentation.pptx")
    build_deck_pdf(out / "final_presentation.pdf")
    n = len(SLIDES) + 1
    print(f"Wrote 3 files to {out}/ (deck has {n} slides; required 8-10)")


if __name__ == "__main__":
    main()
