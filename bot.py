#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ربات کاروسل تلگرام
------------------
کاربر تا ۱۰ عکس می‌فرسته، ربات اون‌ها رو به صورت آلبوم (کاروسل/اسلایدی) برمی‌گردونه.
قابلیت‌ها:
- ساخت کاروسل از ۱ تا ۱۰ عکس
- افزودن / ویرایش کپشن (قبل و بعد از ارسال)
- اضافه / حذف کردن عکس بعد از ارسال کاروسل
- فوروارد مخفی: ارسال کاروسل به کانال/گروه مقصد به نام ربات (نام شما دیده نمی‌شود)
- حذف کاروسل

اجرا:
    BOT_TOKEN=xxxx python bot.py
یا با فایل .env
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
# کانال/گروه پیش‌فرض برای فوروارد مخفی (اختیاری) — مثال: -1001234567890 یا @mychannel
FORWARD_CHAT_ID = os.getenv("FORWARD_CHAT_ID", "").strip()
# فقط این کاربر اجازه استفاده دارد (اختیاری) — آیدی عددی تلگرام
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID", "").strip()

if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN تنظیم نشده! فایل .env را بسازید یا متغیر محیطی BOT_TOKEN را ست کنید.")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("carousel-bot")

MAX_PHOTOS = 10
MAX_CAPTION = 1024  # محدودیت تلگرام برای کپشن عکس


# ---------- ابزارها ----------

def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USER_ID:
        return True
    return str(user_id) == ALLOWED_USER_ID


def get_session(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """سشن هر کاربر در user_data ذخیره می‌شود."""
    if "session" not in context.user_data:
        context.user_data["session"] = {
            "photos": [],            # لیست file_id عکس‌ها
            "caption": "",           # کپشن
            "state": "idle",         # idle | collecting | awaiting_caption | awaiting_edit_caption | awaiting_forward_dest | awaiting_add_photo
            "album_ids": [],         # message_id های آلبوم ارسال‌شده
            "album_chat_id": None,   # chat_id که آلبوم در آن ارسال شده
        }
    return context.user_data["session"]


def reset_session(session: dict):
    session["photos"] = []
    session["caption"] = ""
    session["state"] = "collecting"
    session["album_ids"] = []
    session["album_chat_id"] = None


def collect_keyboard(session: dict) -> InlineKeyboardMarkup:
    n = len(session["photos"])
    rows = []
    if n > 0:
        rows.append([InlineKeyboardButton("✅ ارسال کاروسل", callback_data="send_album")])
        rows.append([
            InlineKeyboardButton("✏️ افزودن/تغییر کپشن", callback_data="add_caption"),
            InlineKeyboardButton("🗑 حذف آخرین عکس", callback_data="remove_last"),
        ])
    rows.append([InlineKeyboardButton("❌ لغو و شروع از اول", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


def album_keyboard(session: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ ویرایش کپشن", callback_data="edit_caption")],
        [
            InlineKeyboardButton("➕ اضافه کردن عکس", callback_data="add_photo"),
            InlineKeyboardButton("➖ حذف آخرین عکس", callback_data="remove_photo"),
        ],
        [InlineKeyboardButton("📤 فوروارد مخفی (بدون نام من)", callback_data="forward_hide")],
        [InlineKeyboardButton("🗑 حذف کاروسل", callback_data="delete_album")],
        [InlineKeyboardButton("🆕 ساخت کاروسل جدید", callback_data="new_carousel")],
    ])


async def send_album(context, chat_id, photos: list, caption: str) -> list:
    """ارسال آلبوم و برگرداندن لیست message_id ها."""
    media = []
    for i, file_id in enumerate(photos):
        cap = caption if i == 0 else None
        media.append(InputMediaPhoto(media=file_id, caption=cap))
    messages = await context.bot.send_media_group(chat_id=chat_id, media=media)
    return [m.message_id for m in messages]


async def delete_album(context, chat_id, album_ids: list):
    for mid in album_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            pass


async def resend_album(update, context, session: dict):
    """حذف آلبوم قبلی و ارسال مجدد (برای اضافه/حذف عکس بعد از ارسال)."""
    chat_id = session["album_chat_id"]
    if not session["photos"]:
        await delete_album(context, chat_id, session["album_ids"])
        session["album_ids"] = []
        return
    await delete_album(context, chat_id, session["album_ids"])
    session["album_ids"] = await send_album(context, chat_id, session["photos"], session["caption"])


# ---------- دستورات ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ شما اجازه استفاده از این ربات را ندارید.")
        return
    session = get_session(context)
    reset_session(session)
    await update.message.reply_text(
        "👋 سلام! من ربات <b>کاروسل‌ساز</b> هستم.\n\n"
        "📸 تا <b>۱۰ عکس</b> برام بفرست، من اون‌ها رو به صورت <b>کاروسل (اسلایدی)</b> برات برمی‌گردونم.\n\n"
        "بعد از ارسال هم می‌تونی:\n"
        "✏️ کپشن رو ویرایش کنی\n"
        "➕ عکس اضافه / ➖ عکس حذف کنی\n"
        "📤 کاروسل رو <b>بدون دیده شدن نامت</b> به کانال یا گروه فوروارد کنی\n\n"
        "👇 همین الان عکس اول رو بفرست:",
        parse_mode="HTML",
        reply_markup=collect_keyboard(session),
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ <b>راهنما</b>\n\n"
        "1️⃣ عکس‌ها رو یکی‌یکی یا به صورت آلبوم بفرست (حداکثر ۱۰ تا)\n"
        "2️⃣ روی «✅ ارسال کاروسل» بزن تا به صورت اسلایدی ساخته بشه\n"
        "3️⃣ برای کپشن روی «✏️ افزودن/تغییر کپشن» بزن و متن رو بفرست\n\n"
        "📤 <b>فوروارد مخفی یعنی چی؟</b>\n"
        "وقتی روی «فوروارد مخفی» می‌زنی، ربات کاروسل رو مستقیم به کانال/گروه مقصد می‌فرسته، "
        "پس <b>نام و آیدی شما اصلاً دیده نمی‌شه</b> و فقط اسم ربات/کانال دیده می‌شه.\n"
        "برای این کار ربات باید در کانال/گروه مقصد <b>ادمین</b> باشه.\n\n"
        "دستورها:\n"
        "/start — شروع\n"
        "/new — ساخت کاروسل جدید\n"
        "/cancel — لغو\n"
        "/help — راهنما",
        parse_mode="HTML",
    )


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(context)
    reset_session(session)
    session["state"] = "idle"
    await update.message.reply_text(
        "❌ لغو شد. برای ساخت کاروسل جدید /new رو بزن یا عکس بفرست.",
        reply_markup=collect_keyboard(session),
    )


# ---------- دریافت عکس ----------

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    session = get_session(context)

    # اگر آلبوم قبلاً ارسال شده و کاربر در حالت اضافه کردن عکس است
    if session["state"] == "awaiting_add_photo":
        if len(session["photos"]) >= MAX_PHOTOS:
            await update.message.reply_text(f"⚠️ حداکثر {MAX_PHOTOS} عکس می‌تونی داشته باشی.")
            return
        session["photos"].append(update.message.photo[-1].file_id)
        # اگر کپشن همراه عکس فرستاده شده بود، ذخیره کن
        if update.message.caption:
            session["caption"] = update.message.caption[:MAX_CAPTION]
        await resend_album(update, context, session)
        session["state"] = "idle"
        await update.message.reply_text(
            f"✅ عکس اضافه شد! الان {len(session['photos'])} عکس داری.",
            reply_markup=album_keyboard(session),
        )
        return

    # حالت عادی: جمع‌آوری عکس
    if session["state"] == "idle":
        session["state"] = "collecting"

    if len(session["photos"]) >= MAX_PHOTOS:
        await update.message.reply_text(
            f"⚠️ به حداکثر {MAX_PHOTOS} عکس رسیدی. روی «✅ ارسال کاروسل» بزن.",
            reply_markup=collect_keyboard(session),
        )
        return

    session["photos"].append(update.message.photo[-1].file_id)
    if update.message.caption:
        session["caption"] = update.message.caption[:MAX_CAPTION]

    n = len(session["photos"])
    await update.message.reply_text(
        f"📸 عکس {n} از {MAX_PHOTOS} دریافت شد."
        + (f"\n📝 کپشن ذخیره شد." if update.message.caption else "")
        + "\n\nعکس بعدی رو بفرست یا روی «✅ ارسال کاروسل» بزن:",
        reply_markup=collect_keyboard(session),
    )


# ---------- دریافت متن (کپشن / مقصد فوروارد) ----------

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    session = get_session(context)
    text = (update.message.text or "").strip()

    # --- دریافت کپشن قبل از ارسال ---
    if session["state"] == "awaiting_caption":
        session["caption"] = text[:MAX_CAPTION]
        session["state"] = "collecting"
        await update.message.reply_text(
            "✅ کپشن ذخیره شد:\n\n" + (session["caption"] or "(خالی)"),
            reply_markup=collect_keyboard(session),
        )
        return

    # --- ویرایش کپشن بعد از ارسال ---
    if session["state"] == "awaiting_edit_caption":
        new_caption = text[:MAX_CAPTION]
        if not session["album_ids"]:
            await update.message.reply_text("⚠️ آلبوم فعالی پیدا نشد.")
            session["state"] = "idle"
            return
        try:
            await context.bot.edit_message_caption(
                chat_id=session["album_chat_id"],
                message_id=session["album_ids"][0],
                caption=new_caption,
            )
            session["caption"] = new_caption
            session["state"] = "idle"
            await update.message.reply_text(
                "✅ کپشن کاروسل ویرایش شد!",
                reply_markup=album_keyboard(session),
            )
        except Exception as e:
            logger.exception("edit caption failed: %s", e)
            await update.message.reply_text(
                "❌ ویرایش کپشن انجام نشد. ممکنه آلبوم قدیمی باشه یا تلگرام اجازه نده.\n"
                "می‌تونی کاروسل رو حذف کنی و از اول بسازی.",
                reply_markup=album_keyboard(session),
            )
            session["state"] = "idle"
        return

    # --- مقصد فوروارد مخفی ---
    if session["state"] == "awaiting_forward_dest":
        dest = text
        # تبدیل آیدی عددی به int
        try:
            dest_id = int(dest)
        except ValueError:
            dest_id = dest  # مثل @mychannel

        if not session["photos"]:
            await update.message.reply_text("⚠️ عکسی برای فوروارد نیست.")
            session["state"] = "idle"
            return

        try:
            await send_album(context, dest_id, session["photos"], session["caption"])
            session["state"] = "idle"
            await update.message.reply_text(
                "✅ کاروسل با موفقیت فوروارد شد!\n"
                "🤫 نام شما دیده نمی‌شه — فقط ربات/کانال مقصد مشخصه.",
                reply_markup=album_keyboard(session),
            )
        except Exception as e:
            logger.exception("forward failed: %s", e)
            await update.message.reply_text(
                "❌ فوروارد انجام نشد.\n"
                "مطمئن شو ربات در کانال/گروه مقصد <b>ادمین</b> هست و اجازه ارسال عکس داره، "
                "بعد دوباره امتحان کن.\n\n"
                "مقصد رو دوباره بفرست (مثل @mychannel یا آیدی عددی):",
                parse_mode="HTML",
            )
        return

    # متن معمولی بدون حالت خاص
    if session["state"] == "idle":
        await update.message.reply_text(
            "📸 برای ساخت کاروسل، عکس بفرست. برای راهنما /help رو بزن.",
            reply_markup=collect_keyboard(session),
        )


# ---------- دکمه‌ها ----------

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_allowed(update.effective_user.id):
        return

    session = get_session(context)
    data = query.data

    # ساخت کاروسل جدید
    if data == "new_carousel":
        reset_session(session)
        await query.edit_message_text(
            "🆕 کاروسل جدید — عکس اول رو بفرست:",
            reply_markup=collect_keyboard(session),
        )
        return

    # لغو
    if data == "cancel":
        reset_session(session)
        session["state"] = "idle"
        await query.edit_message_text(
            "❌ لغو شد. /new برای شروع دوباره.",
            reply_markup=collect_keyboard(session),
        )
        return

    # ارسال کاروسل
    if data == "send_album":
        if not session["photos"]:
            await query.answer("هنوز عکسی نفرستادی!", show_alert=True)
            return
        try:
            ids = await send_album(context, query.message.chat_id, session["photos"], session["caption"])
            session["album_ids"] = ids
            session["album_chat_id"] = query.message.chat_id
            session["state"] = "idle"
            await query.message.reply_text(
                f"🎉 کاروسل با {len(session['photos'])} عکس ساخته شد!\n"
                "حالا می‌تونی کپشن رو ویرایش کنی، عکس اضافه/حذف کنی یا مخفی فورواردش کنی:",
                reply_markup=album_keyboard(session),
            )
        except Exception as e:
            logger.exception("send album failed: %s", e)
            await query.answer("❌ ارسال آلبوم ناموفق بود.", show_alert=True)
        return

    # افزودن/تغییر کپشن قبل از ارسال
    if data == "add_caption":
        session["state"] = "awaiting_caption"
        await query.edit_message_text(
            "✏️ متن کپشن رو بفرست (حداکثر ۱۰۲۴ کاراکتر):",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ برگشت", callback_data="back_collect")]
            ]),
        )
        return

    # برگشت به جمع‌آوری
    if data == "back_collect":
        session["state"] = "collecting"
        await query.edit_message_text(
            f"📸 {len(session['photos'])} عکس داری. عکس بعدی رو بفرست یا ارسال کن:",
            reply_markup=collect_keyboard(session),
        )
        return

    # حذف آخرین عکس (قبل از ارسال)
    if data == "remove_last":
        if session["photos"]:
            session["photos"].pop()
        await query.edit_message_text(
            f"🗑 حذف شد. الان {len(session['photos'])} عکس داری.",
            reply_markup=collect_keyboard(session),
        )
        return

    # ویرایش کپشن بعد از ارسال
    if data == "edit_caption":
        session["state"] = "awaiting_edit_caption"
        await query.message.reply_text(
            "✏️ کپشن جدید رو بفرست (حداکثر ۱۰۲۴ کاراکتر):\n"
            f"کپشن فعلی: {session['caption'] or '(خالی)'}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ انصراف", callback_data="cancel_edit")]
            ]),
        )
        return

    if data == "cancel_edit":
        session["state"] = "idle"
        await query.edit_message_text("↩️ انصراف داده شد.", reply_markup=album_keyboard(session))
        return

    # اضافه کردن عکس به آلبوم ارسال‌شده
    if data == "add_photo":
        if len(session["photos"]) >= MAX_PHOTOS:
            await query.answer(f"حداکثر {MAX_PHOTOS} عکس!", show_alert=True)
            return
        session["state"] = "awaiting_add_photo"
        await query.message.reply_text(
            f"➕ عکس جدید رو بفرست (الان {len(session['photos'])} عکس داری، حداکثر {MAX_PHOTOS}):",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ انصراف", callback_data="cancel_edit")]
            ]),
        )
        return

    # حذف آخرین عکس از آلبوم ارسال‌شده (ارسال مجدد)
    if data == "remove_photo":
        if len(session["photos"]) <= 1:
            await query.answer("حداقل ۱ عکس باید بمونه!", show_alert=True)
            return
        session["photos"].pop()
        await resend_album(update, context, session)
        await query.message.reply_text(
            f"➖ عکس حذف شد. الان {len(session['photos'])} عکس داری.",
            reply_markup=album_keyboard(session),
        )
        return

    # فوروارد مخفی
    if data == "forward_hide":
        if not session["photos"]:
            await query.answer("عکسی برای فوروارد نیست!", show_alert=True)
            return
        if FORWARD_CHAT_ID:
            # اگر کانال پیش‌فرض تنظیم شده، دکمه ارسال مستقیم نشان بده
            await query.message.reply_text(
                "📤 کجا فوروارد کنم؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📤 ارسال به کانال پیش‌فرض", callback_data="forward_default")],
                    [InlineKeyboardButton("✏️ مقصد دیگه‌ای بفرستم", callback_data="forward_custom")],
                ]),
            )
        else:
            session["state"] = "awaiting_forward_dest"
            await query.message.reply_text(
                "📤 مقصد فوروارد رو بفرست:\n"
                "• آیدی کانال/گروه مثل @mychannel\n"
                "• یا آیدی عددی مثل -1001234567890\n\n"
                "⚠️ ربات باید در مقصد ادمین باشه.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("↩️ انصراف", callback_data="cancel_edit")]
                ]),
            )
        return

    if data == "forward_default":
        try:
            dest_id = int(FORWARD_CHAT_ID)
        except ValueError:
            dest_id = FORWARD_CHAT_ID
        try:
            await send_album(context, dest_id, session["photos"], session["caption"])
            await query.message.reply_text(
                "✅ کاروسل به کانال پیش‌فرض فوروارد شد! 🤫 نام شما مخفیه.",
                reply_markup=album_keyboard(session),
            )
        except Exception as e:
            logger.exception("forward default failed: %s", e)
            await query.message.reply_text(
                "❌ فوروارد ناموفق بود. مطمئن شو ربات در کانال ادمینه.",
                reply_markup=album_keyboard(session),
            )
        return

    if data == "forward_custom":
        session["state"] = "awaiting_forward_dest"
        await query.message.reply_text(
            "📤 مقصد رو بفرست (مثل @mychannel یا آیدی عددی):",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ انصراف", callback_data="cancel_edit")]
            ]),
        )
        return

    # حذف کاروسل
    if data == "delete_album":
        await delete_album(context, session["album_chat_id"], session["album_ids"])
        session["album_ids"] = []
        reset_session(session)
        session["state"] = "idle"
        await query.message.reply_text(
            "🗑 کاروسل حذف شد. برای ساخت جدید /new رو بزن.",
            reply_markup=collect_keyboard(session),
        )
        return


# ---------- خطایابی ----------

async def error_handler(update, context):
    logger.exception("Update %s caused error %s", update, context.error)


# ---------- اجرا ----------

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("new", start))
    app.add_handler(CommandHandler("cancel", cancel_cmd))

    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(CallbackQueryHandler(callback_handler))

    app.add_error_handler(error_handler)

    logger.info("🤖 ربات کاروسل شروع به کار کرد...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
