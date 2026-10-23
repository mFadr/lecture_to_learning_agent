"""Příkazová řádka – zpracování PDF bez grafického rozhraní.

  python -m lecture_to_learning.cli check
  python -m lecture_to_learning.cli process prednaska.pdf --project "Ekonomie" --export vystup
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .agent.orchestrator import STEP_LABELS, STEP_ORDER, ConsentRequired, Orchestrator, RunOptions
from .config import LLMMode, data_dir, load_settings
from .export.exporters import export_all
from .llm.factory import ProviderRouter
from .logging_setup import setup_logging
from .models import DetailLevel
from .pdf.extractor import PdfError
from .storage import ProjectStore


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="lecture_to_learning.cli", description="Lecture-to-Learning Agent (CLI)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="ověří dostupnost nastaveného modelu")
    pr = sub.add_parser("process", help="zpracuje PDF do nového nebo existujícího projektu")
    pr.add_argument("pdf", nargs="+", type=Path)
    pr.add_argument("--project", default="Projekt z příkazové řádky")
    pr.add_argument("--steps", default=",".join(STEP_ORDER))
    pr.add_argument("--detail", choices=[d.value for d in DetailLevel], default="standard")
    pr.add_argument("--viz", type=int, default=4, help="počet automaticky vizualizovaných příkladů")
    pr.add_argument("--export", type=Path, help="složka pro export výstupů")
    args = ap.parse_args(argv)

    settings = load_settings()
    root = data_dir(settings)
    setup_logging(root / "logs")
    router = ProviderRouter(settings)
    if args.cmd == "check":
        print("Režim:", router.describe())
        problems = router.check(["analyze", "summary"])
        print("\n".join(problems) if problems else "Model je dostupný.")
        return 1 if problems else 0

    if settings.mode != LLMMode.LOCAL and not settings.cloud_consent:
        print("Cloudový režim vyžaduje souhlas s odesíláním dat – udělte ho v Nastavení aplikace.")
        return 2
    store = ProjectStore(root)
    project = next((p for p in store.list_projects() if p.name == args.project), None) or store.create_project(args.project)
    for pdf in args.pdf:
        try:
            store.add_document(project, pdf)
        except PdfError as exc:
            print(f"Přeskakuji {pdf.name}: {exc}")

    def progress(step: str, frac: float, msg: str) -> None:
        print(f"[{STEP_LABELS[step]:<34}] {frac * 100:5.1f} %  {msg}", flush=True)
    try:
        res = Orchestrator(store, project, settings, router, progress=progress).run(
            RunOptions(steps=args.steps.split(","), detail=DetailLevel(args.detail), max_auto_viz=args.viz))
    except ConsentRequired as exc:
        print(exc)
        return 2
    print("\nVýsledek:")
    for s in res.steps:
        print(f"  {STEP_LABELS[s.name]:<36} {s.status.value:<9} {s.message}")
    print(f"\nProjekt: {store.project_dir(project.id)}")
    if args.export:
        args.export.mkdir(parents=True, exist_ok=True)
        files = export_all(project, res, store.project_dir(project.id), args.export, set(settings.export_formats) | {"json"})
        print(f"Export: {files[0].parent if files else args.export} ({len(files)} souborů)")
    return 0 if all(s.status.value in ("done", "skipped") for s in res.steps) else 1


if __name__ == "__main__":
    sys.exit(main())
