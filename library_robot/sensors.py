"""
sensors.py - what the robot "sees"
==================================
Two interchangeable sensors with the same interface:

    detect(place)  -> list of object classes seen there   (["Book", "Bag", ...])
    read_qr(obj)   -> book id decoded from the QR, or None

SimulatedSensor : reads the fake world of library_data.json   (demo / tests)
RealSensor      : a real camera (webcam, phone stream, video file or image)
                  QR decoding with OpenCV, optional YOLO for object detection.
"""
import re
import time
from typing import List, Optional

try:
    import cv2
except ImportError:          # simulation still works without OpenCV
    cv2 = None

QR_PREFIX = "LIB:"           # QR text may be "B15" or "LIB:B15"
BOOK_ID = re.compile(r"^[A-Za-z]+\d+$")


def normalize_qr(text: str) -> Optional[str]:
    """Clean the raw QR text. Returns the book id, or None if it is not ours."""
    text = (text or "").strip()
    if text.upper().startswith(QR_PREFIX):
        text = text[len(QR_PREFIX):]
    return text if BOOK_ID.match(text) else None


class Sensor:
    def detect(self, place) -> List[str]:
        raise NotImplementedError

    def read_qr(self, obj) -> Optional[str]:
        raise NotImplementedError


# ===============================================================
# Simulation (what robot.py did before)
# ===============================================================
class SimulatedSensor(Sensor):
    def __init__(self, world, qr_sides):
        self.world, self.qr_sides = world, qr_sides

    def detect(self, place):
        return [o["class"] for o in self.world.get(place, [])]

    def read_qr(self, obj):
        for side in (1, 2):
            print(f"   [QR] checking side {side} ...")
            if side in self.qr_sides.get(obj["id"], []):
                print(f"   [QR] found -> {obj['id']}")
                return obj["id"]
            if side == 1:
                print("   [Flip] QR not found, flipping the book")
        print("   [QR] no QR on both sides")
        return None


# ===============================================================
# Real camera
# ===============================================================
class RealSensor(Sensor):
    """
    source : 0 / 1 ... (webcam index), a video file, an image file,
             or a stream URL (e.g. phone app "IP Webcam": http://IP:8080/video)
    show   : open a preview window (needs a desktop; use show=False on a server)
    """

    def __init__(self, source=0, show=True, side_timeout=8.0, yolo_model=None):
        if cv2 is None:
            raise RuntimeError("OpenCV is required:  pip install opencv-python")
        self.source = int(source) if str(source).isdigit() else source
        self.show = show
        self.side_timeout = side_timeout
        self.cap = None
        self.qr = cv2.QRCodeDetector()
        self.yolo = None
        self._still = None
        self._yolo_name = yolo_model

    # ---------------- camera ----------------
    def open(self):
        if self.cap is not None or self._still is not None:
            return self
        if isinstance(self.source, str) and \
                self.source.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
            self._still = cv2.imread(self.source)
            if self._still is None:
                raise RuntimeError(f"cannot read image: {self.source}")
            return self
        self.cap = cv2.VideoCapture(self.source)
        if not self.cap.isOpened():
            raise RuntimeError(f"cannot open camera/source: {self.source!r}")
        return self

    def close(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        if self.show:
            try:
                cv2.destroyAllWindows()
            except cv2.error:
                pass

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    def frame(self):
        """Next frame, or None when a video file has ended."""
        self.open()
        if self._still is not None:
            return self._still.copy()
        ok, img = self.cap.read()
        return img if ok else None

    # ---------------- QR ----------------
    def decode(self, img) -> List[str]:
        """All QR texts found in one frame (also tries a 2x upscale for small codes)."""
        found = []
        for im in (img, cv2.resize(img, None, fx=2, fy=2)):
            try:
                ok, texts, _, _ = self.qr.detectAndDecodeMulti(im)
            except cv2.error:
                ok, texts = False, []
            if ok:
                found = [t for t in texts if t]
            if found:
                break
        return found

    def scan_once(self, timeout, label="show the QR code to the camera"):
        """Look for a valid book QR for up to `timeout` seconds. Returns (id, raw) or (None, None)."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            img = self.frame()
            if img is None:
                break
            for raw in self.decode(img):
                book = normalize_qr(raw)
                if book:
                    self._draw(img, f"QR: {book}", ok=True)
                    return book, raw
            self._draw(img, label)
            if self.show and cv2.waitKey(1) & 0xFF in (27, ord("q")):
                break
            if self._still is not None:
                break
        return None, None

    def read_qr(self, obj=None):
        """Same behaviour as the simulator: try side 1, ask to flip, try side 2."""
        for side in (1, 2):
            print(f"   [QR] checking side {side} ...")
            book, _ = self.scan_once(self.side_timeout,
                                     f"side {side}: show the QR code")
            if book:
                print(f"   [QR] found -> {book}")
                return book
            if side == 1:
                print("   [Flip] QR not found, flipping the book")
        print("   [QR] no QR on both sides")
        return None

    # ---------------- object detection (optional) ----------------
    def detect(self, place=None) -> List[str]:
        """Classes visible in the current frame. Needs:  pip install ultralytics
        (COCO already knows 'book', 'chair', 'laptop' ...). Without it -> []."""
        if self._yolo_name is None:
            return []
        if self.yolo is None:
            from ultralytics import YOLO          # imported lazily on purpose
            self.yolo = YOLO(self._yolo_name)
        img = self.frame()
        if img is None:
            return []
        res = self.yolo(img, verbose=False)[0]
        names = res.names
        out = []
        for c in res.boxes.cls.tolist():
            n = names[int(c)]
            out.append("Book" if n == "book" else n.capitalize())
        return out

    # ---------------- preview ----------------
    def _draw(self, img, text, ok=False):
        if not self.show:
            return
        color = (0, 170, 0) if ok else (0, 120, 255)
        cv2.rectangle(img, (0, 0), (img.shape[1], 34), (30, 30, 30), -1)
        cv2.putText(img, text, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        cv2.imshow("Library robot - camera (q = quit)", img)
        cv2.waitKey(1 if not ok else 600)
