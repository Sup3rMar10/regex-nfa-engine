"""Lazy DFA built on demand from the NFA program (no captures).

A DFA state is an ordered tuple of NFA "leaf" entries (consuming instructions,
MATCH, and pending ``$`` assertions).  Transitions are computed the first time
they are needed and cached; ``max_states`` caps the cache and a full flush
rebuilds it from the current state.

Three modes share this class, each with its own cache:

  * ``first``  anchored, priority-ordered states, a MATCH entry cuts lower
               priority entries (RE2's trick).  Gives the leftmost-FIRST end of a
               match, i.e. what ``re`` returns.
  * ``full``   anchored, no cutting, unordered (plain subset construction).
               Used by fullmatch.
  * ``any``    unanchored (the start state is re-added at every step), no
               cutting.  Linear-time "is there any match from here" check.

``$`` depends on what follows, so a state keeps ``$`` entries pending and the
transition symbol says how to resolve them: a normal character (``$`` false),
NLLAST (a newline that is the final character: ``$`` true), EOT (end of text:
``$`` true).  The initial state also carries two flags: at_start (``^`` may
hold) and ignore_match (used for the "must advance" rule of finditer).

``steps`` counts character transitions taken (cache hit or miss).
"""

from __future__ import annotations

from .program import BOL, CONSUME, EOL, JMP, LCHECK, LSTART, MATCH, SAVE, SPLIT, Program

NLLAST = "\n$"  # a "\n" that is the last character of the text
EOT = ""        # end of text


class _State:
    __slots__ = ("entries", "flags", "has_match", "has_eol", "trans")

    def __init__(self, entries, flags, ins) -> None:
        self.entries = entries
        self.flags = flags
        self.has_match = any(ins[pc][0] == MATCH for pc, _ in entries)
        self.has_eol = any(ins[pc][0] == EOL for pc, _ in entries)
        self.trans: dict = {}


class LazyDFA:
    AT_START = 1
    IGNORE_MATCH = 2

    def __init__(self, prog: Program, mode: str = "first", max_states: int = 2000) -> None:
        if mode not in ("first", "full", "any"):
            raise ValueError(mode)
        self.prog = prog
        self.ins = prog.ins
        self.mode = mode
        self.cut = mode == "first"
        self.max_states = max_states
        self._states: dict = {}
        self._inits: dict = {}
        # statistics
        self.steps = 0
        self.hits = 0
        self.misses = 0
        self.flushes = 0
        self.states_built = 0
        self.max_live = 0

    def reset_stats(self) -> None:
        self.steps = self.hits = self.misses = self.flushes = 0
        self.states_built = 0
        self.max_live = 0

    @property
    def live_states(self) -> int:
        return len(self._states)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    # -- closure ---------------------------------------------------------
    def _closure(self, items, at_start, resolve_eol, ignore_match, seen, out) -> None:
        ins = self.ins
        stack = list(reversed(items))
        while stack:
            pc, ls = stack.pop()
            op, a, b, c = ins[pc]
            leaf = op == CONSUME or op == MATCH
            key = pc if (leaf or ls == 0) else (pc, ls)
            if key in seen:
                continue
            seen.add(key)
            if op == CONSUME:
                out.append((pc, 0))
            elif op == MATCH:
                if ignore_match:
                    continue
                out.append((pc, 0))
                if self.cut:
                    return
            elif op == EOL:
                if resolve_eol:
                    stack.append((pc + 1, ls))
                else:
                    out.append((pc, ls))
            elif op == BOL:
                if at_start:
                    stack.append((pc + 1, ls))
            elif op == JMP:
                stack.append((a, ls))
            elif op == SPLIT:
                stack.append((b, ls))
                stack.append((a, ls))
            elif op == SAVE:
                stack.append((pc + 1, ls))
            elif op == LSTART:
                stack.append((pc + 1, ls | (1 << a)))
            elif op == LCHECK:
                bit = 1 << a
                stack.append((b if ls & bit else c, ls & ~bit))

    def _intern(self, entries, flags=0):
        if self.mode != "first":
            entries = tuple(sorted(set(entries)))
        key = (tuple(entries), flags)
        st = self._states.get(key)
        if st is None:
            if len(self._states) >= self.max_states:
                self._states.clear()
                self._inits.clear()
                self.flushes += 1
            st = _State(key[0], flags, self.ins)
            self._states[key] = st
            self.states_built += 1
            if len(self._states) > self.max_live:
                self.max_live = len(self._states)
        return st

    def initial(self, pos_is_zero: bool, ignore_match: bool = False):
        flags = (self.AT_START if pos_is_zero else 0) | (self.IGNORE_MATCH if ignore_match else 0)
        st = self._inits.get(flags)
        if st is not None and self._states.get((st.entries, st.flags)) is st:
            return st
        out: list = []
        self._closure([(0, 0)], pos_is_zero, False, ignore_match, set(), out)
        st = self._intern(out, flags)
        self._inits[flags] = st
        return st

    # -- transitions -----------------------------------------------------
    def _compute(self, st: _State, sym: str):
        ins = self.ins
        resolve = sym == NLLAST or sym == EOT
        entries = st.entries
        if resolve and st.has_eol:
            out: list = []
            self._closure(list(entries), bool(st.flags & self.AT_START), True,
                          bool(st.flags & self.IGNORE_MATCH), set(), out)
            entries = tuple(out)
            matched = any(ins[pc][0] == MATCH for pc, _ in entries)
        else:
            matched = st.has_match
        if sym == EOT:
            return None, matched
        ch = "\n" if sym == NLLAST else sym
        nxt_items = [(pc + 1, 0) for pc, _ in entries
                     if ins[pc][0] == CONSUME and ins[pc][1](ch)]
        out = []
        seen: set = set()
        self._closure(nxt_items, False, False, False, seen, out)
        if self.mode == "any":
            self._closure([(0, 0)], False, False, False, seen, out)
        if not out:
            return None, matched
        return self._intern(out), matched

    def _step(self, st: _State, sym: str):
        self.steps += 1
        t = st.trans.get(sym)
        if t is not None:
            self.hits += 1
            return t
        self.misses += 1
        t = self._compute(st, sym)
        st.trans[sym] = t
        return t

    # -- runners ---------------------------------------------------------
    def run_anchored(self, text: str, s: int, must_advance: bool = False):
        """Return the end of the match starting exactly at s (or None).

        ``first``: leftmost-first end.  ``full``: n if the whole tail matches.
        """
        n = len(text)
        st = self.initial(s == 0, must_advance)
        last = None
        for i in range(s, n):
            c = text[i]
            sym = NLLAST if (c == "\n" and i == n - 1) else c
            st2, matched = self._step(st, sym)
            if matched and self.mode == "first":
                last = i
            if st2 is None:
                return last
            st = st2
        _, matched = self._step(st, EOT)
        if matched:
            return n
        return last if self.mode == "first" else None

    def any_match_from(self, text: str, s: int, must_advance: bool = False) -> bool:
        """``any`` mode: does any match start at some position >= s?"""
        n = len(text)
        st = self.initial(s == 0, must_advance)
        for i in range(s, n):
            c = text[i]
            sym = NLLAST if (c == "\n" and i == n - 1) else c
            st2, matched = self._step(st, sym)
            if matched:
                return True
            st = st2 if st2 is not None else self.initial(False)
        _, matched = self._step(st, EOT)
        return matched


class DFAEngine:
    """Search / match / fullmatch on spans, using the three lazy DFAs."""

    name = "dfa"

    def __init__(self, prog: Program, max_states: int = 2000) -> None:
        self.first = LazyDFA(prog, "first", max_states)
        self.full = LazyDFA(prog, "full", max_states)
        self.any = LazyDFA(prog, "any", max_states)

    @property
    def steps(self) -> int:
        return self.first.steps + self.full.steps + self.any.steps

    def reset(self) -> None:
        for d in (self.first, self.full, self.any):
            d.reset_stats()

    def run(self, text: str, start: int, mode: str, must_advance: bool = False):
        """Returns (s, e) or None."""
        n = len(text)
        if mode == "full":
            return (start, n) if self.full.run_anchored(text, start) == n else None
        if mode == "match":
            e = self.first.run_anchored(text, start, must_advance)
            return None if e is None else (start, e)
        if not self.any.any_match_from(text, start, must_advance):
            return None
        for s in range(start, n + 1):
            e = self.first.run_anchored(text, s, must_advance and s == start)
            if e is not None:
                return (s, e)
        return None
