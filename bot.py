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

STATE_FILE = Path(__file__).with_name("state.json")
FOOTER = (
    "📩 <b>Kanalga eʼlon joylash 👉</b> @Khazratov\n\n"
    "📌 @Finderuz — eng yuqori maoshlar!"
)

# ============================ YORDAMCHI ============================

def log(*a):
    print(datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text("utf-8"))
    return {"posted": {}, "queue": [], "queue_date": None, "last_sent": None}


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
EXCLUDE_TITLE = re.compile(
    r"sotuv|savdo|sotuvchi|sales|b2b|продаж|продав|кассир|konsultant|консультант"
    r"|dasturchi|dasturlash|developer|frontend|front-end|backend|back-end|full\s*stack|fullstack"
    r"|python|java|php|flutter|react|node|devops|qa engineer|tester|программист|разработчик"
    r"|it mentor|frontend mentor|backend mentor",
    re.I,
)


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
            sal, raw = find_salary(p["text"])
            if not sal:
                continue
            lo, hi = sal
            score = (lo + hi) / 2
            if score < MIN_SALARY:
                continue
            fp = fingerprint(p["text"])
            if fp in state["posted"] or any(c["fp"] == fp for c in cands):
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


def build_day_queue(state, day):
    cands = collect(state)
    good = [c for c in cands if c["score"] >= GOOD_SALARY]
    n = max(MIN_POSTS, len(good))
    chosen = cands[:n]
    times = plan_times(len(chosen), day, earliest=datetime.now(TZ))
    chosen = chosen[:len(times)]
    queue = []
    for c, t in zip(chosen, times):
        queue.append({"at": t.isoformat(), "src": c["src"], "fp": c["fp"],
                      "html": to_template(c["text"], c["lo"], c["hi"], c["raw"])})
    log(f"{len(cands)} ta nomzod, {len(queue)} ta post rejalashtirildi:",
        ", ".join(datetime.fromisoformat(q["at"]).strftime("%H:%M") for q in queue))
    return queue


# ============================ YUBORISH ============================

def send(html_text):
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": TARGET_CHANNEL, "text": html_text, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(r.text)


def tick(state, now=None):
    """Bir martalik tekshiruv: kerak bo'lsa kunlik navbatni tuzadi va vaqti kelgan
    bitta postni yuboradi (oldingi postdan kamida MIN_GAP_MIN daqiqa o'tgan bo'lsa)."""
    now = now or datetime.now(TZ)
    today = now.date().isoformat()
    collect_time = now.replace(hour=COLLECT_AT[0], minute=COLLECT_AT[1], second=0, microsecond=0)
    last_time = now.replace(hour=LAST_POST[0], minute=LAST_POST[1], second=0, microsecond=0)

    # kecha yuborilmay qolganlar bugun chiqmaydi
    if state.get("queue_date") and state["queue_date"] != today and state.get("queue"):
        log(f"Kechagi {len(state['queue'])} ta yuborilmagan post bekor qilindi")
        state["queue"] = []

    if (state.get("queue_date") != today and collect_time <= now < last_time
            and (not START_DATE or today >= START_DATE)):
        state["queue"] = build_day_queue(state, now)
        state["queue_date"] = today

    due = [q for q in state.get("queue", []) if datetime.fromisoformat(q["at"]) <= now]
    last_sent = state.get("last_sent")
    gap_ok = (not last_sent or
              now - datetime.fromisoformat(last_sent) >= timedelta(minutes=MIN_GAP_MIN))
    if due and gap_ok:
        item = due[0]
        try:
            send(item["html"])
            log("Yuborildi:", item["src"])
            state["posted"][item["fp"]] = today
            state["last_sent"] = now.isoformat()
            state["queue"].remove(item)
        except Exception as ex:
            log("Yuborishda xato (keyingi safar qayta uriniladi):", ex)
    elif due:
        log("Post vaqti keldi, lekin oldingi postdan hali 1 soat o'tmadi - kutilmoqda")

    cutoff = (now - timedelta(days=14)).date().isoformat()
    state["posted"] = {k: v for k, v in state["posted"].items() if v >= cutoff}
    save_state(state)


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
        tick(load_state())
    else:
        run_forever()
