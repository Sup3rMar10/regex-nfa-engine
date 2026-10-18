"""Exceptions raised by the engine."""

from __future__ import annotations


class RegexError(Exception):
    """Base class for every error this package raises on purpose."""


class RegexSyntaxError(RegexError):
    """A pattern could not be compiled.

    ``kind`` is one of:
      * ``"syntax"``      the pattern is malformed (Python's ``re`` rejects it too)
      * ``"unsupported"`` valid in ``re`` but outside this engine's subset
      * ``"limit"``       a nesting / size / repeat limit of this engine was hit
    """

    def __init__(self, msg: str, pos: int, kind: str = "syntax") -> None:
        super().__init__(f"{msg} at position {pos}")
        self.msg = msg
        self.pos = pos
        self.kind = kind


class StepBudgetExceeded(RegexError):
    """The backtracking matcher used more steps than its budget allows."""

    def __init__(self, steps: int, budget: int) -> None:
        super().__init__(f"step budget exceeded: {steps} steps > budget {budget}")
        self.steps = steps
        self.budget = budget


class RecursionLimitExceeded(RegexError):
    """The backtracking matcher ran out of recursion depth (very long input)."""


class CapturesUnavailable(RegexError):
    """The lazy DFA reports spans only, so group(n) with n > 0 is unavailable."""
