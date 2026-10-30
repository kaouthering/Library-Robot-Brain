"""LibraryService: every decision, with its proof, saved in SQLite."""
import os
import unittest

import cv2
import numpy as np

from library_robot.database import LibraryDB
from library_robot.robot import Robot
from library_robot.service import Conflict, LibraryError, LibraryService, NotFound
from tests.helpers import fresh_db_path


def qr_bytes(text):
    im = cv2.QRCodeEncoder.create().encode(text)
    im = cv2.copyMakeBorder(im, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)
    ok, buf = cv2.imencode(".png", cv2.resize(im, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST))
    return buf.tobytes()


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.path = fresh_db_path()
        self.svc = LibraryService(self.path)

    def book(self, b):
        return next(x for x in self.svc.list_books() if x["id"] == b)

    # ---- checkout
    def test_lend_free_book_and_prove_it_is_now_out(self):
        r = self.svc.checkout("Karim", "B30")
        self.assertTrue(r["ok"])
        self.assertEqual((r["outcome"], r["copy"]), ("lent", "C30"))
        self.assertTrue(r["proofs"][-1]["proved"])
        self.assertEqual(r["proofs"][-1]["rule"], "R4_borrowed")
        self.assertEqual(self.book("B30")["status"], "borrowed")

    def test_loan_survives_a_new_service(self):
        self.svc.checkout("Karim", "B30")
        again = LibraryService(self.path)
        self.assertEqual(next(x for x in again.list_books() if x["id"] == "B30")["status"], "borrowed")

    def test_reserved_for_someone_else_is_refused_with_proof(self):
        r = self.svc.checkout("Karim", "B25")
        self.assertFalse(r["ok"])
        self.assertEqual(r["outcome"], "reserved")
        self.assertEqual(r["proofs"][0]["rule"], "R5_reserved")
        self.assertFalse([l for l in self.svc.loans() if l["user"] == "Karim"])   # nothing was lent
        self.assertEqual(self.book("B25")["copies_free"], 1)

    def test_reserver_can_borrow_and_the_reservation_is_used_up(self):
        r = self.svc.checkout("Sara", "B25")
        self.assertTrue(r["ok"])
        self.assertEqual(self.book("B25")["reserved_for"], [])

    def test_borrowed_book_is_refused_and_names_the_borrower(self):
        r = self.svc.checkout("Karim", "B20")
        self.assertEqual(r["outcome"], "borrowed")
        self.assertIn("Ahmed", r["message"])

    def test_two_copies_book_lends_the_second_copy(self):
        r = self.svc.checkout("Karim", "B15")           # C16 is Ahmed's, C15 is free
        self.assertEqual(r["copy"], "C15")
        r2 = self.svc.checkout("Sara", "B15")
        self.assertEqual(r2["outcome"], "borrowed")

    def test_same_member_cannot_take_two_copies_by_accident(self):
        self.svc.checkout("Karim", "B15")
        self.assertEqual(self.svc.checkout("Karim", "B15")["outcome"], "already_yours")

    def test_unknown_member_and_unknown_book(self):
        self.assertEqual(self.svc.checkout("Nobody", "B30")["outcome"], "unknown_user")
        self.assertEqual(self.svc.checkout("Karim", "B999")["outcome"], "unknown_book")

    # ---- return
    def test_return_closes_the_loan(self):
        r = self.svc.checkin("Ahmed", "B20")
        self.assertEqual(r["outcome"], "returned")
        self.assertFalse(r["proofs"][-1]["proved"])      # NotAvailable(C20) no longer provable
        self.assertEqual(self.book("B20")["status"], "available")
        self.assertEqual(self.svc.checkout("Karim", "B20")["outcome"], "lent")

    def test_return_without_a_loan(self):
        self.assertEqual(self.svc.checkin("Karim", "B20")["outcome"], "no_loan")

    def test_return_of_reserved_book_says_who_gets_it(self):
        self.svc.reserve("Karim", "B20")
        self.assertIn("Karim", self.svc.checkin("Ahmed", "B20")["message"])

    # ---- reserve
    def test_reserve_blocks_other_members(self):
        self.assertTrue(self.svc.reserve("Karim", "B30")["ok"])
        self.assertEqual(self.svc.checkout("Sara", "B30")["outcome"], "reserved")
        self.assertEqual(self.svc.reserve("Karim", "B30")["outcome"], "already_reserved")

    # ---- admin
    def test_add_user_validation(self):
        self.assertEqual(self.svc.add_user("Layla M.")["name"], "Layla M.")
        for bad in ("", "?x", "a(b)", "x" * 41, "<script>"):
            with self.assertRaises(LibraryError):
                self.svc.add_user(bad)
        with self.assertRaises(Conflict):
            self.svc.add_user("Layla M.")

    def test_add_shelf_then_book_gets_a_target(self):
        self.svc.add_shelf("S20", "Room3", "RowC1")
        row = self.svc.add_book("B60", "Deep Learning", "S20", 2)
        self.assertEqual(len(row["copies"]), 2)
        detail = self.svc.book_detail("B60")
        self.assertEqual(detail["target"], {"room": "Room3", "row": "RowC1", "shelf": "S20"})
        with self.assertRaises(Conflict):
            self.svc.add_book("B60", "Again", "S20")

    def test_add_book_validation(self):
        for args in (("60", "T", "S1"), ("B60", "", "S1"), ("B60", "T", "S1", 0), ("B60", "T", "S1", "x")):
            with self.assertRaises(LibraryError):
                self.svc.add_book(*args)
        with self.assertRaises(NotFound):
            self.svc.add_book("B60", "T", "S404")

    def test_add_shelf_validation(self):
        with self.assertRaises(LibraryError):
            self.svc.add_shelf("S 1", "Room3", "RowC1")
        with self.assertRaises(Conflict):
            self.svc.add_shelf("S7", "Room3", "RowC1")
        with self.assertRaises(Conflict):
            self.svc.add_shelf("S30", "Room1", "RowC1")           # RowC1 is in Room3

    # ---- ask the logic
    def test_ask_ground_and_variable_queries(self):
        a = self.svc.ask("Reserved(B25)")
        self.assertTrue(a["true"])
        self.assertEqual(a["proof"]["rule"], "R5_reserved")
        b = self.svc.ask("BorrowedBy(?c, Ahmed)")
        self.assertEqual({x["atom"] for x in b["answers"]}, {"BorrowedBy(C16, Ahmed)", "BorrowedBy(C20, Ahmed)"})
        self.assertFalse(self.svc.ask("Reserved(B20)")["true"])

    def test_ask_what_if(self):
        self.assertEqual(self.svc.ask("TargetShelf(B15, ?s)")["answers"], [])
        got = self.svc.ask("TargetShelf(B15, ?s)", ["QRRead(B15)"])
        self.assertEqual(got["answers"][0]["bindings"], {"?s": "S7"})

    def test_ask_rejects_bad_input(self):
        for q in ("", "hello", "Dragon(x)", "Book(a, b)", "Book()"):
            with self.assertRaises(LibraryError):
                self.svc.ask(q)
        with self.assertRaises(LibraryError):
            self.svc.ask("Book(?x)", ["Book(?y)"])

    # ---- QR
    def test_scan_image_reads_the_book_and_explains(self):
        out = self.svc.scan_image(qr_bytes("B15"))
        self.assertEqual(out["book"], "B15")
        self.assertEqual(out["detail"]["target"]["shelf"], "S7")

    def test_scan_prefix_unknown_and_foreign_codes(self):
        self.assertEqual(self.svc.scan_image(qr_bytes("LIB:B20"))["book"], "B20")
        out = self.svc.scan_image(qr_bytes("B777"))
        self.assertEqual((out["book"], out["known"]), ("B777", False))
        out = self.svc.scan_image(qr_bytes("hello world"))
        self.assertTrue(out["found"])
        self.assertIsNone(out["book"])

    def test_scan_bad_images(self):
        with self.assertRaises(LibraryError):
            self.svc.scan_image(b"not an image")
        with self.assertRaises(LibraryError):
            self.svc.scan_image(b"")
        blank = cv2.imencode(".png", np.full((200, 200, 3), 255, "uint8"))[1].tobytes()
        self.assertFalse(self.svc.scan_image(blank)["found"])

    def test_qr_png_is_a_png_that_scans_back(self):
        png = LibraryService.qr_png("B25")
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertEqual(self.svc.scan_image(png)["book"], "B25")
        with self.assertRaises(LibraryError):
            LibraryService.qr_png("../etc")


class SameDecisionsAsTheRobot(unittest.TestCase):
    """The web service and robot.serve() must agree on who may borrow what."""

    def test_parity(self):
        for user, book in [("Karim", "B25"), ("Karim", "B20"), ("Karim", "B15"),
                           ("Sara", "B15"), ("Sara", "B30"), ("Sara", "B25"), ("Ahmed", "B35")]:
            path, robot_path = fresh_db_path(), fresh_db_path()    # separate databases
            svc = LibraryService(path)
            db = LibraryDB(robot_path)
            data, kb = db.load()
            robot = Robot(data, kb, db=db)
            was_free = {c for c, b in kb.functions()["CopyOf"].items() if b == book}
            robot.serve(user, book)
            robot_lent = any(kb.has_fact("BorrowedBy", c, user) for c in was_free)
            db.close()
            self.assertEqual(svc.checkout(user, book)["ok"], robot_lent, f"{user} {book}")


if __name__ == "__main__":
    unittest.main()
