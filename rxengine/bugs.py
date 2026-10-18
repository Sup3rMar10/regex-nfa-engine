"""Deliberately injected engine bugs, used to prove the fuzzer has teeth.

``injected(name)`` is a context manager yielding a regex factory.  Four bugs are
AST rewrites after parsing (they hit all three matchers, like a parser bug
would); two patch a shared helper or a single matcher.
"""

from __future__ import annotations

from contextlib import contextmanager

from . import semantics
from .api import Regex
from .nodes import Alt, Any, CharSet, Repeat, transform
from .parser import parse
from .pikevm import PikeVM


def _lazy_as_greedy(node):
    return Repeat(node.node, node.min, node.max, True) if isinstance(node, Repeat) else node


def _counted_max_minus_one(node):
    if isinstance(node, Repeat) and node.max is not None and node.max > max(node.min, 1):
        return Repeat(node.node, node.min, node.max - 1, node.greedy)
    return node


def _dot_matches_newline(node):
    return CharSet((), True) if isinstance(node, Any) else node


def _alt_reversed(node):
    return Alt(tuple(reversed(node.items))) if isinstance(node, Alt) else node


def _negation_ignored(node):
    return CharSet(node.items, False) if isinstance(node, CharSet) and node.negated else node


AST_BUGS = {
    "lazy_quantifier_treated_as_greedy": _lazy_as_greedy,
    "counted_repeat_max_off_by_one": _counted_max_minus_one,
    "dot_matches_newline": _dot_matches_newline,
    "alternation_order_reversed": _alt_reversed,
    "negated_class_not_negated": _negation_ignored,
}
OTHER_BUGS = ("dollar_matches_before_any_newline", "pike_no_cut_on_match")
ALL_BUGS = tuple(AST_BUGS) + OTHER_BUGS


def _ast_factory(fn, budget):
    def make(pattern: str) -> Regex:
        parsed = parse(pattern)
        return Regex.from_ast(transform(parsed.ast, fn), parsed.ngroups, pattern, step_budget=budget)
    return make


@contextmanager
def injected(name: str, budget: int = 200_000):
    if name in AST_BUGS:
        yield _ast_factory(AST_BUGS[name], budget)
    elif name == "dollar_matches_before_any_newline":
        orig = semantics.at_eol
        semantics.at_eol = lambda text, i: i == len(text) or text[i] == "\n"
        try:
            yield lambda p: Regex(p, step_budget=budget)
        finally:
            semantics.at_eol = orig
    elif name == "pike_no_cut_on_match":
        PikeVM.cut_on_match = False
        try:
            yield lambda p: Regex(p, step_budget=budget)
        finally:
            PikeVM.cut_on_match = True
    else:
        raise KeyError(name)
