# Finder vakansiya boti

Bot har kuni ertalab @rizqimuz, @kasbim_uz, @Click_Jobs, @marketingdaishla va @data_ish
kanallarini o'qiydi. Oyligi aniq yozilgan e'lonlardan eng yuqorilarini tanlaydi, sotuv va
dasturlash vakansiyalarini tashlab ketadi. Tanlanganlarini Finder shabloniga solib,
**9:00 dan 19:00 gacha** @Finderuz kanaliga joylaydi.

- Kuniga kamida 5 ta post chiqadi, postlar orasida kamida 1 soat bo'ladi.
- Bir xil e'lon ikki marta chiqmaydi.
- Bot 2026-yil 19-noyabrdan boshlab ishlaydi (18-noyabr postlari qo'lda rejalashtirilgan).

Bot **GitHub Actions** orqali bepul ishlaydi: server ham, bank kartasi ham kerak emas.

---

## 1-qadam. Telegram bot ochish

1. Telegramda **@BotFather** ga yozing va `/newbot` buyrug'ini yuboring.
2. Botga nom bering (masalan, `Finder Poster`) va username bering (masalan, `finderuz_poster_bot`).
3. BotFather bergan **tokenni** nusxalab oling. U `123456789:AA...` ko'rinishida bo'ladi.
   **Tokenni hech kimga yubormang.**
4. @Finderuz kanaliga kiring → **Administrators → Add Admin** → botingizni toping va
   **Post Messages** huquqini yoqing.

## 2-qadam. GitHub akkaunt va repozitoriy

1. **github.com** saytida ro'yxatdan o'ting (bepul, faqat email kerak).
2. O'ng yuqoridagi **+** tugmasi → **New repository**.
   - Repository name: `finder-bot`
   - **Public** ni tanlang. Public bo'lmasa, jadval bo'yicha avtomatik ishga tushirish bepul ishlamaydi.
     Tokeningiz ko'rinmaydi: u yashirin Secrets bo'limida saqlanadi.
   - **Create repository** ni bosing.

## 3-qadam. Fayllarni yuklash

1. Repozitoriy sahifasida **uploading an existing file** havolasini bosing
   (yoki **Add file → Upload files**).
2. `finder-bot.zip` ni kompyuteringizda oching (Extract) va ichidagi **hamma narsani** yuklang:
   `bot.py`, `requirements.txt`, `README.md`, `.env.example` va `.github` papkasi.
3. Pastda **Commit changes** ni bosing.

> **Muhim:** `.github/workflows/bot.yml` fayli yuklanganini tekshiring.
> Agar `.github` papkasi yuklanmagan bo'lsa: **Add file → Create new file** ni bosing,
> nom maydoniga aynan `.github/workflows/bot.yml` deb yozing, `bot.yml` faylining
> ichidagi matnni nusxalab qo'ying va **Commit changes** ni bosing.

## 4-qadam. Tokenni yashirin saqlash

1. Repozitoriyda **Settings → Secrets and variables → Actions** bo'limiga kiring.
2. **New repository secret** ni bosing:
   - Name: `BOT_TOKEN`
   - Secret: BotFather bergan token
3. **Add secret** ni bosing.

(Ixtiyoriy) AI bilan chiroyliroq formatlash uchun shu yerga `ANTHROPIC_API_KEY` ni ham qo'shing.
Batafsil 7-bo'limda.

## 5-qadam. Ruxsat berish va sinash

1. **Settings → Actions → General** bo'limiga kiring. Eng pastdagi **Workflow permissions** da
   **Read and write permissions** ni tanlang va **Save** ni bosing. Bot o'z holatini
   (`state.json`) saqlashi uchun bu kerak.
2. Yuqoridagi **Actions** bo'limiga o'ting. **Finder vakansiya boti** → **Run workflow**
   tugmasini bosing.
3. Bir daqiqadan keyin yashil ✅ belgi chiqsa, hammasi ishlayapti. Qizil ❌ chiqsa,
   ustiga bosib xatoni ko'ring, yoki skrinshotini Claude'ga yuboring.

Shundan keyin bot har 15 daqiqada (07:00–20:00 oralig'ida) o'zi ishga tushadi:
- ertalab e'lonlarni yig'ib, kun jadvalini tuzadi;
- vaqti kelgan postni kanalga yuboradi.

---

## Bilishingiz kerak bo'lgan narsalar

- **Vaqt aniq emas.** GitHub jadvaldagi ishga tushirishni ba'zan 5–30 daqiqa kechiktiradi.
  Shuning uchun 9:00 dagi post 9:00–9:30 oralig'ida chiqadi. Postlar orasidagi 1 soatlik
  oraliq baribir saqlanadi.
- **60 kun qoidasi.** GitHub 60 kun faolligi bo'lmagan public repozitoriyda jadvalni o'chirib
  qo'yadi. Bot har kuni `state.json` ni yangilagani uchun repozitoriy doim faol turadi.
- **Bot to'xtab qolsa:** Actions bo'limida workflow yonida "disabled" yozuvi chiqsa,
  **Enable workflow** ni bosing.

## 6-qadam. Sozlamalar (`bot.py` boshida)

GitHub'da `bot.py` ni oching, qalam ✏️ belgisini bosing, o'zgartiring va **Commit changes** ni bosing.

| Sozlama | Ma'nosi |
|---|---|
| `SOURCE_CHANNELS` | O'qiladigan kanallar |
| `EXCLUDE_TITLE` | Qabul qilinmaydigan sohalar (hozir: sotuv, dasturlash) |
| `FIRST_POST` / `LAST_POST` | Postlar oralig'i (9:00–19:00) |
| `MIN_POSTS` | Kuniga kamida nechta post (5) |
| `MIN_GAP_MIN` | Postlar orasidagi eng kam oraliq, daqiqada (60) |
| `GOOD_SALARY` | "Yaxshi" maosh chegarasi (8 mln). Bunday e'lonlar ko'p bo'lsa, post soni oshadi |
| `MIN_SALARY` | Bundan past maoshli e'lonlar olinmaydi (2 mln) |

## 7. AI bilan formatlash (ixtiyoriy)

E'lonlar har xil yoziladi, shuning uchun oddiy qoidalar ba'zan kompaniya nomi yoki talablarni
tushirib qoldiradi. `ANTHROPIC_API_KEY` ni Secrets'ga qo'shsangiz (console.anthropic.com),
bot har bir e'lonni Claude Haiku orqali shablonga soladi va imlo xatolarini tuzatadi.
Xarajat juda kam bo'ladi, bu Claude obunangiz limitiga ta'sir qilmaydi.
API kalit olish uchun xalqaro karta kerak bo'lishi mumkin. Kalitsiz ham bot ishlayveradi.

---

## Muqobil: o'z serveringizda (VPS)

Agar keyinchalik server olsangiz: `pip install -r requirements.txt`, `.env.example` ni `.env`
ga ko'chirib tokenni yozing, keyin `python bot.py` ni ishga tushiring. Bot doimiy ishlab turadi.
Sinov uchun `python bot.py --dry-run` buyrug'ini ishlating: u hech narsa yubormaydi.
