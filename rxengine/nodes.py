"""AST node types.  All three matchers consume exactly these nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

from . import semantics as sem


@dataclass(frozen=True)
class Empty:
    pass


@dataclass(frozen=True)
class Char:
    ch: str
    pred: object = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        ch = self.ch
        object.__setattr__(self, "pred", lambda c: c == ch)


@dataclass(frozen=True)
class Any:
    pred: object = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "pred", sem.any_pred)


@dataclass(frozen=True)
class CharSet:
    items: Tuple[tuple, ...]
    negated: bool = False
    pred: object = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "pred", sem.build_set_predicate(self.items, self.negated))


@dataclass(frozen=True)
class Cat:
    items: Tuple[object, ...]


@dataclass(frozen=True)
class Alt:
    items: Tuple[object, ...]


@dataclass(frozen=True)
class Group:
    index: int
    node: object


@dataclass(frozen=True)
class Repeat:
    node: object
    min: int
    max: Optional[int]  # None = unbounded
    greedy: bool = True


@dataclass(frozen=True)
class Assert:
    kind: str  # "bol" | "eol"


SINGLE = (Char, Any, CharSet)


def nullable(node) -> bool:
    """Can the node match the empty string (ignoring assertions' context)?"""
    if isinstance(node, (Empty, Assert)):
        return True
    if isinstance(node, SINGLE):
        return False
    if isinstance(node, Cat):
        return all(nullable(x) for x in node.items)
    if isinstance(node, Alt):
        return any(nullable(x) for x in node.items)
    if isinstance(node, Group):
        return nullable(node.node)
    if isinstance(node, Repeat):
        return node.min == 0 or nullable(node.node)
    raise TypeError(node)


def transform(node, fn):
    """Bottom-up rebuild: fn(new_node) -> node.  Used by the injected-bug tests."""
    if isinstance(node, Cat):
        node = Cat(tuple(transform(x, fn) for x in node.items))
    elif isinstance(node, Alt):
        node = Alt(tuple(transform(x, fn) for x in node.items))
    elif isinstance(node, Group):
        node = Group(node.index, transform(node.node, fn))
    elif isinstance(node, Repeat):
        node = Repeat(transform(node.node, fn), node.min, node.max, node.greedy)
    return fn(node)
