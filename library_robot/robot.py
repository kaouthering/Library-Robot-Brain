"""
robot.py  -  برنامج الروبوت
===========================
يقرأ library_data.json  ->  يبني Knowledge Base  ->  يتجول ويراقب بالكاميرا
->  يستنتج (Forward/Backward Chaining)  ->  يتحرك على الخريطة  ->  ينظّم الكتب.

تشغيل:  library-robot sim
"""
import json
import sys
from collections import deque

from .knowledge_base import KnowledgeBase, load_default_rules
from . import reasoning_engine as RE
from .sensors import SimulatedSensor



def load_library(path):
    """Old behaviour: read a JSON file (demo / simulator). Nothing is saved."""
    data = json.load(open(path, encoding='utf-8'))
    kb = KnowledgeBase()

    for r in data['rooms']:
        kb.add_fact('Room', r)
    for row in data['rows']:
        kb.add_fact('Row', row['id'])
    for s in data['shelves']:
        kb.add_fact('Shelf', s['id'])
        kb.add_fact('InRow', s['id'], s['row'])
        kb.add_fact('InRoom', s['id'], s['room'])
    for p in data['places']:
        if p['type'] in ('Table', 'Chair'):
            kb.add_fact(p['type'], p['id'])
    for u in data['users']:
        kb.add_fact('User', u)
    for b in data['books']:
        kb.add_fact('Book', b['id'])
        kb.set_function('LocationOf', b['id'], b['shelf'])
    for c in data['copies']:
        kb.add_fact('Copy', c['id'])
        kb.set_function('CopyOf', c['id'], c['book'])
    for x in data['borrowed']:
        kb.add_fact('BorrowedBy', x['copy'], x['user'])
    for x in data['reserved']:
        kb.add_fact('ReservedFor', x['book'], x['user'])
    for shelf, books in data['shelf_state'].items():
        for b in books:
            kb.add_fact('OnShelf', b, shelf)

    kb.add_fact('Robot', data['robot']['id'])
    kb.add_fact('At', data['robot']['id'], data['robot']['start'])
    load_default_rules(kb)
    return data, kb


# ===============================================================
# 2) الروبوت
# ===============================================================
class Robot:
    def __init__(self, data, kb, db=None, sensor=None):
        self.kb = kb
        self.db = db                                        # LibraryDB or None (= nothing saved)
        self.id = data['robot']['id']
        self.pos = data['robot']['start']
        self.patrol = data['robot']['patrol']
        self.place_type = {p['id']: p['type'] for p in data['places']}
        self.qr_sides = data['qr_sides']
        self.world = {k: [dict(o) for o in v] for k, v in data['world'].items()}
        self.world.setdefault('LostAndFound', [])
        self.graph = {}
        for a, b in data['map_edges']:                      # Physical Map
            self.graph.setdefault(a, []).append(b)
            self.graph.setdefault(b, []).append(a)
        self.tmp = 0
        self.log = []
        self.requests = data.get('requests', [])            # طلبات استعارة الزبائن
        self.titles = {b['id']: b['title'] for b in data['books']}
        # camera + QR reader: simulated by default, or a RealSensor
        self.sensor = sensor or SimulatedSensor(self.world, self.qr_sides)

    def _save(self, method, *args):
        """Write a change to the database (if there is one)."""
        if self.db is not None:
            getattr(self.db, method)(*args)

    # ---------- Physical Map : Navigation (BFS) ----------
    def find_path(self, start, goal):
        queue, seen = deque([[start]]), {start}
        while queue:
            path = queue.popleft()
            if path[-1] == goal:
                return path
            for nxt in self.graph[path[-1]]:
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(path + [nxt])
        return None

    def move_to(self, goal):
        if goal == self.pos:
            return
        path = self.find_path(self.pos, goal)
        print(f"   [Move] {' -> '.join(path)}")
        self.kb.remove_fact('At', self.id, self.pos)
        self.pos = goal
        self.kb.add_fact('At', self.id, goal)

    # ---------- Camera / Object Detection (via the sensor) ----------
    def camera(self, place):
        return self.sensor.detect(place)

    # ---------- QR (via the sensor) ----------
    def read_qr(self, obj):
        return self.sensor.read_qr(obj)

    # ---------- ربط ما تراه الكاميرا بالـKB ----------
    def observe(self, tmp, place):
        t = self.place_type[place]
        self.kb.add_fact('Book', tmp)
        if t == 'Table':
            self.kb.add_fact('OnTable', tmp, place)
        elif t == 'Chair':
            self.kb.add_fact('OnChair', tmp, place)
        else:
            self.kb.add_fact('OnFloor', tmp)

    def forget(self, tmp):
        for f in [f for f in self.kb.facts() if tmp in f[1:]]:
            self.kb.remove_fact(*f)

    # ---------- حلقة العمل ----------
    def run(self):
        print(f"Robot starts at {self.pos}. Patrol: {self.patrol}\n")
        for place in self.patrol:
            print(f"== Inspecting {place} ({self.place_type[place]}) ==")
            self.move_to(place)
            print(f"   [Camera] sees: {self.camera(place)}")
            for obj in list(self.world[place]):
                self.move_to(place)                     # يرجع لمكان الفحص (بعد ما يرتّب كتاب)
                if obj['class'] != 'Book':
                    print(f"   [Camera] {obj['class']} -> obstacle, ignored")
                    continue
                self.handle_book(place, obj)
            print()
        self.serve_customers()                          # الزبائن يطلبون استعارة كتب
        self.report()

    def handle_book(self, place, obj):
        self.tmp += 1
        tmp = f"Obj{self.tmp}"
        self.observe(tmp, place)                       # facts جديدة من الكاميرا

        # --- Forward Chaining ---
        facts, trace = RE.forward_chain(self.kb.facts(), self.kb.rules())
        print(f"   [Forward] new facts about {tmp}: "
              f"{[RE.fmt(f) for f, _ in trace if tmp in f[1:]]}")

        # --- Backward Chaining (تأكيد الهدف) ---
        goal = ('NeedToOrganize', tmp)
        proof = RE.explain(goal, self.kb.facts(), self.kb.rules())
        if proof is None:
            print("   [Backward] goal not provable -> book is fine, leave it")
            self.forget(tmp)
            return
        print(f"   [Backward] {proof}")

        # --- PickUp ---
        print(f"   [PickUp] {tmp}")
        self.world[place].remove(obj)
        self.kb.add_fact('Holding', self.id, tmp)

        # --- QR ---
        book = self.read_qr(obj)
        self.forget(tmp)                               # Holding(tmp) يُحذف أيضاً

        if book is None:                               # لا نخمّن هوية الكتاب
            print("   [Action] GoToLostAndFound")
            self.move_to('LostAndFound')
            self.world['LostAndFound'].append(obj)
            print("   [Drop] book left at LostAndFound")
            self.log.append(f"unknown book from {place} -> LostAndFound")
            self._save('log_event', 'lost_and_found', f"book without QR from {place}")
            return

        if not self.kb.has_fact('Book', book):         # QR decoded but not in our library
            print(f"   [QR] {book} is not in the library system")
            self.kb.remove_fact('Holding', self.id, book)
            print("   [Action] GoToLostAndFound")
            self.move_to('LostAndFound')
            self.world['LostAndFound'].append(obj)
            print("   [Drop] book left at LostAndFound")
            self.log.append(f"unknown QR {book} from {place} -> LostAndFound")
            self._save('log_event', 'lost_and_found', f"unknown QR {book} from {place}")
            return

        self.kb.add_fact('Holding', self.id, book)

        # --- Query KB ---
        shelf = self.kb.get_function('LocationOf', book)
        path = " -> ".join(self.kb.location_path(book))
        print(f"   [KB] LocationOf({book}) = {shelf}   ({path})")

        # معرفة إضافية تُستنتج من الـKB (ليست مهمة الروبوت الأساسية)
        facts, _ = RE.forward_chain(self.kb.facts(), self.kb.rules())
        if ('Reserved', book) in facts:
            who = [f[2] for f in self.kb.find('ReservedFor', book, '?u')]
            print(f"   [KB] note: {book} is Reserved for {who}")
        for c, b in self.kb.functions()['CopyOf'].items():
            if b == book and ('NotAvailable', c) in facts:
                print(f"   [KB] note: copy {c} of {book} is NotAvailable (borrowed)")

        # --- Navigation + Verify + Put ---
        self.move_to(shelf)
        read_id = self.pos                              # الرف يقرأ معرّفه
        ok = (read_id == shelf) and self.kb.has_fact('Shelf', shelf)
        print(f"   [Verify] shelf ID read = {read_id}, expected = {shelf} -> {ok}")
        if not ok:
            print("   [Abort] wrong shelf!")
            return
        print(f"   [PutOnShelf] {book} -> {shelf}")
        self.world.setdefault(shelf, []).append(obj)
        self.kb.remove_fact('Holding', self.id, book)
        self.kb.add_fact('OnShelf', book, shelf)
        self._save('put_on_shelf', book, shelf)          # saved in the database
        self.log.append(f"{book} moved from {place} to {shelf}")

    # ===========================================================
    # طلبات الاستعارة من الزبائن (محجوز / مستعار / يمدّه ويسجّله مستعار)
    # ===========================================================
    # دوال العرض: فارغة هنا، و visual_robot.py يعيد تعريفها للرسم والتحريك
    def customer_arrives(self, user, book):
        pass

    def say(self, text, kind="info"):
        print(f"   [Say] {text}")

    def hand_over(self, user, copy):
        pass

    def customer_leaves(self):
        pass

    def serve_customers(self):
        if not self.requests:
            return
        print("== Customer desk (borrow requests) ==")
        self.move_to('Entrance')
        for req in self.requests:
            self.serve(req['user'], req['book'])
            print()

    def serve(self, user, book):
        rules = self.kb.rules()
        title = self.titles.get(book, book)
        print(f"   [Customer] {user} asks to borrow {book} ({title})")
        self.customer_arrives(user, book)

        # الكتاب غير موجود في النظام
        if not self.kb.has_fact('Book', book):
            self.say(f"Sorry {user}, {book} is not in the library system.", "borrowed")
            self.customer_leaves()
            return

        # 1) هل الكتاب محجوز؟  (R5: ReservedFor(b,u) -> Reserved(b))
        proof = RE.explain(('Reserved', book), self.kb.facts(), rules)
        owners = [f[2] for f in self.kb.find('ReservedFor', book, '?u')]
        if proof:
            print(f"   [Backward] {proof}")
        if proof and user not in owners:
            print(f"   [Reply] reserved for {owners}, refuse {user}")
            self.say(f"Sorry {user}, '{title}' ({book}) is RESERVED for "
                     f"{', '.join(owners)}.", "reserved")
            self.log.append(f"{user} refused {book}: reserved for {owners}")
            self.customer_leaves()
            return

        # 2) هل توجد نسخة غير مستعارة؟  (R4: BorrowedBy(c,u) -> NotAvailable(c))
        copies = [c for c, b in self.kb.functions()['CopyOf'].items() if b == book]
        free = None
        for c in copies:
            p = RE.explain(('NotAvailable', c), self.kb.facts(), rules)
            if p:
                print(f"   [Backward] {p}")
            else:
                print(f"   [Check] copy {c} has no BorrowedBy fact -> Available")
                if free is None:
                    free = c
        if free is None:
            who = sorted({f[2] for c in copies
                          for f in self.kb.find('BorrowedBy', c, '?p')})
            print(f"   [Reply] borrowed, all copies of {book} are out")
            if copies:
                msg = (f"Sorry {user}, '{title}' ({book}) is already BORROWED "
                       f"(by {', '.join(who)}).")
            else:
                msg = f"Sorry {user}, there is no copy of '{title}' ({book})."
            self.say(msg, "borrowed")
            self.log.append(f"{user} refused {book}: borrowed")
            self.customer_leaves()
            return

        # 3) متاح: يمدّه للزبون ويسجّله مستعاراً في الـKB
        print(f"   [Reply] lend copy {free} of {book} to {user}")
        self.say(f"'{title}' ({book}) is available, {user}. Here is copy {free}.", "ok")
        self.hand_over(user, free)
        self.kb.add_fact('BorrowedBy', free, user)
        if user in owners:                               # حجزه تحقق: نحذف الحجز
            self.kb.remove_fact('ReservedFor', book, user)
        self._save('lend', free, user, book)             # saved in the database
        facts, _ = RE.forward_chain(self.kb.facts(), rules)
        print(f"   [Mark] BorrowedBy({free}, {user}) saved in KB -> "
              f"NotAvailable({free}) = {('NotAvailable', free) in facts}")
        self.say(f"Copy {free} is now marked as BORROWED by {user}.", "done")
        self.log.append(f"{free} of {book} lent to {user}")
        self.customer_leaves()

    def return_book(self, user, book):
        """A customer brings a book back: the loan is closed in the KB and in the DB."""
        title = self.titles.get(book, book)
        print(f"   [Customer] {user} returns {book} ({title})")
        if not self.kb.has_fact('Book', book):
            self.say(f"Sorry {user}, {book} is not in the library system.", "borrowed")
            return False
        mine = [c for c, b in self.kb.functions()['CopyOf'].items()
                if b == book and self.kb.has_fact('BorrowedBy', c, user)]
        if not mine:
            self.say(f"{user}, I have no record that you borrowed '{title}' ({book}).",
                     "borrowed")
            self.log.append(f"{user} tried to return {book}: no loan on record")
            return False
        copy = mine[0]
        self.kb.remove_fact('BorrowedBy', copy, user)
        self._save('give_back', copy, user)              # saved in the database
        facts, _ = RE.forward_chain(self.kb.facts(), self.kb.rules())
        print(f"   [Mark] BorrowedBy({copy}, {user}) removed -> "
              f"NotAvailable({copy}) = {('NotAvailable', copy) in facts}")
        self.say(f"Thank you {user}. Copy {copy} of '{title}' is back and available.", "done")
        shelf = self.kb.get_function('LocationOf', book)
        if shelf:
            print(f"   [KB] put it back on {shelf} ({' -> '.join(self.kb.location_path(book))})")
        self.log.append(f"{copy} of {book} returned by {user}")
        return True

    # ---------- تقرير نهائي + Truth Evaluation ----------
    def report(self):
        print("== Final report ==")
        for line in self.log:
            print("  -", line)
        facts, _ = RE.forward_chain(self.kb.facts(), self.kb.rules())
        rules = self.kb.rules()
        print("\n== Truth evaluation ==")
        for atom in [('OnShelf', 'B15', 'S7'), ('OnShelf', 'B25', 'S10'),
                     ('OnShelf', 'B30', 'S1'), ('Reserved', 'B25'),
                     ('NotAvailable', 'C20')]:
            print(f"  {RE.fmt(atom):28} -> {RE.is_true(atom, facts, rules)}")
        print("  exists x Book(x)              ->", RE.exists(('Book', '?x'), facts, rules))
        ok, bad = RE.forall('Book', 'PhysicalObject', facts, rules)
        print("  forall x (Book(x) -> PhysicalObject(x)) ->", ok)


# ===============================================================
# 3) عرض مفاهيم المادة قبل التشغيل
# ===============================================================
def concepts_demo(kb):
    print("== Concepts demo ==")
    print("  Unify Book(?x) with Book(B15):",
          RE.unify(('Book', '?x'), ('Book', 'B15'), {}))
    print("  Unify BorrowedBy(?x,Ahmed) with BorrowedBy(C20,Ahmed):",
          RE.unify(('BorrowedBy', '?x', 'Ahmed'), ('BorrowedBy', 'C20', 'Ahmed'), {}))
    print("  Resolution {Book(B15) v Magazine(B15)} , {~Book(B15)} ->",
          [set(r) for r in RE.resolve({'Book(B15)', 'Magazine(B15)'}, {'~Book(B15)'})])
    facts, trace = RE.forward_chain(kb.facts(), kb.rules())
    print("  Forward chaining on the initial KB infers:")
    for f, r in trace:
        print(f"     {RE.fmt(f):28} by {r}")
    print()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "library_data.json"
    data, kb = load_library(path)
    print(f"Library: {data['name']}\n")
    concepts_demo(kb)
    Robot(data, kb).run()