"""Command line entry point: python -m enex2mela input/*.enex --out out/"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import mela
from .convert import Options, bucket, categories, convert, dedupe_key, load_category_map
from .enex import iter_notes
from .llm import DEFAULT_MODEL, LLMExtractor


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="enex2mela", description="Convert Evernote .enex exports to Mela recipes.")
    p.add_argument("inputs", nargs="+", type=Path, help=".enex files (one per notebook)")
    p.add_argument("--out", type=Path, default=Path("out"), help="output directory (default: out/)")
    p.add_argument("--triage-only", action="store_true", help="classify notes and write the report, convert nothing")
    p.add_argument("--no-llm", action="store_true", help="don't call Claude; photos and messy notes get flagged instead")
    p.add_argument("--no-fetch", action="store_true", help="don't re-fetch web clips from their source URL")
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"Claude model (default: {DEFAULT_MODEL})")
    p.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--categories", type=Path, help="JSON file mapping tag/notebook names to Mela categories")
    p.add_argument("--no-notebook-category", action="store_true", help="don't add the notebook name as a category")
    p.add_argument("--max-images", type=int, default=2, help="images to keep per recipe (default: 2)")
    p.add_argument("--workers", type=int, default=4, help="notes converted in parallel (default: 4)")
    p.add_argument("--limit", type=int, help="only process the first N notes (for test runs)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for path in args.inputs:
        if not path.exists():
            print(f"not found: {path}", file=sys.stderr)
            return 1

    notes = [n for path in args.inputs for n in iter_notes(path)]
    if args.limit:
        notes = notes[: args.limit]
    args.out.mkdir(parents=True, exist_ok=True)
    report_path = args.out / "report.csv"

    if args.triage_only:
        counts = Counter()
        with report_path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["notebook", "title", "bucket", "words", "images", "pdfs", "source_url", "tags"])
            for n in notes:
                b = bucket(n)
                counts[b] += 1
                w.writerow([n.notebook, n.title, b, len(n.text().split()), len(n.images), len(n.pdfs),
                            n.source_url or "", "; ".join(n.tags)])
        print(f"{len(notes)} notes: " + ", ".join(f"{k} {v}" for k, v in counts.most_common()))
        print(f"report: {report_path}")
        return 0

    opts = Options(
        fetch_urls=not args.no_fetch,
        llm=None if args.no_llm else LLMExtractor(model=args.model, effort=args.effort,
                                                  cache_dir=args.out / ".cache" / "llm"),
    )
    mapping = load_category_map(args.categories)
    recipes_dir = args.out / "recipes"

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = []
        for i, r in enumerate(pool.map(lambda n: convert(n, opts), notes), 1):
            results.append(r)
            print(f"[{i}/{len(notes)}] {r.status:8} {r.method or r.bucket:10} {r.note.title}", file=sys.stderr)

    seen: dict[tuple[str, str], str] = {}
    written: list[Path] = []
    with report_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["notebook", "title", "bucket", "method", "status", "warnings", "file"])
        for r in results:
            path = ""
            if r.recipe and r.status != "skipped":
                key = dedupe_key(r.recipe)
                if key in seen:
                    r.status = "skipped"
                    r.warnings.append(f"duplicate of '{seen[key]}'")
                else:
                    seen[key] = r.note.title
                    created = r.note.created.isoformat() if r.note.created else ""
                    data = mela.to_mela(
                        r.recipe,
                        recipe_id=mela.stable_id(r.note.notebook, r.note.title, created),
                        categories=categories(r.note, mapping, not args.no_notebook_category),
                        images=r.images,
                        max_images=args.max_images,
                    )
                    p = mela.write_recipe(data, recipes_dir)
                    written.append(p)
                    path = str(p)
            w.writerow([r.note.notebook, r.note.title, r.bucket, r.method, r.status, " | ".join(r.warnings), path])

    archive = mela.bundle(written, args.out / "import.melarecipes") if written else None
    counts = Counter(r.status for r in results)
    print(f"{len(results)} notes: " + ", ".join(f"{k} {v}" for k, v in counts.most_common()))
    if archive:
        print(f"import file: {archive}")
    print(f"report: {report_path}")
    return 0
