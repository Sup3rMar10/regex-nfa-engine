import pytest

from rxengine import Regex, RegexSyntaxError
from rxengine.nodes import Alt, Any, Assert, Cat, Char, CharSet, Empty, Group, Repeat
from rxengine.parser import MAX_DEPTH, MAX_GROUPS, MAX_PATTERN_LEN, MAX_REPEAT, parse


def err(pattern):
    with pytest.raises(RegexSyntaxError) as ei:
        parse(pattern)
    return ei.value


def test_ast_shape_for_a_mixed_pattern():
    p = parse(r"^(a|b)+?c.$")
    assert p.ngroups == 1
    assert p.ast == Cat((
        Assert("bol"),
        Repeat(Group(1, Alt((Char("a"), Char("b")))), 1, None, False),
        Char("c"), Any(), Assert("eol")))


def test_empty_pattern_and_empty_branches():
    assert parse("").ast == Empty()
    assert parse("a|").ast == Alt((Char("a"), Empty()))
    assert parse("(|a)").ast == Group(1, Alt((Empty(), Char("a"))))


def test_group_numbering_follows_open_parens_and_noncapturing_is_skipped():
    p = parse("((a)(?:b)(c))")
    assert p.ngroups == 3
    assert p.ast.index == 1


@pytest.mark.parametrize("pattern,lo,hi,greedy", [
    ("a*", 0, None, True), ("a+", 1, None, True), ("a?", 0, 1, True),
    ("a{3}", 3, 3, True), ("a{2,}", 2, None, True), ("a{,4}", 0, 4, True),
    ("a{2,5}", 2, 5, True), ("a{,}", 0, None, True), ("a*?", 0, None, False),
    ("a{2,5}?", 2, 5, False), ("a??", 0, 1, False),
])
def test_quantifier_forms(pattern, lo, hi, greedy):
    assert parse(pattern).ast == Repeat(Char("a"), lo, hi, greedy)


@pytest.mark.parametrize("pattern", ["a{", "a{x}", "a{1", "a{1,x}", "a{ 1}", "a{}", "{x"])
def test_brace_that_is_not_a_quantifier_is_a_literal(pattern):
    assert Regex(pattern).fullmatch(pattern, "pike") is not None


def test_class_items_ranges_and_negation():
    node = parse(r"[^a-c\d_]").ast
    assert node == CharSet((("r", "a", "c"), ("k", "d"), ("c", "_")), True)
    assert parse("[]a]").ast == CharSet((("c", "]"), ("c", "a")), False)
    assert parse("[a-]").ast == CharSet((("c", "a"), ("c", "-")), False)


@pytest.mark.parametrize("pattern,pos,fragment", [
    ("*a", 0, "nothing to repeat"),
    ("a|*", 2, "nothing to repeat"),
    ("{2}", 0, "nothing to repeat"),
    ("^*", 1, "nothing to repeat"),
    ("$+", 1, "nothing to repeat"),
    ("a**", 2, "multiple repeat"),
    ("a{2}{3}", 4, "multiple repeat"),
    ("a*??", 3, "multiple repeat"),
    ("(a", 0, "missing )"),
    ("ab(c(d)", 2, "missing )"),
    ("a)", 1, "unbalanced parenthesis"),
    ("(a))", 3, "unbalanced parenthesis"),
    ("[a", 0, "unterminated character set"),
    ("x[]", 1, "unterminated character set"),
    ("a\\", 1, "bad escape (end of pattern)"),
    ("\\q", 0, "bad escape"),
    ("a{2,1}", 1, "min repeat greater than max repeat"),
    ("[z-a]", 1, "bad character range"),
    ("[a-\\d]", 1, "bad character range"),
    ("[\\w-a]", 1, "bad character range"),
])
def test_syntax_errors_report_position_and_message(pattern, pos, fragment):
    e = err(pattern)
    assert e.kind == "syntax"
    assert e.pos == pos
    assert fragment in e.msg
    assert f"at position {pos}" in str(e)


@pytest.mark.parametrize("pattern", [r"(?P<n>a)", r"(?=a)", r"(?i)a", r"\1", r"\b", r"\x41", r"a*+", r"a++", r"[\b]"])
def test_valid_in_re_but_outside_the_subset_is_kind_unsupported(pattern):
    assert err(pattern).kind == "unsupported"


def test_nesting_limit_is_exact():
    parse("(" * MAX_DEPTH + "a" + ")" * MAX_DEPTH)
    e = err("(" * (MAX_DEPTH + 1) + "a" + ")" * (MAX_DEPTH + 1))
    assert e.kind == "limit" and e.pos == MAX_DEPTH


def test_size_limits():
    parse("a" * MAX_PATTERN_LEN)
    assert err("a" * (MAX_PATTERN_LEN + 1)).kind == "limit"
    parse(f"a{{{MAX_REPEAT}}}")
    assert err(f"a{{{MAX_REPEAT + 1}}}").kind == "limit"
    parse("()" * MAX_GROUPS)
    assert err("()" * (MAX_GROUPS + 1)).kind == "limit"


def test_counted_repeat_expansion_is_capped_at_compile_time():
    with pytest.raises(RegexSyntaxError) as ei:
        Regex("(a{1000}){1000}")
    assert ei.value.kind == "limit"
    assert "larger than" in ei.value.msg


def test_noncapturing_group_around_an_anchor_is_repeatable_like_re():
    assert Regex("(?:^)*a").fullmatch("a", "pike") is not None
