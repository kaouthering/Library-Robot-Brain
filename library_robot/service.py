"""
service.py - library operations used by the web app
===================================================
No web framework in this file. Every function takes plain values and returns plain
dicts, so it can be used from FastAPI, Starlette, Flask or a script.

The important rule: **every decision is made by the reasoning engine**, and the
answer comes back together with its logical proof:

    result = svc.checkout("Karim", "B25")
    result["outcome"]   -> "reserved"
    result["proofs"]    -> [{"goal": "Reserved(B25)", "rule": "R5_reserved", "text": "...", ...}]

The Knowledge Base is rebuilt from SQLite for every request, so the database is
the single source of truth and nothing can get out of sync.
"""
import re
import sqlite3
import threading
from contextlib import contextmanager
from typing import Dict, List, Optional

from . import reasoning_engine as RE
from .database import LibraryDB
from .knowledge_base import KnowledgeBase, is_var, target_of
from .rules import ACTION_RULES, KB_RULES, RULE_TEXT
from .sensors import BOOK_ID, cv2, normalize_qr

try:
    import numpy as np
except ImportError:          # only needed for the QR endpoints
    np = None

USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}$")
PLACE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,19}$")
ATOM_RE = re.compile(r"^\s*([A-Za-z]\w*)\s*\((.*)\)\s*$")
RULE_IN_TEXT = re.compile(r"\[(R\d+_\w+)\]")
MAX_IMAGE_BYTES = 8 * 1024 * 1024


class LibraryError(Exception):
    """A problem the caller can fix. `status` is the matching HTTP code."""
    status = 400


class NotFound(LibraryError):
    status = 404


class Conflict(LibraryError):
    status = 409


class Unsupported(LibraryError):
    status = 501


def _natural(key: str):
    m = re.match(r"^([A-Za-z]*)(\d*)(.*)$", key)
    return (m.group(1), int(m.group(2)) if m.group(2) else 0, m.group(3))


# =====================================================================
# Proofs
# =====================================================================
def prove(kb: KnowledgeBase, goal: tuple, facts=None) -> dict:
    """Backward chaining on a ground goal -> a proof the UI can show."""
    facts = kb.facts() if facts is None else facts
    text = RE.explain(goal, facts, kb.rules())
    rule = None
    if text:
        m = RULE_IN_TEXT.search(text)
        rule = m.group(1) if m else None
    return {
        "goal": RE.fmt(goal),
        "proved": text is not None,
        "text": text or f"{RE.fmt(goal)} cannot be proved from the facts and rules",
        "rule": rule,
        "rule_text": RULE_TEXT.get(rule) if rule else None,
    }


class _View:
    """A fresh Knowledge Base plus everything derived from it (forward chaining)."""

    def __init__(self, db: LibraryDB):
        self.data, self.kb = db.load()
        self.facts = self.kb.facts()
        self.derived, _ = RE.forward_chain(self.facts, self.kb.rules())
        self.titles = {b["id"]: b["title"] for b in self.data["books"]}

    def copies_of(self, book: str) -> List[str]:
        return sorted((c for c, b in self.kb.functions()["CopyOf"].items() if b == book),
                      key=_natural)

    def borrowers(self, copy: str) -> List[str]:
        return sorted(f[2] for f in self.kb.find("BorrowedBy", copy, "?p"))

    def reserved_for(self, book: str) -> List[str]:
        return sorted(f[2] for f in self.kb.find("ReservedFor", book, "?u"))

    def book_row(self, book: str) -> dict:
        copies = []
        for c in self.copies_of(book):
            out = ("NotAvailable", c) in self.derived
            copies.append({"id": c, "available": not out,
                           "borrowed_by": self.borrowers(c)[0] if self.borrowers(c) else None})
        reserved = ("Reserved", book) in self.derived
        free = sum(1 for c in copies if c["available"])
        if reserved:
            status = "reserved"
        elif copies and free == 0:
            status = "borrowed"
        elif not copies:
            status = "no_copy"
        else:
            status = "available"
        on_shelf = next((f[2] for f in self.kb.find("OnShelf", book, "?s")), None)
        return {
            "id": book,
            "title": self.titles.get(book, book),
            "status": status,
            "copies": copies,
            "copies_free": free,
            "reserved_for": self.reserved_for(book),
            "home_shelf": self.kb.get_function("LocationOf", book),
            "on_shelf": on_shelf,
            "location": self.kb.location_path(book),
        }


# =====================================================================
# Service
# =====================================================================
class LibraryService:
    def __init__(self, db_path: str = "library.db", sample_json: Optional[str] = None):
        self.db_path = db_path
        self._lock = threading.Lock()          # makes "check, then write" atomic
        with self._db() as db:
            if db.is_empty() and sample_json:
                db.init_from_json(sample_json)

    @contextmanager
    def _db(self):
        db = LibraryDB(self.db_path)
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------ reading
    def overview(self) -> dict:
        with self._db() as db:
            v = _View(db)
            rows = [v.book_row(b["id"]) for b in v.data["books"]]
            return {
                "name": v.data["name"],
                "books": len(rows),
                "copies": sum(len(r["copies"]) for r in rows),
                "copies_out": sum(1 for r in rows for c in r["copies"] if not c["available"]),
                "reservations": len(v.data["reserved"]),
                "members": len(v.data["users"]),
                "shelves": len(v.data["shelves"]),
            }

    def list_books(self) -> List[dict]:
        with self._db() as db:
            v = _View(db)
            rows = [v.book_row(b["id"]) for b in v.data["books"]]
        return sorted(rows, key=lambda r: _natural(r["id"]))

    def book_detail(self, book: str) -> dict:
        with self._db() as db:
            v = _View(db)
            if not v.kb.has_fact("Book", book):
                raise NotFound(f"{book} is not in the library system")
            return self._detail(v, book)

    def _detail(self, v: _View, book: str) -> dict:
        row = v.book_row(book)
        proofs = []
        got = target_of(v.kb, book)               # adds QRRead(book), then infers the shelf
        if got:
            room, rw, shelf, proof = got
            row["target"] = {"room": room, "row": rw, "shelf": shelf}
            proofs.append(prove(v.kb, ("TargetShelf", book, shelf)))
        v.kb.remove_fact("QRRead", book)
        if row["reserved_for"]:
            proofs.append(prove(v.kb, ("Reserved", book)))
        for c in row["copies"]:
            proofs.append(prove(v.kb, ("NotAvailable", c["id"])))
        row["proofs"] = proofs
        return row

    def list_users(self) -> List[dict]:
        with self._db() as db:
            v = _View(db)
            out = []
            for u in sorted(v.data["users"], key=str.lower):
                loans = [{"copy": c, "book": b, "title": v.titles.get(b, b)}
                         for c, b in sorted(v.kb.functions()["CopyOf"].items(),
                                            key=lambda kv: _natural(kv[0]))
                         if v.kb.has_fact("BorrowedBy", c, u)]
                res = [{"book": f[1], "title": v.titles.get(f[1], f[1])}
                       for f in sorted(v.kb.find("ReservedFor", "?b", u), key=lambda f: _natural(f[1]))]
                out.append({"name": u, "loans": loans, "reservations": res})
            return out

    def list_shelves(self) -> List[dict]:
        with self._db() as db:
            v = _View(db)
            home: Dict[str, int] = {}
            for b in v.data["books"]:
                home[b["shelf"]] = home.get(b["shelf"], 0) + 1
            return [{"id": s["id"], "row": s["row"], "room": s["room"], "books": home.get(s["id"], 0)}
                    for s in sorted(v.data["shelves"], key=lambda s: _natural(s["id"]))]

    def loans(self) -> List[dict]:
        with self._db() as db:
            rows = db.q("SELECT w.copy, c.book, b.title, w.user, w.borrowed_at FROM borrowings w "
                        "JOIN copies c ON c.id = w.copy JOIN books b ON b.id = c.book "
                        "WHERE w.returned_at IS NULL ORDER BY w.borrowed_at DESC, w.id DESC")
            return [dict(r) for r in rows]

    def events(self, n: int = 30) -> List[dict]:
        with self._db() as db:
            return [dict(r) for r in db.recent_events(max(1, min(int(n), 200)))]

    @staticmethod
    def rules() -> dict:
        return {
            "inference": [{"name": n, "text": RULE_TEXT[n]} for n, _, _ in KB_RULES],
            "actions": [{"name": n, "text": t} for n, t in ACTION_RULES.items()],
        }

    # ------------------------------------------------------------ desk actions
    def checkout(self, user: str, book: str) -> dict:
        """Borrow. Same decisions as Robot.serve(): reserved? borrowed? else lend."""
        with self._lock, self._db() as db:
            v = _View(db)
            if user not in v.data["users"]:
                return self._refuse("unknown_user", f"{user} is not a registered member.", v, book)
            if not v.kb.has_fact("Book", book):
                return self._refuse("unknown_book", f"{book} is not in the library system.", v, None)
            title = v.titles.get(book, book)
            proofs = []

            # 1) reserved for somebody else?   R5: ReservedFor(b,u) -> Reserved(b)
            owners = v.reserved_for(book)
            if owners:
                proofs.append(prove(v.kb, ("Reserved", book)))
                if user not in owners:
                    return self._refuse("reserved",
                                        f"'{title}' ({book}) is reserved for {', '.join(owners)}.",
                                        v, book, proofs)

            # 2) the same member cannot take a second copy by accident (double click)
            copies = v.copies_of(book)
            if any(user in v.borrowers(c) for c in copies):
                return self._refuse("already_yours",
                                    f"{user} already has a copy of '{title}' ({book}).",
                                    v, book, proofs)

            # 3) is there a copy nobody has borrowed?   R4: BorrowedBy(c,p) -> NotAvailable(c)
            free = None
            for c in copies:
                p = prove(v.kb, ("NotAvailable", c))
                proofs.append(p)
                if not p["proved"] and free is None:
                    free = c
            if free is None:
                who = sorted({p for c in copies for p in v.borrowers(c)})
                msg = (f"'{title}' ({book}) is already borrowed (by {', '.join(who)})."
                       if copies else f"There is no copy of '{title}' ({book}).")
                return self._refuse("borrowed", msg, v, book, proofs)

            # 4) lend, record it, show the new fact is derived
            v.kb.add_fact("BorrowedBy", free, user)
            if user in owners:
                v.kb.remove_fact("ReservedFor", book, user)
            db.lend(free, user, book)
            proofs.append(prove(v.kb, ("NotAvailable", free)))
            return {"ok": True, "outcome": "lent", "copy": free, "user": user,
                    "message": f"'{title}' ({book}): copy {free} lent to {user}.",
                    "proofs": proofs, "book": self._fresh_row(db, book)}

    def checkin(self, user: str, book: str) -> dict:
        with self._lock, self._db() as db:
            v = _View(db)
            if user not in v.data["users"]:
                return self._refuse("unknown_user", f"{user} is not a registered member.", v, book)
            if not v.kb.has_fact("Book", book):
                return self._refuse("unknown_book", f"{book} is not in the library system.", v, None)
            title = v.titles.get(book, book)
            mine = [c for c in v.copies_of(book) if v.kb.has_fact("BorrowedBy", c, user)]
            if not mine:
                return self._refuse("no_loan",
                                    f"No record that {user} borrowed '{title}' ({book}).", v, book)
            copy = mine[0]
            before = prove(v.kb, ("NotAvailable", copy))
            v.kb.remove_fact("BorrowedBy", copy, user)
            db.give_back(copy, user)
            after = prove(v.kb, ("NotAvailable", copy))
            shelf = v.kb.get_function("LocationOf", book)
            owners = v.reserved_for(book)
            msg = f"Thank you {user}. Copy {copy} of '{title}' is back"
            if shelf:
                msg += f"; it goes on {' > '.join(v.kb.location_path(book)[1:])}"
            msg += f". Held for {', '.join(owners)}." if owners else "."
            return {"ok": True, "outcome": "returned", "copy": copy, "user": user,
                    "message": msg, "proofs": [before, after],
                    "book": self._fresh_row(db, book)}

    def reserve(self, user: str, book: str) -> dict:
        with self._lock, self._db() as db:
            v = _View(db)
            if user not in v.data["users"]:
                return self._refuse("unknown_user", f"{user} is not a registered member.", v, book)
            if not v.kb.has_fact("Book", book):
                return self._refuse("unknown_book", f"{book} is not in the library system.", v, None)
            title = v.titles.get(book, book)
            if user in v.reserved_for(book):
                return self._refuse("already_reserved",
                                    f"'{title}' ({book}) is already reserved for {user}.", v, book)
            db.reserve(book, user)
            v.kb.add_fact("ReservedFor", book, user)
            return {"ok": True, "outcome": "reserved_ok", "user": user,
                    "message": f"'{title}' ({book}) is now reserved for {user}.",
                    "proofs": [prove(v.kb, ("Reserved", book))],
                    "book": self._fresh_row(db, book)}

    def _refuse(self, outcome, message, v, book, proofs=None) -> dict:
        row = v.book_row(book) if book and v.kb.has_fact("Book", book) else None
        return {"ok": False, "outcome": outcome, "message": message,
                "proofs": proofs or [], "book": row}

    def _fresh_row(self, db: LibraryDB, book: str) -> dict:
        return _View(db).book_row(book)

    # ------------------------------------------------------------ admin
    def add_user(self, name: str) -> dict:
        name = (name or "").strip()
        if not USER_RE.match(name):
            raise LibraryError("Member names use letters, digits, spaces, '.', '_' or '-' "
                               "(1-40 characters, starting with a letter or digit).")
        with self._lock, self._db() as db:
            if name in [r[0] for r in db.q("SELECT id FROM users")]:
                raise Conflict(f"{name} is already a member.")
            db.add_user(name)
        return {"name": name}

    def add_shelf(self, shelf: str, room: str, row: str) -> dict:
        for label, val in (("Shelf", shelf), ("Room", room), ("Row", row)):
            if not PLACE_RE.match(val or ""):
                raise LibraryError(f"{label} id must start with a letter and use only letters, "
                                   "digits, '_' or '-' (max 20 characters).")
        with self._lock, self._db() as db:
            if db.q("SELECT 1 FROM shelves WHERE id=?", shelf):
                raise Conflict(f"Shelf {shelf} already exists.")
            owner = db.q("SELECT room FROM rows WHERE id=?", row)
            if owner and owner[0][0] != room:
                raise Conflict(f"Row {row} already belongs to {owner[0][0]}.")
            db.add_shelf(shelf, room, row)
        return {"id": shelf, "room": room, "row": row}

    def add_book(self, book: str, title: str, shelf: str, copies: int = 1) -> dict:
        title = (title or "").strip()
        if not BOOK_ID.match(book or ""):
            raise LibraryError("Book id is letters followed by digits, for example B60.")
        if not title or len(title) > 120:
            raise LibraryError("Give the book a title (up to 120 characters).")
        try:
            copies = int(copies)
        except (TypeError, ValueError):
            raise LibraryError("Copies must be a whole number.")
        if not 1 <= copies <= 20:
            raise LibraryError("Copies must be between 1 and 20.")
        with self._lock, self._db() as db:
            if not db.q("SELECT 1 FROM shelves WHERE id=?", shelf):
                raise NotFound(f"Shelf {shelf} does not exist. Add the shelf first.")
            if db.q("SELECT 1 FROM books WHERE id=?", book):
                raise Conflict(f"Book {book} already exists.")
            try:
                db.add_book(book, title, shelf, copies)
            except sqlite3.IntegrityError:
                raise Conflict(f"A copy id for {book} clashes with an existing book "
                               "(copy ids use the digits of the book id).")
            return self._fresh_row(db, book)

    # ------------------------------------------------------------ ask the logic
    def ask(self, query: str, given: Optional[List[str]] = None) -> dict:
        """Backward chaining on a query such as 'Reserved(B25)' or 'BorrowedBy(?c, Ahmed)'.
        `given` are extra ground facts to assume (what-if), e.g. ['QRRead(B15)']."""
        with self._db() as db:
            v = _View(db)
        kb = v.kb
        atom = self._parse_atom(kb, query, allow_vars=True)
        facts = kb.facts()
        for g in given or []:
            a = self._parse_atom(kb, g, allow_vars=False)
            facts.add(a)
        rules = kb.rules()
        ground = not any(is_var(x) for x in atom[1:])
        answers = RE.ask(atom, facts, rules)
        out = {"query": RE.fmt(atom), "ground": ground, "mode": "backward chaining",
               "given": [RE.fmt(self._parse_atom(kb, g, False)) for g in given or []],
               "true": bool(answers), "answers": []}
        if ground:
            out["proof"] = prove(kb, atom, facts)
        else:
            for ans in answers[:25]:
                inst = RE.subst(atom, ans)
                out["answers"].append({"bindings": ans, "atom": RE.fmt(inst),
                                       "proof": prove(kb, inst, facts)})
            out["truncated"] = len(answers) > 25
        return out

    @staticmethod
    def _parse_atom(kb: KnowledgeBase, text: str, allow_vars: bool) -> tuple:
        m = ATOM_RE.match(text or "")
        if not m:
            raise LibraryError("Write a query like Reserved(B25) or BorrowedBy(?c, Ahmed).")
        pred = m.group(1)
        args = [a.strip() for a in m.group(2).split(",")] if m.group(2).strip() else []
        if pred not in kb.PREDICATES:
            raise LibraryError(f"Unknown predicate {pred}. Known: {', '.join(sorted(kb.PREDICATES))}.")
        if len(args) != kb.PREDICATES[pred]:
            raise LibraryError(f"{pred} takes {kb.PREDICATES[pred]} argument(s), not {len(args)}.")
        if any(not a for a in args):
            raise LibraryError("Empty argument in the query.")
        if not allow_vars and any(is_var(a) for a in args):
            raise LibraryError("Assumed facts cannot contain variables (?x).")
        return (pred, *args)

    # ------------------------------------------------------------ QR codes
    def scan_image(self, data: bytes) -> dict:
        """Decode a QR code from a photo (jpeg/png). Used by the 'scan' button on a phone."""
        if cv2 is None or np is None:
            raise Unsupported("QR scanning needs OpenCV:  pip install opencv-python")
        if not data:
            raise LibraryError("No image received.")
        if len(data) > MAX_IMAGE_BYTES:
            raise LibraryError("Image is too large (max 8 MB).")
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise LibraryError("That file is not a readable image.")
        raw = self._decode(img)
        book = normalize_qr(raw) if raw else None
        out = {"found": bool(raw), "raw": raw or None, "book": book}
        if raw and not book:
            out["note"] = "The QR code was read, but it is not a library book code."
        if book:
            with self._db() as db:
                v = _View(db)
                out["known"] = v.kb.has_fact("Book", book)
                out["detail"] = self._detail(v, book) if out["known"] else None
        return out

    @staticmethod
    def _decode(img) -> Optional[str]:
        det = cv2.QRCodeDetector()
        attempts = [img, cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)]
        h, w = img.shape[:2]
        if max(h, w) < 900:
            attempts.append(cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))
        for im in attempts:
            text, _, _ = det.detectAndDecode(im)
            if text:
                return text
        return None

    @staticmethod
    def qr_png(book: str) -> bytes:
        """A printable QR code for a book id."""
        if cv2 is None or np is None:
            raise Unsupported("QR codes need OpenCV:  pip install opencv-python")
        if not BOOK_ID.match(book or ""):
            raise LibraryError("Not a book id.")
        code = cv2.QRCodeEncoder.create().encode(book)
        code = cv2.copyMakeBorder(code, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)
        code = cv2.resize(code, None, fx=10, fy=10, interpolation=cv2.INTER_NEAREST)
        ok, buf = cv2.imencode(".png", code)
        return buf.tobytes()
