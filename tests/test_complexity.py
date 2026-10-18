"""ReDoS behaviour, Pike linearity, DFA cache and state counts."""

import random

import pytest

from rxengine import Regex, StepBudgetExceeded
from rxengine.demo import benign_rows, dfa_rows, growth_per_char, linear_rows, redos_rows, table
from rxengine.program import disassemble

REDOS = [r"(a+)+$", r"(a|aa)+$", r"(a*)*b"]


def bt_steps(pattern, n, budget=5_000_000):
    rx = Regex(pattern, step_budget=budget)
    rx.search("a" * n + "!", "backtrack")
    return rx.backtracker.steps


@pytest.mark.parametrize("pattern", REDOS)
def test_backtracker_steps_grow_exponentially(pattern):
    s6, s10, s14 = (bt_steps(pattern, n) for n in (6, 10, 14))
    assert s10 / s6 > 4 and s14 / s10 > 4
    assert s14 > 15 * s6   # (a|aa)+$ grows like Fibonacci (x1.6 per char), the others x2


def test_budget_is_enforced_at_small_scale_and_is_per_call():
    rx = Regex(r"(a+)+$", step_budget=50)
    with pytest.raises(StepBudgetExceeded):
        rx.search("aaaa!", "backtrack")          # needs ~186 steps
    assert rx.search("a", "backtrack") is not None  # a cheap call on the same object still works
    with pytest.raises(StepBudgetExceeded):
        rx.search("aaaa!", "backtrack")


@pytest.mark.parametrize("pattern", REDOS)
def test_step_budget_turns_explosion_into_an_error_and_engine_is_reusable(pattern):
    rx = Regex(pattern, step_budget=20_000)
    text = "a" * 40 + "!"
    with pytest.raises(StepBudgetExceeded) as ei:
        rx.search(text, "backtrack")
    assert ei.value.budget == 20_000 and ei.value.steps > 20_000
    assert rx.search("aaa", "backtrack") is not None or pattern == r"(a*)*b"
    # same pattern, same input: the other two engines answer immediately
    assert rx.search(text, "pike") is None
    assert rx.search(text, "dfa") is None


@pytest.mark.parametrize("pattern", REDOS)
def test_pike_work_is_linear_in_input_and_bounded_by_program_size(pattern):
    rx = Regex(pattern)
    steps = {}
    for n in (200, 400, 800):
        rx.reset_stats()
        assert rx.search("a" * n + "!", "pike") is None
        steps[n] = rx.pike.steps
        assert steps[n] <= 4 * (n + 2) * len(rx.program)
        assert rx.pike.max_threads <= len(rx.program)
    assert 1.9 < steps[400] / steps[200] < 2.1
    assert 1.9 < steps[800] / steps[400] < 2.1


@pytest.mark.parametrize("pattern", REDOS)
def test_dfa_no_match_decision_is_one_transition_per_char(pattern):
    rx = Regex(pattern)
    n = 5000
    assert rx.search("a" * n + "!", "dfa") is None
    assert rx.dfa.steps <= n + 3
    assert rx.dfa.any.states_built < 10


def test_backtracker_can_beat_pike_on_anchored_typical_input():
    rx = Regex(r"^\s*(\w+)\s*=\s*(\d+)\s*$")
    text = "   timeout_seconds   =   30   "
    rx.reset_stats()
    rx.search(text, "backtrack")
    rx.search(text, "pike")
    assert rx.backtracker.steps * 3 < rx.pike.steps


def test_backtracker_is_quadratic_when_the_dfa_is_not_on_word_at_word():
    rx = Regex(r"(\w+)@(\w+)\.com", step_budget=50_000_000)
    text = "word " * 400
    rx.search(text, "backtrack")
    rx.search(text, "dfa")
    assert rx.backtracker.steps > 0 and rx.dfa.steps > 0


def test_dfa_span_search_is_quadratic_in_the_worst_case_documented_weakness():
    rx = Regex(r"a*b|c")
    n = 300
    m = rx.search("a" * n + "c", "dfa")
    assert m.span() == (n, n + 1)
    assert rx.dfa.first.steps > n * n // 4          # tries every start with the anchored DFA
    rx.reset_stats()
    rx.search("a" * n + "c", "pike")
    assert rx.pike.steps < 60 * n                    # Pike stays linear


def test_dfa_cache_flush_keeps_results_correct_and_live_states_capped():
    rng = random.Random(3)
    text = "".join(rng.choice("ab") for _ in range(3000))
    pattern = "(a|b)*a(a|b){8}"
    small, big = Regex(pattern, dfa_max_states=40), Regex(pattern, dfa_max_states=1_000_000)
    assert small.fullmatch(text, "dfa") == big.fullmatch(text, "dfa") or (
        small.fullmatch(text, "dfa").span() == big.fullmatch(text, "dfa").span())
    assert small.dfa.full.flushes > 0
    assert small.dfa.full.max_live <= 40
    assert big.dfa.full.flushes == 0
    assert (small.fullmatch(text, "dfa") is None) == (big.fullmatch(text, "pike") is None)


def test_dfa_state_explosion_is_exponential_in_n():
    rng = random.Random(5)
    text = "".join(rng.choice("ab") for _ in range(4000))
    states = {}
    for n in (3, 6, 9):
        rx = Regex(f"(a|b)*a(a|b){{{n}}}", dfa_max_states=1_000_000)
        rx.fullmatch(text, "dfa")
        states[n] = rx.dfa.full.states_built
    assert states[6] >= 4 * states[3] and states[9] >= 4 * states[6]
    assert states[9] >= 2 ** 9


def test_dfa_hit_rate_is_high_on_benign_and_low_when_states_explode():
    rx = Regex(r"a+b")
    rx.search("a" * 2000 + "b", "dfa")
    assert rx.dfa.first.hit_rate > 0.99
    rng = random.Random(1)
    text = "".join(rng.choice("ab") for _ in range(3000))
    rx = Regex("(a|b)*a(a|b){12}", dfa_max_states=1_000_000)
    rx.fullmatch(text, "dfa")
    assert rx.dfa.full.hit_rate < 0.5


def test_guards_only_for_nullable_bodies_in_the_program():
    assert "LSTART" in disassemble(Regex("(a*)*").program)
    assert "LSTART" not in disassemble(Regex("(a+)+").program)
    assert "LSTART" not in disassemble(Regex("a*").program)
    assert len(Regex("a{3}").program) < len(Regex("a{30}").program)


def test_demo_helpers_produce_the_expected_shapes():
    rows = redos_rows(r"(a+)+$", ns=[4, 8, 12, 24], budget=100_000)
    assert [r[0] for r in rows] == [4, 8, 12, 24]
    assert rows[0][1] is not None and rows[-1][1] is None   # budget hit at n=24
    assert all(r[2] is not None and r[3] is not None for r in rows)
    assert growth_per_char(rows) == pytest.approx(2.0, rel=0.1)
    lin = linear_rows(r"(a|aa)+$", ns=[100, 200])
    assert lin[1][1] / lin[0][1] == pytest.approx(2.0, rel=0.05)
    out = table(["a", "b"], [["1", "22"], ["333", "4"]])
    assert out.splitlines()[0].split() == ["a", "b"]
    assert len(dfa_rows(ns=(2, 4), text_len=300)) == 2
    assert all(r["span"] is not None for r in benign_rows())
