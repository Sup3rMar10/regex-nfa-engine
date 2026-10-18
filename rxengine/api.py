"""Public API: compile a pattern once, run it on any of the three engines."""

from __future__ import annotations

from typing import Iterator, List, Optional, Tuple

from .backtrack import Backtracker
from .dfa import DFAEngine
from .errors import CapturesUnavailable, RegexError
from .parser import parse
from .pikevm import PikeVM
from .program import Program, compile_program

ENGINES = ("backtrack", "pike", "dfa")


class Match:
    """Result of a successful match.  Mirrors the useful part of ``re.Match``."""

    def __init__(self, text: str, caps: tuple, ngroups: int, has_captures: bool) -> None:
        self.string = text
        self._caps = caps
        self.ngroups = ngroups
        self.has_captures = has_captures

    def span(self, g: int = 0) -> Tuple[int, int]:
        if g < 0 or g > self.ngroups:
            raise IndexError("no such group")
        if g > 0 and not self.has_captures:
            raise CapturesUnavailable("the dfa engine reports spans only")
        return (self._caps[2 * g], self._caps[2 * g + 1])

    def start(self, g: int = 0) -> int:
        return self.span(g)[0]

    def end(self, g: int = 0) -> int:
        return self.span(g)[1]

    def group(self, g: int = 0) -> Optional[str]:
        s, e = self.span(g)
        return None if s < 0 else self.string[s:e]

    def groups(self) -> Tuple[Optional[str], ...]:
        return tuple(self.group(g) for g in range(1, self.ngroups + 1))

    def spans(self) -> Tuple[Tuple[int, int], ...]:
        return tuple(self.span(g) for g in range(1, self.ngroups + 1))

    def __repr__(self) -> str:
        return f"<Match span={self.span()} match={self.group()!r}>"


class Regex:
    def __init__(self, pattern: str, *, step_budget: int = 1_000_000,
                 dfa_max_states: int = 2000) -> None:
        parsed = parse(pattern)
        self._init(pattern, parsed.ast, parsed.ngroups, step_budget, dfa_max_states)

    @classmethod
    def from_ast(cls, ast, ngroups: int, pattern: str = "<ast>", *, step_budget: int = 1_000_000,
                 dfa_max_states: int = 2000) -> "Regex":
        """Build from an AST (used by the injected-bug tests)."""
        self = cls.__new__(cls)
        self._init(pattern, ast, ngroups, step_budget, dfa_max_states)
        return self

    def _init(self, pattern, ast, ngroups, step_budget, dfa_max_states) -> None:
        self.pattern = pattern
        self.ast = ast
        self.ngroups = ngroups
        self.program: Program = compile_program(ast, ngroups)
        self.backtracker = Backtracker(ast, ngroups, step_budget)
        self.pike = PikeVM(self.program)
        self.dfa = DFAEngine(self.program, dfa_max_states)
        self._engines = {"backtrack": self.backtracker, "pike": self.pike, "dfa": self.dfa}

    def engine(self, name: str):
        try:
            return self._engines[name]
        except KeyError:
            raise ValueError(f"unknown engine {name!r}; choose from {ENGINES}") from None

    def reset_stats(self) -> None:
        for e in self._engines.values():
            e.reset()

    # -- matching --------------------------------------------------------
    def _wrap(self, engine_name: str, text: str, res) -> Optional[Match]:
        if res is None:
            return None
        if engine_name == "dfa":
            s, e = res
            return Match(text, (s, e), self.ngroups, False)
        return Match(text, res, self.ngroups, True)

    def _run(self, text: str, pos: int, mode: str, engine: str, must_advance: bool = False):
        eng = self.engine(engine)
        pos = max(0, pos)
        if pos > len(text):
            return None
        return self._wrap(engine, text, eng.run(text, pos, mode, must_advance))

    def search(self, text: str, engine: str = "pike", pos: int = 0) -> Optional[Match]:
        return self._run(text, pos, "search", engine)

    def match(self, text: str, engine: str = "pike", pos: int = 0) -> Optional[Match]:
        return self._run(text, pos, "match", engine)

    def fullmatch(self, text: str, engine: str = "pike", pos: int = 0) -> Optional[Match]:
        return self._run(text, pos, "full", engine)

    def finditer(self, text: str, engine: str = "pike") -> Iterator[Match]:
        """Same stepping rules as ``re.finditer`` (Python 3.7+): after an empty
        match the next match may not be empty at the same position."""
        pos = 0
        must_advance = False
        produced = 0
        while pos <= len(text):
            m = self._run(text, pos, "search", engine, must_advance)
            if m is None:
                return
            produced += 1
            if produced > 2 * len(text) + 2:  # at most one empty + one non-empty match per position
                raise RegexError("internal error: finditer is not advancing")
            yield m
            s, e = m.span()
            pos = e
            must_advance = e == s

    def findall_spans(self, text: str, engine: str = "pike") -> List[Tuple[int, int]]:
        return [m.span() for m in self.finditer(text, engine)]


def compile(pattern: str, **kw) -> Regex:  # noqa: A001 - mirrors re.compile
    return Regex(pattern, **kw)


def search(pattern: str, text: str, engine: str = "pike") -> Optional[Match]:
    return Regex(pattern).search(text, engine)


def fullmatch(pattern: str, text: str, engine: str = "pike") -> Optional[Match]:
    return Regex(pattern).fullmatch(text, engine)


def finditer(pattern: str, text: str, engine: str = "pike") -> Iterator[Match]:
    return Regex(pattern).finditer(text, engine)
