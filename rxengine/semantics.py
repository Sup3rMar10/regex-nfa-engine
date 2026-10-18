"""Character-level semantics shared by all three matchers.

Deliberate difference from ``re``: ``\\d \\w \\s`` are ASCII-only here (Python's
default for ``str`` patterns is Unicode).  The oracle in the tests and the demo
is compiled with ``re.ASCII`` so the comparison is like for like.
"""

from __future__ import annotations

import string

DIGITS = frozenset("0123456789")
WORD = frozenset(string.ascii_letters + string.digits + "_")
SPACE = frozenset(" \t\n\r\f\v")

_CLASS_SETS = {"d": DIGITS, "w": WORD, "s": SPACE}


def at_bol(i: int) -> bool:
    """``^`` (no MULTILINE): only at the very start of the text."""
    return i == 0


def at_eol(text: str, i: int) -> bool:
    """``$`` (no MULTILINE): at the end, or just before one final newline."""
    n = len(text)
    return i == n or (i == n - 1 and text[i] == "\n")


def class_matches(kind: str, ch: str) -> bool:
    """``kind`` is one of d D w W s S."""
    members = _CLASS_SETS[kind.lower()]
    return (ch in members) == kind.islower()


def any_pred(ch: str) -> bool:
    """``.`` without DOTALL matches everything except a newline."""
    return ch != "\n"


def build_set_predicate(items: tuple, negated: bool):
    """items: ('c', ch) | ('r', lo, hi) | ('k', kind)."""
    chars = frozenset(it[1] for it in items if it[0] == "c")
    ranges = tuple((it[1], it[2]) for it in items if it[0] == "r")
    kinds = tuple(it[1] for it in items if it[0] == "k")

    def pred(ch: str) -> bool:
        hit = ch in chars
        if not hit:
            for lo, hi in ranges:
                if lo <= ch <= hi:
                    hit = True
                    break
        if not hit:
            for k in kinds:
                if class_matches(k, ch):
                    hit = True
                    break
        return hit != negated

    return pred
