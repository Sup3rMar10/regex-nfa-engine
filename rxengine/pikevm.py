"""Pike VM: Thompson-NFA simulation with thread priority and captures.

One pass over the text, a list of threads in priority order per position.
Leftmost-first semantics: the first MATCH thread in priority order wins and
cuts every lower-priority thread.  Work per character is bounded by the
program size, so total work is O(n * m) regardless of the pattern.

``steps`` counts instruction visits (closure) plus thread executions.
"""

from __future__ import annotations

from . import semantics as sem
from .program import BOL, CONSUME, EOL, JMP, LCHECK, LSTART, MATCH, SAVE, SPLIT, Program


class PikeVM:
    name = "pike"
    # Seam used only by the injected-bug tests: real engines cut on match.
    cut_on_match = True

    def __init__(self, prog: Program) -> None:
        self.prog = prog
        self.ins = prog.ins
        self.steps = 0
        self.max_threads = 0
        self._caps0 = (-1,) * prog.nslots

    def reset(self) -> None:
        self.steps = 0
        self.max_threads = 0

    def _add(self, lst, seen, pc, caps, pos, text) -> None:
        ins = self.ins
        stack = [(pc, 0, caps)]
        steps = 0
        while stack:
            pc, ls, caps = stack.pop()
            steps += 1
            op, a, b, c = ins[pc]
            key = pc if (op == CONSUME or op == MATCH or ls == 0) else (pc, ls)
            if key in seen:
                continue
            seen.add(key)
            if op == CONSUME or op == MATCH:
                lst.append((pc, caps))
            elif op == JMP:
                stack.append((a, ls, caps))
            elif op == SPLIT:
                stack.append((b, ls, caps))
                stack.append((a, ls, caps))
            elif op == SAVE:
                stack.append((pc + 1, ls, caps[:a] + (pos,) + caps[a + 1:]))
            elif op == BOL:
                if sem.at_bol(pos):
                    stack.append((pc + 1, ls, caps))
            elif op == EOL:
                if sem.at_eol(text, pos):
                    stack.append((pc + 1, ls, caps))
            elif op == LSTART:
                stack.append((pc + 1, ls | (1 << a), caps))
            elif op == LCHECK:
                bit = 1 << a
                stack.append((b if ls & bit else c, ls & ~bit, caps))
        self.steps += steps

    def run(self, text: str, start: int, mode: str, must_advance: bool = False):
        """mode: 'search' | 'match' | 'full'.  Returns a caps tuple or None."""
        n = len(text)
        ins = self.ins
        anchored = mode != "search"
        clist: list = []
        seen: set = set()
        matched = None
        pos = start
        while True:
            if matched is None and (pos == start or not anchored):
                self._add(clist, seen, 0, self._caps0, pos, text)
            if len(clist) > self.max_threads:
                self.max_threads = len(clist)
            ch = text[pos] if pos < n else None
            nlist: list = []
            nseen: set = set()
            for pc, caps in clist:
                self.steps += 1
                op, a, _, _ = ins[pc]
                if op == MATCH:
                    if mode == "full" and pos != n:
                        continue
                    if must_advance and pos == start:
                        continue
                    matched = caps
                    if self.cut_on_match:
                        break
                elif ch is not None and a(ch):
                    self._add(nlist, nseen, pc + 1, caps, pos + 1, text)
            clist, seen = nlist, nseen
            if pos >= n or (not clist and (anchored or matched is not None)):
                break
            pos += 1
        return matched
