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
BOT_TOKEN = "8204701331:AAFIbq9WjX9gmy_JQ3cgoTBGGU9v4zKK5Fo"

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=None))
dp = Dispatcher()

# ✅ Теперь сессии по chat_id, а не по user_id
# chat_id -> {"images": [file_id, ...], "msg_id": int}
user_sessions: dict[int, dict] = {}


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


# =============== ЛОГИКА БОТА ===============

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


@dp.message(F.photo | (F.document & F.document.mime_type.startswith("image/")))
async def handle_image(message: Message):
    chat_id = message.chat.id

    # Detect file_id
    if message.photo:
        photo = message.photo[-1]
        file_id = photo.file_id
    else:
        doc = message.document
        file_id = doc.file_id

    session = user_sessions.get(chat_id)

    if session is None:
        # Первая картинка в этом чате
        msg = await message.answer(
            build_summary_text(1),
            reply_markup=build_keyboard(1),
        )
        user_sessions[chat_id] = {
            "images": [file_id],
            "msg_id": msg.message_id,
        }
    else:
        # Добавляем картинку в уже существующую сессию
        session["images"].append(file_id)
        count = len(session["images"])
        old_msg_id = session["msg_id"]

        # Новое сообщение с обновлённым количеством
        new_msg = await message.answer(
            build_summary_text(count),
            reply_markup=build_keyboard(count),
        )

        session["msg_id"] = new_msg.message_id

        # Удаляем старое сообщение-суммарку
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_msg_id)
        except Exception as e:
            print("Delete old summary error:", e)


@dp.callback_query(F.data == "delete_last")
async def delete_last_image(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    session = user_sessions.get(chat_id)

    if not session or not session["images"]:
        await callback.answer("There are no images to delete.", show_alert=True)
        return

    # Remove last image
    session["images"].pop()
    count = len(session["images"])
    old_msg_id = session["msg_id"]

    if count == 0:
        # Сессия пустая — очищаем
        user_sessions.pop(chat_id, None)
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_msg_id)
        except Exception as e:
            print("Delete summary after all removed error:", e)

        await callback.message.answer(
            "All images have been removed. Send a new image to start again."
        )
    else:
        # Обновляем суммарку
        new_msg = await callback.message.answer(
            build_summary_text(count),
            reply_markup=build_keyboard(count),
        )
        session["msg_id"] = new_msg.message_id

        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_msg_id)
        except Exception as e:
            print("Delete old summary after delete_last error:", e)

    await callback.answer()


@dp.callback_query(F.data == "create_pdf")
async def create_pdf(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    session = user_sessions.get(chat_id)

    if not session or not session["images"]:
        await callback.answer("No images to create a document.", show_alert=True)
        return

    await callback.answer("Creating PDF...")

    images_ids = session["images"]
    msg_id = session["msg_id"]

    # Waiting message
    wait_msg = await callback.message.reply(
        "Your document is being created, please wait a moment ⏰"
    )

    try:
        images: list[Image.Image] = []

        # Download all images
        for file_id in images_ids:
            file = await bot.get_file(file_id)
            downloaded = await bot.download_file(file.file_path)
            img = Image.open(downloaded).convert("RGB")
            images.append(img)

        first = images[0]
        others = images[1:]

        # Build PDF
        pdf_io = io.BytesIO()
        if others:
            first.save(pdf_io, format="PDF", save_all=True, append_images=others)
        else:
            first.save(pdf_io, format="PDF")
        pdf_io.seek(0)

        pdf_bytes = pdf_io.getvalue()
        pdf_file = BufferedInputFile(pdf_bytes, filename="document.pdf")

        # Thumbnail
        thumb_img = first.copy()
        thumb_img.thumbnail((320, 320))
        thumb_io = io.BytesIO()
        thumb_img.save(thumb_io, format="JPEG", quality=85)
        thumb_io.seek(0)
        thumb_bytes = thumb_io.getvalue()
        thumb_file = BufferedInputFile(thumb_bytes, filename="thumb.jpg")

        # Delete summary message
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


@dp.message()
async def fallback(message: Message):
    await message.answer(
        "Send me images and I will prepare them for PDF creation."
    )


async def main():
    # одновременно поднимаем HTTP-сервер и бота
    web_task = asyncio.create_task(start_web_server())
    bot_task = asyncio.create_task(dp.start_polling(bot))
    await asyncio.gather(web_task, bot_task)


if __name__ == "__main__":
    asyncio.run(main())

