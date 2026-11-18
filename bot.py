"""
Finder vakansiya boti
---------------------
Har kuni ertalab bir nechta ochiq Telegram kanallarni o'qiydi, oyligi aniq
yozilgan va eng yuqori bo'lgan vakansiyalarni tanlaydi, Finder shabloniga
soladi va 9:00 dan 19:00 gacha teng oraliqlarda (kamida 1 soat) @Finderuz
kanaliga joylaydi.

Ishga tushirish:  python bot.py            (VPS'da 24/7 ishlaydi)
GitHub Actions:   python bot.py --tick     (bir marta tekshiradi, jadval bo'yicha chaqiriladi)
Sinov:            python bot.py --dry-run  (hech narsa yubormaydi, faqat ko'rsatadi)
"""

import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ============================ SOZLAMALAR ============================

SOURCE_CHANNELS = ["rizqimuz", "kasbim_uz", "Click_Jobs", "marketingdaishla", "data_ish"]
TARGET_CHANNEL = "@Finderuz"

TZ = timezone(timedelta(hours=5))   # Toshkent vaqti
COLLECT_AT = (7, 30)                # e'lonlarni yig'ish vaqti (soat, daqiqa)
FIRST_POST = (9, 0)                 # birinchi post
LAST_POST = (19, 0)                 # oxirgi post
MIN_POSTS = 5                       # kuniga kamida
MIN_GAP_MIN = 60                    # postlar orasidagi eng kam oraliq (daqiqa)
GOOD_SALARY = 8_000_000             # shundan yuqori o'rtacha maoshli e'lonlar "yaxshi" hisoblanadi
MIN_SALARY = 2_000_000              # bundan past maoshli e'lonlar umuman olinmaydi
USD_RATE = float(os.getenv("USD_RATE", "12000"))  # $ -> so'm (taxminiy)
LOOKBACK_HOURS = 30                 # qancha vaqt oldingi e'lonlarni ko'rish
START_DATE = os.getenv("START_DATE") or "2026-11-19"  # masalan 2026-11-19: shu kundan oldin post qilmaydi

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")   # ixtiyoriy: AI bilan chiroyliroq formatlash
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5")

BASE = Path(__file__).parent
STATE_FILE = BASE / "state.json"
CONFIG_FILE = BASE / "config.json"       # Telegram admin panel yozadi, bot o'qiydi
MANUAL_FILE = BASE / "manual.json"       # qo'lda qo'shilgan e'lonlar (panel yozadi)
OVERRIDES_FILE = BASE / "overrides.json" # o'tkazib yuborish / tahrir / hozir yuborish (panel yozadi)
MAX_POSTS = 11
EVENING_PLAN_AT = None   # masalan "21:00": har kuni kechqurun ertangi reja oldindan tuziladi
PAUSED = False
ADMINS = []
BLACKLIST = []
FOOTER = (
    "📩 <b>Kanalga eʼlon joylash 👉</b> @Khazratov\n\n"
    "📌 @Finderuz — eng yuqori maoshlar!"
)

# ============================ YORDAMCHI ============================

def log(*a):
    print(datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def load_json(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text("utf-8"))
    except Exception as ex:
        log(f"{path.name} o'qilmadi:", ex)
    return default


def load_state():
    st = load_json(STATE_FILE, {})
    for k, v in (("posted", {}), ("queue", []), ("queue_date", None), ("last_sent", None),
                 ("history", []), ("manual_done", {}), ("now_done", []), ("rebuild_done", None),
                 ("next_queue", []), ("next_date", None), ("tomorrow_done", None)):
        st.setdefault(k, v)
    return st


def _hm(s):
    h, m = str(s).split(":")
    return int(h), int(m)


def apply_config():
    """config.json dagi sozlamalarni (admin paneldan o'zgartirilgan) qo'llaydi."""
    global SOURCE_CHANNELS, FIRST_POST, LAST_POST, MIN_POSTS, MAX_POSTS, MIN_GAP_MIN
    global GOOD_SALARY, MIN_SALARY, USD_RATE, FOOTER, EXCLUDE_TITLE, PAUSED, ADMINS, BLACKLIST
    global EVENING_PLAN_AT
    c = load_json(CONFIG_FILE, {})
    try:
        SOURCE_CHANNELS = [x.lstrip("@") for x in c.get("channels", SOURCE_CHANNELS)]
        if c.get("first_post"): FIRST_POST = _hm(c["first_post"])
        if c.get("last_post"): LAST_POST = _hm(c["last_post"])
        MIN_POSTS = int(c.get("min_posts", MIN_POSTS))
        MAX_POSTS = int(c.get("max_posts", MAX_POSTS))
        MIN_GAP_MIN = int(c.get("min_gap", MIN_GAP_MIN))
        GOOD_SALARY = float(c.get("good_salary", GOOD_SALARY))
        MIN_SALARY = float(c.get("min_salary", MIN_SALARY))
        USD_RATE = float(c.get("usd_rate", USD_RATE))
        FOOTER = c.get("footer", FOOTER)
        if c.get("exclude_words") is not None:
            EXCLUDE_TITLE = words_regex(c["exclude_words"])
        PAUSED = bool(c.get("paused", False))
        EVENING_PLAN_AT = _hm(c["evening_plan_at"]) if c.get("evening_plan_at") else None
        ADMINS = [int(x) for x in c.get("admins", [])]
        BLACKLIST = [w.lower() for w in c.get("blacklist", []) if w.strip()]
    except Exception as ex:
        log("config.json da xato, standart sozlamalar ishlatiladi:", ex)
    return c


def save_state(st):
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=1), "utf-8")


# ============================ KANALLARNI O'QISH ============================

def fetch_channel(name):
    """t.me/s/<kanal> ochiq sahifasidan oxirgi ~20 postni oladi."""
    r = requests.get(f"https://t.me/s/{name}", timeout=30,
                     headers={"User-Agent": "Mozilla/5.0 FinderBot"})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    posts = []
    for m in soup.select(".tgme_widget_message"):
        el = m.select_one(".tgme_widget_message_text")
        tm = m.select_one("time")
        if not el or not tm:
            continue
        for br in el.find_all("br"):
            br.replace_with("\n")
        posts.append({
            "id": m.get("data-post"),
            "date": datetime.fromisoformat(tm["datetime"]),
            "text": el.get_text(),
        })
    return posts


# ============================ MAOSHNI TAHLIL QILISH ============================

SALARY_LINE = re.compile(r"(maosh|oylik|ish haqi|зарплат|salary|💵|💰)", re.I)
NUM = re.compile(r"\d[\d\s.,]*\d|\d")


def _to_number(raw):
    s = raw.replace(" ", "").replace(" ", "")
    # 4.000.000 / 20,000,000 -> ming ajratgich; 1.5 / 2,5 -> kasr
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", s):
        s = re.sub(r"[.,]", "", s)
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def is_usd(text):
    t = text.lower()
    return "$" in t or "usd" in t or "dollar" in t


def parse_salary(line):
    """Maosh satridan (past, yuqori) so'mda qaytaradi yoki None."""
    low = line.lower()
    if not NUM.search(low):
        return None
    usd = is_usd(low)
    nums = []
    # "3-18 mln", "5 000 000 - 10 000 000", "600$-1 800$", "10 mln +"
    for m in re.finditer(r"(\d[\d\s.,]*\d|\d)\s*(mln|million|млн|ming|k\b)?", low):
        v = _to_number(m.group(1))
        if v is None:
            continue
        unit = m.group(2)
        if unit in ("mln", "million", "млн"):
            v *= 1_000_000
        elif unit in ("ming", "k"):
            v *= 1_000
        nums.append(v)
    if not nums:
        return None
    # "3-18 mln" holati: birinchi son birliksiz, ikkinchisi bilan -> bir xil birlik
    if len(nums) >= 2 and nums[0] < 1000 and nums[1] >= 1_000_000:
        nums[0] *= 1_000_000
    # "5 000 - 10 000 000" holati: 5 000 aslida 5 mln
    if len(nums) >= 2 and 1000 <= nums[0] < 100_000 and nums[1] >= 1_000_000 and not usd:
        nums[0] *= 1_000
    if usd:
        nums = [n * USD_RATE if n < 100_000 else n for n in nums]
    nums = [n for n in nums if n >= 100_000]  # "6/1", "09:00" kabi sonlarni tashlash
    if not nums:
        return None
    lo, hi = min(nums[:2]), max(nums[:2])
    if len(nums) == 1 and re.search(r"gacha|до\b", low) and not re.search(r"dan|от\b", low):
        lo = hi * 0.5  # "20 mln gacha" -> pastki chegara noma'lum
    return lo, hi


def find_salary(text):
    for line in text.split("\n"):
        if SALARY_LINE.search(line):
            res = parse_salary(line)
            if res:
                return res, line.strip()
    return None, None


# ============================ SHABLONGA SOLISH ============================

FIELD_PATTERNS = {
    "company": r"(kompaniya|korxona|firma|idora|компания)",
    "place":   r"(manzil|hudud|ish turi|ish formati|адрес)",
    "schedule": r"(ish vaqti|ish grafigi|время работы|grafik\s*:)",
    "phone":   r"(telefon|📞|aloqa|тел)",
    "tg":      r"(murojaat|telegram|aloqa uchun)",
    "req":     r"(talablar|требования)",
    "offer":   r"(takliflar|biz taklif|qulayliklar|условия)",
}

def _clean(s):
    s = re.sub(r"^[\s—\-•▪️✅🔹🔸📌]+", "", s).strip()
    s = re.sub(r"#\w+", "", s).strip()
    return s


def _after_colon(line):
    """'Kalit: qiymat' dan qiymatni oladi (09:00 kabi vaqtdagi ikki nuqtaga qaramaydi)."""
    parts = re.split(r":(?!\d)", line, maxsplit=1)
    if len(parts) == 2:
        return _clean(parts[1])
    # ikki nuqta yo'q: "Ish vaqti ertalab 10:00 dan 18:00 gacha" -> kalit so'zdan keyingi qism
    m = re.match(r"^[^\w]*(ish vaqti|ish grafigi|manzil|hudud)\s*(.*)", line.strip(), re.I)
    return _clean(m.group(2)) if m else ""


def rule_based_fields(text):
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    f = {"title": _clean(lines[0]) if lines else "Vakansiya", "req": [], "offer": []}
    section = None
    for line in lines[1:]:
        low = line.lower()
        if re.search(r"@\w+\s*-|kanalda e.lon|e.lon joylash|ulashishni unutmang|rivojlanish uchun", low):
            continue  # manba kanal reklamasi
        matched = False
        bare = re.sub(r"^[^\w@+]+", "", low)  # boshidagi emoji va tirelarni olib tashlash
        if low.lstrip().startswith("📍") and not f.get("place"):
            f["place"] = _clean(re.sub(r"^📍", "", line.strip()))
            section = None
            continue
        if low.lstrip().startswith("⏱") and not f.get("schedule"):
            f["schedule"] = _after_colon(line) or _clean(line.strip("⏱️ "))
            section = None
            continue
        for key, pat in FIELD_PATTERNS.items():
            if re.match(pat, bare):
                val = _after_colon(line)
                if key in ("req", "offer"):
                    section = key
                    if val:
                        f[key] += [v.strip() for v in re.split(r";|\s—\s", val) if len(v.strip()) > 2]
                elif key == "phone":
                    ph = re.search(r"\+?\d[\d\s\-()]{7,}", line)
                    if ph:
                        f["phone"] = re.sub(r"[^\d+]", "", ph.group())
                    if "@" in line and not f.get("tg"):
                        f["tg"] = re.search(r"@\w+", line).group()
                    section = None
                elif key == "tg":
                    u = re.search(r"@\w+|t\.me/\S+", line)
                    if u:
                        f["tg"] = u.group()
                    ph = re.search(r"\+?998[\d\s\-]{9,}", line)
                    if ph and not f.get("phone"):
                        f["phone"] = re.sub(r"[^\d+]", "", ph.group())
                    section = None
                else:
                    if val and (not f.get(key) or (key == "place" and bare.startswith(("manzil", "hudud")))):
                        f[key] = val
                    section = None
                matched = True
                break
        if matched or SALARY_LINE.search(low):
            if not matched:
                section = None
            continue
        if section and (line.startswith(("—", "-", "•", "▪", "✅", "🔹", "*")) or len(line) < 120):
            item = _clean(line)
            if item:
                f[section].append(item)
    for k in ("req", "offer"):
        f[k] = f[k][:5]
    return f


def fmt_money(n):
    return f"{int(round(n / 1000) * 1000):,}".replace(",", " ")


def salary_text(lo, hi, raw):
    """Maoshni shablon uchun yozadi. Saralash uchun ishlatilgan taxminiy
    pastki chegara bu yerda ko'rsatilmaydi - faqat e'londa yozilgani."""
    body = _clean(raw.split(":", 1)[-1]) if ":" in raw else _clean(raw)
    if is_usd(raw):
        return body  # dollarni asl ko'rinishda qoldiramiz
    low = raw.lower()
    extra = ""
    m = re.search(r"\(([^)]+)\)", raw)
    if m and not re.search(r"\d{4}", m.group(1)):
        extra = f" ({m.group(1)})"
    elif re.search(r"kpi|bonus|foiz", low):
        extra = " (fiks + KPI)"
    nums = [n for n in NUM.findall(low)]
    single = abs(hi - lo) < 1 or (len(nums) == 1 and re.search(r"gacha|до\b", low))
    if single:
        if re.search(r"gacha|до\b", low) and not re.search(r"dan\b|от\b", low):
            return f"{fmt_money(hi)} gacha{extra}"
        if re.search(r"\+|dan|от\b|boshla", low):
            return f"{fmt_money(lo)} dan yuqori{extra}"
        return f"{fmt_money(lo)}{extra}"
    return f"{fmt_money(lo)} dan {fmt_money(hi)} gacha{extra}"


def build_post(f, sal_text):
    e = html.escape
    title = f["title"].rstrip("!").strip()
    if not re.search(r"kerak|требуется|нужен", title, re.I):
        title += " kerak"
    out = [f"<b>❗️ {e(title)}</b>", ""]
    if f.get("company"):
        out.append(f"— <b>Korxona</b>: {e(f['company'])}")
    if f.get("place"):
        out.append(f"— <b>Manzil</b>: {e(f['place'])}")
    if f.get("schedule"):
        out.append(f"— <b>Ish grafigi</b>: {e(f['schedule'])}")
    out.append(f"— <b>Oylik maosh</b>: {e(sal_text)}")
    out.append(f"— <b>Telefon</b>: {e(f.get('phone') or '(telegramdan yozing)')}")
    if f.get("tg"):
        out.append(f"— <b>Telegram</b>: {e(f['tg'])}")
    for key, name in (("req", "Talablar"), ("offer", "Takliflar")):
        if f.get(key):
            out += ["", f"<b>🔻</b> <b>{name}</b>:"] + [f"— {e(x)}" for x in f[key]]
    out += ["", FOOTER]
    return "\n".join(out)


AI_PROMPT = """Quyidagi vakansiya e'lonidan maydonlarni ajratib, faqat JSON qaytar.
Kalitlar: title (lavozim, qisqa, "kerak" bilan tugasin), company, place, schedule,
phone (faqat raqam, +998... ko'rinishda), tg (@username yoki t.me havola), req (ro'yxat, 2-5 ta qisqa band),
offer (ro'yxat, 0-4 ta qisqa band). Ma'lumot bo'lmasa bo'sh qoldir. Matnni o'zbek lotin
alifbosida, imlo xatolarisiz yoz, lekin ma'noni o'zgartirma va yangi narsa qo'shma.
Manba kanal reklamalarini va heshteglarni olib tashla.

E'lon:
"""

def ai_fields(text):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": ANTHROPIC_MODEL, "max_tokens": 800,
              "messages": [{"role": "user", "content": AI_PROMPT + text}]},
        timeout=60,
    )
    r.raise_for_status()
    raw = r.json()["content"][0]["text"]
    data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    for k in ("req", "offer"):
        data[k] = [x for x in (data.get(k) or []) if x][:5]
    return data


def to_template(text, lo, hi, raw_salary):
    fields = None
    if ANTHROPIC_API_KEY:
        try:
            fields = ai_fields(text)
        except Exception as ex:
            log("AI formatlashda xato, oddiy usulga o'tildi:", ex)
    if not fields or not fields.get("title"):
        fields = rule_based_fields(text)
    return build_post(fields, salary_text(lo, hi, raw_salary))


# ============================ TANLASH VA JADVAL ============================

SKIP = re.compile(r"^(ish joyi kerak|ustoz kerak|shogird kerak|sherik kerak)", re.I)

# Qabul qilinmaydigan sohalar (sarlavha bo'yicha tekshiriladi): sotuv va dasturlash
DEFAULT_EXCLUDE = [
    "sotuv", "savdo", "sotuvchi", "sales", "b2b", "продаж", "продав", "кассир", "konsultant", "консультант",
    "dasturchi", "dasturlash", "developer", "frontend", "front-end", "backend", "back-end", "full stack",
    "fullstack", "python", "java", "php", "flutter", "react", "node", "devops", "qa engineer", "tester",
    "программист", "разработчик", "it mentor",
]


def words_regex(words):
    parts = [re.escape(w.strip().lower()).replace(r"\ ", r"\s*") for w in words if w.strip()]
    return re.compile("|".join(parts) if parts else r"(?!x)x", re.I)


EXCLUDE_TITLE = words_regex(DEFAULT_EXCLUDE)


def is_excluded(text):
    lines = [l for l in text.split("\n") if l.strip()]
    head = " ".join(lines[:2]) if lines else ""
    return bool(EXCLUDE_TITLE.search(head))


def fingerprint(text):
    norm = re.sub(r"[^a-zа-я0-9]", "", text.lower())[:300]
    return hashlib.md5(norm.encode()).hexdigest()


def collect(state):
    since = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    cands = []
    for ch in SOURCE_CHANNELS:
        try:
            posts = fetch_channel(ch)
        except Exception as ex:
            log(f"{ch} o'qilmadi:", ex)
            continue
        for p in posts:
            if p["date"] < since or SKIP.match(p["text"].strip()) or is_excluded(p["text"]):
                continue
            low = p["text"].lower()
            if any(w in low for w in BLACKLIST):
                continue
            sal, raw = find_salary(p["text"])
            if not sal:
                continue
            lo, hi = sal
            score = (lo + hi) / 2
            if score < MIN_SALARY:
                continue
            fp = fingerprint(p["text"])
            queued = {q["fp"] for q in state.get("queue", []) + state.get("next_queue", [])}
            if fp in state["posted"] or fp in queued or any(c["fp"] == fp for c in cands):
                continue
            cands.append({"src": p["id"], "fp": fp, "score": score,
                          "lo": lo, "hi": hi, "raw": raw, "text": p["text"]})
        log(f"{ch}: {len(posts)} post o'qildi")
    cands.sort(key=lambda c: c["score"], reverse=True)
    return cands


def plan_times(n, day, earliest=None):
    start = datetime(day.year, day.month, day.day, *FIRST_POST, tzinfo=TZ)
    if earliest and earliest > start:  # bot kun o'rtasida ishga tushsa
        start = earliest.replace(second=0, microsecond=0) + timedelta(minutes=5 - earliest.minute % 5)
    end = datetime(day.year, day.month, day.day, *LAST_POST, tzinfo=TZ)
    total = (end - start).total_seconds() / 60
    max_posts = int(total // MIN_GAP_MIN) + 1
    n = min(n, max_posts)
    if n == 1:
        return [start]
    gap = total / (n - 1)
    times = []
    for i in range(n):
        t = start + timedelta(minutes=round(i * gap / 5) * 5)
        times.append(min(t, end))
    return times


def build_day_queue(state, day, earliest="now"):
    cands = collect(state)
    good = [c for c in cands if c["score"] >= GOOD_SALARY]
    n = min(max(MIN_POSTS, len(good)), MAX_POSTS)
    chosen = cands[:n]
    times = plan_times(len(chosen), day, earliest=datetime.now(TZ) if earliest == "now" else earliest)
    chosen = chosen[:len(times)]
    queue = []
    for c, t in zip(chosen, times):
        h = to_template(c["text"], c["lo"], c["hi"], c["raw"])
        queue.append({"at": t.isoformat(), "src": c["src"], "fp": c["fp"], "html": h,
                      "title": html_title(h), "salary": salary_text(c["lo"], c["hi"], c["raw"])})
    log(f"{len(cands)} ta nomzod, {len(queue)} ta post rejalashtirildi:",
        ", ".join(datetime.fromisoformat(q["at"]).strftime("%H:%M") for q in queue))
    return queue


# ============================ YUBORISH ============================

def html_title(h):
    first = re.sub(r"<[^>]+>", "", h.split("\n")[0])
    return html.unescape(first).replace("❗️", "").strip()[:80]


def tg(method, payload):
    r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(r.text)
    return r.json()


def send(item):
    """Kanalga yuboradi. item: {"html": ..., "photo": file_id (ixtiyoriy)}"""
    if isinstance(item, str):
        item = {"html": item}
    if item.get("photo"):
        tg("sendPhoto", {"chat_id": TARGET_CHANNEL, "photo": item["photo"],
                         "caption": item["html"], "parse_mode": "HTML"})
    else:
        tg("sendMessage", {"chat_id": TARGET_CHANNEL, "text": item["html"], "parse_mode": "HTML",
                           "disable_web_page_preview": True})


def notify(text):
    """Adminlarga shaxsiy xabar (kunlik reja, xatolar)."""
    for a in ADMINS:
        try:
            tg("sendMessage", {"chat_id": a, "text": text, "parse_mode": "HTML",
                               "disable_web_page_preview": True})
        except Exception as ex:
            log("Adminga xabar yuborilmadi:", ex)


def add_history(state, now, item, status, kind):
    state["history"].append({"at": now.isoformat(), "title": item.get("title") or html_title(item["html"]),
                             "src": item.get("src", "qo'lda"), "status": status, "kind": kind})


def _deliver(state, now, item, kind, edits):
    body = dict(item)
    if kind == "auto" and edits.get(item["fp"][:16]):
        body["html"] = edits[item["fp"][:16]]
    send(body)
    log("Yuborildi:", item.get("src") or item.get("id"))
    state["last_sent"] = now.isoformat()
    add_history(state, now, body, "sent", kind)
    if kind == "auto":
        state["posted"][item["fp"]] = now.date().isoformat()
        for key in ("queue", "next_queue"):
            if item in state[key]:
                state[key].remove(item)
    else:
        state["manual_done"][item["id"]] = "sent"


def plan_text(header, queue):
    lines = [f"{datetime.fromisoformat(q['at']).strftime('%H:%M')} — {html.escape(q['title'])} "
             f"({html.escape(q['salary'])})" for q in queue]
    return f"<b>{header} — {len(lines)} ta post</b>\n\n" + ("\n".join(lines) or "Mos e'lon topilmadi.")


def tick(state, now=None):
    """Bir martalik tekshiruv: kerak bo'lsa kunlik rejani tuzadi va vaqti kelgan postni
    yuboradi (oldingi postdan kamida MIN_GAP_MIN daqiqa o'tgan bo'lsa).
    Admin panel buyruqlari (manual.json, overrides.json, config.json) shu yerda bajariladi."""
    apply_config()
    now = now or datetime.now(TZ)
    today = now.date().isoformat()
    manual = load_json(MANUAL_FILE, [])
    ov = load_json(OVERRIDES_FILE, {})
    edits = ov.get("edit", {})
    collect_time = now.replace(hour=COLLECT_AT[0], minute=COLLECT_AT[1], second=0, microsecond=0)
    last_time = now.replace(hour=LAST_POST[0], minute=LAST_POST[1], second=0, microsecond=0)

    # kecha yuborilmay qolganlar bugun chiqmaydi
    if state.get("queue_date") and state["queue_date"] < today and state.get("queue"):
        log(f"Kechagi {len(state['queue'])} ta yuborilmagan post bekor qilindi")
        state["queue"] = []

    if PAUSED:
        log("Bot pauzada (admin panel). Hech narsa yuborilmaydi.")
        save_state(state)
        return

    started = not START_DATE or today >= START_DATE
    tomorrow_day = now + timedelta(days=1)
    tomorrow = tomorrow_day.date().isoformat()

    # oldindan tuzilgan ertangi reja bugun boshlanadi
    if state.get("next_date") == today:
        state["queue"] = state["next_queue"]
        state["queue_date"] = today
        state["next_queue"], state["next_date"] = [], None
        log(f"Oldindan tuzilgan reja ishga tushdi: {len(state['queue'])} ta post")
    elif state.get("next_date") and state["next_date"] < today:
        state["next_queue"], state["next_date"] = [], None

    # kunlik reja yoki admin so'ragan qayta tuzish
    need = ((not state.get("queue_date") or state["queue_date"] < today)
            and collect_time <= now < last_time and started)
    token = ov.get("rebuild")
    if token and token != state.get("rebuild_done") and now < last_time and started:
        need = True
        state["rebuild_done"] = token
    if need:
        state["queue"] = []
        state["queue"] = build_day_queue(state, now)
        state["queue_date"] = today
        notify(plan_text("📋 Bugungi reja", state["queue"]))

    # ertangi reja: admin so'raganda yoki kechqurun avtomatik
    ttoken = ov.get("plan_tomorrow")
    want_t = bool(ttoken and ttoken != state.get("tomorrow_done"))
    if want_t:
        state["tomorrow_done"] = ttoken
    if EVENING_PLAN_AT and state.get("next_date") != tomorrow:
        if now >= now.replace(hour=EVENING_PLAN_AT[0], minute=EVENING_PLAN_AT[1], second=0, microsecond=0):
            want_t = True
    if want_t and (not START_DATE or tomorrow >= START_DATE):
        state["next_queue"] = []
        state["next_queue"] = build_day_queue(state, tomorrow_day, earliest=None)
        state["next_date"] = tomorrow
        notify(plan_text(f"📅 Ertangi reja ({tomorrow_day.strftime('%d.%m')})", state["next_queue"]) +
               "\n\nPanelda <b>📅 Ertangi reja</b> orqali ko'rib, tahrirlashingiz mumkin.")

    # admin o'tkazib yuborganlar
    skip = set(ov.get("skip", []))
    for key in ("queue", "next_queue"):
        for q in list(state[key]):
            if q["fp"][:16] in skip:
                state[key].remove(q)
                state["posted"][q["fp"]] = today   # keyingi rejalarga ham qayta tushmasin
                add_history(state, now, q, "skipped", "auto")
                log("Admin o'tkazib yubordi:", q["src"])

    # qo'lda e'lonlar: 24 soatdan ko'p kechikkanlari bekor
    pending_manual = []
    for m in manual:
        if m.get("status") == "draft" or m["id"] in state["manual_done"]:
            continue
        at = datetime.fromisoformat(m["at"])
        if now - at > timedelta(hours=24):
            state["manual_done"][m["id"]] = "expired"
            continue
        pending_manual.append(m)

    # "Hozir yuborish" — oraliq qoidasiga qaramaydi
    for nid in ov.get("now", []):
        if nid in state["now_done"]:
            continue
        state["now_done"].append(nid)
        item = next((m for m in pending_manual if m["id"] == nid), None)
        kind = "manual"
        if not item:
            item = next((q for q in state["queue"] + state["next_queue"] if q["fp"][:16] == nid), None)
            kind = "auto"
        if not item:
            continue
        try:
            _deliver(state, now, item, kind, edits)
            if kind == "manual":
                pending_manual.remove(item)
            notify(f"🚀 Yuborildi: {html.escape(item.get('title') or html_title(item['html']))}")
        except Exception as ex:
            log("Yuborishda xato:", ex)
            notify(f"⚠️ Yuborishda xato: {html.escape(str(ex))[:300]}")

    # navbatdagi post (qo'lda qo'shilganlar oldin)
    due = [("manual", m) for m in sorted(pending_manual, key=lambda m: m["at"])
           if datetime.fromisoformat(m["at"]) <= now]
    due += [("auto", q) for q in state["queue"] if datetime.fromisoformat(q["at"]) <= now]
    last_sent = state.get("last_sent")
    gap_ok = (not last_sent or
              now - datetime.fromisoformat(last_sent) >= timedelta(minutes=MIN_GAP_MIN))
    if due and gap_ok:
        kind, item = due[0]
        try:
            _deliver(state, now, item, kind, edits)
            if kind == "manual":
                notify(f"✅ Qo'lda qo'shilgan e'lon chiqdi: {html.escape(item.get('title') or html_title(item['html']))}")
        except Exception as ex:
            log("Yuborishda xato (keyingi safar qayta uriniladi):", ex)
            notify(f"⚠️ Yuborishda xato (keyingi safar qayta uriniladi):\n{html.escape(str(ex))[:300]}")
    elif due:
        log("Post vaqti keldi, lekin oldingi postdan hali yetarli vaqt o'tmadi - kutilmoqda")

    cutoff = (now - timedelta(days=14)).date().isoformat()
    state["posted"] = {k: v for k, v in state["posted"].items() if v >= cutoff}
    state["history"] = [h for h in state["history"] if h["at"][:10] >= cutoff][-300:]
    state["now_done"] = state["now_done"][-200:]
    save_state(state)


def check_setup():
    """Qo'lda ishga tushirilganda: token to'g'riligini va botning kanalda
    post yozish huquqi borligini tekshiradi. Kanalga hech narsa yubormaydi."""
    api = f"https://api.telegram.org/bot{BOT_TOKEN}"
    me = requests.get(f"{api}/getMe", timeout=30).json()
    if not me.get("ok"):
        sys.exit(f"XATO: BOT_TOKEN noto'g'ri ({me.get('description')}). Secrets'dagi tokenni tekshiring.")
    bot_id = me["result"]["id"]
    log(f"Token to'g'ri: @{me['result']['username']}")
    mem = requests.get(f"{api}/getChatMember",
                       params={"chat_id": TARGET_CHANNEL, "user_id": bot_id}, timeout=30).json()
    if not mem.get("ok"):
        sys.exit(f"XATO: {TARGET_CHANNEL} kanalini ko'ra olmadim ({mem.get('description')}). "
                 "Botni kanalga admin qilib qo'shing.")
    r = mem["result"]
    if r.get("status") != "administrator" or not r.get("can_post_messages"):
        sys.exit(f"XATO: bot {TARGET_CHANNEL} da admin emas yoki 'Post Messages' huquqi yo'q.")
    log(f"Bot {TARGET_CHANNEL} kanalida admin, post yozish huquqi bor. Hammasi tayyor ✅")


def require_token():
    if not BOT_TOKEN:
        sys.exit("BOT_TOKEN topilmadi. .env faylga yoki GitHub Secrets'ga qo'ying.")


def run_forever():
    """Doimiy server (VPS) uchun: har 30 soniyada tekshiradi."""
    require_token()
    log("Bot ishga tushdi.")
    state = load_state()
    while True:
        tick(state)
        time.sleep(30)


def dry_run():
    apply_config()
    state = load_state()
    queue = build_day_queue(state, datetime.now(TZ) + timedelta(days=0))
    for q in queue:
        print("\n" + "=" * 60)
        print(datetime.fromisoformat(q["at"]).strftime("%H:%M"), " manba:", q["src"])
        print("=" * 60)
        print(q["html"])


if __name__ == "__main__":
    env = Path(__file__).with_name(".env")
    if env.exists():
        for line in env.read_text("utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
        BOT_TOKEN = os.getenv("BOT_TOKEN", "")
        ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    if "--dry-run" in sys.argv:
        dry_run()
    elif "--tick" in sys.argv:      # GitHub Actions: har 15 daqiqada bir marta ishga tushiriladi
        require_token()
        if os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch":
            check_setup()           # qo'lda "Run workflow" bosilganda sozlamalarni tekshirish
        tick(load_state())
    else:
        run_forever()
