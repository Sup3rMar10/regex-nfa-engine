"""Recursive-descent parser: pattern string -> AST.

Grammar (subset of Python ``re``):

    alt    := cat ('|' cat)*
    cat    := (atom quant?)*
    atom   := literal | '.' | '^' | '$' | class | escape | '(' alt ')' | '(?:' alt ')'
    quant  := ('*' | '+' | '?' | '{m}' | '{m,}' | '{,n}' | '{m,n}') '?'?
    class  := '[' '^'? item+ ']'   with ranges and \\d \\w \\s \\D \\W \\S

Errors carry the pattern position and a message.  Limits (nesting depth,
pattern length, repeat count, group count) raise errors of kind ``"limit"``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from .errors import RegexSyntaxError
from .nodes import Alt, Any, Assert, Cat, Char, CharSet, Empty, Group, Repeat

MAX_PATTERN_LEN = 10_000
MAX_DEPTH = 100
MAX_REPEAT = 1000
MAX_GROUPS = 200

_CLASS_ESCAPES = "dDwWsS"
_CONTROL_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "f": "\f", "v": "\v", "a": "\a"}
# Escapes that Python accepts but this engine does not implement.
_UNSUPPORTED_LETTERS = "bBAZxuUN"


@dataclass(frozen=True)
class Parsed:
    ast: object
    ngroups: int


def parse(pattern: str) -> Parsed:
    return _Parser(pattern).parse()


class _Parser:
    def __init__(self, pattern: str) -> None:
        self.p = pattern
        self.n = len(pattern)
        self.pos = 0
        self.ngroups = 0

    # -- helpers ---------------------------------------------------------
    def err(self, msg: str, pos: int, kind: str = "syntax") -> RegexSyntaxError:
        return RegexSyntaxError(msg, pos, kind)

    def parse(self) -> Parsed:
        if self.n > MAX_PATTERN_LEN:
            raise self.err(f"pattern longer than {MAX_PATTERN_LEN} characters", 0, "limit")
        node = self.parse_alt(0)
        if self.pos < self.n:  # only ')' can stop parse_alt early
            raise self.err("unbalanced parenthesis", self.pos)
        return Parsed(node, self.ngroups)

    def parse_alt(self, depth: int):
        branches = [self.parse_cat(depth)]
        while self.pos < self.n and self.p[self.pos] == "|":
            self.pos += 1
            branches.append(self.parse_cat(depth))
        return branches[0] if len(branches) == 1 else Alt(tuple(branches))

    def parse_cat(self, depth: int):
        items = []
        while self.pos < self.n and self.p[self.pos] not in "|)":
            atom = self.parse_atom(depth)
            items.append(self.parse_quantifiers(atom))
        if not items:
            return Empty()
        return items[0] if len(items) == 1 else Cat(tuple(items))

    # -- quantifiers -----------------------------------------------------
    def peek_quant(self, at: int) -> Optional[Tuple[int, Optional[int], int]]:
        """(min, max, end_pos) if a quantifier starts at ``at``, else None.

        A '{' that does not form {m}, {m,}, {,n}, {m,n} or {,} is a literal.
        """
        if at >= self.n:
            return None
        c = self.p[at]
        if c == "*":
            return (0, None, at + 1)
        if c == "+":
            return (1, None, at + 1)
        if c == "?":
            return (0, 1, at + 1)
        if c != "{":
            return None
        i = at + 1
        j = i
        while j < self.n and self.p[j].isascii() and self.p[j].isdigit():
            j += 1
        lo_s = self.p[i:j]
        if j < self.n and self.p[j] == ",":
            k = j + 1
            while k < self.n and self.p[k].isascii() and self.p[k].isdigit():
                k += 1
            hi_s = self.p[j + 1 : k]
            if k < self.n and self.p[k] == "}":
                lo = int(lo_s) if lo_s else 0
                hi = int(hi_s) if hi_s else None
                return (lo, hi, k + 1)
            return None
        if lo_s and j < self.n and self.p[j] == "}":
            v = int(lo_s)
            return (v, v, j + 1)
        return None

    def parse_quantifiers(self, atom):
        q = self.peek_quant(self.pos)
        if q is None:
            return atom
        qpos = self.pos
        if isinstance(atom, Assert):
            raise self.err("nothing to repeat", qpos)
        lo, hi, end = q
        if lo > MAX_REPEAT or (hi is not None and hi > MAX_REPEAT):
            raise self.err(f"repeat count above the limit of {MAX_REPEAT}", qpos, "limit")
        if hi is not None and lo > hi:
            raise self.err("min repeat greater than max repeat", qpos)
        self.pos = end
        greedy = True
        if self.pos < self.n and self.p[self.pos] == "?":
            greedy = False
            self.pos += 1
        nxt = self.peek_quant(self.pos)
        if nxt is not None:
            if self.p[self.pos] == "+" and greedy:
                raise self.err("possessive quantifiers are not supported", self.pos, "unsupported")
            raise self.err("multiple repeat", self.pos)
        return Repeat(atom, lo, hi, greedy)

    # -- atoms -----------------------------------------------------------
    def parse_atom(self, depth: int):
        p = self.p
        c = p[self.pos]
        start = self.pos
        if c in "*+?" or (c == "{" and self.peek_quant(self.pos) is not None):
            raise self.err("nothing to repeat", start)
        if c == "(":
            return self.parse_group(depth)
        if c == "[":
            return self.parse_class()
        if c == ".":
            self.pos += 1
            return Any()
        if c == "^":
            self.pos += 1
            return Assert("bol")
        if c == "$":
            self.pos += 1
            return Assert("eol")
        if c == "\\":
            return self.parse_escape()
        self.pos += 1
        return Char(c)

    def parse_group(self, depth: int):
        start = self.pos
        if depth + 1 > MAX_DEPTH:
            raise self.err(f"nesting deeper than {MAX_DEPTH} groups", start, "limit")
        self.pos += 1
        index = None
        if self.pos < self.n and self.p[self.pos] == "?":
            if self.p.startswith("?:", self.pos):
                self.pos += 2
            else:
                raise self.err("unsupported group syntax (only '(?:' is supported)", start, "unsupported")
        else:
            if self.ngroups >= MAX_GROUPS:
                raise self.err(f"more than {MAX_GROUPS} capture groups", start, "limit")
            self.ngroups += 1
            index = self.ngroups
        inner = self.parse_alt(depth + 1)
        if self.pos >= self.n or self.p[self.pos] != ")":
            raise self.err("missing ), unterminated subpattern", start)
        self.pos += 1
        if index is not None:
            return Group(index, inner)
        # '(?:^)*' is legal in re; keep an assertion from looking like a bare anchor.
        return Cat((inner,)) if isinstance(inner, Assert) else inner

    def parse_escape(self):
        """Escape outside a class."""
        start = self.pos
        item = self.read_escape(start, in_class=False)
        if item[0] == "k":
            return CharSet((item,), False)
        return Char(item[1])

    def read_escape(self, start: int, in_class: bool):
        """Consume '\\x'; returns ('c', ch) or ('k', kind)."""
        self.pos += 1  # the backslash
        if self.pos >= self.n:
            raise self.err("bad escape (end of pattern)", start)
        c = self.p[self.pos]
        self.pos += 1
        if c in _CLASS_ESCAPES:
            return ("k", c)
        if c in _CONTROL_ESCAPES:
            return ("c", _CONTROL_ESCAPES[c])
        if c.isascii() and c.isdigit():
            raise self.err("numeric escapes and backreferences are not supported", start, "unsupported")
        if c in _UNSUPPORTED_LETTERS:
            raise self.err(f"escape \\{c} is not supported", start, "unsupported")
        if c.isascii() and c.isalpha():
            raise self.err(f"bad escape \\{c}", start)
        return ("c", c)

    def parse_class(self):
        start = self.pos
        self.pos += 1
        negated = False
        if self.pos < self.n and self.p[self.pos] == "^":
            negated = True
            self.pos += 1
        items = []
        first = True
        while True:
            if self.pos >= self.n:
                raise self.err("unterminated character set", start)
            if self.p[self.pos] == "]" and not first:
                self.pos += 1
                break
            first = False
            item_start = self.pos
            lo = self.read_class_atom(item_start)
            if self.pos + 1 < self.n and self.p[self.pos] == "-" and self.p[self.pos + 1] != "]":
                self.pos += 1
                hi = self.read_class_atom(self.pos)
                if lo[0] == "k" or hi[0] == "k":
                    raise self.err("bad character range", item_start)
                if hi[1] < lo[1]:
                    raise self.err("bad character range (end before start)", item_start)
                items.append(("r", lo[1], hi[1]))
            else:
                items.append(lo)
        return CharSet(tuple(items), negated)

    def read_class_atom(self, at: int):
        if self.p[self.pos] == "\\":
            return self.read_escape(at, in_class=True)
        c = self.p[self.pos]
        self.pos += 1
        return ("c", c)
