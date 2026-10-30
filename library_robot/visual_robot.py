"""
visual_robot.py  -  تشغيل الروبوت بشكل مرئي
===========================================
يستورد الصنف Robot من robot.py (نفس الـKB ونفس الاستدلال ونفس القرارات)
ويضيف فقط الرسم والتحريك. الإطار الجانبي يعرض اسم القاعدة والقاعدة فقط
مع كل حركة (القواعد كلها في rules.py).

الملفات المستخدمة (داخل الحزمة library_robot):
    robot.py  reasoning_engine.py  knowledge_base.py  rules.py  library_data.json
تشغيل:  python -m library_robot.visual_robot
"""
import math
import os
import queue
import random
import sys
import threading
import time
import tkinter as tk
import traceback

from .robot import Robot, load_library
from .rules import rule_for_log

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, "library_data.json")
STATE = {"speed": 1.0}
RUN = threading.Event()      # مضبوط = يشتغل ، غير مضبوط = إيقاف مؤقت
RUN.set()


def nap(t):
    """نوم يحترم السرعة والإيقاف المؤقت (Pause)."""
    left = t / STATE["speed"]
    while left > 0:
        RUN.wait()
        step = min(0.05, left)
        time.sleep(step)
        left -= step


# ---------------- ألوان وخطوط ----------------
BG = "#f4f5f7"
CARD_BORDER = "#d0d4da"
BLUE, BLUE_DARK = "#4a90e2", "#1c4e80"
ORANGE, ORANGE_DARK = "#e8590c", "#8f2d00"
GREEN = "#2f9e44"
PURPLE, PURPLE_DARK = "#7b4fd0", "#4a2a9a"     # الزبون
DESK = (30, 225)                                # الروبوت يخدم الزبائن عند Entrance
CUST_SPOT, CUST_FROM = (88, 225), (190, 225)    # مكان وقوف الزبون / من أين يأتي
# ألوان فقاعة كلام الروبوت: (خلفية, إطار, عنوان الحالة)
BUBBLE = {
    "reserved": ("#fff4e0", ORANGE, "RESERVED"),
    "borrowed": ("#fde8e8", "#c92a2a", "BORROWED"),
    "ok":       ("#e6f7ea", GREEN, "AVAILABLE"),
    "done":     ("#e6f7ea", GREEN, "HANDED OVER - MARKED BORROWED"),
    "info":     ("#ffffff", BLUE, "ROBOT"),
}
WIN = sys.platform.startswith("win")
FONT = "Segoe UI" if WIN else "DejaVu Sans"       # fonts that exist on each OS
MONO = "Consolas" if WIN else "DejaVu Sans Mono"

# ---------------- الخريطة ----------------
POS = {
    "Entrance": (30, 225), "CorrA": (135, 225), "CorrH": (300, 225),
    "Corridor": (390, 225), "CorrC": (645, 225), "LostAndFound": (695, 300),
    "Room1": (135, 178), "RowA1": (135, 130), "S1": (70, 118), "S2": (200, 118),
    "Room2": (390, 178), "RowB1": (390, 130), "S10": (325, 118), "S11": (455, 118),
    "Room3": (645, 178), "RowC1": (645, 130), "S7": (580, 118), "S8": (710, 118),
    "ReadingHall": (300, 285), "Table1": (120, 400), "Table2": (450, 470),
    "Chair1": (300, 410), "Floor1": (480, 400),
}
BOXES = [
    ("Room1", 10, 10, 260, 200, "#eaf2ff"),
    ("Room2", 265, 10, 515, 200, "#e9f8ec"),
    ("Room3", 520, 10, 770, 200, "#f3ecff"),
    ("Corridor", 10, 200, 770, 250, "#f1f1f1"),
    ("Reading hall", 10, 255, 600, 530, "#fff6e0"),
    ("Lost & Found office", 620, 255, 770, 345, "#fde8e8"),
]
NO_LABEL = {"CorrA", "CorrH", "Corridor", "CorrC", "Room1", "Room2", "Room3",
            "ReadingHall"}

CASE_W = 48                  # نصف عرض خزانة الرف
ROW_H = 28                   # ارتفاع الصف الواحد داخل الخزانة
BOOK_W, BOOK_H = 40, 20
FLY_T, ADJ_T = 0.7, 0.5      # زمن "يضع الكتاب" ثم "يعدّله"


# ===============================================================
# يلتقط print الروبوت ويحوّل كل سطر إلى (اسم القاعدة, القاعدة)
# ===============================================================
class QueueWriter:
    def __init__(self, q, robot):
        self.q, self.robot, self.buf = q, robot, ""

    def write(self, s):
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            kind = self.robot.place_type.get(self.robot.pos, "")
            hit = rule_for_log(line, kind)
            if hit:
                self.q.put(hit)
                if hit[0] in ("A2_Look", "A3_Ignore"):
                    nap(1.2)                        # يبقيهم ظاهرين أطول
            nap(0.45)                               # يبطّئ الروبوت ليظهر ما يحدث

    def flush(self):
        pass


# ===============================================================
# الروبوت مع التحريك
# ===============================================================
class VisualRobot(Robot):
    """نفس Robot تماماً، مع تحريك مرئي."""
    def __init__(self, data, kb):
        super().__init__(data, kb)
        self.data = data
        self.vx, self.vy = POS[self.pos]
        self.cur_path = []
        self.shelves = {s["id"] for s in data["shelves"]}
        self.n_rows = max(1, int(data.get("shelf_rows", 3)))
        self.qr = None          # حالة قراءة الـQR
        self.target = None      # الرف الذي يتجه إليه الروبوت
        self.cust = None        # الزبون الحالي {name, x, y, has}
        self.bubble = None      # ما يقوله الروبوت {text, kind}
        self.give = None        # كتاب يُمدّ من الروبوت للزبون {id, p}

    def slot_of(self, bid):
        """الصف الذي يوضع فيه الكتاب داخل خزانته (1-based بالداتا)."""
        v = self.data.get("book_row_on_shelf", {}).get(bid)
        if v is None:
            d = "".join(ch for ch in bid if ch.isdigit())
            v = (int(d) if d else 0) + 1
        return (int(v) - 1) % self.n_rows

    def pause(self, t):
        nap(t)

    def move_to(self, goal):
        if goal == self.pos:
            return
        if goal in self.patrol or goal == "LostAndFound":
            self.qr, self.target = None, None
        path = self.find_path(self.pos, goal)
        print(f"   [Move] {' -> '.join(path)}")
        self.cur_path = path
        for a, b in zip(path, path[1:]):
            x0, y0 = POS[a]
            x1, y1 = POS[b]
            for i in range(1, 21):
                self.vx = x0 + (x1 - x0) * i / 20
                self.vy = y0 + (y1 - y0) * i / 20
                nap(0.02)
        self.cur_path = []
        self.kb.remove_fact("At", self.id, self.pos)
        self.pos = goal
        self.kb.add_fact("At", self.id, goal)

    def read_qr(self, obj):
        bid = obj["id"]
        self.qr = {"obj": bid, "side": 1, "state": "scanning", "id": None}
        for side in (1, 2):
            self.qr.update(side=side, state="scanning")
            print(f"   [QR] scanning side {side} ...")
            self.pause(1.0)
            if side in self.qr_sides.get(bid, []):
                self.qr.update(state="found", id=bid)
                print(f"   [QR] decoded -> {bid}")
                self.pause(0.8)
                shelf = self.kb.get_function("LocationOf", bid)
                path = " > ".join(self.kb.location_path(bid))
                self.qr.update(state="kb", shelf=shelf, path=path,
                               slot=self.slot_of(bid))
                print(f"   [QR] asking the KB: LocationOf({bid}) = ?")
                self.pause(1.4)
                self.target = shelf
                return bid
            self.qr.update(state="none")
            if side == 1:
                print("   [Flip] QR not found, flipping the book")
                self.pause(0.8)
        print("   [QR] no QR on both sides")
        self.qr.update(state="lost")
        self.pause(0.8)
        return None

    # ---------- الزبائن: وصول، كلام الروبوت، تسليم الكتاب، مغادرة ----------
    def _walk_customer(self, a, b, steps=24):
        for i in range(1, steps + 1):
            self.cust["x"] = a[0] + (b[0] - a[0]) * i / steps
            self.cust["y"] = a[1] + (b[1] - a[1]) * i / steps
            nap(0.03)

    def customer_arrives(self, user, book):
        self.bubble = None
        self.cust = {"name": user, "x": CUST_FROM[0], "y": CUST_FROM[1],
                     "has": None, "wants": book}
        self._walk_customer(CUST_FROM, CUST_SPOT)
        self.pause(0.6)

    def say(self, text, kind="info"):
        super().say(text, kind)                 # نفس سطر الـconsole (وللقواعد)
        self.bubble = {"text": text, "kind": kind}
        self.pause(1.8)

    def hand_over(self, user, copy):
        for i in range(1, 26):                  # الكتاب ينتقل من الروبوت للزبون
            self.give = {"id": copy, "p": i / 25}
            nap(0.03)
        self.give = None
        self.cust["has"] = copy                 # الزبون صار يحمل الكتاب
        self.pause(0.6)

    def customer_leaves(self):
        self.pause(0.8)
        self.bubble = None
        if self.cust:
            self._walk_customer(CUST_SPOT, CUST_FROM)
        self.cust = None


# ===============================================================
# النافذة
# ===============================================================
class App:
    def __init__(self, root):
        self.root = root
        root.title("Library robot - Knowledge Representation and Reasoning")
        root.configure(bg=BG)

        # ----- الشريط العلوي -----
        head = tk.Frame(root, bg=BG)
        head.grid(row=0, column=0, columnspan=2, sticky="we", padx=12, pady=(10, 4))
        tk.Label(head, text="Library Robot", font=(FONT, 14, "bold"),
                 bg=BG, fg="#222").pack(side="left")
        tk.Label(head, text="Knowledge Representation and Reasoning",
                 font=(FONT, 9), bg=BG, fg="#777").pack(side="left", padx=10)
        self.btn = tk.Button(head, text="Start", width=10, command=self.start)
        self.btn.pack(side="right")
        self.pause_btn = tk.Button(head, text="Pause", width=8,
                                   command=self.toggle_pause)
        self.pause_btn.pack(side="right", padx=(0, 6))
        scale = tk.Scale(head, from_=0.1, to=5, resolution=0.1,
                         orient="horizontal", length=130,
                         command=self.set_speed, bg=BG, highlightthickness=0)
        scale.set(1.0)
        scale.pack(side="right", padx=(0, 10))
        tk.Label(head, text="speed", font=(FONT, 9), bg=BG,
                 fg="#555").pack(side="right")

        # ----- الخريطة + المفتاح -----
        left = tk.Frame(root, bg=BG)
        left.grid(row=1, column=0, sticky="n", padx=(12, 8), pady=(0, 10))
        self.canvas = tk.Canvas(left, width=780, height=540, bg="white",
                                highlightthickness=1,
                                highlightbackground=CARD_BORDER)
        self.canvas.pack()
        self.legend = tk.Canvas(left, width=780, height=26, bg=BG,
                                highlightthickness=0)
        self.legend.pack(pady=(6, 0))
        self._draw_legend()

        # ----- الإطار الجانبي: القواعد فقط -----
        panel = tk.Frame(root, bg=BG)
        panel.grid(row=1, column=1, sticky="ns", padx=(0, 12), pady=(0, 10))
        tk.Label(panel, text="QR READER", font=(FONT, 8, "bold"), bg=BG,
                 fg="#777").pack(anchor="w")
        self.qrc = tk.Canvas(panel, width=380, height=146, bg="white",
                             highlightthickness=1,
                             highlightbackground=CARD_BORDER)
        self.qrc.pack(pady=(2, 8))
        tk.Label(panel, text="CURRENT RULE", font=(FONT, 8, "bold"), bg=BG,
                 fg="#777").pack(anchor="w")
        card = tk.Frame(panel, bg="white", highlightthickness=1,
                        highlightbackground=CARD_BORDER, width=380, height=100)
        card.pack(fill="x", pady=(2, 0))
        card.pack_propagate(False)
        self.cur_name = tk.Label(card, text="-", font=(FONT, 13, "bold"),
                                 bg="white", fg=BLUE_DARK, anchor="w")
        self.cur_name.pack(fill="x", padx=12, pady=(10, 2))
        self.cur_rule = tk.Label(card, text="Press Start", font=(MONO, 10),
                                 bg="white", fg="#222", anchor="nw",
                                 justify="left", wraplength=350)
        self.cur_rule.pack(fill="x", padx=12)

        tk.Label(panel, text="RULE HISTORY", font=(FONT, 8, "bold"), bg=BG,
                 fg="#777").pack(anchor="w", pady=(12, 0))
        self.text = tk.Text(panel, width=46, height=16, font=(MONO, 9),
                            wrap="word", bg="white", relief="flat",
                            highlightthickness=1,
                            highlightbackground=CARD_BORDER, state="disabled")
        self.text.pack(fill="both", expand=True, pady=(2, 0))
        self.text.tag_config("kb", font=(MONO, 9, "bold"), foreground=BLUE_DARK)
        self.text.tag_config("act", font=(MONO, 9, "bold"), foreground=ORANGE)
        self.text.tag_config("err", foreground="#b00020")

        self.q = queue.Queue()
        self.thread = None
        self.last_rule = None
        self.reset()
        self.tick()

    @staticmethod
    def set_speed(v):
        STATE["speed"] = float(v)

    def toggle_pause(self):
        if RUN.is_set():
            RUN.clear()
            self.pause_btn.config(text="Resume")
        else:
            RUN.set()
            self.pause_btn.config(text="Pause")

    # ---------------- القواعد ----------------
    def show_rule(self, name, text):
        is_kb = name.startswith("R")
        color = BLUE_DARK if is_kb else ORANGE
        self.cur_name.config(text=name, fg=color)
        self.cur_rule.config(text=text)
        self.text.config(state="normal")
        self.text.insert("end", name + "\n", "kb" if is_kb else "act")
        self.text.insert("end", "  " + text + "\n\n")
        self.text.see("end")
        self.text.config(state="disabled")

    def clear_rules(self):
        self.last_rule = None
        self.cur_name.config(text="-", fg=BLUE_DARK)
        self.cur_rule.config(text="Press Start")
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.config(state="disabled")

    # ---------------- التشغيل ----------------
    def reset(self):
        self.data, kb = load_library(DATA_PATH)
        self.robot = VisualRobot(self.data, kb)
        self.flying = []
        # الكتب الموجودة أصلاً على الرفوف تبدأ بلا أنيميشن
        self.anim = {}
        for place, objs in self.robot.world.items():
            if place in self.robot.shelves:
                for o in objs:
                    if o["class"] == "Book":
                        self.anim[o["id"]] = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.reset()
        self.clear_rules()
        RUN.set()
        self.pause_btn.config(text="Pause")
        self.btn.config(text="Running...")
        robot = self.robot

        def work():
            old = sys.stdout
            sys.stdout = QueueWriter(self.q, robot)
            try:
                robot.run()
            except Exception:
                sys.stdout = old
                self.q.put(("ERROR", traceback.format_exc()))
            finally:
                sys.stdout = old

        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()

    def tick(self):
        try:
            while True:
                name, text = self.q.get_nowait()
                if name == "ERROR":
                    self.text.config(state="normal")
                    self.text.insert("end", text, "err")
                    self.text.config(state="disabled")
                elif name != self.last_rule or name == "A1_Move":
                    self.show_rule(name, text)      # بلا تكرار لنفس القاعدة
                    self.last_rule = name
        except queue.Empty:
            pass
        if self.thread and not self.thread.is_alive():
            self.btn.config(text="Restart")
        self._detect_placements()
        try:
            self.draw()
            self.draw_qr()
        except RuntimeError:
            pass
        self.root.after(30, self.tick)

    def _detect_placements(self):
        """حين يظهر كتاب جديد على رف: نشغّل أنيميشن (يضع الكتاب ثم يعدّله)."""
        r = self.robot
        now = time.time()
        on_shelf = set()
        for place, objs in list(r.world.items()):
            if place in r.shelves:
                for o in objs:
                    if o["class"] == "Book":
                        on_shelf.add(o["id"])
                        if o["id"] not in self.anim:
                            self.anim[o["id"]] = {"t0": now, "fx": r.vx, "fy": r.vy}
        for bid in list(self.anim):
            if bid not in on_shelf:
                del self.anim[bid]

    # ---------------- لوحة QR ----------------
    def draw_qr(self):
        c, r = self.qrc, self.robot
        c.delete("all")
        c.create_text(8, 6, text="QR reader", anchor="nw", fill="#333",
                      font=(FONT, 10, "bold"))
        q = r.qr
        if not q:
            c.create_text(190, 78, text="waiting for a book...", fill="#999",
                          font=(FONT, 10))
            return
        bid, side, st = q["obj"], q["side"], q["state"]
        has = side in r.qr_sides.get(bid, [])
        c.create_rectangle(10, 30, 160, 190, fill=BLUE, outline=BLUE_DARK, width=2)
        c.create_text(85, 38, text=f"book - side {side}", fill="white",
                      font=(FONT, 7))
        if has:
            x0, y0, cell = 40, 60, 11
            c.create_rectangle(x0 - 6, y0 - 6, x0 + 9 * cell + 6, y0 + 9 * cell + 6,
                               fill="white", outline="")
            rng = random.Random(bid)
            for i in range(9):
                for j in range(9):
                    corner = (i < 3 and j < 3) or (i < 3 and j > 5) or (i > 5 and j < 3)
                    if corner:
                        black = (i in (0, 2, 6, 8) or j in (0, 2, 6, 8) or
                                 (i % 3 == 1 and j % 3 == 1))
                    else:
                        black = rng.random() < 0.5
                    if black:
                        c.create_rectangle(x0 + j * cell, y0 + i * cell,
                                           x0 + (j + 1) * cell, y0 + (i + 1) * cell,
                                           fill="black", outline="")
            if st == "scanning":
                ly = y0 + (time.time() * 70) % (9 * cell)
                c.create_line(x0 - 6, ly, x0 + 9 * cell + 6, ly, fill="red", width=2)
            if st in ("found", "kb"):
                c.create_rectangle(x0 - 6, y0 - 6, x0 + 9 * cell + 6, y0 + 9 * cell + 6,
                                   outline=GREEN, width=3)
        else:
            c.create_text(85, 110, text="no QR on\nthis side", fill="white",
                          font=(FONT, 8), justify="center")
        if st == "scanning":
            msg = f"Scanning side {side}..."
        elif st == "none":
            msg = "No QR on this side.\nFlipping the book..." if side == 1 \
                else "No QR on both sides."
        elif st == "found":
            msg = f"Decoded:\n{bid}"
        elif st == "kb":
            row = q.get("slot", 0) + 1
            msg = (f"Decoded: {bid}\n"
                   f"KB: LocationOf({bid}) = {q['shelf']}\n"
                   f"Path: {q['path']}\n"
                   f"Put on {q['shelf']}, row {row}, then adjust")
        else:
            msg = "No QR on both sides.\nGo to Lost & Found"
        c.create_text(172, 34, text=msg, anchor="nw", width=245, fill="#222",
                      font=(MONO, 8))
        c.scale("all", 0, 0, 0.72, 0.72)      # لوحة أصغر

    # ---------------- المفتاح (Legend) ----------------
    def _draw_legend(self):
        c = self.legend
        items = [("Robot", ORANGE, ORANGE_DARK, "oval"),
                 ("Book", BLUE, BLUE_DARK, "rect"),
                 ("Bookcase (numbered rows)", "#8d6e63", "#4e342e", "rect"),
                 ("Table", "#c9a26b", "#8a6a3a", "rect"),
                 ("Chair", "#9ec5a0", "#5f8f62", "rect"),
                 ("Destination", "white", GREEN, "rect"),
                 ("Customer", PURPLE, PURPLE_DARK, "oval")]
        x = 8
        for label, fill, edge, shape in items:
            if shape == "oval":
                c.create_oval(x, 6, x + 14, 20, fill=fill, outline=edge, width=2)
            else:
                c.create_rectangle(x, 6, x + 18, 20, fill=fill, outline=edge,
                                   width=2 if fill == "white" else 1)
            c.create_text(x + 24, 13, text=label, anchor="w", fill="#444",
                          font=(FONT, 9))
            x += 24 + 7 * len(label) + 22

    # ---------------- الخريطة ----------------
    def draw(self):
        c, r = self.canvas, self.robot
        c.delete("all")

        for name, x1, y1, x2, y2, col in BOXES:
            c.create_rectangle(x1, y1, x2, y2, fill=col, outline="#bbb")
            if name.startswith("Room"):
                # اسم الغرفة: شارة واضحة أسفل الغرفة (بعيدة عن الخزانات)
                cx, cy = (x1 + x2) / 2, y2 - 14
                c.create_rectangle(cx - 34, cy - 11, cx + 34, cy + 11,
                                   fill="white", outline="#9aa3ad")
                c.create_text(cx, cy, text=name, fill="#333",
                              font=(FONT, 10, "bold"))
            else:
                c.create_text(x1 + 6, y1 + 3, text=name, anchor="nw",
                              fill="#777", font=(FONT, 9, "bold"))

        for a, b in self.data["map_edges"]:
            c.create_line(*POS[a], *POS[b], fill="#cfcfcf", dash=(3, 3))
        if len(r.cur_path) > 1:
            pts = [v for n in r.cur_path for v in POS[n]]
            c.create_line(*pts, fill="#f08c00", width=3)

        for name, (x, y) in POS.items():
            if name.startswith("Table"):
                c.create_rectangle(x - 36, y - 14, x + 36, y + 14, fill="#c9a26b",
                                   outline="#8a6a3a")
            elif name == "Chair1":
                c.create_rectangle(x - 20, y - 20, x + 20, y + 20, fill="#9ec5a0",
                                   outline="#5f8f62")
            elif name == "Floor1":
                c.create_rectangle(x - 45, y - 24, x + 45, y + 24, outline="#999",
                                   dash=(4, 3))
            elif name in r.shelves:
                self._bookcase(c, x, y)
            if name in r.shelves:
                c.create_text(x, y + 22, text=name, fill="#444",
                              font=(FONT, 8, "bold"))
            elif name not in NO_LABEL:
                c.create_text(x, y + 30, text=name, fill="#444",
                              font=(FONT, 8, "bold"))

        self._shelf_books(c)

        for place, objs in list(r.world.items()):
            if place not in POS or place in r.shelves:
                continue
            x, y = POS[place]
            n = len(objs)
            for i, o in enumerate(list(objs)):
                ox = x + (i - (n - 1) / 2) * 46
                is_book = o["class"] == "Book"
                if not is_book:
                    label = o["class"]
                elif place == "LostAndFound":
                    label = "Book ?"
                else:
                    label = "Book"
                c.create_rectangle(ox - 21, y - 10, ox + 21, y + 10,
                                   fill=BLUE if is_book else "#b0b0b0",
                                   outline=BLUE_DARK if is_book else "#777")
                c.create_text(ox, y, text=label, fill="white" if is_book else "#222",
                              font=(FONT, 8))
                if not is_book:
                    c.create_rectangle(ox - 21, y - 10, ox + 21, y + 10,
                                       outline="#c92a2a", width=2, dash=(3, 2))
                    c.create_text(ox, y + 20, text="obstacle - ignored",
                                  fill="#c92a2a", font=(FONT, 7, "bold"))

        if r.target in POS:
            tx, ty = POS[r.target]
            if r.target in r.shelves:
                top = ty - 14 - r.n_rows * ROW_H
                c.create_rectangle(tx - CASE_W - 6, top - 6, tx + CASE_W + 6, ty - 8,
                                   outline=GREEN, width=3)
                if r.qr and r.qr.get("state") == "kb":      # إبراز الصف المطلوب
                    slot = r.qr.get("slot", 0)
                    cy = ty - 14 - (r.n_rows - slot - 0.5) * ROW_H
                    c.create_rectangle(tx - CASE_W + 2, cy - ROW_H / 2 + 2,
                                       tx + CASE_W - 2, cy + ROW_H / 2 - 2,
                                       outline=GREEN, width=2)
            else:
                c.create_rectangle(tx - 42, ty - 42, tx + 42, ty + 17,
                                   outline=GREEN, width=3)
            c.create_text(tx, ty + 36, text="destination", fill=GREEN,
                          font=(FONT, 9, "bold"))
            c.create_line(r.vx, r.vy, tx, ty, fill=GREEN, dash=(5, 4),
                          arrow="last")

        x, y = r.vx, r.vy
        c.create_oval(x - 14, y - 14, x + 14, y + 14, fill=ORANGE,
                      outline=ORANGE_DARK, width=2)
        c.create_text(x, y, text="R", fill="white", font=(FONT, 10, "bold"))
        if any(f[0] == "Holding" for f in r.kb.facts()):
            label = r.qr["id"] if r.qr and r.qr.get("id") else "Book"
            c.create_rectangle(x - 20, y - 38, x + 20, y - 18, fill=BLUE,
                               outline=BLUE_DARK)
            c.create_text(x, y - 28, text=label, fill="white", font=(FONT, 8))

        for bid, bx, by in self.flying:      # كتاب يُوضع بالصف (فوق كل شيء)
            c.create_rectangle(bx - BOOK_W / 2, by - BOOK_H / 2,
                               bx + BOOK_W / 2, by + BOOK_H / 2,
                               fill=BLUE, outline=BLUE_DARK)
            c.create_text(bx, by, text=bid, fill="white", font=(FONT, 8))

        self._draw_desk(c)

    def _draw_desk(self, c):
        """الزبون + الكتاب الممدود + فقاعة كلام الروبوت (محجوز / مستعار / متاح)."""
        r = self.robot
        cu = r.cust
        if cu:
            x, y = cu["x"], cu["y"]
            c.create_oval(x - 7, y - 21, x + 7, y - 7, fill=PURPLE,
                          outline=PURPLE_DARK, width=2)                # الرأس
            c.create_oval(x - 11, y - 6, x + 11, y + 14, fill=PURPLE,
                          outline=PURPLE_DARK, width=2)                # الجسم
            c.create_text(x, y + 24, text=cu["name"], fill=PURPLE_DARK,
                          font=(FONT, 8, "bold"))
            if cu["has"]:                                               # يحمل الكتاب
                c.create_rectangle(x + 10, y - 4, x + 50, y + 12, fill=BLUE,
                                   outline=BLUE_DARK)
                c.create_text(x + 30, y + 4, text=cu["has"], fill="white",
                              font=(FONT, 8))
        g = r.give
        if g and cu:                                                    # الكتاب في الطريق
            gx = r.vx + (cu["x"] - r.vx) * g["p"]
            gy = r.vy - 4
            c.create_rectangle(gx - 20, gy - 10, gx + 20, gy + 10, fill=BLUE,
                               outline=BLUE_DARK)
            c.create_text(gx, gy, text=g["id"], fill="white", font=(FONT, 8))
        b = r.bubble
        if b:
            bg, edge, tag = BUBBLE.get(b["kind"], BUBBLE["info"])
            x1, y1, x2, y2 = 100, 276, 400, 356
            c.create_polygon(105, y1, 140, y1, 55, 241, fill=bg, outline=edge,
                             width=2)                                   # ذيل الفقاعة
            c.create_rectangle(x1, y1, x2, y2, fill=bg, outline=edge, width=2)
            c.create_line(106, y1, 139, y1, fill=bg, width=3)           # يخفي خط الوصل
            c.create_rectangle(x1 + 8, y1 + 8, x1 + 8 + 7 * len(tag) + 14, y1 + 26,
                               fill=edge, outline="")
            c.create_text(x1 + 15, y1 + 17, text=tag, anchor="w", fill="white",
                          font=(FONT, 8, "bold"))
            c.create_text(x1 + 10, y1 + 32, text=b["text"], anchor="nw",
                          width=x2 - x1 - 20, fill="#222", font=(FONT, 10))

    def _bookcase(self, c, x, y):
        """الرف S = خزانة كبيرة فيها صفوف مرقّمة (shelf_rows من الداتا)."""
        r = self.robot
        h = r.n_rows * ROW_H
        x1, y1, x2, y2 = x - CASE_W, y - 14 - h, x + CASE_W, y - 14
        c.create_rectangle(x1, y1, x2, y2, fill="#8d6e63", outline="#4e342e",
                           width=2)
        for i in range(r.n_rows):
            ry = y1 + i * ROW_H
            if i:
                c.create_line(x1, ry, x2, ry, fill="#4e342e")
            c.create_text(x2 - 9, ry + ROW_H / 2, text=str(i + 1),
                          fill="#efe0d5", font=(FONT, 7, "bold"))

    def _slot_center(self, place, slot, i, n):
        x, y = POS[place]
        nr = self.robot.n_rows
        cy = y - 14 - (nr - slot - 0.5) * ROW_H
        cx = x + (i - (n - 1) / 2) * (BOOK_W + 6)
        return cx, cy

    def _shelf_books(self, c):
        """كتب على صفوف الخزانات + أنيميشن (يضع الكتاب بالصف ثم يعدّله)."""
        r = self.robot
        self.flying = []
        groups = {}
        for place, objs in list(r.world.items()):
            if place not in r.shelves:
                continue
            for o in objs:
                if o["class"] == "Book":
                    groups.setdefault((place, r.slot_of(o["id"])), []).append(o["id"])
        for (place, slot), ids in groups.items():
            ids.sort()
            n = len(ids)
            for i, bid in enumerate(ids):
                cx, cy = self._slot_center(place, slot, i, n)
                a = self.anim.get(bid)
                if a is not None:
                    t = time.time() - a["t0"]
                    if t < FLY_T:                       # يضع الكتاب في الصف
                        p = t / FLY_T
                        p = p * p * (3 - 2 * p)
                        cx = a["fx"] + (cx - a["fx"]) * p
                        cy = a["fy"] + (cy - a["fy"]) * p
                        self.flying.append((bid, cx, cy))
                        continue
                    elif t < FLY_T + ADJ_T:             # ثم يعدّله (يدفّزه للخلف)
                        q = 1 - (t - FLY_T) / ADJ_T
                        cx += 6 * q + math.sin(t * 30) * 2.5 * q
                    else:
                        self.anim[bid] = None
                c.create_rectangle(cx - BOOK_W / 2, cy - BOOK_H / 2,
                                   cx + BOOK_W / 2, cy + BOOK_H / 2,
                                   fill=BLUE, outline=BLUE_DARK)
                c.create_text(cx, cy, text=bid, fill="white", font=(FONT, 8))


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()