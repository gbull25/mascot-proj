import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputFile, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils import executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

from client import ComfyUIClient
import time


API_TOKEN = "6313156647:AAFViiJybWKkqU5OKFPNTaFrGDD8Nge87ms"

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())

# Состояния для FSM
class Form(StatesGroup):
    waiting_for_prompt = State()
    waiting_for_image = State()
    waiting_for_multi_images = State()

# Постоянная клавиатура
main_reply_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("Изменить промт"), KeyboardButton("Текущий промпт")],
        [KeyboardButton("Загрузить картинку"), KeyboardButton("Загрузить до 5 фото")],
        [KeyboardButton("Сгенерировать"), KeyboardButton("Пакетная генерация")],
    ],
    resize_keyboard=True
)

# Хранилище для промта и картинки (на пользователя)
user_data = {}

# Для хранения до 5 фото на пользователя
multi_image_data = {}

# Буфер для хранения фото по media_group_id
media_group_buffers = {}

# Для хранения времени старта batch для каждого пользователя
batch_start_times = {}

# Для хранения времени последнего фото в группе
media_group_last_time = {}

def ensure_user_data(user_id: int):
    """Ensure user data is initialized for the given user ID"""
    if user_id not in user_data:
        user_data[user_id] = {
            "prompt": "make person and mascot pose for a selfie shot together in the beautiful city centre",
            "image_path": None
        }

@dp.message_handler(commands="start")
async def cmd_start(message: types.Message):
    ensure_user_data(message.from_user.id)
    await message.answer(
        "Привет! Я бот для генерации изображений.\nВыберите действие:",
        reply_markup=main_reply_kb
    )

@dp.message_handler(lambda m: m.text == "Изменить промт")
async def process_change_prompt(message: types.Message):
    await Form.waiting_for_prompt.set()
    await message.answer("Введите новый промт:")

@dp.message_handler(state=Form.waiting_for_prompt)
async def process_prompt_input(message: types.Message, state: FSMContext):
    ensure_user_data(message.from_user.id)
    user_data[message.from_user.id]["prompt"] = message.text
    await state.finish()
    await message.answer("Промт обновлён!", reply_markup=main_reply_kb)

@dp.message_handler(lambda m: m.text == "Загрузить картинку")
async def process_upload_image(message: types.Message):
    await Form.waiting_for_image.set()
    await message.answer("Отправьте одну картинку (jpg/png):", reply_markup=main_reply_kb)

@dp.message_handler(content_types=types.ContentType.PHOTO, state=Form.waiting_for_image)
async def process_image_input(message: types.Message, state: FSMContext):
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    file_path = file.file_path
    local_path = f"temp_{message.from_user.id}.jpg"
    await bot.download_file(file_path, local_path)
    ensure_user_data(message.from_user.id)
    user_data[message.from_user.id]["image_path"] = local_path  # всегда перезаписывать
    await state.finish()
    await message.answer("Картинка загружена!", reply_markup=main_reply_kb)

@dp.message_handler(lambda m: m.text == "Загрузить до 5 фото")
async def process_upload_multi(message: types.Message):
    multi_image_data[message.from_user.id] = []
    await Form.waiting_for_multi_images.set()
    await message.answer("Отправьте альбом (media group) из 2-5 фотографий (jpg/png) одним сообщением.", reply_markup=main_reply_kb)

@dp.message_handler(content_types=types.ContentType.PHOTO, state=Form.waiting_for_multi_images)
async def handle_media_group_photo(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    mgid = message.media_group_id
    if not mgid:
        await message.answer("Пожалуйста, отправьте именно альбом (media group) из нескольких фото одним сообщением.", reply_markup=main_reply_kb)
        return

    # Инициализация буфера для media_group_id
    if mgid not in media_group_buffers:
        media_group_buffers[mgid] = []
    # Сохраняем время последнего фото
    media_group_last_time[mgid] = time.time()

    # Сохраняем фото во временный буфер
    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    local_path = f"multi_{user_id}_{int(time.time()*1000)}.jpg"
    await message.bot.download_file(file.file_path, local_path)
    media_group_buffers[mgid].append(local_path)

    # Запускаем отложенную обработку (если уже не запущена)
    async def finalize_media_group(mgid_local, user_id_local, state_local):
        await asyncio.sleep(2.0)  # Ждем, вдруг еще фото придут
        # Проверяем, что буфер еще существует
        if mgid_local not in media_group_last_time or mgid_local not in media_group_buffers:
            return
        # Если новых фото не было 2 секунды — считаем альбом завершенным
        if time.time() - media_group_last_time[mgid_local] >= 2.0:
            multi_image_data[user_id_local] = media_group_buffers[mgid_local][:5]
            del media_group_buffers[mgid_local]
            del media_group_last_time[mgid_local]
            await state_local.finish()
            await message.answer(
                f"Загружено {len(multi_image_data[user_id_local])} фото. Теперь нажмите 'Пакетная генерация'.",
                reply_markup=main_reply_kb
            )

    asyncio.create_task(finalize_media_group(mgid, user_id, state))

@dp.message_handler(lambda m: m.text == "Сгенерировать")
async def process_generate(message: types.Message):
    ensure_user_data(message.from_user.id)
    data = user_data.get(message.from_user.id)
    if not data or not data.get("image_path"):
        await message.answer("Сначала загрузите картинку!", reply_markup=main_reply_kb)
        return
    
    prompt = data["prompt"]
    image_path = data["image_path"]
    
    await message.answer("Генерирую изображение, подождите...", reply_markup=main_reply_kb)
    
    start_time = time.time()
    result_path = await ComfyUIClient.generate_with_comfyui(
        prompt, 
        image_path
    )
    elapsed = time.time() - start_time
    
    if result_path:
        await message.answer_photo(
            InputFile(result_path),
            caption=f"Время генерации: {elapsed:.1f} сек.",
            reply_markup=main_reply_kb
        )
    else:
        await message.answer("Ошибка генерации.", reply_markup=main_reply_kb)

@dp.message_handler(lambda m: m.text == "Пакетная генерация")
async def process_batch_generate(message: types.Message):
    user_id = message.from_user.id
    ensure_user_data(user_id)
    prompt = user_data.get(user_id, {}).get("prompt", "Ваш промт по умолчанию")
    images = multi_image_data.get(user_id, [])
    if not images:
        await message.answer("Сначала загрузите фото через 'Загрузить до 5 фото' (альбомом одним сообщением).", reply_markup=main_reply_kb)
        return
    
    await message.answer(f"Запускаю пакетную генерацию для {len(images)} фото...", reply_markup=main_reply_kb)
    
    batch_start_times[user_id] = time.time()
    for idx, image_path in enumerate(images, 1):
        start_time = time.time()
        result_path = await ComfyUIClient.generate_with_comfyui(
            prompt, 
            image_path
        )
        elapsed = time.time() - start_time
        batch_elapsed = time.time() - batch_start_times[user_id]
        if result_path:
            await message.answer_photo(
                InputFile(result_path),
                caption=f"Фото {idx}: Время генерации: {elapsed:.1f} сек.\nВремя от получения группы: {batch_elapsed:.1f} сек.",
                reply_markup=main_reply_kb
            )
        else:
            await message.answer(f"Фото {idx}: Ошибка генерации.", reply_markup=main_reply_kb)
    multi_image_data[user_id] = []
    batch_start_times.pop(user_id, None)

# Обработка кнопки "Текущий промпт"
@dp.message_handler(lambda m: m.text == "Текущий промпт")
async def show_current_prompt(message: types.Message):
    ensure_user_data(message.from_user.id)
    prompt = user_data.get(message.from_user.id, {}).get("prompt", "Промт не установлен.")
    await message.answer(f"Ваш текущий промпт:\n{prompt}", reply_markup=main_reply_kb)

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)