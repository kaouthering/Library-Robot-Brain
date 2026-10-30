import re

KB_RULES = [

  
    ("R1_table",
     [("Book", "?x"), ("OnTable", "?x", "?t")],
     ("NeedToOrganize", "?x")),          # كتاب فوق طاولة → يحتاج ترتيب
    ("R2_chair",
     [("Book", "?x"), ("OnChair", "?x", "?c")],
     ("NeedToOrganize", "?x")),          # كتاب فوق كرسي → يحتاج ترتيب
    ("R3_floor",
     [("Book", "?x"), ("OnFloor", "?x")],
     ("NeedToOrganize", "?x")),          # كتاب على الأرض → يحتاج ترتيب

   
    ("R4_borrowed",
     [("BorrowedBy", "?x", "?p")],
     ("NotAvailable", "?x")),            # الكتاب مستعار → غير متوفر
    ("R5_reserved",
     [("ReservedFor", "?x", "?u")],
     ("Reserved", "?x")),                # الكتاب محجوز لشخص → محجوز
    ("R6_physical",
     [("Book", "?x")],
     ("PhysicalObject", "?x")),          # الكتاب شيء مادي

   
    ("R7_shelf",
     [("QRRead", "?b"), ("BelongsOn", "?b", "?s")],
     ("TargetShelf", "?b", "?s")),       # الرف المستهدف
    ("R8_row",
     [("TargetShelf", "?b", "?s"), ("InRow", "?s", "?r")],
     ("TargetRow", "?b", "?r")),         # الصف المستهدف
    ("R9_room",
     [("TargetShelf", "?b", "?s"), ("InRoom", "?s", "?r")],
     ("TargetRoom", "?b", "?r")),        # الغرفة المستهدفة
]


def load_kb_rules(kb):
    # تحميل كل القواعد داخل قاعدة المعرفة
    for name, premises, conclusion in KB_RULES:
        kb.add_rule(name, premises, conclusion)



ACTION_RULES = {
    "A1_Move":       "At(r,x) ∧ Path(x,y)  →  At(r,y)",                          # تحرك من مكان لآخر
    "A2_Look":       "At(r,p) ∧ Sees(r,b)  →  Observed(b,p)",                    # يسجل أنه شاف كتاب
    "A3_Ignore":     "Sees(r,o) ∧ ¬Book(o)  →  Ignore(o)",                       # الشيء مش كتاب → تجاهله
    "A4_PickUp":     "NeedToOrganize(b) ∧ HandEmpty(r)  →  Holding(r,b)",        # يحتاج ترتيب ويد الروبوت فارغة → التقط الكتاب
    "A5_ScanQR":     "Holding(r,b)  →  Scan(r,b)",                               # مسح رمز QR للكتاب الممسوك
    "A6_Flip":       "Scan(r,b) ∧ ¬QRVisible(b)  →  Flip(b)",                    # QR مش ظاهر → اقلب الكتاب
    "A7_Decode":     "Scan(r,b) ∧ QRVisible(b)  →  QRRead(b)",                   # QR ظاهر → اقرأه
    "A8_LostFound":  "Holding(r,b) ∧ ¬QRRead(b)  →  GoTo(r,LostAndFound)",       # ما قدرش يقرأ QR → روح لقسم المفقودات
    "A9_Drop":       "At(r,LostAndFound) ∧ Holding(r,b)  →  ¬Holding(r,b)",      # في المفقودات → ضع الكتاب
    "A10_Verify":    "At(r,s) ∧ TargetShelf(b,s)  →  CorrectShelf(r,s)",         # إذا الرف هو المستهدف → يعتبر صحيح
    "A11_Put":       "Holding(r,b) ∧ CorrectShelf(r,s)  →  OnShelf(b,s) ∧ ¬Holding(r,b)",  # ضع الكتاب على الرف الصحيح
    "A12_Abort":     "Holding(r,b) ∧ ¬CorrectShelf(r,s)  →  Abort(r)",           # إذا شرط الرف غير متحقق → ألغِ العملية
    "A13_Leave":     "¬NeedToOrganize(b)  →  Leave(b)",                          # الكتاب ما يحتاجش ترتيب → لا تلتقطه

    # ---- طلبات الاستعارة من الزبائن ----
    "A14_Request":   "Request(u,b)  →  Check(b)",                                # زبون يطلب كتاباً → افحص حالته في النظام
    "A15_DenyRes":   "Request(u,b) ∧ Reserved(b) ∧ ¬ReservedFor(b,u)  →  Say(u,Reserved)",   # الكتاب محجوز لغيره → أخبره أنه محجوز
    "A16_DenyBor":   "Request(u,b) ∧ ∀c (CopyOf(c)=b → NotAvailable(c))  →  Say(u,Borrowed)",  # كل النسخ مستعارة → أخبره أنه مستعار
    "A17_Lend":      "Request(u,b) ∧ CopyOf(c)=b ∧ ¬NotAvailable(c)  →  Handover(r,u,c)",       # نسخة متاحة → امدده بها
    "A18_MarkBor":   "Handover(r,u,c)  →  BorrowedBy(c,u) ∧ NotAvailable(c)",   # سجّل النسخة مستعارة باسم الزبون
}


def _fmt(atom):
    # تحويل ذرة (atom) إلى نص مثل: Book(x)
    return f"{atom[0]}({','.join(str(x).lstrip('?') for x in atom[1:])})"


def _fol(premises, conclusion):
    # تحويل قاعدة إلى صيغة منطقية نصية: شروط ∧ شروط → نتيجة
    return " ∧ ".join(_fmt(p) for p in premises) + "  →  " + _fmt(conclusion)


# نص كل قاعدة (قواعد KB + قواعد الأفعال) في قاموس واحد
RULE_TEXT = {name: _fol(p, c) for name, p, c in KB_RULES}
RULE_TEXT.update(ACTION_RULES)


LOG_MAP = [
    ("[Move]",                    "A1_Move"),
    ("[Camera] sees",             "A2_Look"),
    ("[Camera]",                  "A3_Ignore"),
    ("[PickUp]",                  "A4_PickUp"),
    ("[QR] scanning",             "A5_ScanQR"),
    ("[QR] checking",             "A5_ScanQR"),
    ("[Flip]",                    "A6_Flip"),
    ("[QR] decoded",              "A7_Decode"),
    ("[QR] found",                "A7_Decode"),
    ("[QR] asking",               "R7_shelf"),
    ("[KB] LocationOf",           "R7_shelf"),
    ("[QR] no QR",                "A8_LostFound"),
    ("[Action] GoToLostAndFound", "A8_LostFound"),
    ("[Drop]",                    "A9_Drop"),
    ("[Verify]",                  "A10_Verify"),
    ("[PutOnShelf]",              "A11_Put"),
    ("[Abort]",                   "A12_Abort"),
    ("[Customer]",                "A14_Request"),
    ("[Reply] reserved",          "A15_DenyRes"),
    ("[Reply] borrowed",          "A16_DenyBor"),
    ("[Reply] lend",              "A17_Lend"),
    ("[Mark]",                    "A18_MarkBor"),
]


def _entry(name):
    # يرجع (اسم القاعدة، نصها)
    return name, RULE_TEXT[name]


def rule_for_log(line, place_kind=""):
    """
    يرجع (اسم القاعدة, نصها) للسطر، أو None إذا السطر لا يقابل قاعدة.
    place_kind: نوع المكان الحالي ('Table' / 'Chair' / غيره = أرضية)
    """
    line = line.strip()
    if not line.startswith("["):
        return None

    # استدلال أمامي: القاعدة تعتمد على مكان الكتاب
    if line.startswith("[Forward]"):
        name = {"Table": "R1_table", "Chair": "R2_chair"}.get(place_kind, "R3_floor")
        return _entry(name)

    # استدلال خلفي: إما اسم القاعدة مكتوب في السطر، أو "not provable"
    if line.startswith("[Backward]"):
        m = re.search(r"\[(R\d+_\w+)\]", line)
        if m and m.group(1) in RULE_TEXT:
            return _entry(m.group(1))
        if "not provable" in line:
            return _entry("A13_Leave")   # غير قابل للإثبات → اتركه
        return None

    # ملاحظات KB: محجوز أو مستعار
    if line.startswith("[KB] note"):
        return _entry("R5_reserved" if "Reserved" in line else "R4_borrowed")

    # باقي الحالات: نبحث في LOG_MAP عن البادئة المناسبة
    for prefix, name in LOG_MAP:
        if line.startswith(prefix):
            return _entry(name)
    return None


if __name__ == "__main__":
    # طباعة كل القواعد ونصوصها
    for name, text in RULE_TEXT.items():
        print(f"{name:14} {text}")