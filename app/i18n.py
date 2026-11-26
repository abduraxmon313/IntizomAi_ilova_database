"""Server xabarlari uch tilda (uz/ru/en). Til `Accept-Language` sarlavhasidan olinadi.

Ilova xato matnini (`detail`) to'g'ridan-to'g'ri foydalanuvchiga ko'rsatadi,
shuning uchun xabarlar shu yerda tarjima qilinadi.
"""
from __future__ import annotations

from contextvars import ContextVar

SUPPORTED = ("uz", "ru", "en")
current_lang: ContextVar[str] = ContextVar("current_lang", default="uz")
# So'rov yuborgan ilova platformasi (X-App-Platform: ios | android), noma'lum bo'lsa None.
current_platform: ContextVar[str | None] = ContextVar("current_platform", default=None)


def pick_platform(header: str | None) -> str | None:
    p = (header or "").strip().lower()
    return p if p in ("ios", "android") else None


def pick_lang(header: str | None) -> str:
    for part in (header or "").split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in SUPPORTED:
            return code
    return "uz"


_M: dict[str, tuple[str, str, str]] = {
    # ── Umumiy ──
    "unauthorized": (
        "Sessiya tugagan. Qaytadan kiring.",
        "Сессия истекла. Войдите снова.",
        "Your session has expired. Please sign in again.",
    ),
    "not_found": ("Ma'lumot topilmadi.", "Данные не найдены.", "Not found."),
    "too_many": ("Juda ko'p so'rov. Biroz kuting.", "Слишком много запросов. Подождите.", "Too many requests. Please wait."),
    "too_large": ("So'rov hajmi juda katta.", "Слишком большой запрос.", "Request is too large."),
    "server_error": ("Serverda xatolik. Qaytadan urinib ko'ring.", "Ошибка сервера. Попробуйте ещё раз.", "Server error. Please try again."),
    "invalid_input": (
        "Kiritilgan ma'lumot noto'g'ri yoki juda uzun.",
        "Неверные или слишком длинные данные.",
        "The data is invalid or too long.",
    ),
    "bad_date": ("Sana formati noto'g'ri.", "Неверный формат даты.", "Invalid date format."),
    "bad_time": ("Vaqt formati noto'g'ri (HH:MM).", "Неверный формат времени (ЧЧ:ММ).", "Invalid time format (HH:MM)."),
    "title_required": ("Sarlavha bo'sh bo'lmasin.", "Название не может быть пустым.", "Title can't be empty."),
    "onboarding_required": (
        "Avval profilingizni to'ldiring.",
        "Сначала заполните профиль.",
        "Please complete your profile first.",
    ),
    # ── Kirish ──
    "auth_invalid_token": (
        "Kirish tasdiqlanmadi. Qaytadan urinib ko'ring.",
        "Вход не подтверждён. Попробуйте снова.",
        "Sign-in couldn't be verified. Please try again.",
    ),
    "auth_not_configured": (
        "Bu kirish usuli hozircha sozlanmagan.",
        "Этот способ входа пока не настроен.",
        "This sign-in method isn't configured yet.",
    ),
    "auth_email_unverified": (
        "Google hisobingiz pochtasi tasdiqlanmagan.",
        "Почта вашего Google-аккаунта не подтверждена.",
        "Your Google account email isn't verified.",
    ),
    # ── Profil / onboarding ──
    "name_invalid": (
        "Ism 2–60 belgidan iborat bo'lsin.",
        "Имя должно быть от 2 до 60 символов.",
        "Name must be 2–60 characters.",
    ),
    "age_invalid": (
        "Yoshingizni to'g'ri kiriting ({min}–{max}).",
        "Укажите корректный возраст ({min}–{max}).",
        "Enter a valid age ({min}–{max}).",
    ),
    "username_invalid": (
        "Username 3–20 belgi: lotin harflari, raqam va _ . Harf bilan boshlansin.",
        "Username 3–20 символов: латиница, цифры и _ . Начинается с буквы.",
        "Username must be 3–20 characters: letters, digits and _ , starting with a letter.",
    ),
    "username_taken": ("Bu username band.", "Этот username занят.", "This username is taken."),
    "username_reserved": ("Bu username'ni olib bo'lmaydi.", "Этот username недоступен.", "This username isn't available."),
    "username_cooldown": (
        "Username'ni {days} kunda bir marta o'zgartirish mumkin.",
        "Username можно менять раз в {days} дн.",
        "You can change your username once every {days} days.",
    ),
    "referrer_not_found": (
        "Taklif qilgan foydalanuvchi topilmadi.",
        "Пригласивший пользователь не найден.",
        "The inviting user wasn't found.",
    ),
    "photo_invalid": (
        "Rasm formati noto'g'ri (JPEG yoki PNG, 700 KB gacha).",
        "Неверный формат фото (JPEG или PNG, до 700 КБ).",
        "Invalid photo (JPEG or PNG, up to 700 KB).",
    ),
    "admin_cannot_delete": ("Bu hisobni o'chirib bo'lmaydi.", "Этот аккаунт нельзя удалить.", "This account can't be deleted."),
    # ── Rejalar ──
    "plan_not_found": ("Reja topilmadi.", "План не найден.", "Plan not found."),
    "plan_past_create": (
        "O'tib ketgan kun uchun reja qo'shib bo'lmaydi.",
        "Нельзя добавить план на прошедший день.",
        "You can't add a plan for a past day.",
    ),
    "plan_past_edit": (
        "O'tib ketgan kundagi rejani o'zgartirib bo'lmaydi.",
        "Нельзя изменить план прошедшего дня.",
        "Plans from past days can't be changed.",
    ),
    "plan_too_far": (
        "Reja ko'pi bilan 1 yil oldinga qo'shiladi.",
        "План можно добавить максимум на год вперёд.",
        "Plans can be added at most one year ahead.",
    ),
    "plan_not_due": ("Rejaning vaqti hali kelmagan.", "Время плана ещё не наступило.", "This plan's time hasn't come yet."),
    "plan_limit": (
        "Bepul tarifda kuniga {limit} ta reja. Cheksiz rejalar uchun Premium oling.",
        "В бесплатном тарифе — {limit} планов в день. Оформите Премиум для безлимита.",
        "The free plan allows {limit} plans per day. Get Premium for unlimited plans.",
    ),
    "member_plan_limit": (
        "A'zoning bepul kunlik reja limiti tugagan ({limit}).",
        "У участника закончился бесплатный дневной лимит планов ({limit}).",
        "This member has reached the free daily plan limit ({limit}).",
    ),
    # ── Odatlar ──
    "habit_not_found": ("Odat topilmadi.", "Привычка не найдена.", "Habit not found."),
    "habit_limit": (
        "Bepul tarifda {limit} ta odat. Cheksiz odatlar uchun Premium oling.",
        "В бесплатном тарифе — {limit} привычки. Оформите Премиум для безлимита.",
        "The free plan allows {limit} habits. Get Premium for unlimited habits.",
    ),
    "member_habit_limit": (
        "A'zoning bepul odat limiti tugagan ({limit}).",
        "У участника закончился бесплатный лимит привычек ({limit}).",
        "This member has reached the free habit limit ({limit}).",
    ),
    "habit_future": ("Kelajak kunni belgilab bo'lmaydi.", "Нельзя отметить будущий день.", "You can't mark a future day."),
    "habit_too_old": (
        "Faqat oxirgi 7 kunni belgilash mumkin.",
        "Можно отмечать только последние 7 дней.",
        "Only the last 7 days can be marked.",
    ),
    "habit_not_due_day": (
        "Bu kunda odat rejalashtirilmagan.",
        "В этот день привычка не запланирована.",
        "This habit isn't scheduled for that day.",
    ),
    # ── Maqsadlar ──
    "goal_not_found": ("Maqsad topilmadi.", "Цель не найдена.", "Goal not found."),
    "goal_type_invalid": (
        "Faqat yillik yoki oylik maqsad yaratish mumkin.",
        "Можно создать только годовую или месячную цель.",
        "Only yearly or monthly goals are allowed.",
    ),
    "goal_period_invalid": ("Maqsad davri noto'g'ri.", "Неверный период цели.", "Invalid goal period."),
    "goal_future": (
        "Bu maqsad davri hali boshlanmagan.",
        "Период этой цели ещё не начался.",
        "This goal's period hasn't started yet.",
    ),
    "goal_limit": (
        "Bepul tarifda {limit} ta maqsad. Cheksiz maqsadlar uchun Premium oling.",
        "В бесплатном тарифе — {limit} цели. Оформите Премиум для безлимита.",
        "The free plan allows {limit} goals. Get Premium for unlimited goals.",
    ),
    # ── AI ──
    "ai_limit": (
        "Bugungi bepul AI xabarlar tugadi ({limit}/kun). Cheksiz murabbiy uchun Premium oling.",
        "Бесплатные сообщения ИИ на сегодня закончились ({limit}/день). Оформите Премиум.",
        "Today's free AI messages are used up ({limit}/day). Get Premium for unlimited coaching.",
    ),
    "ai_unavailable": (
        "AI murabbiy hozir javob bera olmadi. Birozdan keyin urinib ko'ring.",
        "ИИ-тренер сейчас недоступен. Попробуйте чуть позже.",
        "The AI coach couldn't reply right now. Please try again shortly.",
    ),
    "ai_empty": ("Xabar bo'sh.", "Пустое сообщение.", "Empty message."),
    # ── Premium ──
    "group_create_premium": (
        "Guruh yaratish Premium bilan ochiladi.",
        "Создание групп доступно в Премиум.",
        "Creating groups is a Premium feature.",
    ),
    "group_join_premium": (
        "Guruhga qo'shilish Premium bilan ochiladi.",
        "Вступление в группу доступно в Премиум.",
        "Joining groups is a Premium feature.",
    ),
    "billing_disabled": (
        "To'lov tizimi vaqtincha ishlamayapti.",
        "Платёжная система временно недоступна.",
        "Payments are temporarily unavailable.",
    ),
    "billing_failed": (
        "To'lovni boshlab bo'lmadi. Qaytadan urinib ko'ring.",
        "Не удалось начать оплату. Попробуйте ещё раз.",
        "Couldn't start the payment. Please try again.",
    ),
    "plan_unknown": ("Noma'lum tarif.", "Неизвестный тариф.", "Unknown plan."),
    "provider_unknown": ("Noma'lum to'lov usuli.", "Неизвестный способ оплаты.", "Unknown payment method."),
    "order_not_found": ("To'lov topilmadi.", "Платёж не найден.", "Payment not found."),
    "promo_invalid": ("Promokod yaroqsiz.", "Промокод недействителен.", "Invalid promo code."),
    "promo_plan_mismatch": (
        "Bu promokod tanlangan tarifga amal qilmaydi.",
        "Этот промокод не действует для выбранного тарифа.",
        "This promo code doesn't apply to the selected plan.",
    ),
    "sales_paused": (
        "Sotuv vaqtincha to'xtatilgan. Birozdan keyin urinib ko'ring.",
        "Продажи временно приостановлены. Попробуйте позже.",
        "Sales are temporarily paused. Please try again later.",
    ),
    "account_banned": (
        "Hisobingiz bloklangan. Savollar bo'lsa, qo'llab-quvvatlash xizmatiga yozing.",
        "Ваш аккаунт заблокирован. Если есть вопросы, напишите в поддержку.",
        "Your account has been blocked. Contact support if you have questions.",
    ),
    "promo_not_found": ("Bunday promokod topilmadi.", "Такой промокод не найден.", "Promo code not found."),
    "promo_inactive": ("Promokod o'chirilgan.", "Промокод отключён.", "This promo code is disabled."),
    "promo_expired": ("Promokod muddati tugagan.", "Срок действия промокода истёк.", "This promo code has expired."),
    "promo_exhausted": ("Promokod limiti tugagan.", "Лимит промокода исчерпан.", "This promo code has been used up."),
    "promo_already_used": (
        "Siz bu promokoddan foydalangansiz.",
        "Вы уже использовали этот промокод.",
        "You've already used this promo code.",
    ),
    "promo_not_free": (
        "Bu chegirma promokodi — uni tarif tanlashda kiriting.",
        "Это промокод на скидку — введите его при выборе тарифа.",
        "This is a discount code — enter it when choosing a plan.",
    ),
    "promo_free_at_checkout": (
        "Bu promokod bepul Premium beradi — uni «Promokodni faollashtirish» orqali kiriting.",
        "Этот промокод даёт бесплатный Премиум — активируйте его отдельно.",
        "This code grants free Premium — redeem it separately.",
    ),
    # ── Guruhlar ──
    "group_not_found": ("Guruh topilmadi.", "Группа не найдена.", "Group not found."),
    "group_not_member": ("Siz bu guruh a'zosi emassiz.", "Вы не участник этой группы.", "You're not a member of this group."),
    "group_owner_only": ("Bu amal faqat guruh egasi uchun.", "Это действие доступно только владельцу.", "Only the group owner can do this."),
    "group_name_required": ("Guruh nomini yozing.", "Укажите название группы.", "Enter a group name."),
    "group_owner_limit": (
        "Ko'pi bilan {limit} ta guruh yaratish mumkin.",
        "Можно создать не более {limit} групп.",
        "You can create up to {limit} groups.",
    ),
    "group_user_limit": (
        "Ko'pi bilan {limit} ta guruhda bo'lish mumkin.",
        "Можно состоять не более чем в {limit} группах.",
        "You can be in up to {limit} groups.",
    ),
    "group_full": ("Guruh to'lgan ({limit} a'zo).", "Группа заполнена ({limit} участников).", "The group is full ({limit} members)."),
    "group_owner_leave": (
        "Ega guruhdan chiqishdan oldin egalikni boshqa a'zoga topshirishi yoki guruhni o'chirishi kerak.",
        "Владелец должен передать группу другому участнику или удалить её.",
        "The owner must transfer ownership or delete the group before leaving.",
    ),
    "group_member_not_found": ("Bunday a'zo bu guruhda yo'q.", "Такого участника нет в группе.", "No such member in this group."),
    "group_self": ("Bu amalni o'zingizga qo'llab bo'lmaydi.", "Нельзя применить к себе.", "You can't do this to yourself."),
    "group_no_permission": (
        "Bu a'zo sizga ruxsat bermagan.",
        "Этот участник не дал вам разрешения.",
        "This member hasn't given you permission.",
    ),
    "user_not_found": ("Bunday username topilmadi.", "Пользователь с таким username не найден.", "No user with that username."),
    "invite_self": ("O'zingizni taklif qila olmaysiz.", "Нельзя пригласить себя.", "You can't invite yourself."),
    "invite_already_member": ("U allaqachon guruh a'zosi.", "Он уже в группе.", "They're already in the group."),
    "invite_pending": ("Taklif allaqachon yuborilgan.", "Приглашение уже отправлено.", "An invite is already pending."),
    "invite_blocked": (
        "Bu foydalanuvchiga taklif yuborib bo'lmaydi.",
        "Этому пользователю нельзя отправить приглашение.",
        "You can't invite this user.",
    ),
    "invite_cooldown": (
        "U taklifni yaqinda rad etgan. Keyinroq urinib ko'ring.",
        "Он недавно отклонил приглашение. Попробуйте позже.",
        "They recently declined an invite. Try again later.",
    ),
    "invite_daily_limit": (
        "Bugun ko'p taklif yubordingiz ({limit}). Ertaga davom eting.",
        "Сегодня отправлено слишком много приглашений ({limit}). Продолжите завтра.",
        "You've sent too many invites today ({limit}). Continue tomorrow.",
    ),
    "invite_not_found": ("Taklif topilmadi yoki eskirgan.", "Приглашение не найдено или устарело.", "Invite not found or expired."),
    # ── Shikoyatlar ──
    "report_self": ("O'zingiz ustingizdan shikoyat qila olmaysiz.", "Нельзя пожаловаться на себя.", "You can't report yourself."),
    "report_target_not_found": ("Shikoyat qilinayotgan ma'lumot topilmadi.", "Объект жалобы не найден.", "The reported content was not found."),
    "report_reason_unknown": ("Shikoyat sababi noto'g'ri.", "Неверная причина жалобы.", "Invalid report reason."),
    # ── Sotuv ──
    "purchases_unavailable": (
        "Bu imkoniyat ushbu qurilmada mavjud emas.",
        "Эта функция недоступна на этом устройстве.",
        "This feature isn't available on this device.",
    ),
}


def t(key: str, lang: str | None = None, **kwargs) -> str:
    lang = lang or current_lang.get()
    variants = _M.get(key)
    if variants is None:
        return key
    idx = SUPPORTED.index(lang) if lang in SUPPORTED else 0
    text = variants[idx]
    return text.format(**kwargs) if kwargs else text
