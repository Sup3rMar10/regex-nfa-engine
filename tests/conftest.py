import warnings

import pytest

from rxengine import Regex
from rxengine.fuzz import OPS, engine_results, oracle_compile, oracle_results

warnings.simplefilter("ignore", FutureWarning)


def strip_groups(res):
    if isinstance(res, list):
        return [None if x is None else (x[0], None) for x in res]
    return None if res is None else (res[0], None)


def compare_with_re(pattern, text, engines=("backtrack", "pike", "dfa")):
    """Return a list of human-readable mismatches against Python's re (ASCII mode)."""
    rx = Regex(pattern, step_budget=2_000_000)
    want = oracle_results(oracle_compile(pattern), text)
    bad = []
    for e in engines:
        got = engine_results(rx, e, text)
        for op in OPS:
            w = strip_groups(want[op]) if e == "dfa" else want[op]
            if got[op] != w:
                bad.append(f"{e}/{op}: pattern={pattern!r} text={text!r} want={w} got={got[op]}")
    return bad


@pytest.fixture
def cmp():
    return compare_with_re
