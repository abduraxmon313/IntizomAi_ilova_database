# IntizomAI — mobil ilova backend'i

Flutter ilovasi (`IntizomAi_ilova`) uchun alohida server. O'z PostgreSQL bazasi va o'z
foydalanuvchilari bor (Google / Apple orqali kirish). Telegram bot backend'i
(`IntizomAi_Bot_Pro_Copy`) bilan ma'lumot almashmaydi — **faqat obuna sotib olishda**
botning `/partner/v1` API'siga murojaat qiladi, chunki Paylov shartnomasi va IP'lar
o'sha Railway hisobiga ulangan.

```
Ilova ──HTTPS──► Ilova backend (shu repo) ──HMAC──► Bot backend ──► Paylov
                        ▲                               │
                        └──── imzolangan callback ◄─────┘  (Paylov webhooki botga keladi)
```

## Imkoniyatlar

| Bo'lim | Nima bor |
|---|---|
| Kirish | iOS: Apple yoki Google. Android: Google. Bir xil tasdiqlangan pochta — bitta hisob. Sessiya tokeni serverda faqat xesh ko'rinishida, chiqishda bekor qilinadi. |
| Onboarding | Ism, yosh (tug'ilgan yil sifatida saqlanadi), username (noyob, `a-z0-9_`, 3–20 belgi), ixtiyoriy "kim taklif qildi". |
| Reja / odat / maqsad | Botdagi qoidalar: o'tgan kun o'zgarmaydi, vaqti kelmagan reja belgilanmaydi, odat 7 kungacha orqaga belgilanadi, o'chirilgan odat arxivlanadi. |
| Gamifikatsiya | XP, daraja, unvon, seriya, muzlatgich, intizom bali, 15 ta yutuq. Hammasi bajarilgan ishlardan qayta hisoblanadi — belgilash/bekor qilishda xato yig'ilmaydi. |
| Guruhlar | Faqat ilova ichida. Ega username orqali taklif yuboradi, foydalanuvchi qabul qiladi yoki rad etadi (bloklash bilan). Ruxsatlar, pauza, egalikni topshirish. |
| AI murabbiy | OpenAI (REST), foydalanuvchi tilida javob. AI ishlamasa bepul xabar sarflanmaydi. |
| Premium | Tariflar va narxlar botdan (admin botda o'zgartiradi). Chegirma va bepul promokodlar. To'lov holati callback + so'rov orqali. |
| Hisobni o'chirish | Hamma ma'lumot o'chadi, guruh egaligi topshiriladi, Apple ruxsati bekor qilinadi. |

Bepul tarif: kuniga 5 reja, 3 odat, 3 maqsad, kuniga 3 AI xabar. Guruh yaratish va reyting — Premium.
Guruh taklifini qabul qilish — bepul. Hammasi `.env.example`dagi o'zgaruvchilar bilan sozlanadi.

## API

Hamma yo'llar `/api/v1` ostida, `Authorization: Bearer <token>`. Xatolar: `{"detail": "matn"}`
(`Accept-Language: uz|ru|en` bo'yicha tarjima qilingan). Kodlar: 401 qayta kirish, 402 Premium
kerak, 403 ruxsat yo'q yoki onboarding kerak, 409 hozir mumkin emas, 422 noto'g'ri ma'lumot, 429 juda tez.

| Guruh | Endpointlar |
|---|---|
| Kirish | `POST /auth/google {id_token}`, `POST /auth/apple {identity_token, raw_nonce, authorization_code?, given_name?, family_name?}`, `POST /auth/logout` |
| Profil | `GET/PUT/DELETE /profile`, `POST /profile/onboarding`, `GET /users/username-available?username=`, `GET /users/lookup?username=`, `GET /avatars/{key}` |
| Reja | `GET /plans?date_from&date_to`, `POST /plans`, `PUT/DELETE /plans/{id}` |
| Odat | `GET/POST /habits`, `PUT/DELETE /habits/{id}`, `POST /habits/{id}/toggle {done?, date?}` |
| Maqsad | `GET/POST /goals`, `PUT/DELETE /goals/{id}` |
| Statistika | `GET /stats`, `GET /stats/activity?days=98`, `GET /announcements`, `POST /announcements/{id}/seen`, `GET /leaderboard?period=all|week|streak`, `GET/POST /checkin` |
| AI | `POST /ai/chat {messages}` |
| Guruhlar | `GET/POST /groups`, `GET/PATCH/DELETE /groups/{id}`, `POST /groups/{id}/leave`, `POST /groups/{id}/transfer`, `POST /groups/{id}/invites {username}`, `DELETE /groups/{id}/invites/{invite_id}`, `GET /groups/{id}/members/{uid}?date=`, `DELETE /groups/{id}/members/{uid}`, `PUT /groups/{id}/members/{uid}/active`, `GET /groups/{id}/permissions`, `PUT /groups/{id}/permissions/{uid}`, `POST /groups/{id}/members/{uid}/plans`, `POST /groups/{id}/members/{uid}/habits` |
| Takliflar | `GET /invites`, `POST /invites/{id}/accept`, `POST /invites/{id}/decline {block}`, `GET /blocks`, `DELETE /blocks/{uid}` |
| Obuna | `GET /subscription?promo_code=`, `POST /payments/checkout {plan, provider, promo_code?}`, `GET /payments/{order_id}`, `POST /promo/redeem {code}` |
| Ichki | `POST /internal/billing/callback` (faqat bot backend, HMAC imzo) |

Brauzer sahifalari: `/privacy`, `/pay/return`, `/i/{username}` (taklif havolasi), `/health`.

## Admin panel — `/admin`

Brauzerda: `https://<domen>/admin`. Hamma ma'lumot shu servisning bazasidan. Bot faqat
Paylov to'lovini o'tkazadi: tarif, narx va promokodlar shu yerda.

**Birinchi kirish.** Railway Variables'ga qo'shing va deploy qiling:

```
ADMIN_USERNAME=login
ADMIN_PASSWORD=kamida-10-belgili-parol
```

Adminlar jadvali bo'sh bo'lsa, shu login «Egasi» bo'lib yaratiladi. Keyingi adminlarni panelning
o'zida qo'shasiz. Parolni unutsangiz: `ADMIN_RESET_PASSWORD=true` qo'yib deploy qiling, kiring,
so'ng o'zgaruvchini o'chiring.

| Bo'lim | Imkoniyatlar |
|---|---|
| Bosh sahifa | Foydalanuvchilar, Premium, konversiya, daromad (bugun/30 kun/jami), yangi va faol foydalanuvchilar, 30 kunlik grafiklar (faollik, ro'yxatdan o'tish, daromad), Premium manbalari, unvonlar, top ball/seriya, tugayotgan obunalar |
| Foydalanuvchilar | Ism/@username/email/ID bo'yicha qidiruv, filtrlar va saralash, CSV eksport. Kartada: profil, natijalar, rejalar/odatlar/maqsadlar, obuna tarixi, to'lovlar, promokodlar, takliflar, guruhlar |
| Foydalanuvchi amallari | Premium berish (tarif yoki kun) va bekor qilish, bloklash/blokdan chiqarish, barcha qurilmalardan chiqarish, ilova ichida shaxsiy xabar, AI limitini yangilash, seriya muzlatgichi, ism/username tahrirlash, hisobni o'chirish |
| Premium | Faol obunachilar manba bo'yicha, tez tugaydiganlar |
| To'lovlar | Holat bo'yicha filtr, qidiruv, CSV, tarif bo'yicha sotuvlar. Bot'dan holatni tekshirish, qo'lda faollashtirish (sabab bilan), bekor qilish |
| Promokodlar | Bepul Premium, bonus kunlar, chegirma (1–99%), tariflarga cheklash, limit, boshlanish/tugash sanasi, bir martalik kodlar to'plami (1000 tagacha), kim ishlatgani va daromad, CSV, bot'dan import |
| Tariflar | Narx, nom, kun, chizilgan eski narx, belgi, tartib, sotuvda/nofaol; yangi tarif qo'shish; bot'dagi narxlarni olish |
| E'lonlar | Ilovada «Bugun» sahifasida: hammaga, bepul yoki Premium foydalanuvchilarga, vaqt oralig'i, tugma, ko'rishlar soni |
| Guruhlar | Ro'yxat, a'zolar, takliflar, guruhni o'chirish (sabab bilan) |
| Sozlamalar | Sotuvni yoqish/to'xtatish, bepul limitlar, reyting va guruh cheklovlari, guruh limitlari, taklif mukofotlari |
| Adminlar | Rollar: Egasi, Admin, Yordamchi. Vaqtinchalik parol bilan qo'shish, parolni tiklash, o'chirish |
| Hisobim | Parol, ikki bosqichli kirish (TOTP), faol sessiyalar |
| Audit jurnali | Panelda kim, qachon, nima qilgani |
| Tizim holati | Bot bilan aloqa, Railway Variables tekshiruvi, fon vazifalari, xato to'lovlar |

Xavfsizlik: parol scrypt bilan saqlanadi. 5 marta noto'g'ri urinishda hisob 15 daqiqaga qulflanadi
va IP bo'yicha limit bor. Sessiya cookie'si HttpOnly va SameSite=Strict, 2 soat harakatsizlikda tugaydi.
Har formada CSRF tokeni tekshiriladi, sahifalar qat'iy CSP bilan beriladi. Muhim amallar audit jurnaliga yoziladi.

**Muhim:** avval botni yangilang (IntizomAi_Bot_Pro_Copy) — u ilova yuborgan narxni qabul qiladi.
Bot eski bo'lsa, checkout xavfsiz rad etiladi va foydalanuvchidan boshqa summa olinmaydi.

### Intizom bali (0–100)

Oxirgi 30 kun bo'yicha, har `GET /stats` so'rovida joriy holatdan hisoblanadi:

- **Kerakli ishlar** — bajarilgan har bir reja/odat, kuni o'tib ketgan bajarilmagan reja/odat
  va "bajarilmadi" deb belgilangan reja. Bugungi bajarilmagan ish hali o'tkazib yuborilgan
  hisoblanmaydi. O'chirilgan (arxivlangan) odatning o'chirilgan kungacha bo'lgan kunlari qoladi.
- **Formula**: `bajarilish × 50 + seriya/30 × 25 + 7 kundagi ishlar/21 × 15 + so'nggi faollik × 10`.
- **Boshlang'ich 50**: kerakli ish yo'q bo'lsa ball 50. Dastlabki 5 ta kerakli ishgacha natija 50
  bilan aralashtiriladi, shuning uchun bitta ish ballni keskin sakratmaydi. Hamma ish o'chirilsa,
  ball yana 50 ga qaytadi.

Rekord seriya va "mukammal kunlar" ham haqiqiy kunlardan hisoblanadi: belgilab-bekor qilish
ularni sun'iy oshirmaydi.

## Railway'ga joylash

1. Railway'da yangi project → **Deploy from GitHub repo** → shu repo. `Dockerfile` avtomatik ishlatiladi.
2. Project ichida **+ New → Database → PostgreSQL**. Servis Variables'ga
   `DATABASE_URL = ${{Postgres.DATABASE_URL}}` qo'shing.
3. **Settings → Networking → Generate Domain**.
4. Variables'ga `.env.example`dagi qiymatlarni kiriting (kamida `SECRET_KEY`,
   `GOOGLE_CLIENT_IDS`, `OPENAI_API_KEY`, `BILLING_*`).
5. Har deployda `alembic upgrade head` avtomatik bajariladi. Tekshirish: `https://<domen>/health`.

### Bot backend tomonida (IntizomAi_Bot_Pro_Copy Railway Variables)

```
PARTNER_APP_KEY=<ilova backend'dagi BILLING_PARTNER_KEY bilan bir xil>
PARTNER_APP_SECRET=<ilova backend'dagi BILLING_PARTNER_SECRET bilan bir xil>
PARTNER_APP_CALLBACK_URL=https://<ilova-backend-domen>/api/v1/internal/billing/callback
```

Kalitlarni yaratish: `python -c "import secrets;print(secrets.token_urlsafe(32))"` (ikkita alohida qiymat).
Paylov webhook manzili o'zgarmaydi — u avvalgidek bot serveriga keladi.

### Google Sign-In

Google Cloud Console → APIs & Services → Credentials:

- **Web application** client — ID'si ilovada `GOOGLE_SERVER_CLIENT_ID` (Android'da ID token shu `aud` bilan keladi).
- **iOS** client — bundle `uz.intizomai.intizomAi`. ID'si ilovada `GOOGLE_IOS_CLIENT_ID`.
- **Android** client — paket `uz.intizomai.intizom_ai` va imzo kalitining SHA-1'i (upload kalit va Play App Signing kaliti — ikkalasini qo'shing).

Uchala ID'ni ham serverdagi `GOOGLE_CLIENT_IDS`ga vergul bilan yozing.

### Sign in with Apple

developer.apple.com → Identifiers → `uz.intizomai.intizomAi` → **Sign In with Apple** yoqing.
Hisob o'chirilganda Apple ruxsatini bekor qilish uchun Keys → yangi kalit (Sign in with Apple)
yarating va `APPLE_TEAM_ID`, `APPLE_KEY_ID`, `APPLE_PRIVATE_KEY` (.p8 mazmuni) ni kiriting.

## Lokal ishga tushirish

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/intizom_app APP_ENV=dev
alembic upgrade head
uvicorn app.main:app --reload
```

`APP_ENV=dev` bo'lsa `/docs` ochiq bo'ladi.

## Ma'lumotlar bazasi o'zgarsa

```bash
alembic revision --autogenerate -m "nima o'zgardi"
alembic upgrade head
```
