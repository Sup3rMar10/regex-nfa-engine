"""`python -m rxengine demo`: every number in the README comes from here.

Fixed seeds.  Step / state counts are deterministic; wall-clock columns are
secondary and noisy (best of 3, or the first run for the cold DFA).
"""

from __future__ import annotations

import random
import time
from typing import List

from .api import Regex
from .bugs import ALL_BUGS, injected
from .errors import StepBudgetExceeded
from .fuzz import OPS, run_fuzz, run_syntax_fuzz

FUZZ_SEED = 20241018
SYNTAX_SEED = 77
BUG_SEED = 31337
REDOS_BUDGET = 300_000

REDOS_PATTERNS = [r"(a+)+$", r"(a|aa)+$", r"(a*)*b"]
REDOS_NS = [4, 8, 12, 16, 20]
LINEAR_NS = [100, 200, 400, 800, 1600]


def fmt(n) -> str:
    return f"{n:,}"


def table(headers: List[str], rows: List[List[str]]) -> str:
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    line = lambda r: "  ".join(str(x).rjust(w) for x, w in zip(r, widths))
    return "\n".join([line(headers), "  ".join("-" * w for w in widths)] + [line(r) for r in rows])


def _steps(rx: Regex, engine: str, text: str):
    rx.reset_stats()
    try:
        rx.search(text, engine)
    except StepBudgetExceeded:
        return None
    return rx.engine(engine).steps


def redos_rows(pattern: str, ns=REDOS_NS, budget=REDOS_BUDGET):
    rx = Regex(pattern, step_budget=budget)
    rows = []
    for n in ns:
        text = "a" * n + "!"
        rows.append((n, _steps(rx, "backtrack", text), _steps(rx, "pike", text), _steps(rx, "dfa", text)))
    return rows


def growth_per_char(rows):
    """Estimated multiplier of backtracker steps per +1 input char (last two finished rows)."""
    done = [(n, s) for n, s, _, _ in rows if s is not None]
    if len(done) < 2:
        return None
    (n1, s1), (n2, s2) = done[-2], done[-1]
    return (s2 / s1) ** (1.0 / (n2 - n1))


def linear_rows(pattern: str, ns=LINEAR_NS):
    rx = Regex(pattern)
    rows = []
    for n in ns:
        text = "a" * n + "!"
        rows.append((n, _steps(rx, "pike", text), _steps(rx, "dfa", text)))
    return rows


def _best_ms(fn, reps=3) -> float:
    best = float("inf")
    for _ in range(reps):
        t = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t)
    return best * 1000


def benign_cases():
    log = "INFO request ok\n" * 150 + "ERROR 42\n"
    prose = "the quick brown fox jumps over the lazy dog " * 60 + "bob@example.com"
    return [
        ("literal prefix in a log", r"ERROR (\d+)", log),
        ("anchored phone-ish", r"^\s*(\d+)-(\d+)$", "  1234-5678"),
        ("anchored key = value line", r"^\s*(\w+)\s*=\s*(\d+)\s*$", "   timeout_seconds   =   30   "),
        ("alternation of words",r"(cat|dog|bird)s?", prose),
        ("word@word.com in prose (many failing starts)",r"(\w+)@(\w+)\.com", prose),
    ]


def benign_rows():
    rows = []
    for label, pat, text in benign_cases():
        rx = Regex(pat, step_budget=50_000_000)
        out = {"label": label, "pattern": pat, "n": len(text)}
        for e in ("backtrack", "pike", "dfa"):
            rx.reset_stats()
            t0 = time.perf_counter()
            m = rx.search(text, e)
            first_ms = (time.perf_counter() - t0) * 1000
            out[e + "_steps"] = rx.engine(e).steps
            out[e + "_ms"] = first_ms if e == "dfa" else _best_ms(lambda: rx.search(text, e))
            out["span"] = m.span() if m else None
        rows.append(out)
    return rows


def dfa_rows(ns=(2, 4, 6, 8, 10, 12, 14), text_len=6000, capped=2000):
    rng = random.Random(5)
    text = "".join(rng.choice("ab") for _ in range(text_len))
    rows = []
    for n in ns:
        pat = f"(a|b)*a(a|b){{{n}}}"
        big = Regex(pat, dfa_max_states=1_000_000)
        big.reset_stats()
        big.fullmatch(text, "dfa")
        d = big.dfa.full
        built, hit = d.states_built, d.hit_rate
        small = Regex(pat, dfa_max_states=capped)
        small.reset_stats()
        small.fullmatch(text, "dfa")
        c = small.dfa.full
        big.reset_stats()
        big.fullmatch(text, "pike")
        rows.append({"n": n, "states": built, "hit": hit,
                     "capped_built": c.states_built, "flushes": c.flushes,
                     "capped_hit": c.hit_rate, "pike_steps": big.pike.steps})
    return rows


def dfa_quadratic_rows(ns=(100, 200, 400, 800)):
    """Honest weakness: DFA span search retries the anchored DFA at every start."""
    rows = []
    for n in ns:
        rx = Regex(r"a*b|c")
        text = "a" * n + "c"
        rx.reset_stats()
        rx.search(text, "dfa")
        d = rx.dfa.steps
        rx.reset_stats()
        rx.search(text, "pike")
        rows.append((n, d, rx.pike.steps))
    return rows


def dfa_cache_benign():
    rx = Regex(r"(\w+)@(\w+)\.com", dfa_max_states=2000)
    text = "the quick brown fox jumps over the lazy dog " * 60 + "bob@example.com"
    rx.reset_stats()
    rx.search(text, "dfa")
    out = {}
    for name in ("any", "first"):
        d = getattr(rx.dfa, name)
        out[name] = (d.states_built, d.hit_rate, d.steps)
    return out


def main(quick: bool = False) -> int:
    n_fuzz = 600 if quick else 5000
    n_bug = 400 if quick else 1000
    n_syntax = 1000 if quick else 5000
    print("rxengine demo (fixed seeds; step/state counts are deterministic)")
    print("=" * 72)

    # 1. differential fuzz
    t0 = time.perf_counter()
    rep = run_fuzz(FUZZ_SEED, n_fuzz, 4)
    print(f"\n[1] Differential fuzz vs Python re (re.ASCII), seed {FUZZ_SEED}")
    print(f"    {fmt(rep.patterns)} random patterns x 4 texts = {fmt(rep.cases)} cases; each case runs "
          "search, fullmatch, finditer")
    print(f"    re.search matched in {fmt(rep.oracle_search_hits)} cases, fullmatch in "
          f"{fmt(rep.oracle_full_hits)}, search with a capture group set in {fmt(rep.oracle_group_hits)}")
    rows = []
    for e in ("backtrack", "pike", "dfa"):
        rows.append([e, "span+groups" if e != "dfa" else "span only"] +
                    [f"{rep.counts[(e, op)][0]}/{rep.counts[(e, op)][1]}" for op in OPS] +
                    [f"{rep.agree(e)}/{rep.total(e)}"])
    print(table(["engine", "compares", *OPS, "total"], rows))
    print(f"    {rep.guard_skipped} further texts were not given to re (the backtracker exceeded its "
          f"budget, and re itself can take minutes on such patterns); pike == dfa on span in "
          f"{rep.cross_agree}/{rep.cross_total} of them")
    print(f"    disagreements: {rep.total_disagreements}; pattern compile mismatches: {len(rep.compile_mismatches)}")
    for d in rep.disagreements[:5]:
        print("    DISAGREE", d)
    srep = run_syntax_fuzz(SYNTAX_SEED, n_syntax)
    print(f"\n    Malformed-pattern fuzz, seed {SYNTAX_SEED}: {fmt(srep.total)} random strings")
    print(f"      both accept {srep.both_ok}, both reject {srep.both_reject}, engine says 'unsupported' "
          f"while re accepts {srep.engine_unsupported} (expected), 'limit' {srep.engine_limit}")
    print(f"      engine rejects a pattern re accepts (kind=syntax): {len(srep.engine_rejects_valid)}; "
          f"engine accepts a pattern re rejects: {len(srep.engine_accepts_invalid)}")

    # 2. injected bugs
    print(f"\n[2] Injected bugs: does the fuzzer notice? ({n_bug} patterns x 4 texts each, seed {BUG_SEED})")
    rows = []
    for name in ALL_BUGS:
        with injected(name) as factory:
            r = run_fuzz(BUG_SEED, n_bug, 4, factory=factory)
        first = r.disagreements[0] if r.disagreements else None
        per = "/".join(f"{r.n_disagree[e]}" for e in ("backtrack", "pike", "dfa"))
        rows.append([name, "DETECTED" if r.total_disagreements else "MISSED", per,
                     f"{first['pattern']!r} on {first['text']!r}" if first else "-"])
    print(table(["bug", "result", "disagreements bt/pike/dfa", "first example"], rows))

    # 3. ReDoS
    print(f"\n[3] ReDoS: search on 'a'*n + '!' (the '!' forces failure). Work units: backtrack = node "
          f"visits, pike = instruction visits + thread steps, dfa = character transitions.")
    print(f"    backtracker budget = {fmt(REDOS_BUDGET)} steps (StepBudgetExceeded beyond that)")
    for pat in REDOS_PATTERNS:
        data = redos_rows(pat)
        print(f"\n    pattern {pat}")
        print(table(["n", "backtrack", "pike", "dfa"],
                    [[n, fmt(b) if b is not None else f">{fmt(REDOS_BUDGET)} (budget)", fmt(p), fmt(d)]
                     for n, b, p, d in data]))
        g = growth_per_char(data)
        if g:
            print(f"    backtracker growth: x{g:.2f} steps per extra character (exponential)")
    print("\n    Linear scaling of pike and dfa on larger n:")
    for pat in REDOS_PATTERNS:
        data = linear_rows(pat)
        print(f"    {pat:12s} " + "  ".join(f"n={n}: pike {fmt(p)} ({p / n:.1f}/char) dfa {fmt(d)}"
                                           for n, p, d in data[-3:]))

    # 4. benign
    print("\n[4] Benign inputs: backtracker vs Pike VM vs lazy DFA (search, one run)")
    rows = []
    for r in benign_rows():
        ratio = r["pike_steps"] / r["backtrack_steps"]
        tratio = r["pike_ms"] / r["backtrack_ms"] if r["backtrack_ms"] else float("nan")
        rows.append([r["label"], r["n"], fmt(r["backtrack_steps"]), fmt(r["pike_steps"]), fmt(r["dfa_steps"]),
                     f"{ratio:.2f}", f"{r['backtrack_ms']:.2f}", f"{r['pike_ms']:.2f}", f"{r['dfa_ms']:.2f}",
                     f"{tratio:.2f}"])
    print(table(["case", "chars", "bt steps", "pike steps", "dfa steps", "pike/bt steps",
                 "bt ms", "pike ms", "dfa ms(cold)", "pike/bt ms"], rows))
    print("    pike/bt ratio > 1: the backtracker did less work (steps) or ran faster (ms); < 1: Pike did.")

    # 5. DFA
    print("\n[5] Lazy DFA: states and cache behaviour")
    cb = dfa_cache_benign()
    print(f"    benign (\\w+)@(\\w+)\\.com: any-DFA {cb['any'][0]} states, hit rate {cb['any'][1]:.4f}; "
          f"first-DFA {cb['first'][0]} states, hit rate {cb['first'][1]:.4f}")
    print("    state explosion: fullmatch of (a|b)*a(a|b){n} on 6000 random a/b chars "
          "(cap 2000 live states, flush when full)")
    rows = [[r["n"], fmt(r["states"]), f"{r['hit']:.3f}", fmt(r["capped_built"]), r["flushes"],
             f"{r['capped_hit']:.3f}", fmt(r["pike_steps"])] for r in dfa_rows()]
    print(table(["n", "dfa states (no cap)", "hit rate", "states built (cap 2000)", "flushes",
                 "hit rate (cap)", "pike steps"], rows))
    print("\n    DFA weakness: finding the SPAN of a match deep in the text retries the anchored DFA at "
          "every start position.")
    print("    search a*b|c on 'a'*n + 'c' (only the last character matches):")
    print(table(["n", "dfa steps", "pike steps"],
                [[n, fmt(d), fmt(p)] for n, d, p in dfa_quadratic_rows()]))
    print(f"\ndemo took {time.perf_counter() - t0:.1f}s (wall-clock, noisy)")
    return 0 if rep.total_disagreements == 0 else 1
