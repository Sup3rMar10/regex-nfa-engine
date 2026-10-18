"""Differential fuzzing against Python's ``re`` (the oracle).

This module and the demo are the only places the ``re`` module is imported.
The oracle is compiled with ``re.ASCII`` because the engine's \\d \\w \\s are
ASCII-only.

Every (regex, text) case is run through search, fullmatch and finditer on each
engine and compared with ``re``: span and capture-group spans for backtrack
and pike, span only for the dfa.
"""

from __future__ import annotations

import random
import re
import warnings
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .api import ENGINES, Regex
from .errors import RecursionLimitExceeded, RegexSyntaxError, StepBudgetExceeded

OPS = ("search", "fullmatch", "finditer")

_LITERALS = ["a", "a", "b", "b", "c", "1", " "]
_ESCAPES = [r"\d", r"\w", r"\s", r"\D", r"\W", r"\S"]
_TEXT_ALPHABET = ["a", "a", "a", "b", "b", "c", "1", " ", "\n", "_"]


# -- generators ----------------------------------------------------------
def gen_regex(rng: random.Random, depth: int = 3) -> str:
    return _alt(rng, depth)


def _alt(rng, depth):
    k = 1 if rng.random() < 0.7 else rng.choice([2, 2, 3])
    return "|".join(_cat(rng, depth) for _ in range(k))


def _cat(rng, depth):
    k = rng.choice([0, 1, 1, 2, 2, 3, 4])
    return "".join(_piece(rng, depth) for _ in range(k))


def _quant(rng) -> str:
    q = rng.choice(["*", "*", "+", "+", "?", "?", "{2}", "{1,2}", "{0,2}", "{2,}", "{,2}", "{0}", "{1,3}"])
    return q + ("?" if rng.random() < 0.3 else "")


def _piece(rng, depth):
    atom, quantifiable = _atom(rng, depth)
    if quantifiable and rng.random() < 0.45:
        return atom + _quant(rng)
    return atom


def _class(rng) -> str:
    items = []
    for _ in range(rng.randint(1, 3)):
        r = rng.random()
        if r < 0.45:
            items.append(rng.choice("abc1 "))
        elif r < 0.75:
            items.append(rng.choice(["a-b", "b-c", "a-c", "0-9", "1-2"]))
        else:
            items.append(rng.choice([r"\d", r"\w", r"\s"]))
    return "[" + ("^" if rng.random() < 0.3 else "") + "".join(items) + "]"


def _atom(rng, depth) -> Tuple[str, bool]:
    r = rng.random()
    if r < 0.40:
        return rng.choice(_LITERALS), True
    if r < 0.48:
        return ".", True
    if r < 0.58:
        return _class(rng), True
    if r < 0.66:
        return rng.choice(_ESCAPES), True
    if r < 0.72:
        return rng.choice(["^", "$"]), False
    if depth > 0:
        inner = _alt(rng, depth - 1)
        return ("(" if rng.random() < 0.6 else "(?:") + inner + ")", True
    return rng.choice(_LITERALS), True


def gen_text(rng: random.Random, max_len: int = 10) -> str:
    return "".join(rng.choice(_TEXT_ALPHABET) for _ in range(rng.randint(0, max_len)))


# -- oracle --------------------------------------------------------------
def oracle_compile(pattern: str):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return re.compile(pattern, re.ASCII)


def _o_match(m):
    if m is None:
        return None
    return (m.span(), tuple(m.span(g) for g in range(1, m.re.groups + 1)))


def _e_match(m, with_groups: bool):
    if m is None:
        return None
    return (m.span(), m.spans() if with_groups else None)


def _strip(res, with_groups: bool):
    if res is None or with_groups:
        return res
    return (res[0], None)


def _spans_only(res):
    """Drop the capture groups from a search/fullmatch/finditer result."""
    if isinstance(res, list):
        return [_strip(x, False) for x in res]
    return _strip(res, False)


def oracle_results(r, text: str):
    return {
        "search": _o_match(r.search(text)),
        "fullmatch": _o_match(r.fullmatch(text)),
        "finditer": [_o_match(m) for m in r.finditer(text)],
    }


def engine_results(rx, engine: str, text: str):
    wg = engine != "dfa"
    return {
        "search": _e_match(rx.search(text, engine), wg),
        "fullmatch": _e_match(rx.fullmatch(text, engine), wg),
        "finditer": [_e_match(m, wg) for m in rx.finditer(text, engine)],
    }


# -- report --------------------------------------------------------------
@dataclass
class FuzzReport:
    seed: int
    patterns: int = 0
    cases: int = 0
    oracle_search_hits: int = 0   # cases where re.search found a match
    oracle_full_hits: int = 0     # cases where re.fullmatch matched
    oracle_group_hits: int = 0    # search matches with at least one capture group set
    counts: Dict[Tuple[str, str], List[int]] = field(default_factory=dict)
    guard_skipped: int = 0        # texts where the backtracker blew its budget (oracle not run)
    cross_agree: int = 0          # of those, pike == dfa on span for search/fullmatch/finditer
    cross_total: int = 0
    compile_mismatches: List[tuple] = field(default_factory=list)
    disagreements: List[dict] = field(default_factory=list)
    n_disagree: Dict[str, int] = field(default_factory=dict)

    def agree(self, engine: str, op: Optional[str] = None) -> int:
        return sum(v[0] for (e, o), v in self.counts.items() if e == engine and (op is None or o == op))

    def total(self, engine: str, op: Optional[str] = None) -> int:
        return sum(v[1] for (e, o), v in self.counts.items() if e == engine and (op is None or o == op))

    @property
    def total_disagreements(self) -> int:
        return sum(self.n_disagree.values()) + len(self.compile_mismatches)


def run_fuzz(seed: int, n_patterns: int, texts_per_pattern: int = 4,
             factory: Optional[Callable[[str], Regex]] = None,
             engines: Tuple[str, ...] = ENGINES, budget: int = 200_000,
             keep: int = 25) -> FuzzReport:
    factory = factory or (lambda p: Regex(p, step_budget=budget))
    rng = random.Random(seed)
    rep = FuzzReport(seed)
    for e in engines:
        rep.n_disagree[e] = 0
        for op in OPS:
            rep.counts[(e, op)] = [0, 0]
    for _ in range(n_patterns):
        pattern = gen_regex(rng)
        texts = [gen_text(rng) for _ in range(texts_per_pattern)]
        try:
            oracle = oracle_compile(pattern)
        except (re.error, OverflowError, RecursionError):
            continue  # the generator should not produce these; nothing to compare
        try:
            rx = factory(pattern)
        except RegexSyntaxError as exc:
            rep.compile_mismatches.append((pattern, str(exc)))
            continue
        rep.patterns += 1
        for text in texts:
            # Guard: ``re`` itself is exponential on some generated patterns (a
            # 9-character input hung it for minutes).  Our backtracker explores in
            # the same order as sre, so if it blows its budget we do not call the
            # oracle at all; pike and dfa are cross-checked against each other.
            try:
                got_bt = engine_results(rx, "backtrack", text)
            except (StepBudgetExceeded, RecursionLimitExceeded):
                rep.guard_skipped += 1
                a, b = engine_results(rx, "pike", text), engine_results(rx, "dfa", text)
                rep.cross_total += 1
                rep.cross_agree += int(all(_spans_only(a[op]) == b[op] for op in OPS))
                continue
            rep.cases += 1
            want = oracle_results(oracle, text)
            if want["search"] is not None:
                rep.oracle_search_hits += 1
                if any(s != (-1, -1) for s in want["search"][1]):
                    rep.oracle_group_hits += 1
            if want["fullmatch"] is not None:
                rep.oracle_full_hits += 1
            for e in engines:
                got = got_bt if e == "backtrack" else engine_results(rx, e, text)
                for op in OPS:
                    w = want[op]
                    if e == "dfa":
                        w = [_strip(x, False) for x in w] if op == "finditer" else _strip(w, False)
                    cell = rep.counts[(e, op)]
                    cell[1] += 1
                    if got[op] == w:
                        cell[0] += 1
                    else:
                        rep.n_disagree[e] += 1
                        if len(rep.disagreements) < keep:
                            rep.disagreements.append(
                                {"engine": e, "op": op, "pattern": pattern, "text": text,
                                 "expected": w, "got": got[op]})
    return rep


# -- malformed-pattern fuzz ---------------------------------------------
_SYNTAX_ALPHABET = list("ab()[]|*+?{},^$\\.-:0123dws") + ["(?:", "{1,2}", "[^", "\\d"]


@dataclass
class SyntaxReport:
    seed: int
    total: int = 0
    both_ok: int = 0
    both_reject: int = 0
    engine_unsupported: int = 0   # re accepts, engine says "unsupported" (expected)
    engine_limit: int = 0         # re accepts, engine says "limit" (expected)
    engine_rejects_valid: List[str] = field(default_factory=list)   # BUG: kind=syntax but re accepts
    engine_accepts_invalid: List[str] = field(default_factory=list)  # BUG: re rejects, engine accepts


def run_syntax_fuzz(seed: int, n: int) -> SyntaxReport:
    rng = random.Random(seed)
    rep = SyntaxReport(seed)
    for _ in range(n):
        pattern = "".join(rng.choice(_SYNTAX_ALPHABET) for _ in range(rng.randint(1, 8)))
        rep.total += 1
        try:
            oracle_compile(pattern)
            re_ok = True
        except (re.error, OverflowError, RecursionError):
            re_ok = False
        try:
            Regex(pattern)
            eng_kind = None
        except RegexSyntaxError as exc:
            eng_kind = exc.kind
        if eng_kind is None and re_ok:
            rep.both_ok += 1
        elif eng_kind is not None and not re_ok:
            rep.both_reject += 1
        elif eng_kind is None and not re_ok:
            rep.engine_accepts_invalid.append(pattern)
        elif eng_kind == "unsupported":
            rep.engine_unsupported += 1
        elif eng_kind == "limit":
            rep.engine_limit += 1
        else:
            rep.engine_rejects_valid.append(pattern)
    return rep
