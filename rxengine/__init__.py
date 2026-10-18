"""rxengine: a from-scratch regex engine with three matchers over one AST.

Never imports the ``re`` module.  (``rxengine.fuzz`` and the demo import it as
an oracle; the test suite checks that no engine module does.)
"""

from .api import ENGINES, Match, Regex, compile, finditer, fullmatch, search
from .errors import (CapturesUnavailable, RecursionLimitExceeded, RegexError,
                     RegexSyntaxError, StepBudgetExceeded)

__all__ = [
    "ENGINES", "Match", "Regex", "compile", "finditer", "fullmatch", "search",
    "RegexError", "RegexSyntaxError", "StepBudgetExceeded", "RecursionLimitExceeded",
    "CapturesUnavailable",
]
__version__ = "0.1.0"
