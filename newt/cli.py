"""Command-line entry point: `newt`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from newt import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="newt", description="Every doc, tried by a newcomer."
    )
    parser.add_argument("--version", action="version", version=f"newt {__version__}")
    sub = parser.add_subparsers(dest="command")

    page = sub.add_parser("page", help="Follow one docs page in a fresh sandbox and print its verdict.")
    page.add_argument("path", type=Path, help="Markdown file of the docs page.")
    page.add_argument("--no-gate", action="store_true", help="Fix-everything baseline: skip the docs-fault gate.")
    page.add_argument("--out", type=Path, help="Also write the full result JSON here.")
    page.add_argument("--max-turns", type=int, default=60)
    return parser


def cmd_page(args: argparse.Namespace) -> int:
    from rich.console import Console

    from newt import tracing
    from newt.llm import DailyLimitReached
    from newt.worker import Worker

    console = Console(stderr=True)
    if not args.path.is_file():
        console.print(f"[red]No such file:[/red] {args.path}")
        return 2
    traced = tracing.init_tracing(workflow_name="newt-page")
    console.print(f"[bold]newt[/bold] following {args.path} with {tracing.model_name()} (tracing {'on' if traced else 'off: no NEATLOGS_API_KEY'})")
    worker = Worker(
        args.path.read_text(encoding="utf-8"),
        page_name=args.path.name,
        gate=not args.no_gate,
        max_turns=args.max_turns,
        on_event=lambda msg: console.print(f"  [dim]{msg}[/dim]"),
    )
    try:
        result = worker.run()
    except DailyLimitReached as e:
        console.print(f"[red]{e}[/red]")
        console.print("No verdict: the run stopped before the page was checked.")
        return 3
    finally:
        tracing.flush()

    v, g = result.verdict, result.gate
    color = {"pass": "green", "docs_broken": "red"}.get(v.status, "yellow")
    console.print(f"\n[bold {color}]{v.status}[/bold {color}]  {v.summary}")
    if v.model_status:
        console.print(f"  model said {v.model_status}; gate changed it to {v.status}")
    if g.action != "none":
        console.print(f"  gate: {g.action}")
        for reason in g.reasons:
            console.print(f"    - {reason}")
    console.print(
        f"  {result.turns} turns, {result.input_tokens} in / {result.output_tokens} out tokens "
        f"({result.cache_read_tokens} cached), {result.duration_s}s"
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(result.to_json(), encoding="utf-8")
        console.print(f"  full result: {args.out}")
    sys.stdout.write(result.to_json() + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "page":
        return cmd_page(args)
    return 0
