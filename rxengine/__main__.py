"""Command line: ``python -m rxengine demo | search | fullmatch``.

Exit codes (grep-like, fail closed): 0 match / demo clean, 1 no match / demo
found a disagreement, 2 bad pattern, bad arguments or an engine error.
"""

from __future__ import annotations

import argparse
import sys

from .api import ENGINES, Regex
from .errors import RegexError


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rxengine", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="print the measured tables")
    d.add_argument("--quick", action="store_true", help="smaller fuzz sizes (used by CI)")
    for name in ("search", "fullmatch"):
        s = sub.add_parser(name, help=f"{name} TEXT with PATTERN")
        s.add_argument("pattern")
        s.add_argument("text")
        s.add_argument("--engine", choices=ENGINES, default="pike")
        s.add_argument("--budget", type=int, default=1_000_000, help="backtracker step budget")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:  # argparse exits 2 on bad usage already
        return exc.code if isinstance(exc.code, int) else 2
    try:
        if args.cmd == "demo":
            from .demo import main as demo_main
            return demo_main(quick=args.quick)
        rx = Regex(args.pattern, step_budget=args.budget)
        m = getattr(rx, args.cmd)(args.text, args.engine)
    except RegexError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # fail closed: never report "no match" on a crash
        print(f"internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if m is None:
        print("no match")
        return 1
    print(f"match span={m.span()} text={m.group()!r}")
    if args.engine != "dfa":
        for i, g in enumerate(m.groups(), 1):
            print(f"  group {i}: {g!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
