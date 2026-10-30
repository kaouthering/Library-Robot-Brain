"""The inference engine on its own: unification, chaining, quantifiers, resolution."""
import unittest

from library_robot import reasoning_engine as RE
from library_robot.knowledge_base import KnowledgeBase, build_sample_kb, load_default_rules, target_of


class Unification(unittest.TestCase):
    def test_variable_binds_to_constant(self):
        self.assertEqual(RE.unify(("Book", "?x"), ("Book", "B15"), {}), {"?x": "B15"})

    def test_two_variables_bind_consistently(self):
        s = RE.unify(("BorrowedBy", "?x", "Ahmed"), ("BorrowedBy", "C20", "Ahmed"), {})
        self.assertEqual(s, {"?x": "C20"})
        self.assertIsNone(RE.unify(("P", "?x", "?x"), ("P", "a", "b"), {}))

    def test_clash_fails(self):
        self.assertIsNone(RE.unify(("Book", "B15"), ("Book", "B20"), {}))
        self.assertIsNone(RE.unify(("Book", "B15"), ("Copy", "B15"), {}))
        self.assertIsNone(RE.unify(("Book", "B15"), ("Book", "B15", "x"), {}))


class Chaining(unittest.TestCase):
    def setUp(self):
        self.kb = build_sample_kb()
        self.facts, self.rules = self.kb.facts(), self.kb.rules()

    def test_forward_chaining_derives_everything(self):
        derived, trace = RE.forward_chain(self.facts, self.rules)
        self.assertIn(("NotAvailable", "C20"), derived)
        self.assertIn(("Reserved", "B15"), derived)
        self.assertIn(("NeedToOrganize", "B20"), derived)       # R1: B20 is on a table
        names = {r for _, r in trace}
        self.assertTrue({"R1_table", "R4_borrowed", "R5_reserved"} <= names)

    def test_forward_chaining_does_not_change_input(self):
        before = set(self.facts)
        RE.forward_chain(self.facts, self.rules)
        self.assertEqual(self.facts, before)

    def test_backward_chaining_proves_with_rule_name(self):
        text = RE.explain(("NotAvailable", "C20"), self.facts, self.rules)
        self.assertIn("R4_borrowed", text)
        self.assertIn("BorrowedBy(C20, Ahmed)", text)

    def test_backward_chaining_fails_when_not_provable(self):
        self.assertIsNone(RE.explain(("NotAvailable", "C15"), self.facts, self.rules))
        self.assertFalse(RE.is_true(("Reserved", "B20"), self.facts, self.rules))

    def test_query_with_variable_returns_all_answers(self):
        got = RE.ask(("BorrowedBy", "?c", "?p"), self.facts, self.rules)
        self.assertEqual(got, [{"?c": "C20", "?p": "Ahmed"}])

    def test_exists_and_forall(self):
        self.assertTrue(RE.exists(("Book", "?x"), self.facts, self.rules))
        self.assertFalse(RE.exists(("Book", "B999"), self.facts, self.rules))
        ok, bad = RE.forall("Book", "PhysicalObject", self.facts, self.rules)
        self.assertTrue(ok)
        self.assertEqual(bad, [])
        ok, bad = RE.forall("Book", "Reserved", self.facts, self.rules)    # only B15 is reserved
        self.assertFalse(ok)
        self.assertIn("B20", bad)

    def test_target_shelf_needs_the_qr(self):
        self.assertEqual(RE.ask(("TargetShelf", "B15", "?s"), self.facts, self.rules), [])
        room, row, shelf, proof = target_of(self.kb, "B15")
        self.assertEqual((room, row, shelf), ("Room3", "RowA2", "S7"))
        self.assertIn("R7_shelf", proof)

    def test_unknown_book_has_no_target(self):
        self.assertIsNone(target_of(self.kb, "B99"))


class Resolution(unittest.TestCase):
    def test_resolves_complementary_literals(self):
        out = RE.resolve({"Book(B15)", "Magazine(B15)"}, {"~Book(B15)"})
        self.assertEqual(out, [frozenset({"Magazine(B15)"})])

    def test_empty_clause_is_a_contradiction(self):
        self.assertEqual(RE.resolve({"P(a)"}, {"~P(a)"}), [frozenset()])

    def test_nothing_to_resolve(self):
        self.assertEqual(RE.resolve({"P(a)"}, {"Q(a)"}), [])


class KnowledgeBaseChecks(unittest.TestCase):
    def test_rejects_unknown_predicate_and_wrong_arity(self):
        kb = KnowledgeBase()
        with self.assertRaises(ValueError):
            kb.add_fact("Dragon", "x")
        with self.assertRaises(ValueError):
            kb.add_fact("Book", "a", "b")

    def test_facts_must_be_ground(self):
        with self.assertRaises(ValueError):
            KnowledgeBase().add_fact("Book", "?x")

    def test_default_rules_load(self):
        kb = KnowledgeBase()
        load_default_rules(kb)
        self.assertEqual(len(kb.rules()), 9)


if __name__ == "__main__":
    unittest.main()
