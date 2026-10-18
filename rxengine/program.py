"""AST -> NFA program (a flat list of instructions) for the Pike VM and the DFA.

Instructions are tuples ``(op, a, b, c)``:

    CONSUME pred          consume one character if pred(ch)
    SPLIT   x y           try x first, then y (this order IS the priority)
    JMP     x
    SAVE    slot          record the position in capture slot
    BOL / EOL             assertions
    LSTART  k             enter an iteration of loop k (marks "started at this position")
    LCHECK  k empty cont  end of an iteration: if it consumed nothing go to ``empty``
                          else go to ``cont``
    MATCH

LSTART / LCHECK reproduce CPython's empty-iteration rule: an iteration that
matches the empty string may complete (its captures stay set) but the loop does
not run again.  They are only emitted for loops whose body can match empty.
The "started at this position" mark only lives inside one epsilon-closure, so
it is carried as a bitmask during closure and never stored in a thread.

Counted repetition {m,n} is expanded by copying the body, so the program size
is capped (``MAX_PROGRAM``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from .errors import RegexSyntaxError
from .nodes import Alt, Assert, Cat, Empty, Group, Repeat, SINGLE, nullable

CONSUME, SPLIT, JMP, SAVE, BOL, EOL, LSTART, LCHECK, MATCH = range(9)
OP_NAMES = ["CONSUME", "SPLIT", "JMP", "SAVE", "BOL", "EOL", "LSTART", "LCHECK", "MATCH"]

MAX_PROGRAM = 50_000


@dataclass(frozen=True)
class Program:
    ins: Tuple[tuple, ...]
    ngroups: int

    @property
    def nslots(self) -> int:
        return 2 * (self.ngroups + 1)

    def __len__(self) -> int:
        return len(self.ins)


class _Compiler:
    def __init__(self, max_size: int) -> None:
        self.ins: List[list] = []
        self.max_size = max_size
        self.loop_ids: dict = {}

    def emit(self, op, a=None, b=None, c=None) -> int:
        self.ins.append([op, a, b, c])
        if len(self.ins) > self.max_size:
            raise RegexSyntaxError(
                f"compiled program larger than {self.max_size} instructions "
                "(counted repetitions are expanded)", 0, "limit")
        return len(self.ins) - 1

    def loop_id(self, node) -> int:
        return self.loop_ids.setdefault(id(node), len(self.loop_ids))

    def gen(self, node) -> None:
        if isinstance(node, Empty):
            return
        if isinstance(node, SINGLE):
            self.emit(CONSUME, node.pred, node)
        elif isinstance(node, Cat):
            for x in node.items:
                self.gen(x)
        elif isinstance(node, Alt):
            jumps = []
            for idx, branch in enumerate(node.items):
                last = idx == len(node.items) - 1
                if not last:
                    split = self.emit(SPLIT, None, None)
                    self.ins[split][1] = len(self.ins)
                self.gen(branch)
                if not last:
                    jumps.append(self.emit(JMP, None))
                    self.ins[split][2] = len(self.ins)
            for j in jumps:
                self.ins[j][1] = len(self.ins)
        elif isinstance(node, Group):
            self.emit(SAVE, 2 * node.index)
            self.gen(node.node)
            self.emit(SAVE, 2 * node.index + 1)
        elif isinstance(node, Assert):
            self.emit(BOL if node.kind == "bol" else EOL)
        elif isinstance(node, Repeat):
            self.gen_repeat(node)
        else:
            raise TypeError(node)

    def gen_repeat(self, node: Repeat) -> None:
        body, mn, mx = node.node, node.min, node.max
        for _ in range(mn):
            self.gen(body)
        guarded = nullable(body)
        k = self.loop_id(node) if guarded else None

        def order(body_pc: int, exit_pc: int):
            return (body_pc, exit_pc) if node.greedy else (exit_pc, body_pc)

        if mx is None:
            head = self.emit(SPLIT, None, None)
            body_pc = len(self.ins)
            if guarded:
                self.emit(LSTART, k)
            self.gen(body)
            check = self.emit(LCHECK, k, None, head) if guarded else self.emit(JMP, head)
            exit_pc = len(self.ins)
            self.ins[head][1], self.ins[head][2] = order(body_pc, exit_pc)
            if guarded:
                self.ins[check][2] = exit_pc
            return
        splits, checks = [], []
        for _ in range(mx - mn):
            split = self.emit(SPLIT, None, None)
            body_pc = len(self.ins)
            if guarded:
                self.emit(LSTART, k)
            self.gen(body)
            if guarded:
                check = self.emit(LCHECK, k, None, len(self.ins) + 1)
                checks.append(check)
            splits.append((split, body_pc))
        exit_pc = len(self.ins)
        for split, body_pc in splits:
            self.ins[split][1], self.ins[split][2] = order(body_pc, exit_pc)
        for check in checks:
            self.ins[check][2] = exit_pc


def compile_program(ast, ngroups: int, max_size: int = MAX_PROGRAM) -> Program:
    c = _Compiler(max_size)
    c.emit(SAVE, 0)
    c.gen(ast)
    c.emit(SAVE, 1)
    c.emit(MATCH)
    return Program(tuple(tuple(i) for i in c.ins), ngroups)


def disassemble(prog: Program) -> str:
    lines = []
    for pc, (op, a, b, c) in enumerate(prog.ins):
        args = [] if op == CONSUME else [str(x) for x in (a, b, c) if x is not None]
        extra = ""
        if op == CONSUME:
            extra = " " + type(b).__name__ if b is not None else ""
        lines.append(f"{pc:4d} {OP_NAMES[op]}{extra} {' '.join(args)}".rstrip())
    return "\n".join(lines)
