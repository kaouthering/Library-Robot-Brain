"""Run:  python -m unittest discover tests   (from the project folder)"""
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
from library_robot.database import LibraryDB
from library_robot.robot import Robot
from library_robot.sensors import RealSensor, normalize_qr

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def qr_image(path, text):
    im = cv2.QRCodeEncoder.create().encode(text)
    im = cv2.copyMakeBorder(im, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)
    cv2.imwrite(path, cv2.resize(im, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST))


class SystemTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "t.db")
        db = LibraryDB(self.dbpath)
        db.init_from_json(os.path.join(HERE, "library_robot", "library_data.json"))
        db.close()

    def robot(self):
        db = LibraryDB(self.dbpath)
        data, kb = db.load()
        return Robot(data, kb, db=db), db

    def test_qr_roundtrip_and_prefix(self):
        for text in ("B15", "LIB:B15"):
            p = os.path.join(self.tmp, "q.png")
            qr_image(p, text)
            with RealSensor(p, show=False) as cam:
                self.assertEqual(cam.read_qr(), "B15")
        self.assertIsNone(normalize_qr("hello world"))

    def test_loan_survives_restart(self):
        r, db = self.robot()
        r.serve("Karim", "B30")
        db.close()
        r2, db2 = self.robot()                    # "restart": rebuilt from the database
        self.assertTrue(r2.kb.has_fact("BorrowedBy", "C30", "Karim"))
        db2.close()

    def test_reserved_book_refused_and_return(self):
        r, db = self.robot()
        r.serve("Karim", "B25")                   # reserved for Sara
        self.assertFalse(any(f[0] == "BorrowedBy" and f[1] == "C25" for f in r.kb.facts()))
        self.assertTrue(r.return_book("Ahmed", "B20"))
        db.close()
        r2, db2 = self.robot()
        self.assertFalse(r2.kb.has_fact("BorrowedBy", "C20", "Ahmed"))
        db2.close()

    def test_new_book_gets_a_qr_target(self):
        db = LibraryDB(self.dbpath)
        db.add_book("B60", "Deep Learning", "S8", copies=2)
        _, kb = db.load()
        from library_robot.knowledge_base import target_of
        self.assertEqual(target_of(kb, "B60")[:3], ("Room3", "RowC1", "S8"))
        db.close()


if __name__ == "__main__":
    unittest.main()
