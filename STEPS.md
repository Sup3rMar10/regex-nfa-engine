# STEPS

**Part A** is what I did, in order, including the problems. **Part B** is how you run it.

---

## Part A: steps taken

1. **Probed `re` before designing.** A scratch script printed how CPython treats the corner cases I would otherwise guess: `(a*)*` on `b` gives group 1 `''` (the empty iteration completes, then the loop stops), `(a?)*?` on `aa` leaves group 1 `None`, `{` that is not a valid quantifier is a literal, `^*` and `(?:^)*` differ (error vs legal), `finditer` after an empty match may still return a non-empty match at the same position. I copied these rules from the CPython source behaviour rather than from textbook leftmost-first.
2. **One AST, three matchers.** `parser.py` builds frozen dataclass nodes; `backtrack.py` walks them directly (continuation-passing, one fast iterative path for single-character loops); `program.py` compiles them to a flat NFA for `pikevm.py` and `dfa.py`.
3. **The empty-iteration rule in an NFA.** Loops whose body can match empty get `LSTART k` / `LCHECK k`: if the iteration consumed nothing, jump to the loop exit instead of looping. The "started at this position" mark only lives inside one epsilon-closure (a bitmask), so threads and DFA states never store positions. This is what lets the lazy DFA reproduce a position-dependent rule.
4. **Pike VM:** priority-ordered thread list, first MATCH cuts lower-priority threads, `fullmatch` ignores matches before the end without cutting, `must_advance` ignores a match at the search start.
5. **Lazy DFA:** states are ordered tuples of NFA entries, a MATCH entry cuts lower priority entries (so the span is leftmost-first like `re`, not leftmost-longest). Three cached modes (`first`, `full`, `any`). `$` stays pending in a state and is resolved by the transition symbol (normal char, newline-as-last-char, end of text). State cap with a full flush.
6. **Fuzzer and injected bugs** before trusting anything: seeded grammar generator, `re.ASCII` oracle, six-plus injected bugs (`bugs.py`).
7. **First fuzz run: 0 disagreements in 500 patterns.** I did not believe it until the injected bugs were detected (all 7 are) and later the mutation run. The only real defects: the parser rejected `(?:^)*` because a non-capturing group around an anchor returned the bare anchor (2 compile mismatches in the first run; fixed by wrapping it).
8. **The demo hung for over five minutes.** Cause: `re` itself is exponential on one generated pattern (a 9-character text, minutes inside `re`). Two orphaned demo processes kept burning CPU for an hour and made my first mutation timeouts misleading until I found and killed them. Fix: the fuzzer runs the backtracker first (it explores in the same order as sre) and skips the oracle when the backtracker exceeds its budget; those texts are cross-checked pike against dfa only.
9. **Demo tuning, with mistakes:** ReDoS budget 2,000,000 made the demo take over a minute per table, now 300,000; the first "literal prefix" benign case searched for `ERROR (\d+)` in text containing `ERROR code 42` (no match, so the numbers meant nothing; fixed and a test asserts every benign case matches); the DFA table read its stats after a reset (all zeros; fixed).
10. **Tests (177):** parser tables with exact error positions, 53 hand-picked pattern groups (76 semantics test cases in that file) checked on all three engines against `re`, complexity tests (exponential backtracker, linear Pike, DFA one-transition-per-character, cache flush, state explosion), fuzz with fixed seeds, injected-bug detection with per-engine attribution, an AST scan proving no engine module imports `re`, CLI exit codes. One of my own thresholds was wrong (I assumed 50x growth for `(a|aa)+$`; it is Fibonacci, 1.6x per character) and I corrected the test, not the engine.
11. **Mutation check, 15 mutants in scratch copies.** First pass: 13 killed by assertion, 2 "killed by timeout" (step budget never enforced; `finditer` never advancing past an empty match). A hang is a poor failure, so I made both fail fast: `finditer` now raises `RegexError` if it produces more than 2n+2 matches (fail closed), a small-scale budget test and a smaller CLI budget test do not need an exponential run, and the mutation script runs the focused test files first. Result: **15/15 killed**, all by an assertion or error.
12. **Stress run (not in the demo):** pattern depth 4, text length 24, three other seeds, 13,475 cases: 0 disagreements, 25 texts skipped by the guard (pike == dfa on all 25).
13. **Wrote README and this file** with numbers copied from a real `python -m rxengine demo` run, then re-ran the demo to check the deterministic columns are identical.

### Where this engine differs from `re` (all of it that I know of)
- `\d \w \s` (and `\D \W \S`) are ASCII-only; Python's `str` default is Unicode. The oracle runs with `re.ASCII`. Test: `test_digit_word_space_are_ascii_only_documented_difference`.
- Unsupported (error kind `unsupported`): lookaround, backreferences, `\b \B \A \Z`, `\x \u \N`, numeric escapes, named groups, inline flags, possessive quantifiers, atomic groups. 30 of 5,000 random malformed strings hit this.
- Limits (error kind `limit`): nesting 100, pattern 10,000 characters, repeat 1,000, 200 groups, 50,000 compiled instructions.
- The DFA returns spans only.
- `\a` is accepted (bell) like `re`; `\b` inside a class is *unsupported* here (backspace in `re`).
- No genuine disagreement in matching behaviour was found inside the supported subset.

---

## Part B: steps for you

### Run it
1. Python 3.11 or newer, standard library only (pytest for the tests).
2. `python -m rxengine demo` prints every table in the README (about 30 seconds on an idle machine; fixed seeds, so step and state counts repeat exactly, I checked two runs; millisecond columns vary). `python -m rxengine demo --quick` is the smaller CI run.
3. `python -m rxengine search 'a(b|c)+' 'xabcb' --engine backtrack` (also `fullmatch`, `--engine pike|dfa`, `--budget N`). Exit codes: 0 match, 1 no match, 2 bad pattern, step budget exceeded or any internal error (it never reports "no match" after a crash).

### Run the tests
`python -m pytest` (177 tests, 9 to 18 seconds depending on machine load).

### Mutation check
`python tools/mutation_check.py` (a few minutes). It copies the repo to a temp directory per mutant, applies one exact-text replacement, runs the tests, and prints KILLED or SURVIVED. "pattern not found" is reported as an error (exit 2), never as a pass.

### What to read first
1. `rxengine/program.py` (the LSTART/LCHECK idea) and `rxengine/pikevm.py` (about 100 lines).
2. `rxengine/dfa.py` docstring, then `_closure` and `_compute`.
3. `rxengine/fuzz.py` (`run_fuzz`, the oracle guard) and `rxengine/bugs.py`.
4. `rxengine/demo.py` for how each README number is produced.
