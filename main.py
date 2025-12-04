import asyncio
import io
import os

from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.client.default import DefaultBotProperties
from PIL import Image

# 🔑 YOUR BOT TOKEN
BOT_TOKEN = "8204701331:AAEnYW9H6VA_iDL9Gp3Bs9i0TjFi9uXuxZU"

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=None))
dp = Dispatcher()

# chat_id -> {"images": [file_id, ...], "msg_id": int | None}
user_sessions: dict[int, dict] = {}

# (chat_id, media_group_id) -> [file_id, ...]  (для альбомов)
album_sessions: dict[tuple[int, str], list[str]] = {}


# ========= HTTP СЕРВЕР ДЛЯ RENDER =========

async def handle(request):
    return web.Response(text="Bot is running")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle)

    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Web server listening on port {port}")


# =============== ВСПОМОГАТЕЛЬНОЕ ===============

def build_summary_text(count: int) -> str:
    return (
        f"Number of images ( {count} ) 📂\n"
        "Send me images to prepare them for PDF conversion."
    )


def build_keyboard(count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Create document 📄",
                    callback_data="create_pdf",
                ),
                InlineKeyboardButton(
                    text="Delete last image 🗑",
                    callback_data="delete_last",
                ),
            ]
        ]
    )


async def update_summary(chat_id: int):
    """
    Всегда отправляет НОВОЕ сообщение с счётчиком
    и удаляет старое, если оно есть.
    """
    session = user_sessions.get(chat_id)
    if not session:
        return

    count = len(session["images"])
    old_msg_id = session.get("msg_id")

    if count == 0:
        # Если больше нет картинок — удалить старое сообщение и обнулить msg_id
        if old_msg_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=old_msg_id)
            except Exception:
                pass
        session["msg_id"] = None
        return

    # Сначала отправляем новое сообщение
    new_msg = await bot.send_message(
        chat_id,
        build_summary_text(count),
        reply_markup=build_keyboard(count),
    )
    session["msg_id"] = new_msg.message_id

    # Потом удаляем старое, если было
    if old_msg_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_msg_id)
        except Exception as e:
            print("Delete old summary error:", e)


# =============== ОБРАБОТКА ФОТО ===============

@dp.message(F.photo | (F.document & F.document.mime_type.startswith("image/")))
async def handle_image(message: Message):
    chat_id = message.chat.id

    if message.photo:
        file_id = message.photo[-1].file_id
    else:
        file_id = message.document.file_id

    if chat_id not in user_sessions:
        user_sessions[chat_id] = {"images": [], "msg_id": None}
    session = user_sessions[chat_id]

    media_group_id = message.media_group_id

    if media_group_id:
        # Альбом — копим отдельно, потом разом добавим
        key = (chat_id, media_group_id)
        if key not in album_sessions:
            album_sessions[key] = []
            asyncio.create_task(process_album(chat_id, media_group_id))
        album_sessions[key].append(file_id)
    else:
        # Одиночная картинка
        session["images"].append(file_id)
        await update_summary(chat_id)


async def process_album(chat_id: int, media_group_id: str):
    await asyncio.sleep(2)  # ждём, пока приедут все фотки альбома

    key = (chat_id, media_group_id)
    file_ids = album_sessions.pop(key, [])

    if not file_ids:
        return

    if chat_id not in user_sessions:
        user_sessions[chat_id] = {"images": [], "msg_id": None}
    session = user_sessions[chat_id]

    session["images"].extend(file_ids)
    await update_summary(chat_id)


# =============== КНОПКА delete_last ===============

@dp.callback_query(F.data == "delete_last")
async def delete_last_image(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    session = user_sessions.get(chat_id)

    if not session or not session["images"]:
        await callback.answer("There are no images to delete.", show_alert=True)
        return

    session["images"].pop()

    if len(session["images"]) == 0:
        # Удаляем последнее сообщение и очищаем сессию
        old_msg_id = session.get("msg_id")
        if old_msg_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=old_msg_id)
            except Exception as e:
                print("Delete summary after all removed error:", e)
        user_sessions.pop(chat_id, None)
        await callback.message.answer(
            "All images have been removed. Send a new image to start again."
        )
    else:
        await update_summary(chat_id)

    await callback.answer()


# =============== КНОПКА create_pdf ===============

@dp.callback_query(F.data == "create_pdf")
async def create_pdf(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    session = user_sessions.get(chat_id)

    if not session or not session["images"]:
        await callback.answer("No images to create a document.", show_alert=True)
        return

    await callback.answer("Creating PDF...")

    images_ids = list(session["images"])
    msg_id = session.get("msg_id")

    wait_msg = await callback.message.reply(
        "Your document is being created, please wait a moment ⏰"
    )

    try:
        images: list[Image.Image] = []

        for file_id in images_ids:
            file = await bot.get_file(file_id)
            downloaded = await bot.download_file(file.file_path)
            img = Image.open(downloaded).convert("RGB")
            images.append(img)

        first = images[0]
        others = images[1:]

        pdf_io = io.BytesIO()
        if others:
            first.save(pdf_io, format="PDF", save_all=True, append_images=others)
        else:
            first.save(pdf_io, format="PDF")
        pdf_io.seek(0)

        pdf_bytes = pdf_io.getvalue()
        pdf_file = BufferedInputFile(pdf_bytes, filename="document.pdf")

        # Миниатюра
        thumb_img = first.copy()
        thumb_img.thumbnail((320, 320))
        thumb_io = io.BytesIO()
        thumb_img.save(thumb_io, format="JPEG", quality=85)
        thumb_io.seek(0)
        thumb_bytes = thumb_io.getvalue()
        thumb_file = BufferedInputFile(thumb_bytes, filename="thumb.jpg")

        # Удаляем последнее сообщение-счётчик
        if msg_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception as e:
                print("Delete summary before send pdf error:", e)

        await wait_msg.delete()

        await callback.message.answer_document(
            pdf_file,
            thumbnail=thumb_file,
        )

    except Exception as e:
        print("Create PDF error:", e)
        try:
            await wait_msg.edit_text("Error while creating the PDF.")
        except Exception:
            pass
    finally:
        user_sessions.pop(chat_id, None)


# =============== ФОЛБЭК ===============

@dp.message()
async def fallback(message: Message):
    await message.answer(
        "Send me images (single or album), then press “Create document” to get PDF."
    )


# =============== ЗАПУСК ===============

async def main():
    web_task = asyncio.create_task(start_web_server())
    bot_task = asyncio.create_task(dp.start_polling(bot))
    await asyncio.gather(web_task, bot_task)


if __name__ == "__main__":
    asyncio.run(main())


