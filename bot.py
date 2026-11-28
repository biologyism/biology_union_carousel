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

import asyncio
import io
import json
import os
import re
import logging
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

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
# 🔒 مالک ربات: فقط این کاربر می‌تواند «انتشار در کانال» و «حذف» انجام دهد.
# برای گروه اکیداً پیشنهاد می‌شود این را پر کنی تا بقیه نتوانند در کانالت پست بگذارند.
OWNER_ID = os.getenv("OWNER_ID", "").strip()

# ---------------- ذخیره‌سازی دائمی ----------------
# کاروسل‌ها در یک فایل JSON نگه داشته می‌شوند تا بعد از ری‌استارت هم زنده بمانند.
STATE_FILE = os.getenv("STATE_FILE", "state.json").strip() or "state.json"
# file = فقط روی دیسک | git = روی دیسک + کامیت به ریپو (برای GitHub Actions)
STATE_PERSIST = os.getenv("STATE_PERSIST", "file").strip().lower()
# فاصله‌ی بررسی و کامیت خودکار (ثانیه)
PERSIST_INTERVAL = int(os.getenv("PERSIST_INTERVAL", "90"))
# سقف تعداد کاروسل‌های نگهداری‌شده (قدیمی‌ترها حذف می‌شوند)
MAX_STORED = 500

if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN تنظیم نشده! فایل .env بسازید یا متغیر محیطی BOT_TOKEN را ست کنید.")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("carousel-bot")

MAX_PHOTOS = 10
MAX_CAPTION = 1024  # محدودیت کپشن عکس در تلگرام

# ---------------- کاروسل نیتیو تلگرام (Rich Messages) ----------------
# Bot API 10.1+ متد sendRichMessage را دارد و بلوک «slideshow» آن
# دقیقاً همان کاروسل با فلش ◀ ▶ و نقطه‌های پایین است که تلگرام خودش رندر می‌کند.
# auto = اول تلاش می‌کند نیتیو بفرستد، اگر نشد حالت دکمه‌ای | off = فقط حالت دکمه‌ای
RICH_MODE = os.getenv("RICH_CAROUSEL", "auto").strip().lower()
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}/"
RTL_RE = re.compile(r"[\u0600-\u06FF]")
FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
LRM = "\u200e"   # علامت چپ‌به‌راست
RLM = "\u200f"   # علامت راست‌به‌چپ


def bidi_fix(text: str) -> str:
    """فیلد کپشن فارسی + کلمات انگلیسی را مرتب می‌کند.

    تلگرام برای متن دوزبانه، نقطه‌ویرگول و پرانتز را جابه‌جا می‌کند.
    گذاشتن یک علامت نامرئی RTL در ابتدای هر خط این مشکل را حل می‌کند.
    """
    if not text:
        return text
    out = []
    for line in text.split("\n"):
        if not line.strip():
            out.append(line)
        elif RTL_RE.search(line):
            out.append(RLM + line)
        else:
            out.append(line)
    return "\n".join(out)

ADMIN_HELP = (
    "❌ نشد در <b>{dest}</b> منتشر کنم.\n\n"
    "تلگرام اجازه نمی‌ده ربات در کانالی که ادمین نیست پیام بذاره. "
    "این کار <b>فقط یک بار</b> لازمه:\n\n"
    "1️⃣ کانال رو باز کن → اسم کانال (بالا) → <b>Manage Channel</b>\n"
    "2️⃣ <b>Administrators</b> → <b>Add Administrator</b>\n"
    "3️⃣ یوزرنیم ربات رو سرچ کن و انتخابش کن\n"
    "4️⃣ فقط گزینه <b>Post Messages</b> کافیه → <b>Save</b>\n\n"
    "بعدش دوباره «📤 انتشار» رو بزن — از این به بعد با یک کلیک می‌ره."
)


# ---------------------------------------------------------------- ابزارها

def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USER_ID:
        return True
    return str(user_id) == ALLOWED_USER_ID


def is_owner(user_id: int) -> bool:
    """انتشار در کانال و حذف فقط برای مالک (اگر OWNER_ID تنظیم شده باشد)."""
    if not OWNER_ID:
        return True
    return str(user_id) == OWNER_ID


async def ack(query, text: str = None, alert: bool = False):
    """پاسخ به callback بدون خطا (چندبار صدا زدن مشکلی ندارد)."""
    try:
        await query.answer(text=text, show_alert=alert)
    except Exception:
        pass


def store(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.bot_data.setdefault("carousels", {})


# ------------------------------------------------ ذخیره/بازیابی روی دیسک

def load_state(context: ContextTypes.DEFAULT_TYPE):
    path = Path(STATE_FILE)
    if not path.exists():
        logger.info("state file not found — starting fresh (%s)", STATE_FILE)
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("state file unreadable, starting fresh: %s", e)
        return
    carousels = {}
    for k, v in (raw.get("carousels") or {}).items():
        try:
            carousels[int(k)] = v
        except (TypeError, ValueError):
            continue
    context.bot_data["carousels"] = carousels
    context.bot_data["next_cid"] = int(raw.get("next_cid") or 1)
    context.bot_data["destinations"] = raw.get("destinations") or []
    context.bot_data["scheduled"] = raw.get("scheduled") or []
    logger.info("✅ %d کاروسل از فایل state بازیابی شد", len(carousels))


def save_state(context: ContextTypes.DEFAULT_TYPE):
    """نوشتن اتمیک روی دیسک. هر تغییری در کاروسل‌ها این را صدا می‌زند."""
    d = store(context)
    if len(d) > MAX_STORED:
        for k in sorted(d)[: len(d) - MAX_STORED]:
            d.pop(k, None)
    payload = {
        "next_cid": context.bot_data.get("next_cid", 1),
        "carousels": {str(k): v for k, v in d.items()},
        "destinations": context.bot_data.get("destinations", []),
        "scheduled": context.bot_data.get("scheduled", []),
    }
    try:
        path = Path(STATE_FILE)
        if path.parent and str(path.parent) not in ("", "."):
            path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        logger.warning("save_state failed: %s", e)


def _git_push_state() -> bool:
    """کامیت و پوش state.json به ریپو (فقط حالت STATE_PERSIST=git)."""
    def run(*args, timeout=60):
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)

    if not Path(STATE_FILE).exists():
        return False
    try:
        run("git", "add", STATE_FILE)
        if run("git", "diff", "--cached", "--quiet").returncode == 0:
            return False  # تغییری نیست
        run(
            "git", "-c", "user.name=carousel-bot",
            "-c", "user.email=carousel-bot@users.noreply.github.com",
            "commit", "-m", "chore: state [skip ci]",
        )
        push = run("git", "push", "origin", "HEAD:main", timeout=90)
        if push.returncode != 0:
            # احتمالاً اجرای دیگری زودتر پوش کرده — یک‌بار ری‌بیس و تلاش دوباره
            logger.info("git push retry after rebase...")
            run("git", "pull", "--rebase", "origin", "main", timeout=90)
            push = run("git", "push", "origin", "HEAD:main", timeout=90)
        if push.returncode != 0:
            logger.warning("git push failed: %s", (push.stderr or "").strip()[:300])
            return False
        logger.info("💾 state در ریپو ذخیره شد")
        return True
    except Exception as e:
        logger.warning("git persistence failed: %s", e)
        return False


async def persist_loop(app: Application):
    """هر چند ثانیه اگر تغییری بوده، state را در ریپو کامیت می‌کند."""
    while True:
        await asyncio.sleep(max(20, PERSIST_INTERVAL))
        app.create_task(scheduler_loop(app))
    if STATE_PERSIST == "git":
            try:
                await asyncio.to_thread(_git_push_state)
            except Exception as e:
                logger.warning("persist loop error: %s", e)


async def post_init(app: Application):
    load_state(app)
    app.create_task(scheduler_loop(app))
    if STATE_PERSIST == "git":
        app.create_task(persist_loop(app))
        logger.info("💾 ذخیره‌سازی دائمی روی گیت فعال است")
    else:
        logger.info("💾 ذخیره‌سازی دائمی روی فایل: %s", STATE_FILE)


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
            "sched_dest": None,
            "status_chat_id": None,   # پیام وضعیتی که ویرایش می‌شود
            "status_message_id": None,
        }
    return context.user_data["session"]


def reset_session(s: dict):
    s["photos"] = []
    s["captions"] = []
    s["state"] = "collecting"
    s["cid"] = None
    s["pending"] = None
    s["status_chat_id"] = None
    s["status_message_id"] = None


async def update_status(context, chat_id: int, text: str, kb=None):
    """یک پیام وضعیت ثابت که با هر عکس ویرایش می‌شود (نه پیام جدید)."""
    s = get_session(context)
    mid = s.get("status_message_id")
    if mid and s.get("status_chat_id") == chat_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=mid, text=text, reply_markup=kb
            )
            return
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
        except Exception:
            pass
    try:
        msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)
        s["status_chat_id"] = chat_id
        s["status_message_id"] = msg.message_id
    except Exception as e:
        logger.warning("update_status failed: %s", e)


# ---------------------------------------------------------------- کیبوردها

def collect_kb(s: dict) -> InlineKeyboardMarkup:
    n = len(s["photos"])
    rows = []
    if n:
        rows.append([InlineKeyboardButton(f"🎬 ساخت کاروسل ({n} عکس)", callback_data="build")])
        rows.append([
            InlineKeyboardButton("✏️ کپشن کاروسل", callback_data="cap_all"),
            InlineKeyboardButton("🗑 حذف آخرین عکس", callback_data="rm_last"),
        ])
        rows.append([InlineKeyboardButton("🖼 ارسال به صورت آلبوم", callback_data="send_album")])
    rows.append([InlineKeyboardButton("❌ پاک کردن و شروع دوباره", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


def carousel_kb(cid: int, idx: int, n: int, mode: str = "media") -> InlineKeyboardMarkup:
    """در حالت نیتیو فقط ⚙️؛ در حالت جایگزین ردیف باریک ◀ ⚙️ ▶."""
    if mode == "rich":
        return InlineKeyboardMarkup([[InlineKeyboardButton("⚙️", callback_data=f"c|{cid}|menu")]])
    row = []
    if n > 1:
        row.append(InlineKeyboardButton("◀️", callback_data=f"c|{cid}|p"))
    row.append(InlineKeyboardButton("⚙️", callback_data=f"c|{cid}|menu"))
    if n > 1:
        row.append(InlineKeyboardButton("▶️", callback_data=f"c|{cid}|n"))
    return InlineKeyboardMarkup([row])


def menu_kb(cid: int, idx: int, n: int, mode: str = "media") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ کپشن کاروسل", callback_data=f"c|{cid}|cap")],
        [
            InlineKeyboardButton("➕ عکس", callback_data=f"c|{cid}|add"),
            InlineKeyboardButton("➖ حذف این اسلاید", callback_data=f"c|{cid}|rem"),
        ],
        [InlineKeyboardButton("📤 انتشار فوری", callback_data=f"c|{cid}|pub")],
        [InlineKeyboardButton("⏰ زمان‌بندی انتشار", callback_data=f"c|{cid}|sched")],
        [InlineKeyboardButton("🗑 پاک کردن کاروسل", callback_data=f"c|{cid}|del")],
        [InlineKeyboardButton("✖️ بستن", callback_data=f"c|{cid}|close")],
    ])


# ---------------------------------------------------------------- کاروسل نیتیو

def _api_sync(method: str, payload: dict) -> dict:
    """فراخوانی مستقیم Bot API (برای متدهایی که کتابخانه هنوز ندارد)."""
    req = urllib.request.Request(
        API_BASE + method,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw)
        except Exception:
            raise RuntimeError(f"HTTP {e.code}: {raw[:200]}")
    if not body.get("ok"):
        raise RuntimeError(body.get("description") or str(body)[:200])
    return body.get("result") or {}


async def tg_api(method: str, payload: dict) -> dict:
    return await asyncio.to_thread(_api_sync, method, payload)


def carousel_caption(d: dict) -> str:
    """کپشن مشترک کاروسل = اولین کپشن غیرخالی."""
    for c in d.get("captions", []):
        if c and c.strip():
            return c.strip()[:MAX_CAPTION]
    return ""


def build_rich_message(photos, caption: str, style: str = "block") -> dict:
    """ساخت پیام rich.

    style="block"   → متن کپشن به‌صورت بلوک متن معمولی زیر کاروسل
                      (رنگ متن عادی است و در تم تاریک خوانا می‌ماند)
    style="caption" → متن به‌عنوان کپشن خودِ اسلایدشو (رنگ کم‌رنگ تلگرام)
    """
    slides = [
        {"type": "photo", "photo": {"type": "photo", "media": fid}}
        for fid in photos
    ]
    show = {"type": "slideshow", "blocks": slides}
    text = bidi_fix(caption) if caption else ""

    if style == "caption":
        if text:
            show["caption"] = {"text": text}
        blocks = [show]
    else:
        blocks = [show]
        if text:
            blocks.append({"type": "paragraph", "text": text})

    rich = {"blocks": blocks}
    if RTL_RE.search(caption or ""):
        rich["is_rtl"] = True
    return rich


def rich_menu_markup(cid: int) -> dict:
    return {"inline_keyboard": [[{"text": "⚙️", "callback_data": f"c|{cid}|menu"}]]}


def get_destinations(context) -> list:
    return context.bot_data.setdefault("destinations", [])


def remember_destination(context, chat_id, title: str):
    lst = get_destinations(context)
    lst[:] = [x for x in lst if str(x.get("id")) != str(chat_id)]
    lst.insert(0, {"id": chat_id, "title": title or str(chat_id)})
    del lst[5:]
    save_state(context)


async def publish_carousel(context, cid: int, dest) -> tuple:
    """ارسال کاروسل به مقصد. (موفق؟, عنوان مقصد, متن خطا)"""
    d = store(context).get(cid)
    if not d:
        return False, None, "کاروسل پیدا نشد."
    try:
        dest_id = int(dest)
    except (TypeError, ValueError):
        dest_id = str(dest).strip()

    title = None
    try:
        chat = await context.bot.get_chat(dest_id)
        title = getattr(chat, "title", None) or getattr(chat, "username", None)
    except Exception:
        pass

    ok = await send_carousel(context, dest_id, d["photos"], d["captions"], with_menu=False)
    if ok:
        remember_destination(context, dest_id, title)
        return True, title, None
    return False, title, "send failed"


def parse_when(text: str):
    """«14:30» یا «+30» یا «30» → زمان دقیق. None اگر نامعتبر بود."""
    s = (text or "").translate(FA_DIGITS).strip().replace("：", ":").replace("٫", ":")
    if not s:
        return None
    if s.startswith("+"):
        s = s[1:].strip()
    m = re.fullmatch(r"(\d{1,3})\s*(m|min|دقیقه|د)?", s)
    if m and (s.endswith(("m", "د", "دقیقه")) or (len(s) <= 3 and int(m.group(1)) <= 600)):
        minutes = int(m.group(1))
        if 0 < minutes <= 600:
            return time.time() + minutes * 60
    m = re.fullmatch(r"(\d{1,2})[:.](\d{2})", s)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if 0 <= hh < 24 and 0 <= mm < 60:
            now = datetime.now()
            target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            return target.timestamp()
    return None


def fmt_when(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y/%m/%d — %H:%M")


def get_scheduled(app) -> list:
    return app.bot_data.setdefault("scheduled", [])


async def send_rich_photos(bot, chat_id, photos, caption: str) -> bool:
    """ارسال کاروسل نیتیو بدون نیاز به context (برای زمان‌بند)."""
    if len(photos) >= 2:
        for style in (["block", "caption"] if caption else ["block"]):
            try:
                await tg_api("sendRichMessage", {
                    "chat_id": chat_id,
                    "rich_message": build_rich_message(photos, caption, style),
                })
                return True
            except Exception as e:
                logger.warning("scheduled sendRichMessage (%s) failed: %s", style, e)
    try:
        if len(photos) == 1:
            await bot.send_photo(chat_id=chat_id, photo=photos[0], caption=caption or None)
        else:
            await bot.send_media_group(chat_id=chat_id, media=[
                InputMediaPhoto(media=f, caption=(caption if i == 0 else None))
                for i, f in enumerate(photos)
            ])
        return True
    except Exception as e:
        logger.warning("scheduled fallback failed: %s", e)
        return False


async def scheduler_loop(app):
    """هر ۲۰ ثانیه پست‌های زمان‌بندی‌شده را بررسی می‌کند."""
    while True:
        try:
            jobs = get_scheduled(app)
            if jobs:
                now = time.time()
                due = [j for j in jobs if j.get("when", 0) <= now]
                for job in due:
                    ok = await send_rich_photos(
                        app.bot, job["chat_id"], job["photos"], job.get("caption", ""))
                    jobs.remove(job)
                    save_state(app)
                    try:
                        await app.bot.send_message(
                            chat_id=job["user_id"],
                            text=(f"⏰ زمان انتشار رسید! کاروسل در <b>{job.get('title') or job['chat_id']}</b> "
                                  + ("منتشر شد ✅" if ok else "منتشر نشد ❌ (ربات ادمین هست؟)")),
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
        except Exception as e:
            logger.warning("scheduler loop error: %s", e)
        await asyncio.sleep(20)


def add_scheduled(context, cid: int, dest, when: float, user_id: int):
    d = store(context).get(cid)
    if not d:
        return None
    jobs = context.bot_data.setdefault("scheduled", [])
    job = {
        "id": int(time.time() * 1000) % 10_000_000,
        "chat_id": dest,
        "title": None,
        "photos": list(d["photos"]),
        "caption": carousel_caption(d),
        "when": when,
        "user_id": user_id,
    }
    jobs.append(job)
    save_state(context)
    return job


async def refresh_carousel(context, cid: int) -> bool:
    """بازسازی کامل پیام کاروسل پس از هر تغییر (کپشن/افزودن/حذف)."""
    d = store(context).get(cid)
    if not d or not d.get("photos"):
        return False
    if d.get("mode") == "rich":
        n = len(d["photos"])
        if n < 2:
            # تک‌عکس: کاروسل معنا ندارد → پیام را با عکس معمولی عوض کن
            try:
                await context.bot.delete_message(chat_id=d["chat_id"], message_id=d["message_id"])
            except Exception:
                pass
            return await send_carousel(context, d["chat_id"], d["photos"], d["captions"])
        payload = {
            "chat_id": d["chat_id"],
            "message_id": d["message_id"],
            "rich_message": build_rich_message(
                d["photos"], carousel_caption(d), d.get("caption_style", "block")),
            "reply_markup": rich_menu_markup(cid),
        }
        try:
            await tg_api("editMessageText", payload)
            save_state(context)
            return True
        except Exception as e:
            logger.warning("editMessageText(rich) failed: %s", e)
            return False
    return await render_slide(context, cid, d["chat_id"], d["message_id"])


# ---------------------------------------------------------------- پردازش عکس

# کش بایت‌های عکس اصلی: file_id → bytes
_RAW_CACHE: dict = {}
# کش عکس آماده‌شده: "file_id|idx|n" → file_id جدید تلگرام
RENDER_CACHE_KEY = "render_cache"


async def fetch_raw(context, file_id: str) -> bytes:
    if file_id in _RAW_CACHE:
        return _RAW_CACHE[file_id]
    f = await context.bot.get_file(file_id)
    data = bytes(await f.download_as_bytearray())
    if len(_RAW_CACHE) > 80:
        _RAW_CACHE.clear()
    _RAW_CACHE[file_id] = data
    return data


def draw_decor(img: Image.Image, idx: int, n: int) -> Image.Image:
    """فلش‌های چپ/راست روی لبه‌ها + نقطه‌های نشانگر پایین عکس — دقیقاً مثل نمونه."""
    base = img.convert("RGBA")
    W, H = base.size
    if n < 2:
        return base.convert("RGB")

    r = max(14, int(min(W, H) * 0.052))
    cy = H // 2
    margin = max(6, int(W * 0.018))

    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # ---- فلش‌ها ----
    for side in ("l", "r"):
        cx = margin + r if side == "l" else W - margin - r
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(0, 0, 0, 115),
                  outline=(255, 255, 255, 90), width=max(1, int(r * 0.06)))
        h = r * 0.40
        o = r * 0.26
        if side == "l":
            pts = [(cx + o, cy - h), (cx + o, cy + h), (cx - o * 1.7, cy)]
        else:
            pts = [(cx - o, cy - h), (cx - o, cy + h), (cx + o * 1.7, cy)]
        d.polygon(pts, fill=(255, 255, 255, 245))

    # ---- نقطه‌ها ----
    dot_r = max(3, int(min(W, H) * 0.012))
    gap = dot_r * 3.2
    y = H - max(12, int(H * 0.055))
    x0 = W / 2 - ((n - 1) * gap) / 2
    span = ((n - 1) * gap) / 2 + dot_r * 2

    # رنگ نقطه‌ها بر اساس روشنایی پس‌زمینه (روی هر عکسی دیده شود)
    y0, y1 = int(max(0, y - dot_r * 3)), int(min(H, y + dot_r * 3))
    xa, xb = int(max(0, W / 2 - span - 4)), int(min(W, W / 2 + span + 4))
    sample = base.crop((xa, y0, xb, y1)).convert("L")
    pixels = list(sample.getdata())
    avg = (sum(pixels) / len(pixels)) if pixels else 128
    light_bg = avg > 145
    col_active = (0, 0, 0, 225) if light_bg else (255, 255, 255, 250)
    col_idle = (0, 0, 0, 105) if light_bg else (255, 255, 255, 120)

    for i in range(n):
        x = x0 + i * gap
        d.ellipse([x - dot_r, y - dot_r, x + dot_r, y + dot_r],
                  fill=col_active if i == idx else col_idle)

    return Image.alpha_composite(base, layer).convert("RGB")


async def prepared_photo(context, file_id: str, idx: int, n: int):
    """file_id عکس آماده را برمی‌گرداند؛ اگر نبود، عکس را می‌سازد و آپلود می‌کند."""
    if n < 2:
        return file_id  # تزئین لازم نیست
    cache = context.bot_data.setdefault(RENDER_CACHE_KEY, {})
    key = f"{file_id}|{idx}|{n}"
    if key in cache:
        return cache[key]
    raw = await fetch_raw(context, file_id)
    img = Image.open(io.BytesIO(raw))
    out = draw_decor(img, idx, n)
    bio = io.BytesIO()
    out.save(bio, format="JPEG", quality=92)
    bio.seek(0)
    bio.name = "slide.jpg"
    return ("file", bio, key)


# ---------------------------------------------------------------- نمایش اسلاید

def slide_caption(d: dict, idx: int):
    caps = d["captions"]
    if idx < len(caps) and caps[idx].strip():
        return bidi_fix(caps[idx][:MAX_CAPTION])
    return None


async def render_slide(context, cid: int, chat_id: int, message_id: int) -> bool:
    """همان پیام را به اسلاید جاری تغییر می‌دهد (فلش و نقطه روی عکس کشیده می‌شود)."""
    d = store(context).get(cid)
    if not d or not d["photos"]:
        return False
    idx = d["idx"]
    n = len(d["photos"])
    cap = slide_caption(d, idx)
    prepared = await prepared_photo(context, d["photos"][idx], idx, n)

    if isinstance(prepared, str):
        media = InputMediaPhoto(media=prepared, caption=cap)
    else:
        _, bio, _ = prepared
        media = InputMediaPhoto(media=bio, caption=cap)

    try:
        msg = await context.bot.edit_message_media(
            chat_id=chat_id,
            message_id=message_id,
            media=media,
            reply_markup=carousel_kb(cid, idx, n),
        )
    except BadRequest as e:
        m = str(e).lower()
        if "not modified" in m or "unchange" in m:
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

    # ذخیره file_id عکس آماده برای دفعات بعد (بدون آپلود مجدد)
    if not isinstance(prepared, str) and getattr(msg, "photo", None):
        context.bot_data.setdefault(RENDER_CACHE_KEY, {})[prepared[2]] = msg.photo[-1].file_id
    return True


async def send_carousel(context, chat_id, photos, captions, with_menu: bool = True) -> bool:
    """کاروسل می‌فرستد: ترجیحاً نیتیو تلگرام (rich slideshow)، وگرنه حالت دکمه‌ای."""
    cid = create_carousel(context, photos, captions)
    d = store(context)[cid]
    n = len(d["photos"])
    caption = carousel_caption(d)

    # ---- ۱) تلاش برای کاروسل نیتیو ----
    if RICH_MODE != "off" and n >= 2:
        for style in (["block", "caption"] if caption else ["block"]):
            payload = {
                "chat_id": chat_id,
                "rich_message": build_rich_message(d["photos"], caption, style),
            }
            if with_menu:
                payload["reply_markup"] = rich_menu_markup(cid)
            try:
                res = await tg_api("sendRichMessage", payload)
                d["mode"] = "rich"
                d["caption_style"] = style
                d["chat_id"] = (res.get("chat") or {}).get("id", chat_id)
                d["message_id"] = res.get("message_id")
                save_state(context)
                logger.info("✅ کاروسل نیتیو ارسال شد (%s, style=%s)", d["message_id"], style)
                return True
            except Exception as e:
                logger.warning("sendRichMessage با style=%s نشد: %s", style, e)

    # ---- ۲) حالت جایگزین: یک عکس با دکمه‌ها ----
    d["mode"] = "media"
    prepared = await prepared_photo(context, d["photos"][0], 0, n)
    photo = prepared if isinstance(prepared, str) else prepared[1]
    try:
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=photo,
            caption=slide_caption(d, 0),
            reply_markup=carousel_kb(cid, 0, n, d["mode"]) if with_menu else None,
        )
    except Exception as e:
        logger.exception("send_carousel failed: %s", e)
        store(context).pop(cid, None)
        return False
    if not isinstance(prepared, str) and getattr(msg, "photo", None):
        context.bot_data.setdefault(RENDER_CACHE_KEY, {})[prepared[2]] = msg.photo[-1].file_id
    d["chat_id"] = msg.chat_id
    d["message_id"] = msg.message_id
    save_state(context)
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
        "نتیجه یک <b>کاروسل نیتیو تلگرام</b> می‌شه: فلش‌های ◀ ▶ روی لبه‌ها و "
        "نقطه‌های پایین — با انگشت یا موس ورق می‌خوره، درست مثل نمونه‌ای که دیدی.\n\n"
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
        "3️⃣ خود تلگرام کاروسل رو می‌سازه — با فلش و نقطه ورق می‌خوره\n"
        "4️⃣ «✏️ کپشن این اسلاید» برای نوشتن متن روی همون عکس\n"
        "5️⃣ «📤 انتشار» برای فرستادن به کانال یا گروه (بدون نام تو)\n\n"
        "دستورها:\n"
        "/start — شروع\n"
        "/new — کاروسل جدید\n"
        "/cancel — لغو\n"
        "/help — راهنما",
        parse_mode="HTML",
    )


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اگر کسی دستور را اشتباه تایپ کرد، راهنمایی کن (فقط در چت خصوصی)."""
    chat_type = update.effective_chat.type if update.effective_chat else "private"
    if chat_type in ("group", "supergroup"):
        return
    await update.message.reply_text(
        "🤔 این دستور رو نمی‌شناسم.\n\n"
        "برای شروع بنویس:  /start\n"
        "راهنما:  /help",
        reply_markup=collect_kb(get_session(context)),
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
        save_state(context)
        await refresh_carousel(context, cid)
        return

    # --- جمع‌آوری عکس ---
    if s["state"] == "idle":
        s["state"] = "collecting"

    if len(s["photos"]) >= MAX_PHOTOS:
        await update_status(
            context, update.effective_chat.id,
            f"⚠️ به حداکثر {MAX_PHOTOS} عکس رسیدی. روی «🎬 ساخت کاروسل» بزن.",
            collect_kb(s),
        )
        return

    s["photos"].append(file_id)
    s["captions"].append(cap)
    n = len(s["photos"])
    if cap:
        # کپشنی که همراه عکس آمده، کپشن کل کاروسل می‌شود
        s["captions"] = [cap] * n
    await update_status(
        context,
        update.effective_chat.id,
        f"📸 <b>{n}</b> عکس ذخیره شد."
        + ("  |  📝 کپشن ثبت شد" if cap else "")
        + "\n\nعکس بعدی رو بفرست، یا روی «🎬 ساخت کاروسل» بزن:",
        collect_kb(s),
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
        await update_status(
            context, update.effective_chat.id,
            f"📸 <b>{len(s['photos'])}</b> عکس  |  📝 کپشن: {text or '(خالی)'}\n\n"
            "عکس بعدی رو بفرست، یا روی «🎬 ساخت کاروسل» بزن:",
            collect_kb(s),
        )
        return

    # --- مقصد زمان‌بندی ---
    if action == "sched_dest":
        s["sched_dest"] = text
        s["state"] = "idle"
        await update.message.reply_text(
            "⏰ کِی منتشر بشه؟",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("۳۰ دقیقه دیگه", callback_data=f"c|{cid}|st|30"),
                 InlineKeyboardButton("۱ ساعت دیگه", callback_data=f"c|{cid}|st|60")],
                [InlineKeyboardButton("۳ ساعت دیگه", callback_data=f"c|{cid}|st|180"),
                 InlineKeyboardButton("۱ روز دیگه", callback_data=f"c|{cid}|st|1440")],
                [InlineKeyboardButton("🕐 ساعت دلخواه (مثلاً 14:30)", callback_data=f"c|{cid}|stc")],
            ]),
        )
        return

    # --- ساعت دلخواه ---
    if action == "sched_at":
        when = parse_when(text)
        if not when:
            s["pending"] = {"action": "sched_at", "cid": cid}
            await update.message.reply_text(
                "❌ متوجه نشدم. این‌طوری بفرست:\n<code>14:30</code>  یا  <code>+45</code>",
                parse_mode="HTML",
            )
            return
        dest = s.get("sched_dest")
        if not dest:
            await update.message.reply_text("❌ مقصد انتخاب نشده. از منو دوباره شروع کن.")
            return
        add_scheduled(context, cid, dest, when, update.effective_user.id)
        await update.message.reply_text(
            f"⏰ ثبت شد! کاروسل در <b>{fmt_when(when)}</b> در <code>{dest}</code> منتشر می‌شه.",
            parse_mode="HTML",
        )
        return

    d = store(context).get(cid)
    if not d:
        await update.message.reply_text("⚠️ این کاروسل منقضی شده. از اول بساز.")
        return

    # --- کپشن یک اسلاید ---
    if action == "cap":
        # یک کپشن واحد برای کل کاروسل
        d["captions"] = [text[:MAX_CAPTION]] * len(d["photos"])
        save_state(context)
        ok = await refresh_carousel(context, cid)
        await update.message.reply_text(
            "✅ کپشن کاروسل ذخیره شد." if ok else "✅ ذخیره شد (برای دیدنش پیام را دوباره باز کن)."
        )
        return

    # --- انتشار ---
    if action == "pub":
        if not is_owner(update.effective_user.id):
            await update.message.reply_text("🔒 فقط مدیر ربات می‌تونه در کانال منتشر کنه.")
            return
        dest = text
        ok, title, _ = await publish_carousel(context, cid, dest)
        if ok:
            await update.message.reply_text(
                f"✅ کاروسل در <b>{title or dest}</b> منتشر شد!\n"
                "🤫 نام تو دیده نمی‌شه.\n"
                "💾 مقصد ذخیره شد — دفعه بعد با یک کلیک می‌ره.",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(ADMIN_HELP.format(dest=title or dest), parse_mode="HTML")
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
                s["status_message_id"] = None
                s["status_chat_id"] = None
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
    extra = None
    try:
        parts = data.split("|", 3)
        cid = int(parts[1])
        act = parts[2]
        if len(parts) > 3:
            extra = parts[3]
    except (ValueError, IndexError):
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
        save_state(context)
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
            save_state(context)
            await render_slide(context, cid, chat_id, message_id)
        await ack(query)
        return

    if act == "info":
        await ack(query, f"اسلاید {d['idx'] + 1} از {n}")
        return

    # منوی گزینه‌ها
    if act == "menu":
        await ack(query)
        await query.message.reply_text(
            f"⚙️ <b>گزینه‌های کاروسل</b> — اسلاید {d['idx'] + 1} از {n}",
            parse_mode="HTML",
            reply_markup=menu_kb(cid, d["idx"], n, d.get("mode", "media")),
        )
        return

    if act == "close":
        await ack(query)
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    # کپشن این اسلاید
    if act == "cap":
        if not is_allowed(update.effective_user.id):
            await ack(query, "⛔ اجازه نداری.", True)
            return
        s["pending"] = {"action": "cap", "cid": cid}
        s["state"] = "awaiting_text"
        cur = next((c for c in d["captions"] if c and c.strip()), "")
        await ack(query)
        await query.message.reply_text(
            "✏️ کپشن کاروسل رو بفرست (یک متن برای کل کاروسل):\n\n"
            f"متن فعلی: {cur or '(خالی)'}\n\n"
            "برای خالی کردن کپشن، یک نقطه (.) بفرست.",
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
        save_state(context)
        if d.get("mode") == "rich" and len(d["photos"]) < 2:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            except Exception:
                pass
            store(context).pop(cid, None)
            await ack(query, "➖ حذف شد. برای ادامه یک کاروسل جدید بساز.")
            return
        await refresh_carousel(context, cid)
        await ack(query, "➖ اسلاید حذف شد.")
        return

    # پاک کردن کل کاروسل
    if act == "del":
        if not is_allowed(update.effective_user.id):
            await ack(query, "⛔ اجازه نداری.", True)
            return
        if not is_owner(update.effective_user.id):
            await ack(query, "🔒 فقط مدیر ربات می‌تونه کاروسل رو پاک کنه.", True)
            return
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass
        store(context).pop(cid, None)
        save_state(context)
        await ack(query, "🗑 پاک شد.")
        return

    # انتشار
    if act == "pub":
        if not is_allowed(update.effective_user.id):
            await ack(query, "⛔ اجازه نداری.", True)
            return
        if not is_owner(update.effective_user.id):
            await ack(query, "🔒 فقط مدیر ربات می‌تونه در کانال منتشر کنه.", True)
            return
        dests = get_destinations(context)
        rows = []
        if FORWARD_CHAT_ID:
            rows.append([InlineKeyboardButton(
                "📤 ارسال به کانال پیش‌فرض", callback_data=f"c|{cid}|topub|{FORWARD_CHAT_ID}")])
        for x in dests:
            rows.append([InlineKeyboardButton(
                f"📤 {x['title']}", callback_data=f"c|{cid}|topub|{x['id']}")])
        rows.append([InlineKeyboardButton("➕ مقصد جدید", callback_data=f"c|{cid}|newdest")])
        await ack(query)
        await query.message.reply_text(
            "📤 کجا منتشر بشه؟" if rows[:-1] else "📤 مقصد انتشار رو بفرست:",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    # ارسال سریع به مقصد ذخیره‌شده
    if act == "topub":
        if not is_allowed(update.effective_user.id):
            await ack(query, "⛔ اجازه نداری.", True)
            return
        if not is_owner(update.effective_user.id):
            await ack(query, "🔒 فقط مدیر ربات می‌تونه در کانال منتشر کنه.", True)
            return
        await ack(query, "⏳ در حال ارسال…")
        dest = extra
        notes = await query.message.reply_text(f"⏳ دارم به {dest} می‌فرستم…")
        ok, title, _ = await publish_carousel(context, cid, dest)
        try:
            await notes.delete()
        except Exception:
            pass
        if ok:
            await query.message.reply_text(
                f"✅ منتشر شد در <b>{title or dest}</b>\n"
                "🤫 نام تو دیده نمی‌شه — پیام از طرف کانال/رباته.",
                parse_mode="HTML",
            )
        else:
            await query.message.reply_text(ADMIN_HELP.format(dest=title or dest), parse_mode="HTML")
        return

    # ---- زمان‌بندی: انتخاب مقصد ----
    if act == "sched":
        if not is_owner(update.effective_user.id):
            await ack(query, "🔒 فقط مدیر ربات می‌تونه زمان‌بندی کنه.", True)
            return
        dests = get_destinations(context)
        rows = [[InlineKeyboardButton(f"📤 {x['title']}", callback_data=f"c|{cid}|sd|{x['id']}")]
                for x in dests]
        rows.append([InlineKeyboardButton("➕ مقصد جدید", callback_data=f"c|{cid}|snew")])
        await ack(query)
        await query.message.reply_text(
            "⏰ برای کدوم مقصد زمان‌بندی کنم؟" if dests else "⏰ اول مقصد رو بفرست:",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if act == "snew":
        if not is_owner(update.effective_user.id):
            await ack(query, "🔒 اجازه نداری.", True)
            return
        s["pending"] = {"action": "sched_dest", "cid": cid}
        s["state"] = "awaiting_text"
        await ack(query)
        await query.message.reply_text(
            "⏰ مقصد رو بفرست:\n<code>@my_channel</code> یا <code>-1001234567890</code>",
            parse_mode="HTML",
        )
        return

    if act == "sd":
        if not is_owner(update.effective_user.id):
            await ack(query, "🔒 اجازه نداری.", True)
            return
        s["sched_dest"] = extra
        await ack(query)
        await query.message.reply_text(
            "⏰ کِی منتشر بشه؟",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("۳۰ دقیقه دیگه", callback_data=f"c|{cid}|st|30"),
                 InlineKeyboardButton("۱ ساعت دیگه", callback_data=f"c|{cid}|st|60")],
                [InlineKeyboardButton("۳ ساعت دیگه", callback_data=f"c|{cid}|st|180"),
                 InlineKeyboardButton("۱ روز دیگه", callback_data=f"c|{cid}|st|1440")],
                [InlineKeyboardButton("🕐 ساعت دلخواه (مثلاً 14:30)", callback_data=f"c|{cid}|stc")],
            ]),
        )
        return

    if act in ("st", "stc"):
        if not is_owner(update.effective_user.id):
            await ack(query, "🔒 اجازه نداری.", True)
            return
        dest = s.get("sched_dest")
        if not dest:
            await ack(query, "اول مقصد رو انتخاب کن.", True)
            return
        await ack(query)
        if act == "stc":
            s["pending"] = {"action": "sched_at", "cid": cid}
            await query.message.reply_text(
                "🕐 ساعت رو بفرست — مثلاً:\n<code>14:30</code> یعنی امروز ساعت ۱۴:۳۰ "
                "(اگه گذشته باشه، فردا)\n<code>+45</code> یعنی ۴۵ دقیقه دیگه",
                parse_mode="HTML",
            )
            return
        try:
            minutes = int(extra)
        except (TypeError, ValueError):
            minutes = 30
        when = time.time() + minutes * 60
        add_scheduled(context, cid, dest, when, update.effective_user.id)
        await query.message.reply_text(
            f"⏰ ثبت شد! کاروسل در <b>{fmt_when(when)}</b> در <code>{dest}</code> منتشر می‌شه.",
            parse_mode="HTML",
        )
        return

    if act == "newdest":
        if not is_owner(update.effective_user.id):
            await ack(query, "🔒 فقط مدیر ربات می‌تونه در کانال منتشر کنه.", True)
            return
        s["pending"] = {"action": "pub", "cid": cid}
        s["state"] = "awaiting_text"
        await ack(query)
        await query.message.reply_text(
            "📤 مقصد رو بفرست:\n"
            "• یوزرنیم کانال: <code>@mychannel</code>\n"
            "• یا آیدی عددی: <code>-1001234567890</code>\n\n"
            "⚠️ ربات باید در مقصد <b>ادمین</b> باشه (فقط یک بار).",
            parse_mode="HTML",
        )
        return

    await ack(query)


# ---------------------------------------------------------------- خطاها و اجرا

async def error_handler(update, context):
    logger.exception("Update %s caused error %s", update, context.error)


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("new", start))
    app.add_handler(CommandHandler("carousel", start))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    # ⚠️ باید بعد از همه‌ی CommandHandlerها باشد
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(CallbackQueryHandler(callback_handler))

    app.add_error_handler(error_handler)

    logger.info("🤖 ربات کاروسل اسلایدی شروع به کار کرد...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
