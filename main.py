import asyncio
import io

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

# user_id -> {"images": [file_id, ...], "names": [orig_name_or_None, ...], "msg_id": int, "chat_id": int}
user_sessions: dict[int, dict] = {}


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

    # Detect file_id and original name
    if message.photo:
        photo = message.photo[-1]
        file_id = photo.file_id
        orig_name = None  # у фото в Telegram нет имени файла, потом возьмём из file_path
    else:
        doc = message.document
        file_id = doc.file_id
        orig_name = doc.file_name or None  # тут нормальное имя файла

    session = user_sessions.get(user_id)

    if session is None:
        # First image — create session and message
        msg = await message.answer(
            build_summary_text(1),
            reply_markup=build_keyboard(1),
        )
        user_sessions[user_id] = {
            "images": [file_id],
            "names": [orig_name],
            "msg_id": msg.message_id,
            "chat_id": msg.chat.id,
        }
    else:
        # Add another image
        session["images"].append(file_id)
        session["names"].append(orig_name)
        count = len(session["images"])

        old_msg_id = session["msg_id"]

        # Send NEW summary message with updated counter
        new_msg = await message.answer(
            build_summary_text(count),
            reply_markup=build_keyboard(count),
        )

        session["msg_id"] = new_msg.message_id
        session["chat_id"] = new_msg.chat.id

        # Delete old summary message
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

    # Remove last image + имя
    session["images"].pop()
    if "names" in session and session["names"]:
        session["names"].pop()

    count = len(session["images"])
    old_msg_id = session["msg_id"]
    chat_id = session["chat_id"]

    if count == 0:
        # Session empty — reset everything
        user_sessions.pop(user_id, None)
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_msg_id)
        except Exception as e:
            print("Delete summary after all removed error:", e)

        await callback.message.answer(
            "All images have been removed. Send a new image to start again."
        )
    else:
        # Send updated summary message
        new_msg = await callback.message.answer(
            build_summary_text(count),
            reply_markup=build_keyboard(count),
        )
        session["msg_id"] = new_msg.message_id
        session["chat_id"] = new_msg.chat.id

        # Delete old summary
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_msg_id)
        except Exception as e:
            print("Delete old summary after delete_last error:", e)

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
    names = session.get("names", [])
    chat_id = session["chat_id"]
    msg_id = session["msg_id"]

    # Waiting message
    wait_msg = await callback.message.reply(
        "Your document is being created, please wait a moment ⏰"
    )

    try:
        images: list[Image.Image] = []

        # Сначала получаем мету по файлам
        files_meta = []
        for file_id in images_ids:
            f = await bot.get_file(file_id)
            files_meta.append(f)

        # ---------- ГЕНЕРАЦИЯ ИМЕНИ PDF ----------
        pdf_filename = "document.pdf"

        # 1) пробуем взять из оригинального имени первой картинки/дока
        base_name = None
        if names and names[0]:
            base_name = names[0].rsplit(".", 1)[0]

        # 2) если имя не пришло (фото), берём из file_path
        if not base_name:
            file_path = files_meta[0].file_path or ""
            if file_path:
                base_name = file_path.split("/")[-1].rsplit(".", 1)[0]
            else:
                base_name = "document"

        pdf_filename = base_name + ".pdf"
        # -----------------------------------------

        # Download all images
        for f in files_meta:
            downloaded = await bot.download_file(f.file_path)
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
        pdf_file = BufferedInputFile(pdf_bytes, filename=pdf_filename)

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
