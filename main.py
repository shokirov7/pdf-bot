import asyncio
import io
import os
import threading
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
BOT_TOKEN = "8204701331:AAGk63tYGLDBqSHkCs3Z-e_h3cFUy7bqkGQ"

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=None))
dp = Dispatcher()

# user_id -> {"images": [file_id, ...], "msg_id": int, "chat_id": int}
user_sessions: dict[int, dict] = {}


# =====================================
# ✅ ФЕЙКОВЫЙ ВЕБ-СЕРВЕР (ОТКЛЮЧАЕТ ПОИСК ПОРТОВ НА RENDER)
# =====================================
async def handle(request):
    return web.Response(text="Bot is running")

def start_fake_server():
    app = web.Application()
    app.router.add_get("/", handle)

    port = int(os.environ.get("PORT", 8080))
    web.run_app(app, port=port)

threading.Thread(target=start_fake_server, daemon=True).start()
# =====================================



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
    user_id = message.from_user.id

    if message.photo:
        file_id = message.photo[-1].file_id
    else:
        file_id = message.document.file_id

    session = user_sessions.get(user_id)

    if session is None:
        msg = await message.answer(
            build_summary_text(1),
            reply_markup=build_keyboard(1),
        )
        user_sessions[user_id] = {
            "images": [file_id],
            "msg_id": msg.message_id,
            "chat_id": msg.chat.id,
        }
    else:
        session["images"].append(file_id)
        count = len(session["images"])
        old_msg_id = session["msg_id"]

        new_msg = await message.answer(
            build_summary_text(count),
            reply_markup=build_keyboard(count),
        )

        session["msg_id"] = new_msg.message_id
        session["chat_id"] = new_msg.chat.id

        try:
            await bot.delete_message(chat_id=message.chat.id, message_id=old_msg_id)
        except Exception as e:
            print("Delete old summary error:", e)


@dp.callback_query(F.data == "delete_last")
async def delete_last_image(callback: CallbackQuery):
    user_id = callback.from_user.id
    session = user_sessions.get(user_id)

    if not session or not session["images"]:
        await callback.answer("There are no images to delete.", show_alert=True)
        return

    session["images"].pop()
    count = len(session["images"])
    old_msg_id = session["msg_id"]
    chat_id = session["chat_id"]

    if count == 0:
        user_sessions.pop(user_id, None)
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_msg_id)
        except:
            pass

        await callback.message.answer(
            "All images have been removed. Send a new image to start again."
        )
    else:
        new_msg = await callback.message.answer(
            build_summary_text(count),
            reply_markup=build_keyboard(count),
        )
        session["msg_id"] = new_msg.message_id
        session["chat_id"] = new_msg.chat.id

        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_msg_id)
        except:
            pass

    await callback.answer()


@dp.callback_query(F.data == "create_pdf")
async def create_pdf(callback: CallbackQuery):
    user_id = callback.from_user.id
    session = user_sessions.get(user_id)

    if not session or not session["images"]:
        await callback.answer("No images to create a document.", show_alert=True)
        return

    await callback.answer("Creating PDF...")

    images_ids = session["images"]
    chat_id = session["chat_id"]
    msg_id = session["msg_id"]

    wait_msg = await callback.message.reply(
        "Your document is being created, please wait ⏰"
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

        pdf_file = BufferedInputFile(pdf_io.getvalue(), filename="document.pdf")

        thumb = first.copy()
        thumb.thumbnail((320, 320))
        thumb_io = io.BytesIO()
        thumb.save(thumb_io, "JPEG", quality=80)
        thumb_io.seek(0)
        thumb_file = BufferedInputFile(thumb_io.getvalue(), filename="thumb.jpg")

        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except:
            pass

        await wait_msg.delete()

        await callback.message.answer_document(pdf_file, thumbnail=thumb_file)

    except Exception as e:
        print("PDF error:", e)
        try:
            await wait_msg.edit_text("Error while creating PDF.")
        except:
            pass
    finally:
        user_sessions.pop(user_id, None)


@dp.message()
async def fallback(message: Message):
    await message.answer(
        "Send me images and I will prepare them for PDF creation."
    )


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
