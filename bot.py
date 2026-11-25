#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ربات کاروسل اسلایدی تلگرام — نسخه گروهی
---------------------------------------
هر کاروسل = یک پیام (یک عکس) که با دکمه‌های ◀ ▶ عکسش عوض می‌شود.
دقیقاً مثل اسلاید‌شو: نقطه‌های پایین نشان می‌دهند اسلاید چندم هستی.

قابلیت‌ها:
- ساخت کاروسل از ۱ تا ۱۰ عکس (اسلایدی، نه آلبوم)
- ناوبری با دکمه ◀ / ▶ و نقطه‌ها، بدون ارسال پیام جدید
- کپشن جدا برای هر اسلاید + ویرایش آن
- افزودن / حذف اسلاید
- انتشار در کانال یا گروه بدون دیده شدن نام فرستنده (ربات می‌فرستد)
- کار در گروه: هر عضو کاروسل خودش را می‌سازد

نکته برای گروه:
در BotFather دستور /setprivacy را روی Disable بگذار،
ربات را به گروه اضافه و ادمین کن، و ALLOWED_USER_ID را خالی بگذار.

اجرا:
    BOT_TOKEN=xxxx python bot.py
"""

import os
import logging

from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
)
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
# کانال/گروه پیش‌فرض برای انتشار (اختیاری) — مثال: -1001234567890 یا @mychannel
FORWARD_CHAT_ID = os.getenv("FORWARD_CHAT_ID", "").strip()
# فقط این کاربر اجازه ساخت دارد (اختیاری) — برای گروه خالی بگذار
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID", "").strip()

if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN تنظیم نشده! فایل .env بسازید یا متغیر محیطی BOT_TOKEN را ست کنید.")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("carousel-bot")

MAX_PHOTOS = 10
MAX_CAPTION = 1024  # محدودیت کپشن عکس در تلگرام


# ---------------------------------------------------------------- ابزارها

def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USER_ID:
        return True
    return str(user_id) == ALLOWED_USER_ID


async def ack(query, text: str = None, alert: bool = False):
    """پاسخ به callback بدون خطا (چندبار صدا زدن مشکلی ندارد)."""
    try:
        await query.answer(text=text, show_alert=alert)
    except Exception:
        pass


def store(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.bot_data.setdefault("carousels", {})


def create_carousel(context, photos, captions) -> int:
    """یک کاروسل جدید در حافظه می‌سازد و شناسه‌اش را برمی‌گرداند."""
    d = store(context)
    cid = context.bot_data.get("next_cid", 1)
    context.bot_data["next_cid"] = cid + 1
    d[cid] = {
        "photos": list(photos),
        "captions": list(captions),
        "idx": 0,
        "chat_id": None,
        "message_id": None,
    }
    return cid


def get_session(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """سشن هر کاربر (در گروه هر عضو سشن جدای خودش را دارد)."""
    if "session" not in context.user_data:
        context.user_data["session"] = {
            "photos": [],
            "captions": [],
            "state": "idle",   # idle | collecting | awaiting_text
            "cid": None,
            "pending": None,   # {"action": "add"|"cap"|"pub", "cid": int}
        }
    return context.user_data["session"]


def reset_session(s: dict):
    s["photos"] = []
    s["captions"] = []
    s["state"] = "collecting"
    s["cid"] = None
    s["pending"] = None


# ---------------------------------------------------------------- کیبوردها

def collect_kb(s: dict) -> InlineKeyboardMarkup:
    n = len(s["photos"])
    rows = []
    if n:
        rows.append([InlineKeyboardButton(f"🎬 ساخت کاروسل ({n} عکس)", callback_data="build")])
        rows.append([
            InlineKeyboardButton("✏️ کپشن همه اسلایدها", callback_data="cap_all"),
            InlineKeyboardButton("🗑 حذف آخرین عکس", callback_data="rm_last"),
        ])
        rows.append([InlineKeyboardButton("🖼 ارسال به صورت آلبوم", callback_data="send_album")])
    rows.append([InlineKeyboardButton("❌ پاک کردن و شروع دوباره", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


def carousel_kb(cid: int, idx: int, n: int) -> InlineKeyboardMarkup:
    rows = []

    nav = []
    if n > 1:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"c|{cid}|p"))
    nav.append(InlineKeyboardButton(f"{idx + 1} از {n}", callback_data=f"c|{cid}|info"))
    if n > 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"c|{cid}|n"))
    rows.append(nav)

    # نقطه‌های نشانگر (تا ۵ اسلاید)
    if 2 <= n <= 5:
        rows.append([
            InlineKeyboardButton("●" if i == idx else "○", callback_data=f"c|{cid}|j{i}")
            for i in range(n)
        ])

    rows.append([
        InlineKeyboardButton("✏️ کپشن این اسلاید", callback_data=f"c|{cid}|cap"),
        InlineKeyboardButton("📤 انتشار", callback_data=f"c|{cid}|pub"),
    ])
    rows.append([
        InlineKeyboardButton("➕ عکس", callback_data=f"c|{cid}|add"),
        InlineKeyboardButton("➖ حذف این اسلاید", callback_data=f"c|{cid}|rem"),
        InlineKeyboardButton("🗑 پاک کردن", callback_data=f"c|{cid}|del"),
    ])
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------- نمایش اسلاید

def slide_caption(d: dict, idx: int):
    caps = d["captions"]
    if idx < len(caps) and caps[idx].strip():
        return caps[idx][:MAX_CAPTION]
    return None


async def render_slide(context, cid: int, chat_id: int, message_id: int) -> bool:
    """همان پیام را به اسلاید جاری تغییر می‌دهد (بدون ارسال پیام جدید)."""
    d = store(context).get(cid)
    if not d or not d["photos"]:
        return False
    idx = d["idx"]
    n = len(d["photos"])
    try:
        await context.bot.edit_message_media(
            chat_id=chat_id,
            message_id=message_id,
            media=InputMediaPhoto(media=d["photos"][idx], caption=slide_caption(d, idx)),
            reply_markup=carousel_kb(cid, idx, n),
        )
        return True
    except BadRequest as e:
        msg = str(e).lower()
        if "not modified" in msg or "unchange" in msg:
            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=chat_id, message_id=message_id,
                    reply_markup=carousel_kb(cid, idx, n),
                )
            except Exception:
                pass
            return True
        logger.warning("render_slide failed: %s", e)
        return False


async def send_carousel(context, chat_id, photos, captions) -> bool:
    """یک کاروسل تازه می‌فرستد و پیامش را ثبت می‌کند."""
    cid = create_carousel(context, photos, captions)
    d = store(context)[cid]
    try:
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=d["photos"][0],
            caption=slide_caption(d, 0),
            reply_markup=carousel_kb(cid, 0, len(d["photos"])),
        )
    except Exception as e:
        logger.exception("send_carousel failed: %s", e)
        store(context).pop(cid, None)
        return False
    d["chat_id"] = msg.chat_id
    d["message_id"] = msg.message_id
    return True


async def send_album(context, chat_id, photos, captions) -> bool:
    """ارسال به صورت آلبوم کلاسیک (همه عکس‌ها یک‌جا)."""
    media = [
        InputMediaPhoto(media=fid, caption=(captions[i][:MAX_CAPTION] if i < len(captions) and captions[i].strip() else None))
        for i, fid in enumerate(photos)
    ]
    try:
        await context.bot.send_media_group(chat_id=chat_id, media=media)
        return True
    except Exception as e:
        logger.exception("send_album failed: %s", e)
        return False


# ---------------------------------------------------------------- دستورات

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ شما اجازه استفاده از این ربات را ندارید.")
        return
    s = get_session(context)
    reset_session(s)
    await update.message.reply_text(
        "👋 سلام! من <b>کاروسل‌ساز</b> هستم.\n\n"
        "📸 تا <b>۱۰ عکس</b> برام بفرست.\n"
        "بعد روی دکمه <b>«🎬 ساخت کاروسل»</b> بزن.\n\n"
        "نتیجه یک <b>پیام اسلایدی</b> می‌شه: با دکمه‌های ◀️ ▶️ بین عکس‌ها می‌چرخی، "
        "نقطه‌های پایین هم نشون می‌دن کجایی — بدون اینکه پیام جدیدی فرستاده بشه.\n\n"
        "بعدش می‌تونی کپشن هر اسلاید رو عوض کنی یا کل کاروسل رو <b>بدون دیده شدن نامت</b> "
        "توی کانال/گروه منتشر کنی.\n\n"
        "👇 عکس اول رو بفرست:",
        parse_mode="HTML",
        reply_markup=collect_kb(s),
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ <b>چطور کار می‌کنه؟</b>\n\n"
        "1️⃣ عکس‌ها رو بفرست (تا ۱۰ تا)\n"
        "2️⃣ دکمه «🎬 ساخت کاروسل» رو بزن\n"
        "3️⃣ با ◀️ و ▶️ بین اسلایدها بچرخ\n"
        "4️⃣ «✏️ کپشن این اسلاید» برای نوشتن متن روی همون عکس\n"
        "5️⃣ «📤 انتشار» برای فرستادن به کانال یا گروه (بدون نام تو)\n\n"
        "دستورها:\n"
        "/start — شروع\n"
        "/new — کاروسل جدید\n"
        "/cancel — لغو\n"
        "/help — راهنما",
        parse_mode="HTML",
    )


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = get_session(context)
    reset_session(s)
    s["state"] = "idle"
    await update.message.reply_text(
        "❌ لغو شد. برای شروع دوباره /new رو بزن.",
        reply_markup=collect_kb(s),
    )


# ---------------------------------------------------------------- دریافت عکس

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    s = get_session(context)
    chat_type = update.effective_chat.type if update.effective_chat else "private"

    # در گروه: تا وقتی /start نزده، به عکس‌های معمولی کاری نداشته باش
    if s["state"] == "idle" and not s["pending"] and chat_type in ("group", "supergroup"):
        return

    file_id = update.message.photo[-1].file_id
    cap = (update.message.caption or "").strip()

    # --- افزودن عکس به کاروسل موجود ---
    if s["pending"] and s["pending"]["action"] == "add":
        cid = s["pending"]["cid"]
        s["pending"] = None
        d = store(context).get(cid)
        if not d:
            await update.message.reply_text("⚠️ این کاروسل منقضی شده. از اول بساز.")
            return
        if len(d["photos"]) >= MAX_PHOTOS:
            await update.message.reply_text(f"⚠️ حداکثر {MAX_PHOTOS} عکس.")
            return
        d["photos"].append(file_id)
        d["captions"].append(cap)
        d["idx"] = len(d["photos"]) - 1
        await render_slide(context, cid, d["chat_id"], d["message_id"])
        return

    # --- جمع‌آوری عکس ---
    if s["state"] == "idle":
        s["state"] = "collecting"

    if len(s["photos"]) >= MAX_PHOTOS:
        await update.message.reply_text(
            f"⚠️ به حداکثر {MAX_PHOTOS} عکس رسیدی. روی «🎬 ساخت کاروسل» بزن.",
            reply_markup=collect_kb(s),
        )
        return

    s["photos"].append(file_id)
    s["captions"].append(cap)
    n = len(s["photos"])
    await update.message.reply_text(
        f"📸 عکس {n} دریافت شد."
        + ("\n📝 کپشنش ذخیره شد." if cap else "")
        + "\n\nعکس بعدی رو بفرست یا روی «🎬 ساخت کاروسل» بزن:",
        reply_markup=collect_kb(s),
    )


# ---------------------------------------------------------------- دریافت متن

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    s = get_session(context)
    text = (update.message.text or "").strip()
    pending = s["pending"]

    if not pending:
        chat_type = update.effective_chat.type if update.effective_chat else "private"
        if chat_type in ("group", "supergroup"):
            return
        await update.message.reply_text(
            "📸 برای ساخت کاروسل عکس بفرست. راهنما: /help",
            reply_markup=collect_kb(s),
        )
        return

    action = pending["action"]
    cid = pending["cid"]
    s["pending"] = None

    # --- کپشن همه اسلایدها (قبل از ساخت — cid ندارد) ---
    if action == "cap_all":
        s["captions"] = [text[:MAX_CAPTION]] * len(s["photos"])
        s["state"] = "collecting"
        await update.message.reply_text(
            "✅ کپشن برای همه اسلایدها ثبت شد:\n\n" + (text or "(خالی)"),
            reply_markup=collect_kb(s),
        )
        return

    d = store(context).get(cid)
    if not d:
        await update.message.reply_text("⚠️ این کاروسل منقضی شده. از اول بساز.")
        return

    # --- کپشن یک اسلاید ---
    if action == "cap":
        idx = d["idx"]
        while len(d["captions"]) <= idx:
            d["captions"].append("")
        d["captions"][idx] = text[:MAX_CAPTION]
        try:
            await context.bot.edit_message_caption(
                chat_id=d["chat_id"],
                message_id=d["message_id"],
                caption=text[:MAX_CAPTION] if text else "",
                reply_markup=carousel_kb(cid, idx, len(d["photos"])),
            )
        except Exception as e:
            logger.warning("edit caption failed: %s", e)
            await render_slide(context, cid, d["chat_id"], d["message_id"])
        await update.message.reply_text(f"✅ کپشن اسلاید {idx + 1} ذخیره شد.")
        return

    # --- انتشار ---
    if action == "pub":
        dest = text
        try:
            dest_id = int(dest)
        except ValueError:
            dest_id = dest  # مثل @mychannel
        ok = await send_carousel(context, dest_id, d["photos"], d["captions"])
        if ok:
            await update.message.reply_text(
                "✅ کاروسل منتشر شد!\n"
                "🤫 نام تو دیده نمی‌شه — پیام از طرف رباته و همه می‌تونن با ◀ ▶ ورقش بزنن."
            )
        else:
            await update.message.reply_text(
                "❌ انتشار انجام نشد.\n"
                "مطمئن شو ربات در کانال/گروه مقصد <b>ادمین</b> هست و اجازه ارسال عکس داره.\n"
                "دوباره مقصد رو بفرست (مثل @mychannel یا -1001234567890):",
                parse_mode="HTML",
            )
        return


# ---------------------------------------------------------------- دکمه‌ها

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    s = get_session(context)

    # ---------- دکمه‌های مرحله ساخت ----------
    if not data.startswith("c|"):
        await ack(query)

        if data == "build":
            if not is_allowed(update.effective_user.id):
                return
            if not s["photos"]:
                await ack(query, "هنوز عکسی نفرستادی!", True)
                return
            while len(s["captions"]) < len(s["photos"]):
                s["captions"].append("")
            ok = await send_carousel(context, query.message.chat_id, s["photos"], s["captions"])
            if ok:
                # مجموعه قبلی پاک می‌شود تا کاروسل بعدی از صفر شروع شود
                s["photos"] = []
                s["captions"] = []
                s["state"] = "idle"
                s["pending"] = None
            else:
                await ack(query, "❌ ساخت کاروسل ناموفق بود.", True)
            return

        if data == "send_album":
            if not s["photos"]:
                await ack(query, "هنوز عکسی نفرستادی!", True)
                return
            await send_album(context, query.message.chat_id, s["photos"], s["captions"])
            return

        if data == "cap_all":
            if not s["photos"]:
                await ack(query, "اول عکس بفرست!", True)
                return
            s["pending"] = {"action": "cap_all", "cid": None}
            s["state"] = "awaiting_text"
            await query.message.reply_text(
                "✏️ متن کپشن رو بفرست.\n"
                "(این متن روی <b>همه</b> اسلایدها می‌شینه — بعداً می‌تونی هر اسلاید رو جدا عوض کنی)",
                parse_mode="HTML",
            )
            return

        if data == "rm_last":
            if s["photos"]:
                s["photos"].pop()
                if s["captions"]:
                    s["captions"].pop()
            await query.edit_message_text(
                f"🗑 حذف شد. الان {len(s['photos'])} عکس داری.",
                reply_markup=collect_kb(s),
            )
            return

        if data == "cancel":
            reset_session(s)
            s["state"] = "idle"
            await query.edit_message_text("❌ پاک شد. /new برای شروع دوباره.", reply_markup=collect_kb(s))
            return

        return

    # ---------- دکمه‌های کاروسل ----------
    try:
        _, sid, act = data.split("|", 2)
        cid = int(sid)
    except ValueError:
        await ack(query)
        return

    d = store(context).get(cid)
    if not d:
        await ack(query, "⚠️ این کاروسل منقضی شده (ربات ری‌استارت شده). یکی جدید بساز.", True)
        return

    n = len(d["photos"])
    chat_id = d["chat_id"] or query.message.chat_id
    message_id = d["message_id"] or query.message.message_id

    # ورق زدن
    if act in ("p", "n"):
        if n < 2:
            await ack(query, "فقط یک اسلاید داری.")
            return
        d["idx"] = (d["idx"] - 1) % n if act == "p" else (d["idx"] + 1) % n
        await render_slide(context, cid, chat_id, message_id)
        await ack(query)
        return

    # پرش به اسلاید خاص
    if act.startswith("j"):
        try:
            target = int(act[1:])
        except ValueError:
            await ack(query)
            return
        if 0 <= target < n:
            d["idx"] = target
            await render_slide(context, cid, chat_id, message_id)
        await ack(query)
        return

    if act == "info":
        await ack(query, f"اسلاید {d['idx'] + 1} از {n}")
        return

    # کپشن این اسلاید
    if act == "cap":
        if not is_allowed(update.effective_user.id):
            await ack(query, "⛔ اجازه نداری.", True)
            return
        s["pending"] = {"action": "cap", "cid": cid}
        s["state"] = "awaiting_text"
        cur = d["captions"][d["idx"]] if d["idx"] < len(d["captions"]) else ""
        await ack(query)
        await query.message.reply_text(
            f"✏️ کپشن اسلاید {d['idx'] + 1} رو بفرست:\n\n"
            f"متن فعلی: {cur or '(خالی)'}\n\n"
            "برای خالی کردن کپشن، یک نقطه (. ) بفرست.",
        )
        return

    # افزودن عکس
    if act == "add":
        if not is_allowed(update.effective_user.id):
            await ack(query, "⛔ اجازه نداری.", True)
            return
        if n >= MAX_PHOTOS:
            await ack(query, f"حداکثر {MAX_PHOTOS} عکس!", True)
            return
        s["pending"] = {"action": "add", "cid": cid}
        s["state"] = "awaiting_text"
        await ack(query)
        await query.message.reply_text(f"➕ عکس جدید رو بفرست (الان {n} اسلاید داری):")
        return

    # حذف اسلاید جاری
    if act == "rem":
        if not is_allowed(update.effective_user.id):
            await ack(query, "⛔ اجازه نداری.", True)
            return
        if n <= 1:
            await ack(query, "حداقل یک اسلاید باید بمونه. برای حذف کل، «🗑 پاک کردن» رو بزن.", True)
            return
        i = d["idx"]
        d["photos"].pop(i)
        if i < len(d["captions"]):
            d["captions"].pop(i)
        d["idx"] = min(i, len(d["photos"]) - 1)
        await render_slide(context, cid, chat_id, message_id)
        await ack(query, "➖ اسلاید حذف شد.")
        return

    # پاک کردن کل کاروسل
    if act == "del":
        if not is_allowed(update.effective_user.id):
            await ack(query, "⛔ اجازه نداری.", True)
            return
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass
        store(context).pop(cid, None)
        await ack(query, "🗑 پاک شد.")
        return

    # انتشار
    if act == "pub":
        if not is_allowed(update.effective_user.id):
            await ack(query, "⛔ اجازه نداری.", True)
            return
        s["pending"] = {"action": "pub", "cid": cid}
        s["state"] = "awaiting_text"
        await ack(query)
        hint = f"\n\nپیشنهاد: {FORWARD_CHAT_ID}" if FORWARD_CHAT_ID else ""
        await query.message.reply_text(
            "📤 مقصد انتشار رو بفرست:\n"
            "• یوزرنیم کانال: <code>@mychannel</code>\n"
            "• یا آیدی عددی: <code>-1001234567890</code>"
            + hint
            + "\n\n⚠️ ربات باید در مقصد <b>ادمین</b> باشه.",
            parse_mode="HTML",
        )
        return

    await ack(query)


# ---------------------------------------------------------------- خطاها و اجرا

async def error_handler(update, context):
    logger.exception("Update %s caused error %s", update, context.error)


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("new", start))
    app.add_handler(CommandHandler("carousel", start))
    app.add_handler(CommandHandler("cancel", cancel_cmd))

    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(CallbackQueryHandler(callback_handler))

    app.add_error_handler(error_handler)

    logger.info("🤖 ربات کاروسل اسلایدی شروع به کار کرد...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
