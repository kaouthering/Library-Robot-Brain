
import itertools
from .knowledge_base import is_var


# ---------------- Unification ----------------
def walk(t, s):
    while is_var(t) and t in s:
        t = s[t]
    return t


def unify(a, b, s):
    if s is None:
        return None
    a, b = walk(a, s), walk(b, s)
    if a == b:
        return s
    if is_var(a):
        return {**s, a: b}
    if is_var(b):
        return {**s, b: a}
    if isinstance(a, tuple) and isinstance(b, tuple) and len(a) == len(b):
        for x, y in zip(a, b):
            s = unify(x, y, s)
            if s is None:
                return None
        return s
    return None


def subst(t, s):
    t = walk(t, s)
    if isinstance(t, tuple):
        return tuple(subst(x, s) for x in t)
    return t


def fmt(atom):
    return f"{atom[0]}({', '.join(str(x) for x in atom[1:])})"


# ---------------- Forward Chaining ----------------
def _match(premises, facts, s):
    if not premises:
        yield s
        return
    p, rest = premises[0], premises[1:]
    for f in facts:
        s2 = unify(p, f, s)
        if s2 is not None:
            yield from _match(rest, facts, s2)


def forward_chain(facts, rules):
    """يرجع (كل الحقائق بعد الاستنتاج, قائمة (حقيقة جديدة, اسم القاعدة))."""
    facts, trace = set(facts), []
    changed = True
    while changed:
        changed = False
        for r in rules:
            for s in list(_match(r.premises, facts, {})):
                new = subst(r.conclusion, s)
                if new not in facts:
                    facts.add(new)
                    trace.append((new, r.name))
                    changed = True
    return facts, trace


# ---------------- Backward Chaining ----------------
_counter = itertools.count()


def _rename(t, n):
    if is_var(t):
        return f"{t}_{n}"
    if isinstance(t, tuple):
        return tuple(_rename(x, n) for x in t)
    return t


def _bc_goal(goal, facts, rules, s):
    goal = subst(goal, s)
    for f in facts:
        s2 = unify(goal, f, s)
        if s2 is not None:
            yield s2
    for r in rules:
        n = next(_counter)
        prem = [_rename(p, n) for p in r.premises]
        concl = _rename(r.conclusion, n)
        s2 = unify(goal, concl, s)
        if s2 is not None:
            yield from _bc_goals(prem, facts, rules, s2)


def _bc_goals(goals, facts, rules, s):
    if not goals:
        yield s
        return
    for s1 in _bc_goal(goals[0], facts, rules, s):
        yield from _bc_goals(goals[1:], facts, rules, s1)


def ask(query, facts, rules):
    """Backward chaining: يرجع قائمة الإجابات (ربط متغيرات الاستعلام)."""
    qvars = [v for v in query if is_var(v)]
    answers = []
    for s in _bc_goal(query, facts, rules, {}):
        ans = {v: subst(v, s) for v in qvars}
        if ans not in answers:
            answers.append(ans)
    return answers


def explain(goal, facts, rules):
    """شرح نصي لإثبات هدف أرضي بالتسلسل الخلفي."""
    if goal in facts:
        return f"{fmt(goal)} is a known fact"
    for r in rules:
        n = next(_counter)
        prem = [_rename(p, n) for p in r.premises]
        concl = _rename(r.conclusion, n)
        s = unify(goal, concl, {})
        if s is None:
            continue
        for s2 in _bc_goals(prem, facts, rules, s):
            parts = " AND ".join(fmt(subst(p, s2)) for p in prem)
            return f"{fmt(goal)}  <=  [{r.name}]  {parts}"
    return None


# ---------------- Truth Evaluation ----------------
def is_true(atom, facts, rules):
    return bool(ask(atom, facts, rules))


def exists(pattern, facts, rules):
    """∃x P(x)"""
    return bool(ask(pattern, facts, rules))


def forall(pred_p, pred_q, facts, rules):
    """∀x (P(x) -> Q(x)) على كل x تحقق P. يرجع (True/False, الأمثلة المضادة)"""
    bad = []
    for ans in ask((pred_p, '?x'), facts, rules):
        x = ans['?x']
        if not is_true((pred_q, x), facts, rules):
            bad.append(x)
    return (not bad), bad


# ---------------- Resolution (ground clauses) ----------------
def resolve(c1, c2):
    """
    c1, c2: مجموعات literals نصية مثل {'Book(B15)', '~Magazine(B15)'}
    يرجع قائمة الـresolvents.
    """
    out = []
    for lit in c1:
        neg = lit[1:] if lit.startswith('~') else '~' + lit
        if neg in c2:
            out.append(frozenset((set(c1) - {lit}) | (set(c2) - {neg})))
    return out