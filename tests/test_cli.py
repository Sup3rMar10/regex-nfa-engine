import subprocess
import sys

import pytest

from rxengine.__main__ import main


def test_search_match_exit_0_with_groups(capsys):
    assert main(["search", r"(\d+)-(\d+)", "tel 12-34"]) == 0
    out = capsys.readouterr().out
    assert "span=(4, 9)" in out and "group 1: '12'" in out and "group 2: '34'" in out


def test_search_no_match_exit_1(capsys):
    assert main(["search", "z", "abc"]) == 1
    assert "no match" in capsys.readouterr().out


def test_bad_pattern_exit_2_with_position(capsys):
    assert main(["search", "a(", "abc"]) == 2
    err = capsys.readouterr().err
    assert "missing )" in err and "position 1" in err


def test_unsupported_feature_exit_2(capsys):
    assert main(["search", r"\bfoo", "foo"]) == 2
    assert "not supported" in capsys.readouterr().err


def test_budget_exceeded_exit_2_not_a_false_no_match(capsys):
    code = main(["search", "(a+)+$", "a" * 12 + "!", "--engine", "backtrack", "--budget", "500"])
    assert code == 2
    assert "step budget exceeded" in capsys.readouterr().err
    # the same input on pike answers (no match) instantly
    assert main(["search", "(a+)+$", "a" * 40 + "!", "--engine", "pike"]) == 1


def test_fullmatch_and_dfa_engine(capsys):
    assert main(["fullmatch", "a+", "aaa", "--engine", "dfa"]) == 0
    assert "span=(0, 3)" in capsys.readouterr().out
    assert main(["fullmatch", "a+", "aab", "--engine", "dfa"]) == 1


def test_bad_arguments_exit_2():
    assert main(["search", "only-one-arg"]) == 2
    assert main(["search", "a", "b", "--engine", "nope"]) == 2
    assert main([]) == 2


def test_module_entry_point_runs_as_a_subprocess():
    r = subprocess.run([sys.executable, "-m", "rxengine", "search", "b+", "abbb"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "span=(1, 4)" in r.stdout
    r = subprocess.run([sys.executable, "-m", "rxengine", "search", "*", "x"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "nothing to repeat" in r.stderr
