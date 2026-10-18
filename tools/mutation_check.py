"""Mutation check: break one behaviour at a time in a scratch COPY and expect a test to fail.

    python tools/mutation_check.py            # all mutants
    python tools/mutation_check.py 3 7        # only mutants number 3 and 7

Each mutant = (file, exact one-line text, replacement, how many occurrences).
If the text is not found the mutant is reported as "pattern not found" (exit 2)
instead of silently testing an unchanged copy.  Exit 0 only when every mutant
was killed.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

# (name, file, old, new, occurrences: 1 = first, 0 = all)
MUTANTS = [
    ("backtrack: empty-iteration guard removed", "rxengine/backtrack.py",
     "                if (mx is None or count < mx) and j != last:",
     "                if (mx is None or count < mx):", 1),
    ("backtrack: captures not restored on failure", "rxengine/backtrack.py",
     "            caps[2 * g], caps[2 * g + 1] = old", "            pass", 1),
    ("backtrack: step budget never enforced", "rxengine/backtrack.py",
     "        if self._used > self.budget:", "        if False:", 0),
    ("pike: no priority cut on MATCH", "rxengine/pikevm.py",
     "                    if self.cut_on_match:", "                    if False:", 1),
    ("pike: fullmatch accepts a match before end of text", "rxengine/pikevm.py",
     '                    if mode == "full" and pos != n:', "                    if False:", 1),
    ("pike: LCHECK targets swapped (empty-iteration rule)", "rxengine/pikevm.py",
     "                stack.append((b if ls & bit else c, ls & ~bit, caps))",
     "                stack.append((c if ls & bit else b, ls & ~bit, caps))", 1),
    ("dfa: no priority cut on MATCH (leftmost-longest)", "rxengine/dfa.py",
     "                if self.cut:", "                if False:", 1),
    ("dfa: cache never flushed", "rxengine/dfa.py",
     "            if len(self._states) >= self.max_states:", "            if False:", 1),
    ("dfa: $ never resolved at end of text", "rxengine/dfa.py",
     "        resolve = sym == NLLAST or sym == EOT", "        resolve = False", 1),
    ("parser: lazy marker ignored", "rxengine/parser.py",
     "            greedy = False", "            greedy = True", 1),
    ("parser: min>max not rejected", "rxengine/parser.py",
     "        if hi is not None and lo > hi:", "        if False:", 1),
    ("semantics: $ only at very end", "rxengine/semantics.py",
     '    return i == n or (i == n - 1 and text[i] == "\\n")', "    return i == n", 1),
    ("semantics: . matches newline", "rxengine/semantics.py",
     '    return ch != "\\n"', "    return True", 1),
    ("semantics: \\D \\W \\S behave like \\d \\w \\s", "rxengine/semantics.py",
     "    return (ch in members) == kind.islower()", "    return ch in members", 1),
    ("api: finditer lets an empty match repeat", "rxengine/api.py",
     "            must_advance = e == s", "            must_advance = False", 1),
]

IGNORE = shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "*.pyc")


def run_one(idx: int, mutant) -> str:
    name, rel, old, new, occ = mutant
    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp) / "repo"
        shutil.copytree(ROOT, work, ignore=IGNORE)
        target = work / rel
        text = target.read_text(encoding="utf-8")
        if old not in text:
            return "NOT FOUND"
        text = text.replace(old, new) if occ == 0 else text.replace(old, new, occ)
        target.write_text(text, encoding="utf-8", newline="")
        try:
            # fast, focused files first so a broken mutant fails (or hangs) early, not after the fuzz tests
            order = ["tests/test_parser.py", "tests/test_semantics.py", "tests/test_cli.py",
                     "tests/test_complexity.py", "tests/test_fuzz.py"]
            r = subprocess.run([sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *order],
                               cwd=work, capture_output=True, text=True, timeout=240)
        except subprocess.TimeoutExpired:
            return "KILLED (timeout: the mutant hangs the suite)"
        if r.returncode == 0:
            return "SURVIVED"
        last = [ln for ln in r.stdout.splitlines() if ln.startswith("FAILED") or "Error" in ln][:1]
        return "KILLED  " + (last[0][:90] if last else f"(exit {r.returncode})")


def main(argv) -> int:
    chosen = [int(a) for a in argv] or list(range(1, len(MUTANTS) + 1))
    results = {}
    for i in chosen:
        res = run_one(i, MUTANTS[i - 1])
        results[i] = res
        print(f"{i:2d}. {MUTANTS[i - 1][0]:58s} {res}", flush=True)
    killed = sum(r.startswith("KILLED") for r in results.values())
    print(f"\n{killed}/{len(results)} mutants killed")
    if any(r == "NOT FOUND" for r in results.values()):
        print("pattern not found for at least one mutant: fix tools/mutation_check.py")
        return 2
    return 0 if killed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
