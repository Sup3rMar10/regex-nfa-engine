import ast
import pathlib
import random

import pytest

from rxengine import Regex
from rxengine.bugs import ALL_BUGS, AST_BUGS, injected
from rxengine.fuzz import gen_regex, gen_text, oracle_compile, run_fuzz, run_syntax_fuzz

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_three_engines_agree_with_re_on_thousands_of_cases():
    rep = run_fuzz(seed=1234, n_patterns=900, texts_per_pattern=4)
    assert rep.cases > 3000
    assert rep.total_disagreements == 0, rep.disagreements[:3]
    for e in ("backtrack", "pike", "dfa"):
        assert rep.agree(e) == rep.total(e) == 3 * rep.cases
    # the generator must actually produce matches, fullmatches and captures
    assert rep.oracle_search_hits > 0.4 * rep.cases
    assert rep.oracle_full_hits > 100
    assert rep.oracle_group_hits > 100
    assert rep.compile_mismatches == []


def test_fuzz_is_deterministic_for_a_seed():
    a = run_fuzz(seed=99, n_patterns=150)
    b = run_fuzz(seed=99, n_patterns=150)
    assert a.counts == b.counts and a.cases == b.cases and a.guard_skipped == b.guard_skipped
    rng1, rng2 = random.Random(5), random.Random(5)
    assert [gen_regex(rng1) for _ in range(20)] == [gen_regex(rng2) for _ in range(20)]
    assert gen_text(random.Random(1)) == gen_text(random.Random(1))


def test_generated_patterns_are_valid_for_both_engine_and_re():
    rng = random.Random(77)
    for _ in range(600):
        p = gen_regex(rng)
        oracle_compile(p)
        Regex(p)


def test_every_generated_construct_shows_up():
    rng = random.Random(8)
    pats = [gen_regex(rng) for _ in range(1500)]
    for needle in ["(?:", "{", "*?", "+?", "??", "[^", r"\d", r"\W", "^", "$", "|", "(a", "."]:
        assert any(needle in p for p in pats), needle


def test_syntax_fuzz_has_no_false_accepts_or_false_rejects():
    rep = run_syntax_fuzz(seed=4, n=3000)
    assert rep.engine_rejects_valid == [], rep.engine_rejects_valid[:5]
    assert rep.engine_accepts_invalid == [], rep.engine_accepts_invalid[:5]
    assert rep.both_ok > 300 and rep.both_reject > 300


@pytest.mark.parametrize("bug", ALL_BUGS)
def test_fuzzer_detects_each_injected_bug(bug):
    with injected(bug) as factory:
        rep = run_fuzz(seed=31337, n_patterns=300, factory=factory)
    assert rep.total_disagreements > 0, f"fuzzer missed {bug}"
    assert rep.disagreements, "a disagreement must come with a reproducer"


def test_injected_bug_attribution_per_engine():
    with injected("pike_no_cut_on_match") as factory:
        rep = run_fuzz(seed=31337, n_patterns=300, factory=factory)
    assert rep.n_disagree["pike"] > 0
    assert rep.n_disagree["backtrack"] == 0 and rep.n_disagree["dfa"] == 0
    with injected("dollar_matches_before_any_newline") as factory:
        rep = run_fuzz(seed=31337, n_patterns=300, factory=factory)
    assert rep.n_disagree["backtrack"] > 0 and rep.n_disagree["pike"] > 0
    assert rep.n_disagree["dfa"] == 0   # the DFA resolves $ on its own


def test_injected_bugs_are_removed_after_the_context_exits():
    for bug in ALL_BUGS:
        with injected(bug):
            pass
    rep = run_fuzz(seed=31337, n_patterns=150)
    assert rep.total_disagreements == 0


def test_there_are_at_least_three_ast_level_bugs():
    assert len(AST_BUGS) >= 3 and len(ALL_BUGS) >= 6


def test_a_wrong_expected_result_is_reported_as_a_disagreement_with_details():
    with injected("alternation_order_reversed") as factory:
        rep = run_fuzz(seed=2, n_patterns=200, factory=factory)
    d = rep.disagreements[0]
    assert set(d) == {"engine", "op", "pattern", "text", "expected", "got"}
    assert d["expected"] != d["got"]


def test_engine_modules_never_import_re():
    allowed = {"fuzz.py"}
    for path in (ROOT / "rxengine").glob("*.py"):
        if path.name in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(a.name.split(".")[0] != "re" for a in node.names), path.name
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] != "re", path.name
    # and the package root does not pull in the fuzz module
    init = ast.parse((ROOT / "rxengine" / "__init__.py").read_text(encoding="utf-8"))
    for node in ast.walk(init):
        if isinstance(node, ast.ImportFrom):
            assert "fuzz" not in (node.module or "")
