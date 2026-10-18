"""Hand-picked semantics, each checked on all three engines and against re."""

import sys

import pytest

import rxengine
from rxengine import CapturesUnavailable, RecursionLimitExceeded, Regex
from rxengine import backtrack

ENGINES = ["backtrack", "pike", "dfa"]

TRICKY = [
    # (pattern, texts)
    (r"a|ab", ["ab", "abab", "b"]),
    (r"(a|ab)(c|bcd)(d*)", ["abcd", "abc"]),
    (r"a*?", ["aa", ""]),
    (r"a+?b", ["aaab", "b"]),
    (r"(a+)(a+)", ["aaaa"]),
    (r"(a+?)(a*)", ["aaaa"]),
    (r"(a*)*", ["b", "aab", ""]),
    (r"(a*)+", ["b", "aa"]),
    (r"(a|)*", ["b", "aab"]),
    (r"(?:|a)*", ["a", "aa"]),
    (r"(?:|a)+", ["a"]),
    (r"(a?)*?", ["aa"]),
    (r"(a?)*", ["aa"]),
    (r"(a??)*", ["aa"]),
    (r"(a*?)*", ["aa"]),
    (r"(a*){3}", ["b", "aaa"]),
    (r"(a*){2,4}", ["aa", "aaaaa"]),
    (r"(a?){3}", ["a"]),
    (r"(?:(a)|b)*", ["ab", "ba"]),
    (r"((a)|(b))*", ["ab"]),
    (r"(?:(a)|(b))+", ["ab", "ba"]),
    (r"(a*)*?b", ["aab"]),
    (r"(^a|b)*", ["ab"]),
    (r"(?:(?:a|b)*?)*", ["ab"]),
    (r"a$", ["a", "a\n", "a\n\n", "ba"]),
    (r"a$\n", ["a\n"]),
    (r"$", ["", "\n", "a"]),
    (r"^$", ["", "\n", "a"]),
    (r"^a", ["a", "ba"]),
    (r"(?:$|a)^", [""]),
    (r".", ["\n", "x"]),
    (r"[^a]", ["\n", "a", "b"]),
    (r"\D\W\S", ["a b", "1 2", "a-b"]),
    (r"[\d\s]+", ["a1 2b", "x"]),
    (r"[]a]+", ["]a]", "b"]),
    (r"[a-]+", ["a-a", "-"]),
    (r"a{2,3}", ["a", "aa", "aaaa", "aaaaaa"]),
    (r"a{,2}b", ["aaab", "b"]),
    (r"(ab){2}", ["ababab"]),
    (r"(ab)*?c", ["ababc"]),
    (r"x*", ["", "xxx", "yxx"]),
    (r"", ["", "abc"]),
    (r"(a)|b", ["b", "a"]),
    (r"(a|b)*c", ["abababc", "ababab"]),
    (r"\.\*\+\?\(\)\[\]\{\}\|\^\$", ["." + "*+?()[]{}|^$"]),
    (r"[^\d]", ["5x"]),
    (r"(.)\s?(.)", ["a b", "ab"]),
    (r".*?(\d+)$", ["abc 123", "abc"]),
    (r"(?:a|b|c)+?d", ["abcabcd"]),
    (r"[a-c]{2,}?c", ["abcabcc"]),
    # non-ASCII text: . and negated classes match it, \w \d \s do not (re.ASCII)
    (r".", ["é"]),
    (r"[^a]+", ["éa日"]),
    (r"\w+", ["é日a"]),
]


@pytest.mark.parametrize("pattern,texts", TRICKY, ids=[t[0] or "<empty>" for t in TRICKY])
def test_matches_python_re_on_every_engine(cmp, pattern, texts):
    bad = []
    for t in texts:
        bad += cmp(pattern, t)
    assert not bad, "\n".join(bad)


@pytest.mark.parametrize("engine", ENGINES)
def test_leftmost_first_not_longest(engine):
    rx = Regex("a|ab")
    assert rx.search("ab", engine).span() == (0, 1)
    assert rx.fullmatch("ab", engine).span() == (0, 2)  # fullmatch may pick the other branch


@pytest.mark.parametrize("engine", ENGINES)
def test_greedy_vs_lazy_spans(engine):
    assert Regex("a+").search("aaa", engine).span() == (0, 3)
    assert Regex("a+?").search("aaa", engine).span() == (0, 1)
    assert Regex("<.*>").search("<a><b>", engine).span() == (0, 6)
    assert Regex("<.*?>").search("<a><b>", engine).span() == (0, 3)


@pytest.mark.parametrize("engine", ["backtrack", "pike"])
def test_captures_and_empty_iteration_rule(engine):
    # CPython: the empty iteration completes, so group 1 is '' (not None).
    m = Regex("(a*)*").search("b", engine)
    assert m.span() == (0, 0) and m.group(1) == ""
    # lazy: the tail is tried first, the group never participates.
    m = Regex("(a?)*?").search("aa", engine)
    assert m.span() == (0, 0) and m.group(1) is None
    # last iteration wins, and an earlier iteration's group survives a later non-participating one
    m = Regex("(?:(a)|(b))+").search("ab", engine)
    assert m.groups() == ("a", "b")
    m = Regex("(?:(a)|b)*").search("ab", engine)
    assert m.groups() == ("a",)


@pytest.mark.parametrize("engine", ENGINES)
def test_finditer_empty_match_stepping_matches_python_3_7_plus(engine):
    assert Regex("a*?").findall_spans("aa", engine) == [(0, 0), (0, 1), (1, 1), (1, 2), (2, 2)]
    assert Regex("a*").findall_spans("baac", engine) == [(0, 0), (1, 3), (3, 3), (4, 4)]
    assert Regex("").findall_spans("ab", engine) == [(0, 0), (1, 1), (2, 2)]
    assert Regex("x").findall_spans("abc", engine) == []


@pytest.mark.parametrize("engine", ENGINES)
def test_caret_is_absolute_start_not_search_start(engine):
    assert Regex("^a").search("ba", engine, pos=1) is None
    assert Regex("a").search("ba", engine, pos=1).span() == (1, 2)
    assert Regex("a").search("ba", engine, pos=5) is None


@pytest.mark.parametrize("engine", ENGINES)
def test_match_is_anchored_at_pos(engine):
    assert Regex("b+").match("abb", engine) is None
    assert Regex("b+").match("abb", engine, pos=1).span() == (1, 3)


def test_match_object_api():
    m = Regex(r"(\w+)@(\w+)(x)?").search("hi bob@example", "pike")
    assert m.span() == (3, 14) and m.group() == "bob@example"
    assert m.group(1) == "bob" and m.start(2) == 7 and m.end(2) == 14
    assert m.groups() == ("bob", "example", None)
    assert m.span(3) == (-1, -1)
    with pytest.raises(IndexError):
        m.group(4)


def test_dfa_has_no_captures_but_has_spans():
    m = Regex("(a)(b)").search("xab", "dfa")
    assert m.span() == (1, 3) and m.group() == "ab"
    with pytest.raises(CapturesUnavailable):
        m.group(1)


def test_unknown_engine_is_rejected():
    with pytest.raises(ValueError):
        Regex("a").search("a", "nfa")


def test_digit_word_space_are_ascii_only_documented_difference():
    # Python's default for str patterns is Unicode; this engine is ASCII-only.
    arabic_three = "٣"
    assert Regex(r"\d").search(arabic_three, "pike") is None
    import re
    assert re.search(r"\d", arabic_three) is not None
    assert re.search(r"\d", arabic_three, re.ASCII) is None


def test_convenience_functions():
    assert rxengine.search("b+", "abbc").span() == (1, 3)
    assert rxengine.fullmatch("a|b", "b").span() == (0, 1)
    assert [m.span() for m in rxengine.finditer("a", "aXa", "dfa")] == [(0, 1), (2, 3)]
    assert rxengine.compile("a").pattern == "a"


def test_backtracker_recursion_limit_is_reported_and_restored(monkeypatch):
    monkeypatch.setattr(backtrack, "RECURSION_LIMIT", 3000)
    before = sys.getrecursionlimit()
    rx = Regex("(?:ab)*", step_budget=10_000_000)
    text = "ab" * 2000
    with pytest.raises(RecursionLimitExceeded):
        rx.fullmatch(text, "backtrack")
    assert sys.getrecursionlimit() == before
    assert rx.fullmatch(text, "pike").span() == (0, 4000)
    assert rx.fullmatch(text, "dfa").span() == (0, 4000)
