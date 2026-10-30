"""
database.py - persistent library storage (SQLite)
=================================================
The Knowledge Base (knowledge_base.py) lives in memory and is rebuilt from this
database every time the program starts. Every change the robot makes
(book put on a shelf, book lent, reservation fulfilled, book returned ...)
is written back here, so nothing is lost when the program stops.

    db = LibraryDB("library.db")
    db.init_from_json("library_data.json")      # first run only (sample data)
    data, kb = db.load()                        # -> same objects robot.py used to get
"""
import json
import os
import sqlite3
import time
from contextlib import contextmanager

from .knowledge_base import KnowledgeBase, load_default_rules

SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms    (id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS rows     (id TEXT PRIMARY KEY, room TEXT NOT NULL REFERENCES rooms(id));
CREATE TABLE IF NOT EXISTS shelves  (id TEXT PRIMARY KEY, row TEXT NOT NULL REFERENCES rows(id),
                                     room TEXT NOT NULL REFERENCES rooms(id));
CREATE TABLE IF NOT EXISTS places   (id TEXT PRIMARY KEY, type TEXT NOT NULL, room TEXT);
CREATE TABLE IF NOT EXISTS users    (id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS books    (id TEXT PRIMARY KEY, title TEXT NOT NULL,
                                     shelf TEXT NOT NULL REFERENCES shelves(id),
                                     shelf_row INTEGER);
CREATE TABLE IF NOT EXISTS copies   (id TEXT PRIMARY KEY, book TEXT NOT NULL REFERENCES books(id));
-- returned_at IS NULL  ->  the copy is still out
CREATE TABLE IF NOT EXISTS borrowings (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                       copy TEXT NOT NULL REFERENCES copies(id),
                                       user TEXT NOT NULL REFERENCES users(id),
                                       borrowed_at TEXT NOT NULL, returned_at TEXT);
CREATE TABLE IF NOT EXISTS reservations (book TEXT NOT NULL REFERENCES books(id),
                                         user TEXT NOT NULL REFERENCES users(id),
                                         created_at TEXT NOT NULL, PRIMARY KEY (book, user));
-- which books are physically standing on which shelf right now
CREATE TABLE IF NOT EXISTS shelf_state (book TEXT PRIMARY KEY REFERENCES books(id),
                                        shelf TEXT NOT NULL REFERENCES shelves(id));
CREATE TABLE IF NOT EXISTS events   (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
                                     kind TEXT NOT NULL, detail TEXT);
-- simulator scenario (map, robot start, fake camera world ...) as JSON blobs
CREATE TABLE IF NOT EXISTS meta     (key TEXT PRIMARY KEY, value TEXT);
"""

DEFAULT_ROBOT = {"id": "Robot1", "start": "Entrance", "patrol": []}


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


class LibraryDB:
    def __init__(self, path="library.db"):
        self.path = path
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")
        self.con.executescript(SCHEMA)
        self.con.commit()

    # ---------------------------------------------------------- helpers
    @contextmanager
    def tx(self):
        try:
            yield self.con
            self.con.commit()
        except Exception:
            self.con.rollback()
            raise

    def q(self, sql, *args):
        return self.con.execute(sql, args).fetchall()

    def is_empty(self):
        return self.q("SELECT COUNT(*) FROM books")[0][0] == 0 and \
               self.q("SELECT COUNT(*) FROM shelves")[0][0] == 0

    def close(self):
        self.con.close()

    # ---------------------------------------------------------- seeding
    def init_from_json(self, json_path):
        """Import the sample library (rooms, shelves, books, users, ...)."""
        d = json.load(open(json_path, encoding="utf-8"))
        with self.tx() as c:
            for r in d["rooms"]:
                c.execute("INSERT OR IGNORE INTO rooms VALUES (?)", (r,))
            for r in d["rows"]:
                c.execute("INSERT OR IGNORE INTO rows VALUES (?,?)", (r["id"], r["room"]))
            for s in d["shelves"]:
                c.execute("INSERT OR IGNORE INTO shelves VALUES (?,?,?)",
                          (s["id"], s["row"], s["room"]))
            for p in d["places"]:
                c.execute("INSERT OR IGNORE INTO places VALUES (?,?,?)",
                          (p["id"], p["type"], p.get("room")))
            for u in d["users"]:
                c.execute("INSERT OR IGNORE INTO users VALUES (?)", (u,))
            slots = d.get("book_row_on_shelf", {})
            for b in d["books"]:
                c.execute("INSERT OR IGNORE INTO books VALUES (?,?,?,?)",
                          (b["id"], b["title"], b["shelf"], slots.get(b["id"])))
            for x in d["copies"]:
                c.execute("INSERT OR IGNORE INTO copies VALUES (?,?)", (x["id"], x["book"]))
            for x in d["borrowed"]:
                c.execute("INSERT INTO borrowings (copy,user,borrowed_at) VALUES (?,?,?)",
                          (x["copy"], x["user"], now()))
            for x in d["reserved"]:
                c.execute("INSERT OR IGNORE INTO reservations VALUES (?,?,?)",
                          (x["book"], x["user"], now()))
            for shelf, books in d["shelf_state"].items():
                for b in books:
                    c.execute("INSERT OR REPLACE INTO shelf_state VALUES (?,?)", (b, shelf))
            scenario = {k: d[k] for k in ("map_edges", "robot", "world", "qr_sides",
                                          "requests", "shelf_rows") if k in d}
            scenario["name"] = d.get("name", "Library")
            c.execute("INSERT OR REPLACE INTO meta VALUES ('scenario', ?)",
                      (json.dumps(scenario),))
        self.log_event("init", f"imported {os.path.basename(json_path)}")

    def scenario(self):
        row = self.q("SELECT value FROM meta WHERE key='scenario'")
        return json.loads(row[0][0]) if row else {}

    # ---------------------------------------------------------- loading
    def load(self):
        """Return (data, kb). `data` has the same shape robot.py always used."""
        sc = self.scenario()
        data = {
            "name": sc.get("name", "Library"),
            "rooms": [r[0] for r in self.q("SELECT id FROM rooms")],
            "rows": [dict(r) for r in self.q("SELECT id, room FROM rows")],
            "shelves": [dict(r) for r in self.q("SELECT id, row, room FROM shelves")],
            "places": [dict(r) for r in self.q("SELECT id, type, room FROM places")],
            "users": [r[0] for r in self.q("SELECT id FROM users")],
            "books": [dict(r) for r in self.q("SELECT id, title, shelf FROM books")],
            "copies": [{"id": r["id"], "book": r["book"]}
                       for r in self.q("SELECT id, book FROM copies")],
            "borrowed": [{"copy": r["copy"], "user": r["user"]} for r in
                         self.q("SELECT copy, user FROM borrowings WHERE returned_at IS NULL")],
            "reserved": [{"book": r["book"], "user": r["user"]}
                         for r in self.q("SELECT book, user FROM reservations")],
            "shelf_state": {},
            "book_row_on_shelf": {r["id"]: r["shelf_row"] for r in
                                  self.q("SELECT id, shelf_row FROM books")
                                  if r["shelf_row"] is not None},
            # simulator scenario (empty / defaults when running with the real camera)
            "map_edges": sc.get("map_edges", []),
            "robot": sc.get("robot", dict(DEFAULT_ROBOT)),
            "world": sc.get("world", {}),
            "qr_sides": sc.get("qr_sides", {}),
            "requests": sc.get("requests", []),
            "shelf_rows": sc.get("shelf_rows", 3),
        }
        for r in self.q("SELECT book, shelf FROM shelf_state"):
            data["shelf_state"].setdefault(r["shelf"], []).append(r["book"])
        return data, self.build_kb(data)

    @staticmethod
    def build_kb(data):
        kb = KnowledgeBase()
        for r in data["rooms"]:
            kb.add_fact("Room", r)
        for row in data["rows"]:
            kb.add_fact("Row", row["id"])
        for s in data["shelves"]:
            kb.add_fact("Shelf", s["id"])
            kb.add_fact("InRow", s["id"], s["row"])
            kb.add_fact("InRoom", s["id"], s["room"])
        for p in data["places"]:
            if p["type"] in ("Table", "Chair"):
                kb.add_fact(p["type"], p["id"])
        for u in data["users"]:
            kb.add_fact("User", u)
        for b in data["books"]:
            kb.add_fact("Book", b["id"])
            kb.set_function("LocationOf", b["id"], b["shelf"])
            kb.add_fact("BelongsOn", b["id"], b["shelf"])   # was missing: rule R7 needs it
        for c in data["copies"]:
            kb.add_fact("Copy", c["id"])
            kb.set_function("CopyOf", c["id"], c["book"])
        for x in data["borrowed"]:
            kb.add_fact("BorrowedBy", x["copy"], x["user"])
        for x in data["reserved"]:
            kb.add_fact("ReservedFor", x["book"], x["user"])
        for shelf, books in data["shelf_state"].items():
            for b in books:
                kb.add_fact("OnShelf", b, shelf)
        kb.add_fact("Robot", data["robot"]["id"])
        kb.add_fact("At", data["robot"]["id"], data["robot"]["start"])
        load_default_rules(kb)
        return kb

    # ---------------------------------------------------------- writes (robot actions)
    def log_event(self, kind, detail=""):
        with self.tx() as c:
            c.execute("INSERT INTO events (ts, kind, detail) VALUES (?,?,?)",
                      (now(), kind, detail))

    def put_on_shelf(self, book, shelf):
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO shelf_state VALUES (?,?)", (book, shelf))
        self.log_event("put_on_shelf", f"{book} -> {shelf}")

    def lend(self, copy, user, book=None):
        """Record a loan; a reservation of this user for this book is fulfilled."""
        with self.tx() as c:
            c.execute("INSERT INTO borrowings (copy,user,borrowed_at) VALUES (?,?,?)",
                      (copy, user, now()))
            book = book or c.execute("SELECT book FROM copies WHERE id=?", (copy,)).fetchone()[0]
            c.execute("DELETE FROM reservations WHERE book=? AND user=?", (book, user))
        self.log_event("lend", f"{copy} of {book} -> {user}")

    def give_back(self, copy, user):
        with self.tx() as c:
            c.execute("UPDATE borrowings SET returned_at=? WHERE copy=? AND user=? "
                      "AND returned_at IS NULL", (now(), copy, user))
        self.log_event("return", f"{copy} <- {user}")

    # ---------------------------------------------------------- admin
    def add_user(self, user):
        with self.tx() as c:
            c.execute("INSERT OR IGNORE INTO users VALUES (?)", (user,))
        self.log_event("add_user", user)

    def add_shelf(self, shelf, room, row):
        with self.tx() as c:
            c.execute("INSERT OR IGNORE INTO rooms VALUES (?)", (room,))
            c.execute("INSERT OR IGNORE INTO rows VALUES (?,?)", (row, room))
            c.execute("INSERT OR IGNORE INTO shelves VALUES (?,?,?)", (shelf, row, room))
        self.log_event("add_shelf", f"{shelf} ({room}/{row})")

    def add_book(self, book, title, shelf, copies=1, shelf_row=None):
        with self.tx() as c:
            c.execute("INSERT INTO books VALUES (?,?,?,?)", (book, title, shelf, shelf_row))
            digits = "".join(ch for ch in book if ch.isdigit()) or book
            for i in range(copies):
                cid = f"C{digits}" if i == 0 else f"C{digits}_{i + 1}"
                c.execute("INSERT INTO copies VALUES (?,?)", (cid, book))
        self.log_event("add_book", f"{book} '{title}' on {shelf}, {copies} copies")

    def reserve(self, book, user):
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO reservations VALUES (?,?,?)",
                      (book, user, now()))
        self.log_event("reserve", f"{book} for {user}")

    # ---------------------------------------------------------- reports
    def status(self):
        out = []
        for b in self.q("SELECT id, title, shelf FROM books ORDER BY id"):
            copies = self.q("SELECT c.id, (SELECT user FROM borrowings w WHERE w.copy=c.id "
                            "AND w.returned_at IS NULL) AS who FROM copies c WHERE c.book=?",
                            b["id"])
            res = [r[0] for r in self.q("SELECT user FROM reservations WHERE book=?", b["id"])]
            on_shelf = self.q("SELECT shelf FROM shelf_state WHERE book=?", b["id"])
            out.append({"id": b["id"], "title": b["title"], "home": b["shelf"],
                        "on_shelf": on_shelf[0][0] if on_shelf else None,
                        "copies": [(c["id"], c["who"]) for c in copies], "reserved": res})
        return out

    def recent_events(self, n=15):
        return self.q("SELECT ts, kind, detail FROM events ORDER BY id DESC LIMIT ?", n)
