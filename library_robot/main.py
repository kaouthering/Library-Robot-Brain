"""
main.py - Library Robot (real camera + persistent database)
===========================================================
    library-robot init                      create library.db (sample data on first run)
    library-robot status                    books, copies, loans, reservations
    library-robot qr                        make printable QR codes (qr_codes/)
    library-robot scan                      camera: show where each scanned book belongs
    library-robot checkout --user Ahmed     camera: scan a book, lend it to Ahmed
    library-robot return   --user Ahmed     camera: scan a book, take it back
    library-robot add-user Karim
    library-robot add-shelf S20 Room3 RowC1
    library-robot add-book B60 "Deep Learning" S20 --copies 2
    library-robot reserve B60 Sara
    library-robot sim                       the old simulation (no camera, nothing saved)
    library-robot serve                     web interface on http://127.0.0.1:8000  (pip install ".[web]")

Camera options (scan / checkout / return):
    --source 0                 webcam (default)
    --source http://IP:8080/video   phone used as a camera (IP Webcam app)
    --source photo.png | clip.mp4   image / video file (also good for testing)
    --no-window                no preview window (servers / SSH)
    --yolo yolov8n.pt          also report objects (books, chairs ...) seen by the camera
"""
import argparse
import os
import sys

from . import reasoning_engine as RE
from .database import LibraryDB
from .knowledge_base import target_of
from .robot import Robot, concepts_demo, load_library
from .sensors import RealSensor, cv2

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_JSON = os.path.join(HERE, "library_data.json")


# ---------------------------------------------------------------- helpers
def open_db(args, create=True):
    db = LibraryDB(args.db)
    if create and db.is_empty() and not getattr(args, "empty", False):
        db.init_from_json(SAMPLE_JSON)
        print(f"(new database {args.db} created with the sample library)")
    return db


def make_sensor(args):
    return RealSensor(args.source, show=not args.no_window, yolo_model=args.yolo,
                      side_timeout=args.timeout)


def make_robot(db, sensor=None):
    data, kb = db.load()
    return Robot(data, kb, db=db, sensor=sensor), data


def book_info(robot, book):
    """Everything the KB can say about a scanned book (uses the reasoning engine)."""
    kb = robot.kb
    title = robot.titles.get(book, book)
    if not kb.has_fact('Book', book):
        return f"  {book}: NOT in the library system (-> Lost & Found)"
    facts, rules = kb.facts(), kb.rules()
    lines = [f"  {book} - {title}"]
    got = target_of(kb, book)                       # adds QRRead(book), then infers
    kb.remove_fact('QRRead', book)
    if got:
        room, row, shelf, proof = got
        lines.append(f"  belongs on : {room} > {row} > {shelf}")
        lines.append(f"  proof      : {proof}")
    owners = [f[2] for f in kb.find('ReservedFor', book, '?u')]
    if owners:
        lines.append(f"  RESERVED for {', '.join(owners)}")
    for c, b in kb.functions()['CopyOf'].items():
        if b == book:
            who = [f[2] for f in kb.find('BorrowedBy', c, '?p')]
            lines.append(f"  copy {c}: " + (f"BORROWED by {', '.join(who)}" if who
                                             else "available"))
    return "\n".join(lines)


def scan_one(args, label):
    with make_sensor(args) as cam:
        print(f"Camera ready - {label}  (press q in the window to cancel)")
        book = cam.read_qr()
    return book


# ---------------------------------------------------------------- commands
def cmd_init(args):
    if args.reset and os.path.exists(args.db):
        os.remove(args.db)
    db = LibraryDB(args.db)
    if db.is_empty() and not args.empty:
        db.init_from_json(SAMPLE_JSON)
        print(f"{args.db}: created with the sample library")
    elif db.is_empty():
        print(f"{args.db}: created EMPTY - add shelves with add-shelf, then books with add-book")
    else:
        print(f"{args.db}: already has data (use --reset to start again)")


def cmd_status(args):
    db = open_db(args)
    print(f"{'book':5} {'title':26} {'home':5} {'on shelf':9} copies / loans / reservations")
    for b in db.status():
        copies = ", ".join(f"{c}" + (f" (out: {w})" if w else "") for c, w in b["copies"])
        res = f"  reserved: {', '.join(b['reserved'])}" if b["reserved"] else ""
        print(f"{b['id']:5} {b['title'][:26]:26} {b['home']:5} "
              f"{(b['on_shelf'] or '-'):9} {copies}{res}")
    print("\nLast events:")
    for e in db.recent_events(8):
        print(f"  {e['ts']}  {e['kind']:14} {e['detail']}")


def cmd_qr(args):
    if cv2 is None:
        sys.exit("OpenCV is required:  pip install opencv-python")
    import numpy as np
    db = open_db(args)
    os.makedirs(args.out, exist_ok=True)
    enc = cv2.QRCodeEncoder.create()
    tiles = []
    for b in db.q("SELECT id, title FROM books ORDER BY id"):
        code = enc.encode(b["id"])
        code = cv2.copyMakeBorder(code, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)
        code = cv2.resize(code, None, fx=10, fy=10, interpolation=cv2.INTER_NEAREST)
        tile = np.full((code.shape[0] + 70, code.shape[1]), 255, np.uint8)
        tile[:code.shape[0]] = code
        title = b["title"].encode("ascii", "replace").decode()[:28]
        cv2.putText(tile, b["id"], (8, code.shape[0] + 32), cv2.FONT_HERSHEY_SIMPLEX,
                    1.0, 0, 2)
        cv2.putText(tile, title, (8, code.shape[0] + 60), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, 60, 1)
        cv2.imwrite(os.path.join(args.out, f"{b['id']}.png"), tile)
        tiles.append(tile)
    h, w = tiles[0].shape
    cols = 3
    rows = -(-len(tiles) // cols)
    sheet = np.full((rows * (h + 30) + 30, cols * (w + 30) + 30), 255, np.uint8)
    for i, t in enumerate(tiles):
        y, x = 30 + (i // cols) * (h + 30), 30 + (i % cols) * (w + 30)
        sheet[y:y + h, x:x + w] = t
    cv2.imwrite(os.path.join(args.out, "_sheet.png"), sheet)
    print(f"{len(tiles)} QR codes in {args.out}/  (print _sheet.png and stick one on each book)")


def cmd_scan(args):
    db = open_db(args)
    robot, _ = make_robot(db)
    last, last_t = None, 0
    import time
    with make_sensor(args) as cam:
        print("Scanning ... show a book QR to the camera (q to quit)")
        while True:
            img = cam.frame()
            if img is None:
                break
            for raw in cam.decode(img):
                from .sensors import normalize_qr
                book = normalize_qr(raw)
                if book and (book != last or time.time() - last_t > 4):
                    last, last_t = book, time.time()
                    print(book_info(robot, book) + "\n")
                    cam._draw(img, f"QR: {book}", ok=True)
            if args.yolo:
                seen = cam.detect()
                if seen:
                    print("  camera sees:", ", ".join(sorted(set(seen))))
            cam._draw(img, "show a book QR code")
            if cam._still is not None:
                break
            if cam.show and cv2.waitKey(1) & 0xFF in (27, ord("q")):
                break


def _need_user(db, user):
    if user not in [r[0] for r in db.q("SELECT id FROM users")]:
        sys.exit(f"Unknown user '{user}'.  Register first:  library-robot add-user {user}")


def cmd_checkout(args):
    db = open_db(args)
    _need_user(db, args.user)
    book = scan_one(args, f"scan the book {args.user} wants to borrow")
    if not book:
        sys.exit("No QR code read.")
    robot, _ = make_robot(db)
    robot.serve(args.user, book)


def cmd_return(args):
    db = open_db(args)
    _need_user(db, args.user)
    book = scan_one(args, f"scan the book {args.user} is returning")
    if not book:
        sys.exit("No QR code read.")
    robot, _ = make_robot(db)
    robot.return_book(args.user, book)


def cmd_sim(args):
    data, kb = load_library(SAMPLE_JSON)
    print(f"Library: {data['name']}\n")
    concepts_demo(kb)
    Robot(data, kb).run()


def cmd_serve(args):
    try:
        from .web import run
    except ImportError:
        sys.exit('The web interface needs extra packages:  pip install ".[web]"')
    print(f"Library web interface: http://{args.host}:{args.port}   (database: {args.db})")
    run(args.host, args.port, args.db)


def cmd_add_user(args):
    open_db(args).add_user(args.name)
    print(f"user {args.name} added")


def cmd_add_shelf(args):
    open_db(args, create=False).add_shelf(args.shelf, args.room, args.row)
    print(f"shelf {args.shelf} added in {args.room} / {args.row}")


def cmd_add_book(args):
    db = open_db(args)
    if not db.q("SELECT 1 FROM shelves WHERE id=?", args.shelf):
        sys.exit(f"Unknown shelf {args.shelf}. Create it first with add-shelf.")
    db.add_book(args.book, args.title, args.shelf, args.copies, args.row)
    print(f"book {args.book} added ({args.copies} copies). Run 'library-robot qr' to print its QR.")


def cmd_reserve(args):
    db = open_db(args)
    _need_user(db, args.user)
    db.reserve(args.book, args.user)
    print(f"{args.book} reserved for {args.user}")


# ---------------------------------------------------------------- CLI
def main(argv=None):
    p = argparse.ArgumentParser(description="Library robot - real camera + database")
    p.add_argument("--db", default=os.environ.get("LIBRARY_DB", "library.db"),
                   help="database file (default: ./library.db or $LIBRARY_DB)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def cam(sp):
        sp.add_argument("--source", default="0")
        sp.add_argument("--no-window", action="store_true")
        sp.add_argument("--yolo", default=None, help="YOLO weights, e.g. yolov8n.pt")
        sp.add_argument("--timeout", type=float, default=8.0,
                        help="seconds to look at each side of the book")

    s = sub.add_parser("init");   s.add_argument("--reset", action="store_true")
    s.add_argument("--empty", action="store_true");           s.set_defaults(f=cmd_init)
    s = sub.add_parser("status");                              s.set_defaults(f=cmd_status)
    s = sub.add_parser("qr");     s.add_argument("--out", default="qr_codes")
    s.set_defaults(f=cmd_qr)
    s = sub.add_parser("scan");   cam(s);                      s.set_defaults(f=cmd_scan)
    s = sub.add_parser("checkout"); cam(s); s.add_argument("--user", required=True)
    s.set_defaults(f=cmd_checkout)
    s = sub.add_parser("return"); cam(s); s.add_argument("--user", required=True)
    s.set_defaults(f=cmd_return)
    s = sub.add_parser("sim");                                 s.set_defaults(f=cmd_sim)
    s = sub.add_parser("serve", help="web interface")
    s.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to open it from a phone on the same Wi-Fi")
    s.add_argument("--port", type=int, default=8000);          s.set_defaults(f=cmd_serve)
    s = sub.add_parser("add-user"); s.add_argument("name");    s.set_defaults(f=cmd_add_user)
    s = sub.add_parser("add-shelf")
    for a in ("shelf", "room", "row"):
        s.add_argument(a)
    s.set_defaults(f=cmd_add_shelf)
    s = sub.add_parser("add-book")
    for a in ("book", "title", "shelf"):
        s.add_argument(a)
    s.add_argument("--copies", type=int, default=1)
    s.add_argument("--row", type=int, default=None, help="row number inside the shelf")
    s.set_defaults(f=cmd_add_book)
    s = sub.add_parser("reserve")
    s.add_argument("book"); s.add_argument("user");            s.set_defaults(f=cmd_reserve)

    args = p.parse_args(argv)
    args.f(args)


if __name__ == "__main__":
    main()
