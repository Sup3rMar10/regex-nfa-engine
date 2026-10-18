# regex-nfa-engine

A **from-scratch regex engine in pure Python** (package `rxengine`, standard library only, no `re` inside the engine) with **three matchers over one AST**: a recursive backtracker with a step budget, a Pike VM (Thompson NFA simulation with thread priority and captures) and a lazy DFA with a capped state cache. A differential fuzzer checks all three against Python's `re`, and a demo measures why the three designs behave so differently (ReDoS, state explosion, honest baselines).

> **What is verified, and what is not.** 177 tests pass locally on Python 3.13. A GitHub Actions workflow (Python 3.11 and 3.13, runs the tests and `demo --quick`) is configured. I broke 15 behaviours on purpose in scratch copies and the tests failed each time (15 of 15 killed, `tools/mutation_check.py`). The fuzz agreement below means "same as CPython 3.13's `re`" on a generated subset: random patterns of depth at most 3 and texts of at most 10 characters over a small alphabet. It is not a proof of equivalence, and it includes CPython's own quirks (for example how an empty loop iteration is treated), which I copied on purpose. The engine is pure Python: absolute speed is not comparable to a real engine, and step counts are *not* comparable across the three engines (different work units, see below).

## Quick start

```
python -m rxengine demo            # every number below (about 30 s, fixed seeds); --quick is the CI size
python -m rxengine search '(\d+)-(\d+)' 'tel 12-34' --engine pike   # exit 0 match, 1 no match, 2 error
python -m pytest                   # 177 tests
python tools/mutation_check.py     # 15 mutants in scratch copies, expect "15/15 mutants killed"
```

```python
import rxengine
rx = rxengine.compile(r"(\w+)@(\w+)\.com")
m = rx.search("mail bob@example.com", engine="pike")      # "backtrack" | "pike" | "dfa"
m.span(), m.groups()                                       # (5, 20), ('bob', 'example')
list(rx.finditer("a1b22", "dfa"))                          # re.finditer stepping rules, spans only
rx.fullmatch("bob@example.com", "backtrack")               # StepBudgetExceeded instead of a hang
```

## Supported subset

Literals, `.`, classes (`[abc]`, ranges, `[^...]`, `\d \w \s` inside), escapes `\d \D \w \W \s \S \n \t \r \f \v \a` and escaped punctuation, capturing `(...)` and non-capturing `(?:...)` groups, alternation, quantifiers `* + ? {m} {m,} {,n} {m,n}` greedy and lazy, anchors `^ $` (no MULTILINE: `$` also matches before one final newline, as in `re`). `fullmatch`, `search`, `match`, `finditer` with `re`'s empty-match stepping.

Syntax errors carry a position and a message and have a `kind`: `syntax` (`re` rejects it too), `unsupported` (valid in `re`, outside this subset: lookaround, backreferences, `\b`, flags, named groups, possessive quantifiers), `limit` (nesting depth 100, pattern length 10,000, repeat count 1,000, 200 groups, 50,000 compiled instructions because `{m,n}` is expanded by copying).

**Deliberate differences from `re`:** `\d \w \s` are ASCII-only (the oracle runs with `re.ASCII`); the DFA reports spans only (no groups); the limits above. Everything else I found differing is listed in STEPS.md.

## Measured results (`python -m rxengine demo`)

### Differential fuzz against `re` (seed 20241018)
5,000 random patterns x 4 texts = **19,993 cases**; each runs `search`, `fullmatch` and `finditer` on each engine and compares span (and capture-group spans for backtrack and pike; span only for the DFA). `re.search` matched in 12,120 cases, `fullmatch` in 1,575, a capture group was set in 1,928.

| engine | compares | search | fullmatch | finditer | total |
|---|---|---|---|---|---|
| backtrack | span + groups | 19993/19993 | 19993/19993 | 19993/19993 | 59979/59979 |
| pike | span + groups | 19993/19993 | 19993/19993 | 19993/19993 | 59979/59979 |
| dfa | span only | 19993/19993 | 19993/19993 | 19993/19993 | 59979/59979 |

Seven more texts were **not** given to `re` because the backtracker blew its budget on them (`re` itself can run for minutes on such patterns, which hung my first demo); pike and DFA agreed with each other on all 7. Malformed-pattern fuzz (5,000 random strings): both accept 1,888, both reject 3,082, engine says `unsupported` while `re` accepts 30 (expected), engine rejects something `re` accepts 0, engine accepts something `re` rejects 0.

### Does the fuzzer notice injected bugs? (1,000 patterns, seed 31337)
| injected bug | found | disagreements backtrack / pike / dfa |
|---|---|---|
| lazy quantifier treated as greedy | yes | 361 / 361 / 287 |
| `{m,n}` max off by one | yes | 222 / 222 / 182 |
| `.` matches newline | yes | 286 / 286 / 281 |
| alternation order reversed | yes | 626 / 626 / 554 |
| negated class not negated | yes | 383 / 383 / 374 |
| `$` matches before any newline (shared helper) | yes | 85 / 85 / 0 (the DFA resolves `$` itself) |
| Pike VM does not cut on match | yes | 0 / 1006 / 0 |

### ReDoS: `search` on `'a'*n + '!'` (the `!` forces failure)
Work units: backtrack = node visits, pike = instruction visits + thread steps, dfa = character transitions. Backtracker budget 300,000.

| n | `(a+)+$` bt | pike | dfa | `(a\|aa)+$` bt | `(a*)*b` bt |
|---|---|---|---|---|---|
| 4 | 186 | 88 | 6 | 209 | 288 |
| 8 | 3,062 | 180 | 10 | 1,697 | 4,608 |
| 12 | 49,138 | 272 | 14 | 12,060 | 73,728 |
| 16 | over budget | 364 | 18 | 83,253 | over budget |
| 20 | over budget | 456 | 22 | over budget | over budget |

Backtracker growth per extra character: x2.00, x1.62 (Fibonacci-like) and x2.00. Pike is 23.0, 27.0 and 18.0 steps per character at n = 1600 (flat from n = 400); the DFA's no-match decision costs one transition per input character plus one for end of text (402 at n = 400).

### Benign inputs: the backtracker is not always the loser
| case | chars | bt steps | pike steps | dfa steps | pike/bt steps | bt ms | pike ms | dfa ms (cold) |
|---|---|---|---|---|---|---|---|---|
| literal prefix in a log | 2409 | 4,812 | 7,251 | 4,817 | 1.51 | 6.61 | 3.82 | 4.63 |
| anchored phone-ish | 11 | 22 | 100 | 24 | 4.55 | 0.04 | 0.11 | 0.35 |
| anchored `key = value` line | 30 | 47 | 318 | 62 | 6.77 | 0.07 | 0.33 | 0.66 |
| alternation of words | 2655 | 373 | 453 | 90 | 1.21 | 0.54 | 0.14 | 0.29 |
| `word@word.com` in prose | 2655 | 23,962 | 26,499 | 10,652 | 1.11 | 28.1 | 14.3 | 4.92 |

On short anchored lines the backtracker does 4.5 to 6.8 times less work and is 3 to 5 times faster in wall-clock (this is the case where the simpler matcher wins). On long scans it also does fewer steps, yet Pike took about 0.3 to 0.6 of the backtracker's time (I did not profile why; the backtracker allocates a closure per step). That mismatch between steps and wall-clock is itself a finding. The milliseconds are single runs on one machine: an earlier run of the same demo with another process hogging the CPU gave the same step columns and millisecond columns 3 to 4 times larger, so only the step columns are reproducible (and they are not comparable across engines).

### Lazy DFA: states, cache, explosion
Benign `(\w+)@(\w+)\.com`: 9 states, cache hit rate 0.984 (unanchored) and 0.992 (anchored). `fullmatch` of `(a|b)*a(a|b){n}` on 6,000 random `a`/`b` characters:

| n | DFA states (no cap) | hit rate | states built (cap 2000) | flushes | hit rate (cap) | pike steps |
|---|---|---|---|---|---|---|
| 2 | 9 | 0.997 | 9 | 0 | 0.997 | 123,464 |
| 6 | 129 | 0.957 | 129 | 0 | 0.957 | 214,193 |
| 10 | 1,930 | 0.481 | 1,930 | 0 | 0.481 | 304,746 |
| 12 | 4,183 | 0.170 | 5,191 | 2 | 0.073 | 350,052 |
| 14 | 5,451 | 0.047 | 5,782 | 2 | 0.020 | 395,360 |

States roughly double with n (2^(n+1) possible) until the 6,000-character text stops visiting new ones; with the cap the cache thrashes and the hit rate falls to 0.02. Pike's work grows only linearly in n.

## Honest negative findings
- **The DFA's span search is quadratic in the worst case.** It first runs a linear any-match check, then tries the anchored DFA from each start position. `search` of `a*b|c` on `'a'*n + 'c'`: 5,254 / 20,504 / 81,004 / 322,004 DFA steps for n = 100 / 200 / 400 / 800, against 1,118 to 8,818 for Pike. (RE2 uses a reverse DFA to find the start; I did not.)
- **The cache cap protects memory, not speed.** At n = 14 with a 2,000-state cap the DFA rebuilt states constantly (hit rate 0.020), so it is slower than Pike there. The cap counts states, not transition entries or bytes.
- **The first fuzz run found nothing, which I did not trust.** It took the injected-bug table and the mutation run to show the fuzzer can see bugs. The only real defects fuzzing found were an engine bug (the parser rejected `(?:^)*`, which `re` accepts) and a test-harness hazard (see below).
- **`re` is exponential too.** One generated pattern on a 9-character text ran for minutes inside `re`. The fuzzer now runs the backtracker first and skips the oracle when it exceeds its budget, so the pathological cases are cross-checked pike against dfa only.
- **Pike carries 18 to 27 steps per character even for tiny patterns** (closure work and capture-tuple copies). It is linear, not fast.
- **The backtracker is recursive.** It raises the recursion limit to 30,000 while it runs and reports `RecursionLimitExceeded` beyond that: `(?:ab)*` fails at roughly 2,600 iterations (about 5,200 characters), while loops over a single character (`a*`) are iterative and handled at 100,000 characters. Long inputs with group loops belong on Pike.

## Limitations
- Subset only (see above); no lookaround, backreferences, flags, `\b`, Unicode classes or case folding.
- Fuzz scope: pattern depth at most 3, text length at most 10, 7 distinct text symbols (a, b, c, 1, space, newline, underscore), almost no non-ASCII text (a few hand-written tests only). A one-off stress run (depth 4, text length 24, three other seeds, 13,475 cases) also found no disagreement, but it is not part of the demo.
- `re` behaviour can differ between Python versions in corner cases; I compared against 3.13 locally. CI runs 3.11 and 3.13.
- Pike's thread deduplication is by (instruction, loops-entered-at-this-position); the O(n*m) bound is tested by `max_threads <= len(program)` and by step ratios, not proved.
- Counted repetition is expanded, so `(a{1000}){1000}` is refused (`limit`) rather than handled.

## Layout
```
rxengine/  parser.py (pattern -> AST, errors, limits)   nodes.py (AST)   semantics.py (chars, anchors)
           backtrack.py   program.py (AST -> NFA)   pikevm.py   dfa.py (lazy DFA, 3 modes)
           api.py (Regex, Match)   fuzz.py (generators, oracle)   bugs.py (injected bugs)   demo.py   __main__.py
tests/     177 tests: parser, semantics vs re, complexity, fuzz and injected bugs, CLI
tools/     mutation_check.py (15 mutants, scratch copies, "pattern not found" is an error)
```
