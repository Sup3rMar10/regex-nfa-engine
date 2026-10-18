"""Recursive backtracking matcher over the AST (continuation-passing style).

Semantics follow CPython's ``re``: leftmost, first alternative that succeeds,
greedy/lazy quantifiers, and the empty-iteration rule (an iteration that
matches empty may complete, but the loop does not run again).

``steps`` counts node entries plus single-character run work.  It is the
quantity that explodes on ReDoS inputs; ``budget`` turns an explosion into a
``StepBudgetExceeded`` error instead of a hang.
"""

from __future__ import annotations

import sys

from . import semantics as sem
from .errors import RecursionLimitExceeded, StepBudgetExceeded
from .nodes import Alt, Any, Assert, Cat, Char, CharSet, Empty, Group, Repeat, SINGLE

RECURSION_LIMIT = 30_000


class Backtracker:
    name = "backtrack"

    def __init__(self, ast, ngroups: int, budget: int = 1_000_000) -> None:
        self.ast = ast
        self.ngroups = ngroups
        self.budget = budget
        self.steps = 0  # cumulative, like the other engines
        self._used = 0
        self._text = ""
        self._n = 0
        self._caps: list = []
        self._dispatch = {
            Empty: self._empty, Char: self._single, Any: self._single, CharSet: self._single,
            Cat: self._cat, Alt: self._alt, Group: self._group, Repeat: self._repeat,
            Assert: self._assert,
        }

    def reset(self) -> None:
        self.steps = 0

    # -- public ----------------------------------------------------------
    def run(self, text: str, start: int, mode: str, must_advance: bool = False):
        """mode: 'search' | 'match' | 'full'.  Returns a caps tuple or None."""
        self._text, self._n, self._used = text, len(text), 0
        n = self._n
        starts = range(start, n + 1) if mode == "search" else (start,)
        old = sys.getrecursionlimit()
        sys.setrecursionlimit(max(old, RECURSION_LIMIT))
        try:
            for s in starts:
                caps = [-1] * (2 * (self.ngroups + 1))
                caps[0] = s
                self._caps = caps

                def final(j, s=s, caps=caps):
                    if mode == "full" and j != n:
                        return False
                    if must_advance and s == start and j == s:
                        return False
                    caps[1] = j
                    return True

                if self._m(self.ast, s, final):
                    return tuple(caps)
        except RecursionError:
            raise RecursionLimitExceeded(
                "recursion depth exceeded (input too long for the recursive matcher)") from None
        finally:
            sys.setrecursionlimit(old)
        return None

    # -- core ------------------------------------------------------------
    def _tick(self, k: int = 1) -> None:
        self.steps += k
        self._used += k
        if self._used > self.budget:
            raise StepBudgetExceeded(self._used, self.budget)

    def _m(self, node, i, k):
        self._used += 1
        self.steps += 1
        if self._used > self.budget:
            raise StepBudgetExceeded(self._used, self.budget)
        return self._dispatch[node.__class__](node, i, k)

    def _empty(self, node, i, k):
        return k(i)

    def _single(self, node, i, k):
        if i < self._n and node.pred(self._text[i]):
            return k(i + 1)
        return False

    def _assert(self, node, i, k):
        ok = sem.at_bol(i) if node.kind == "bol" else sem.at_eol(self._text, i)
        return k(i) if ok else False

    def _cat(self, node, i, k):
        items = node.items
        last = len(items) - 1

        def go(idx, j):
            if idx == last:
                return self._m(items[idx], j, k)
            return self._m(items[idx], j, lambda j2: go(idx + 1, j2))

        return go(0, i)

    def _alt(self, node, i, k):
        for branch in node.items:
            if self._m(branch, i, k):
                return True
        return False

    def _group(self, node, i, k):
        caps = self._caps
        g = node.index

        def after(j):
            old = (caps[2 * g], caps[2 * g + 1])
            caps[2 * g], caps[2 * g + 1] = i, j
            if k(j):
                return True
            caps[2 * g], caps[2 * g + 1] = old
            return False

        return self._m(node.node, i, after)

    def _repeat(self, node, i, k):
        sub, mn, mx = node.node, node.min, node.max
        if isinstance(sub, SINGLE):
            return self._repeat_single(node, sub.pred, i, k)

        def loop(count, j, last):
            self._tick()
            if count < mn:
                return self._m(sub, j, lambda j2: loop(count + 1, j2, last))
            if node.greedy:
                if (mx is None or count < mx) and j != last:
                    if self._m(sub, j, lambda j2: loop(count + 1, j2, j)):
                        return True
                return k(j)
            if k(j):
                return True
            if (mx is not None and count >= mx) or j == last:
                return False
            return self._m(sub, j, lambda j2: loop(count + 1, j2, j))

        return loop(0, i, None)

    def _repeat_single(self, node, pred, i, k):
        text, n, mn, mx = self._text, self._n, node.min, node.max
        limit = n if mx is None else min(n, i + mx)
        if node.greedy:
            j = i
            while j < limit and pred(text[j]):
                j += 1
            self._tick(j - i)
            if j - i < mn:
                return False
            floor = i + mn
            while True:
                self._tick()
                if k(j):
                    return True
                if j == floor:
                    return False
                j -= 1
        j = i
        while j < i + mn:
            if j >= n or not pred(text[j]):
                return False
            j += 1
        self._tick(j - i)
        while True:
            self._tick()
            if k(j):
                return True
            if j >= limit or not pred(text[j]):
                return False
            j += 1
