
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Set, Tuple

Atom = Tuple


def is_var(term) -> bool:
    return isinstance(term, str) and term.startswith('?')


@dataclass(frozen=True)
class Rule:
    name: str
    premises: Tuple[Atom, ...]
    conclusion: Atom

    def __str__(self) -> str:
        def fmt(a):
            return f"{a[0]}({','.join(str(x) for x in a[1:])})"
        left = " AND ".join(fmt(p) for p in self.premises)
        return f"{self.name}: {left} -> {fmt(self.conclusion)}"


class KnowledgeBase:
    PREDICATES: Dict[str, int] = {
        # types
        'Book': 1, 'Copy': 1, 'User': 1, 'Shelf': 1, 'Row': 1, 'Room': 1,
        'Table': 1, 'Chair': 1, 'Robot': 1, 'PhysicalObject': 1,
        # relations
        'OnShelf': 2, 'OnTable': 2, 'OnChair': 2, 'OnFloor': 1,
        'InRow': 2, 'InRoom': 2,
        'BorrowedBy': 2, 'ReservedFor': 2,
        'At': 2, 'Holding': 2,
        'BelongsOn': 2,      # BelongsOn(B15, S7): original place of the book
        'QRRead': 1,         # QRRead(B15): robot decoded the QR of this book
        # derived (by rules)
        'Reserved': 1, 'Available': 1, 'NotAvailable': 1, 'NeedToOrganize': 1,
        'TargetShelf': 2, 'TargetRow': 2, 'TargetRoom': 2,
    }
    FUNCTIONS = ('CopyOf', 'LocationOf')

    def __init__(self):
        self._facts: Set[Atom] = set()
        self._functions: Dict[str, Dict[str, str]] = {f: {} for f in self.FUNCTIONS}
        self._rules: List[Rule] = []

    # ---------------- Facts ----------------
    def add_fact(self, predicate: str, *args) -> bool:
        self._check_atom(predicate, args)
        if any(is_var(a) for a in args):
            raise ValueError("facts must be ground (no variables)")
        fact = (predicate, *args)
        if fact in self._facts:
            return False
        self._facts.add(fact)
        return True

    def remove_fact(self, predicate: str, *args) -> bool:
        fact = (predicate, *args)
        if fact in self._facts:
            self._facts.remove(fact)
            return True
        return False

    def has_fact(self, predicate: str, *args) -> bool:
        return (predicate, *args) in self._facts

    def facts(self) -> Set[Atom]:
        return set(self._facts)

    def find(self, predicate: str, *pattern) -> Iterator[Atom]:
        for fact in self._facts:
            if fact[0] != predicate or len(fact) != len(pattern) + 1:
                continue
            if all(is_var(p) or p == v for p, v in zip(pattern, fact[1:])):
                yield fact

    # ---------------- Functions ----------------
    def set_function(self, name: str, arg: str, value: str) -> None:
        if name not in self.FUNCTIONS:
            raise ValueError(f"undefined function: {name}")
        self._functions[name][arg] = value

    def get_function(self, name: str, arg: str) -> Optional[str]:
        return self._functions[name].get(arg)

    def functions(self) -> Dict[str, Dict[str, str]]:
        return {k: dict(v) for k, v in self._functions.items()}

    # ---------------- Rules ----------------
    def add_rule(self, name: str, premises: List[Atom], conclusion: Atom) -> None:
        for atom in [*premises, conclusion]:
            self._check_atom(atom[0], atom[1:])
        self._rules.append(Rule(name, tuple(premises), conclusion))

    def rules(self) -> List[Rule]:
        return list(self._rules)

    # ---------------- Location path ----------------
    def location_path(self, book: str) -> Optional[List[str]]:
        shelf = self.get_function('LocationOf', book)
        if shelf is None:
            return None
        path = ['Library']
        room = next((f[2] for f in self.find('InRoom', shelf, '?r')), None)
        row = next((f[2] for f in self.find('InRow', shelf, '?r')), None)
        for part in (room, row, shelf):
            if part:
                path.append(part)
        return path

    # ---------------- Helpers ----------------
    def _check_atom(self, predicate: str, args) -> None:
        if predicate not in self.PREDICATES:
            raise ValueError(f"undefined predicate: {predicate}")
        if len(args) != self.PREDICATES[predicate]:
            raise ValueError(
                f"{predicate} needs {self.PREDICATES[predicate]} args, "
                f"got {len(args)}")

    def summary(self) -> str:
        lines = [f"== KB: {len(self._facts)} facts, "
                 f"{sum(len(v) for v in self._functions.values())} function values, "
                 f"{len(self._rules)} rules =="]
        lines.append("-- Facts --")
        for f in sorted(self._facts, key=str):
            lines.append(f"  {f[0]}({', '.join(f[1:])})")
        lines.append("-- Functions --")
        for name, table in self._functions.items():
            for arg, val in sorted(table.items()):
                lines.append(f"  {name}({arg}) = {val}")
        lines.append("-- Rules --")
        for r in self._rules:
            lines.append(f"  {r}")
        return "\n".join(lines)


# ---------------------------------------------------------------
# Rules: كلها في rules.py
# ---------------------------------------------------------------
def load_default_rules(kb: KnowledgeBase) -> None:
    from .rules import load_kb_rules          # lazy import (rules.py مستقل)
    load_kb_rules(kb)


# ---------------------------------------------------------------
# FUNCTIONS to use the rules
# ---------------------------------------------------------------
def register_book(kb: KnowledgeBase, book: str, shelf: str) -> None:
    """Call once per book when building the KB (replaces set_function alone)."""
    kb.add_fact('Book', book)
    kb.set_function('LocationOf', book, shelf)
    kb.add_fact('BelongsOn', book, shelf)


def target_of(kb: KnowledgeBase, book: str):
    """
    After the robot decodes the QR of `book`, infer where it must go.
    Returns (room, row, shelf, proof_text) or None if not derivable.
    """
    from . import reasoning_engine as RE          # lazy import (avoids circular import)
    kb.add_fact('QRRead', book)
    facts, rules = kb.facts(), kb.rules()
    s = RE.ask(('TargetShelf', book, '?s'), facts, rules)
    if not s:
        kb.remove_fact('QRRead', book)
        return None
    shelf = s[0]['?s']
    row = RE.ask(('TargetRow', book, '?r'), facts, rules)[0]['?r']
    room = RE.ask(('TargetRoom', book, '?r'), facts, rules)[0]['?r']
    proof = RE.explain(('TargetShelf', book, shelf), facts, rules)
    return room, row, shelf, proof


# ---------------------------------------------------------------
# Sample KB + test
# ---------------------------------------------------------------
def build_sample_kb() -> KnowledgeBase:
    kb = KnowledgeBase()
    for c in ('C15', 'C20'):
        kb.add_fact('Copy', c)
    for u in ('Ahmed', 'Sara'):
        kb.add_fact('User', u)
    for s in ('S7', 'S8'):
        kb.add_fact('Shelf', s)
        kb.add_fact('InRow', s, 'RowA2')
        kb.add_fact('InRoom', s, 'Room3')
    kb.add_fact('Row', 'RowA2')
    kb.add_fact('Room', 'Room3')
    kb.add_fact('Table', 'Table1')
    kb.add_fact('Chair', 'Chair1')
    kb.add_fact('Robot', 'Robot1')

    register_book(kb, 'B15', 'S7')
    register_book(kb, 'B20', 'S8')
    kb.set_function('CopyOf', 'C15', 'B15')
    kb.set_function('CopyOf', 'C20', 'B20')

    kb.add_fact('OnShelf', 'B15', 'S7')
    kb.add_fact('OnTable', 'B20', 'Table1')
    kb.add_fact('BorrowedBy', 'C20', 'Ahmed')
    kb.add_fact('ReservedFor', 'B15', 'Sara')
    kb.add_fact('At', 'Robot1', 'Room3')

    load_default_rules(kb)
    return kb


if __name__ == "__main__":
    kb = build_sample_kb()
    print(kb.summary())
    print("\n-- QR -> destination --")
    print(target_of(kb, 'B20'))
    print(target_of(kb, 'B99'))