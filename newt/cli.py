"""Command-line entry point: `newt`."""

from __future__ import annotations

import argparse

from newt import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="newt", description="Every doc, tried by a newcomer."
    )
    parser.add_argument("--version", action="version", version=f"newt {__version__}")
    parser.add_subparsers(dest="command")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return 0
