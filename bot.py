import asyncio
import logging
import os
import re
import shutil
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, \
    InlineKeyboardButton, ReplyKeyboardRemove, FSInputFile, InputMediaPhoto
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import aiohttp
from config import (
    get_stage_name,
    get_stage_emoji,
    format_date,
    format_price,
    parse_bitrix_money_with_currency,
    clean_phone,
    BITRIX_FIELDS,
    format_name,
    get_category_name
)

# ====== НАСТРОЙКИ ======
BOT_TOKEN = "8258111612:AAEmqjXRxRlcKAuiBDgLilOOBlz_CmLvmIg"
BITRIX_WEBHOOK = "https://sunway24.bitrix24.ru/rest/326/fiwux7q90yclt8l1/"
ADMIN_IDS = [785219206, 1291085389]

# Директории для файлов
INVOICES_DIR = "invoices"
PHOTOS_DIR = "product_photos"

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# FSM States
class RegistrationStates(StatesGroup):
    waiting_for_phone = State()


class AdminStates(StatesGroup):
    waiting_phone = State()
    waiting_deal_selection = State()
    waiting_document_type = State()
    waiting_invoice = State()
    waiting_photos = State()


# Инициализация
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# База данных
user_phones = {}


# Проверка админа
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ====== ФУНКЦИИ ДЛЯ РАБОТЫ С БИТРИКС ======

async def bitrix_request(method: str, params: dict = None):
    """Универсальный запрос к Битрикс24"""
    url = f"{BITRIX_WEBHOOK}{method}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=params or {}) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('result', [])
                else:
                    text = await response.text()
                    logger.error(f"Bitrix error {response.status}: {text}")
                    return None
    except Exception as e:
        logger.error(f"Request error: {e}")
        return None


async def find_client_by_phone(phone: str):
    """Поиск клиента в Битрикс по телефону"""
    cleaned_phone = clean_phone(phone)
    phone_variants = [
        cleaned_phone,
        f"+{cleaned_phone}",
        f"8{cleaned_phone[1:]}",
    ]
    for variant in phone_variants:
        params = {
            'filter': {'PHONE': variant},
            'select': ['ID', 'NAME', 'LAST_NAME', 'EMAIL', 'PHONE']  # ← КАК БЫЛО
        }
        result = await bitrix_request('crm.contact.list', params)
        if result:
            return result[0]
    return None


async def get_active_deals(client_id: str):
    """Получение активных заказов клиента"""
    params = {
        'filter': {
            'CONTACT_ID': client_id,
            'CLOSED': 'N'
        },
        'select': [
            'ID', 'TITLE', 'DATE_CREATE', 'STAGE_ID', 'OPPORTUNITY',
            BITRIX_FIELDS['client_id'],
            BITRIX_FIELDS['weight'],
            BITRIX_FIELDS['volume'],
            BITRIX_FIELDS['product_category'],
            BITRIX_FIELDS['expected_send_date'],
            BITRIX_FIELDS['expected_arrival_date'],
            BITRIX_FIELDS['insurance'],
            BITRIX_FIELDS['invoice_file'],
            BITRIX_FIELDS['product_photos'],
            BITRIX_FIELDS['invoice_cost']  # ✅ Убедитесь, что это поле здесь есть
        ]
    }
    return await bitrix_request('crm.deal.list', params) or []


async def get_archived_deals(client_id: str):
    """Получение завершенных заказов"""
    params = {
        'filter': {
            'CONTACT_ID': client_id,
            'CLOSED': 'Y'
        },
        'select': [
            'ID', 'TITLE', 'DATE_CREATE', 'DATE_MODIFY', 'STAGE_ID', 'OPPORTUNITY',
            'CURRENCY_ID',  # ← ДОБАВИТЬ ЭТО ПОЛЕ
            BITRIX_FIELDS['client_id'],
            BITRIX_FIELDS['weight'],
            BITRIX_FIELDS['volume'],
            BITRIX_FIELDS['product_category'],
            BITRIX_FIELDS['expected_send_date'],
            BITRIX_FIELDS['expected_arrival_date'],
            BITRIX_FIELDS['insurance'],
            BITRIX_FIELDS['invoice_cost']
        ]
    }
    return await bitrix_request('crm.deal.list', params) or []


async def get_deal_details(deal_id: str):
    """Детали конкретного заказа"""
    params = {
        'ID': deal_id,
        'select': [
            'ID', 'TITLE', 'DATE_CREATE', 'STAGE_ID', 'OPPORTUNITY',
            'CURRENCY_ID',  # ← ДОБАВИТЬ ЭТО ПОЛЕ
            BITRIX_FIELDS['client_id'],
            BITRIX_FIELDS['weight'],
            BITRIX_FIELDS['volume'],
            BITRIX_FIELDS['product_category'],
            BITRIX_FIELDS['expected_send_date'],
            BITRIX_FIELDS['expected_arrival_date'],
            BITRIX_FIELDS['insurance'],
            BITRIX_FIELDS['invoice_cost']
        ]
    }
    result = await bitrix_request('crm.deal.get', params)
    return result if result else None


async def get_deals_by_phone(phone: str):
    """Получить все сделки по номеру телефона"""
    client = await find_client_by_phone(phone)
    if not client:
        return None, None

    deals = await get_active_deals(client['ID'])
    return client, deals


async def send_invoice_to_client(deal_id: str, client_telegram_id: str):
    """Отправка накладной"""
    local_invoice = f"{INVOICES_DIR}/{deal_id}.pdf"
    if os.path.exists(local_invoice):
        try:
            doc = FSInputFile(local_invoice)
            await bot.send_document(
                client_telegram_id,
                doc,
                caption=f"📄 <b>Накладная для заказа #{deal_id}</b>\n\nВаша накладная готова!",
                parse_mode="HTML"
            )
            logger.info(f"✅ Накладная отправлена")
            return True
        except Exception as e:
            logger.error(f"Ошибка отправки накладной: {e}")
    return False


async def send_warehouse_photos(deal_id: str, client_telegram_id: str):
    """Отправка фото"""
    local_photos_dir = f"{PHOTOS_DIR}/{deal_id}"
    if os.path.exists(local_photos_dir):
        photos = os.listdir(local_photos_dir)
        if photos:
            try:
                for idx, photo_file in enumerate(sorted(photos)):
                    photo_path = f"{local_photos_dir}/{photo_file}"
                    photo = FSInputFile(photo_path)

                    caption = None
                    if idx == 0:
                        caption = f"📸 <b>Фото товара на складе</b>\n\nЗаказ #{deal_id}"

                    await bot.send_photo(
                        client_telegram_id,
                        photo,
                        caption=caption,
                        parse_mode="HTML"
                    )

                logger.info(f"✅ {len(photos)} фото отправлены")
                return True
            except Exception as e:
                logger.error(f"Ошибка отправки фото: {e}")
    return False


async def has_invoice(deal_id: str) -> bool:
    """Проверить, есть ли накладная"""
    return os.path.exists(f"{INVOICES_DIR}/{deal_id}.pdf")


async def has_photos(deal_id: str) -> bool:
    """Проверить, есть ли фото"""
    local_photos_dir = f"{PHOTOS_DIR}/{deal_id}"
    if os.path.exists(local_photos_dir):
        files = os.listdir(local_photos_dir)
        return len(files) > 0
    return False


async def notify_on_document_upload(deal_id: str, doc_type: str = "invoice", admin_id: int = None):
    """Автоматическое уведомление клиента при загрузке документа"""
    deal = await get_deal_details(deal_id)
    if not deal:
        return False

    contact_id = deal.get('CONTACT_ID')

    client_telegram_id = None
    for user_id, user_data in user_phones.items():
        if user_data.get('client_id') == str(contact_id):
            client_telegram_id = user_id
            break

    if client_telegram_id:
        if doc_type == "invoice":
            emoji = "📄"
            text = "Накладная готова!"
        else:
            emoji = "📸"
            text = "Фото товара загружены!"

        await bot.send_message(
            client_telegram_id,
            f"{emoji} <b>Уведомление по заказу #{deal_id}</b>\n\n"
            f"{text}\n"
            f"Перейдите в личный кабинет для просмотра.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📦 Мои заказы", callback_data="current_orders")]
            ]),
            parse_mode="HTML"
        )

        # Если это вызов от админа, отправляем ему уведомление с кнопкой возврата
        if admin_id:
            await bot.send_message(
                admin_id,
                f"✅ <b>Клиент уведомлен!</b>\n\n"
                f"Документ ({doc_type}) для заказа #{deal_id} отправлен клиенту.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Вернуться к заказу", callback_data=f"admin_deal_{deal_id}")]
                ]),
                parse_mode="HTML"
            )

        return True
    return False


async def safe_delete_message(message: Message):
    """Безопасное удаление сообщения"""
    try:
        await message.delete()
    except Exception as e:
        logger.debug(f"Не удалось удалить сообщение: {e}")


async def update_deal_menu(message: Message, deal_id: str, state: FSMContext):
    """Обновление меню заказа"""
    deal = await get_deal_details(deal_id)
    if not deal:
        return

    title = deal.get('TITLE', 'Без названия')
    has_invoice = os.path.exists(f"{INVOICES_DIR}/{deal_id}.pdf")

    photos_dir = f"{PHOTOS_DIR}/{deal_id}"
    photo_count = 0
    if os.path.exists(photos_dir):
        photo_count = len(os.listdir(photos_dir))
    has_photos = photo_count > 0

    text = (
        f"📦 <b>Заказ #{deal_id}</b>\n"
        f"📌 {title}\n\n"
        f"📄 Накладная: {'✅ Загружена' if has_invoice else '❌ Отсутствует'}\n"
        f"📸 Фото: {'✅ Загружено ' + str(photo_count) + ' шт.' if has_photos else '❌ Отсутствуют'}\n\n"
        f"Выберите действие:"
    )

    keyboard = []

    # Накладная
    if has_invoice:
        keyboard.append([
            InlineKeyboardButton(text="👁 Просмотр накладной", callback_data=f"admin_view_invoice_{deal_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin_delete_invoice_{deal_id}")
        ])
    else:
        keyboard.append([InlineKeyboardButton(text="➕ Добавить накладную", callback_data="admin_add_invoice")])

    # Фото
    if has_photos:
        keyboard.append([
            InlineKeyboardButton(text=f"👁 Просмотр фото ({photo_count})", callback_data=f"admin_view_photos_{deal_id}"),
            InlineKeyboardButton(text="➕ Добавить еще", callback_data="admin_add_photos")
        ])
        keyboard.append(
            [InlineKeyboardButton(text="🗑 Удалить все фото", callback_data=f"admin_delete_photos_{deal_id}")])
    else:
        keyboard.append([InlineKeyboardButton(text="➕ Добавить фото", callback_data="admin_add_photos")])

    keyboard.append([InlineKeyboardButton(text="🔙 К списку заказов", callback_data="admin_back_to_deals")])

    await message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="HTML"
    )


# ====== КЛАВИАТУРЫ ======

def get_phone_keyboard():
    """Клавиатура для запроса телефона"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Отправить номер телефона", request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    return keyboard


def get_main_menu():
    """Главное меню личного кабинета"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Текущие заказы", callback_data="current_orders")],
        [InlineKeyboardButton(text="📚 Архив заказов", callback_data="archive_orders")],
        [InlineKeyboardButton(text="💬 Консультация", callback_data="consultation")],
        [InlineKeyboardButton(text="👤 Профиль клиента", callback_data="profile")]
    ])
    return keyboard


def get_orders_keyboard_with_status(orders: list, prefix: str = "order"):
    """Клавиатура со списком заказов и статусом документов"""
    keyboard = []
    for order in orders:
        order_id = order.get('ID')
        date = format_date(order.get('DATE_CREATE', ''))
        title = order.get('TITLE', 'Без названия')

        # Ограничиваем длину названия
        if len(title) > 30:
            title = title[:27] + "..."

        # Проверяем наличие документов
        has_doc = os.path.exists(f"{INVOICES_DIR}/{order_id}.pdf")
        has_photo = os.path.exists(f"{PHOTOS_DIR}/{order_id}") and os.listdir(f"{PHOTOS_DIR}/{order_id}")

        # Формируем текст кнопки
        icons = ""
        if has_doc:
            icons += "📄"
        if has_photo:
            icons += "📸"

        text = f"{icons} {title} • {date}"

        keyboard.append([InlineKeyboardButton(text=text, callback_data=f"{prefix}_{order_id}")])

    keyboard.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


async def get_order_details_keyboard(deal_id: str):
    """Клавиатура для деталей заказа"""
    keyboard = []

    if await has_invoice(deal_id):
        keyboard.append([InlineKeyboardButton(text="📄 Скачать накладную", callback_data=f"invoice_{deal_id}")])

    # Проверяем наличие фото без привязки к стадии
    photos_dir = f"{PHOTOS_DIR}/{deal_id}"
    if os.path.exists(photos_dir):
        photo_count = len(os.listdir(photos_dir))
        if photo_count > 0:
            keyboard.append([InlineKeyboardButton(text=f"📸 Посмотреть фото ({photo_count} шт.)",
                                                  callback_data=f"photos_{deal_id}")])

    keyboard.append([InlineKeyboardButton(text="🔙 К списку заказов", callback_data="current_orders")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_back_button():
    """Кнопка назад"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")]
    ])


# ====== АДМИН КОМАНДЫ ======

@dp.message(Command("admin"))
async def admin_panel(message: Message, state: FSMContext):
    """Админ-панель - сразу запрос телефона"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет доступа к админ-панели")
        return

    # Сохраняем ID сообщения для последующего редактирования
    sent = await message.answer(
        "🔧 <b>Админ-панель</b>\n\n"
        "Введите номер телефона клиента:\n"
        "(Пример: 79001234567)",
        parse_mode="HTML"
    )
    await state.update_data(admin_message_id=sent.message_id)
    await state.set_state(AdminStates.waiting_phone)


@dp.message(AdminStates.waiting_phone)
async def admin_process_phone(message: Message, state: FSMContext):
    """Обработка телефона - показываем клиента и все его заказы"""
    phone = message.text.strip()

    # Удаляем сообщение с телефоном
    await safe_delete_message(message)

    # Очистка телефона от лишних символов
    phone = re.sub(r'[^\d+]', '', phone)

    # Получаем ID сообщения админки
    data = await state.get_data()
    admin_msg_id = data.get('admin_message_id')

    # Обновляем сообщение
    try:
        await bot.edit_message_text(
            "⏳ Ищу клиента...",
            chat_id=message.chat.id,
            message_id=admin_msg_id
        )
    except:
        sent = await message.answer("⏳ Ищу клиента...")
        await state.update_data(admin_message_id=sent.message_id)
        admin_msg_id = sent.message_id

    client, deals = await get_deals_by_phone(phone)

    if not client:
        await bot.edit_message_text(
            "❌ Клиент не найден\n\n"
            "Проверьте номер и попробуйте снова",
            chat_id=message.chat.id,
            message_id=admin_msg_id,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Новый поиск", callback_data="admin_new_search")]
            ])
        )
        return

    if not deals:
        await bot.edit_message_text(
            f"❌ У клиента {client.get('NAME', '')} {client.get('LAST_NAME', '')}\n"
            f"нет активных заказов",
            chat_id=message.chat.id,
            message_id=admin_msg_id,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Новый поиск", callback_data="admin_new_search")]
            ])
        )
        return

    # Сохраняем данные клиента
    await state.update_data(client=client, deals=deals, phone=phone)

    # Формируем информацию о клиенте
    text = (
        f"👤 <b>Клиент найден</b>\n"
        f"📝 {client.get('NAME', '')} {client.get('LAST_NAME', '')}\n"
        f"📱 {phone}\n\n"
        f"📦 <b>Активные заказы: {len(deals)}</b>\n\n"
        f"Выберите заказ:"
    )

    # Клавиатура с заказами
    keyboard = []
    for deal in deals:
        deal_id = deal.get('ID')
        title = deal.get('TITLE', 'Без названия')

        # Проверяем наличие документов
        has_invoice_icon = "✅📄" if os.path.exists(f"{INVOICES_DIR}/{deal_id}.pdf") else "❌📄"
        has_photo_icon = "✅📸" if os.path.exists(f"{PHOTOS_DIR}/{deal_id}") else "❌📸"

        # Ограничиваем длину названия
        if len(title) > 25:
            title = title[:22] + "..."

        text_button = f"#{deal_id} {has_invoice_icon}{has_photo_icon} {title}"
        keyboard.append([InlineKeyboardButton(text=text_button, callback_data=f"admin_deal_{deal_id}")])

    keyboard.append([InlineKeyboardButton(text="🔄 Новый поиск", callback_data="admin_new_search")])
    keyboard.append([InlineKeyboardButton(text="🚪 Выйти из админки", callback_data="admin_exit")])

    await bot.edit_message_text(
        text,
        chat_id=message.chat.id,
        message_id=admin_msg_id,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_deal_selection)


@dp.callback_query(F.data.startswith("admin_deal_"))
async def admin_select_deal(callback: CallbackQuery, state: FSMContext):
    """Выбор сделки - показываем меню действий"""
    # Проверяем, были ли загружены фото но не завершена загрузка
    current_state = await state.get_state()
    if current_state == AdminStates.waiting_photos:
        data = await state.get_data()
        photo_messages = data.get('photo_messages', [])
        # Удаляем незавершенные фото
        for msg_id in photo_messages:
            try:
                await bot.delete_message(callback.message.chat.id, msg_id)
            except:
                pass
        await state.update_data(photo_messages=[])

    deal_id = callback.data.split("_")[2]
    await state.update_data(deal_id=deal_id)

    await update_deal_menu(callback.message, deal_id, state)
    await callback.answer()


@dp.callback_query(F.data == "admin_add_invoice")
async def admin_add_invoice(callback: CallbackQuery, state: FSMContext):
    """Начало загрузки накладной"""
    data = await state.get_data()
    deal_id = data['deal_id']

    await callback.message.edit_text(
        f"📄 <b>Загрузка накладной</b>\n"
        f"Заказ #{deal_id}\n\n"
        f"Отправьте файл накладной (PDF или изображение)\n\n"
        f"Или нажмите кнопку для отмены:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin_deal_{deal_id}")]
        ]),
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_invoice)
    await callback.answer()


@dp.callback_query(F.data == "admin_add_photos")
async def admin_add_photos(callback: CallbackQuery, state: FSMContext):
    """Начало загрузки фото"""
    data = await state.get_data()
    deal_id = data['deal_id']

    await callback.message.edit_text(
        f"📸 <b>Загрузка фото товара</b>\n"
        f"Заказ #{deal_id}\n\n"
        f"Отправьте фото (можно несколько)\n"
        f"Нажмите кнопку когда закончите\n\n"
        f"Или нажмите кнопку для отмены:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Готово", callback_data="admin_photos_done")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin_deal_{deal_id}")]
        ]),
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_photos)
    await callback.answer()


@dp.callback_query(F.data.startswith("admin_view_invoice_"))
async def admin_view_invoice(callback: CallbackQuery, state: FSMContext):
    """Просмотр накладной админом"""
    deal_id = callback.data.split("_")[3]
    invoice_path = f"{INVOICES_DIR}/{deal_id}.pdf"

    if os.path.exists(invoice_path):
        await callback.answer("📄 Отправляю накладную...")
        try:
            doc = FSInputFile(invoice_path)
            await bot.send_document(
                callback.from_user.id,
                doc,
                caption=f"📄 <b>Накладная для заказа #{deal_id}</b>",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки накладной админу: {e}")
            await callback.answer("❌ Ошибка отправки накладной", show_alert=True)
    else:
        await callback.answer("❌ Накладная не найдена", show_alert=True)


@dp.callback_query(F.data.startswith("admin_view_photos_"))
async def admin_view_photos(callback: CallbackQuery, state: FSMContext):
    """Просмотр фото админом"""
    deal_id = callback.data.split("_")[3]
    photos_dir = f"{PHOTOS_DIR}/{deal_id}"

    if os.path.exists(photos_dir):
        photos = sorted(os.listdir(photos_dir))
        if photos:
            await callback.answer("📸 Отправляю фото...")
            try:
                # Отправляем фото как альбом
                media = []
                for idx, photo_file in enumerate(photos[:10]):
                    photo_path = f"{photos_dir}/{photo_file}"
                    photo = FSInputFile(photo_path)

                    if idx == 0:
                        media.append(InputMediaPhoto(
                            media=photo,
                            caption=f"📸 <b>Фото товара - Заказ #{deal_id}</b>\n\nВсего фото: {len(photos)}",
                            parse_mode="HTML"
                        ))
                    else:
                        media.append(InputMediaPhoto(media=photo))

                await bot.send_media_group(callback.from_user.id, media)

                # Если фото больше 10, отправляем остальные
                if len(photos) > 10:
                    for i in range(10, len(photos), 10):
                        batch = photos[i:i + 10]
                        media = []
                        for photo_file in batch:
                            photo_path = f"{photos_dir}/{photo_file}"
                            photo = FSInputFile(photo_path)
                            media.append(InputMediaPhoto(media=photo))
                        await bot.send_media_group(callback.from_user.id, media)

                logger.info(f"Отправлено {len(photos)} фото админу для заказа {deal_id}")
            except Exception as e:
                logger.error(f"Ошибка отправки фото админу: {e}")
                await callback.answer("❌ Ошибка отправки фото", show_alert=True)
        else:
            await callback.answer("❌ Фото не найдены", show_alert=True)
    else:
        await callback.answer("❌ Папка с фото не найдена", show_alert=True)


@dp.callback_query(F.data.startswith("admin_delete_invoice_"))
async def admin_delete_invoice(callback: CallbackQuery, state: FSMContext):
    """Удаление накладной"""
    deal_id = callback.data.split("_")[3]
    invoice_path = f"{INVOICES_DIR}/{deal_id}.pdf"

    if os.path.exists(invoice_path):
        os.remove(invoice_path)
        await callback.answer("✅ Накладная удалена")

        # Обновляем меню
        await update_deal_menu(callback.message, deal_id, state)
    else:
        await callback.answer("❌ Файл не найден", show_alert=True)


@dp.callback_query(F.data.startswith("admin_delete_photos_"))
async def admin_delete_photos(callback: CallbackQuery, state: FSMContext):
    """Удаление всех фото"""
    deal_id = callback.data.split("_")[3]
    photos_dir = f"{PHOTOS_DIR}/{deal_id}"

    if os.path.exists(photos_dir):
        photo_count = len(os.listdir(photos_dir))
        shutil.rmtree(photos_dir)
        await callback.answer(f"✅ Удалено {photo_count} фото")

        # Обновляем меню
        await update_deal_menu(callback.message, deal_id, state)
    else:
        await callback.answer("❌ Папка не найдена", show_alert=True)


@dp.callback_query(F.data == "admin_new_search")
async def admin_new_search(callback: CallbackQuery, state: FSMContext):
    """Новый поиск клиента"""
    await state.clear()
    await callback.message.edit_text(
        "🔧 <b>Админ-панель</b>\n\n"
        "Введите номер телефона клиента:\n"
        "(Пример: 79001234567)",
        parse_mode="HTML"
    )
    await state.update_data(admin_message_id=callback.message.message_id)
    await state.set_state(AdminStates.waiting_phone)
    await callback.answer()


@dp.callback_query(F.data == "admin_exit")
async def admin_exit(callback: CallbackQuery, state: FSMContext):
    """Выход из админ-панели"""
    await state.clear()
    await callback.message.edit_text(
        "👋 <b>Вы вышли из админ-панели</b>\n\n"
        "Для возврата используйте /admin\n"
        "Для личного кабинета используйте /start",
        parse_mode="HTML"
    )
    await callback.answer("Вышли из админки")


@dp.callback_query(F.data == "admin_back_to_deals")
async def admin_back_to_deals(callback: CallbackQuery, state: FSMContext):
    """Вернуться к списку заказов"""
    data = await state.get_data()
    client = data.get('client')
    deals = data.get('deals')
    phone = data.get('phone')

    if not client or not deals:
        await callback.answer("❌ Ошибка, начните заново /admin", show_alert=True)
        return

    # Формируем информацию о клиенте
    text = (
        f"👤 <b>Клиент</b>\n"
        f"📝 {client.get('NAME', '')} {client.get('LAST_NAME', '')}\n"
        f"📱 {phone}\n\n"
        f"📦 <b>Активные заказы: {len(deals)}</b>\n\n"
        f"Выберите заказ:"
    )

    # Клавиатура с заказами
    keyboard = []
    for deal in deals:
        deal_id = deal.get('ID')
        title = deal.get('TITLE', 'Без названия')

        # Проверяем наличие документов
        has_invoice = "✅📄" if os.path.exists(f"{INVOICES_DIR}/{deal_id}.pdf") else "❌📄"
        has_photo = "✅📸" if os.path.exists(f"{PHOTOS_DIR}/{deal_id}") else "❌📸"

        # Ограничиваем длину названия
        if len(title) > 25:
            title = title[:22] + "..."

        text_button = f"#{deal_id} {has_invoice}{has_photo} {title}"
        keyboard.append([InlineKeyboardButton(text=text_button, callback_data=f"admin_deal_{deal_id}")])

    keyboard.append([InlineKeyboardButton(text="🔄 Новый поиск", callback_data="admin_new_search")])
    keyboard.append([InlineKeyboardButton(text="🚪 Выйти из админки", callback_data="admin_exit")])

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_deal_selection)
    await callback.answer()


@dp.message(AdminStates.waiting_invoice, F.document)
async def admin_process_invoice(message: Message, state: FSMContext):
    """Обработка файла накладной"""
    data = await state.get_data()
    deal_id = data['deal_id']
    admin_msg_id = data.get('admin_message_id')

    # Удаляем сообщение с файлом
    await safe_delete_message(message)

    os.makedirs(INVOICES_DIR, exist_ok=True)

    document = message.document
    file_path = f"{INVOICES_DIR}/{deal_id}.pdf"

    file = await bot.get_file(document.file_id)
    await bot.download_file(file.file_path, file_path)
    logger.info(f"Накладная сохранена: {file_path}")

    # Автоматическое уведомление клиента
    await notify_on_document_upload(deal_id, "invoice", message.from_user.id)

    # Обновляем админское сообщение с подтверждением и меню заказа
    if admin_msg_id:
        deal = await get_deal_details(deal_id)
        if deal:
            title = deal.get('TITLE', 'Без названия')
            photos_dir = f"{PHOTOS_DIR}/{deal_id}"
            photo_count = 0
            if os.path.exists(photos_dir):
                photo_count = len(os.listdir(photos_dir))
            has_photos = photo_count > 0

            text = (
                f"✅ <b>Накладная успешно загружена!</b>\n\n"
                f"📦 <b>Заказ #{deal_id}</b>\n"
                f"📌 {title}\n\n"
                f"📄 Накладная: ✅ Загружена\n"
                f"📸 Фото: {'✅ Загружено ' + str(photo_count) + ' шт.' if has_photos else '❌ Отсутствуют'}\n\n"
                f"Клиент получил уведомление.\n\n"
                f"Выберите действие:"
            )

            keyboard = []
            keyboard.append([
                InlineKeyboardButton(text="👁 Просмотр накладной", callback_data=f"admin_view_invoice_{deal_id}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin_delete_invoice_{deal_id}")
            ])

            if not has_photos:
                keyboard.append([InlineKeyboardButton(text="➕ Добавить фото", callback_data="admin_add_photos")])
            else:
                keyboard.append([
                    InlineKeyboardButton(text=f"👁 Просмотр фото ({photo_count})",
                                         callback_data=f"admin_view_photos_{deal_id}"),
                    InlineKeyboardButton(text="➕ Добавить еще", callback_data="admin_add_photos")
                ])
                keyboard.append(
                    [InlineKeyboardButton(text="🗑 Удалить все фото", callback_data=f"admin_delete_photos_{deal_id}")])

            keyboard.append([InlineKeyboardButton(text="🔙 К списку заказов", callback_data="admin_back_to_deals")])

            try:
                await bot.edit_message_text(
                    text,
                    chat_id=message.chat.id,
                    message_id=admin_msg_id,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                    parse_mode="HTML"
                )
            except:
                # Если не удалось отредактировать, отправляем новое
                await message.answer(
                    text,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                    parse_mode="HTML"
                )


@dp.message(AdminStates.waiting_photos, F.photo)
async def admin_process_photo(message: Message, state: FSMContext):
    """Обработка фото товара"""
    data = await state.get_data()
    deal_id = data['deal_id']
    admin_msg_id = data.get('admin_message_id')

    # НЕ удаляем сообщение с фото сразу - сохраняем ID для последующего удаления
    photo_messages = data.get('photo_messages', [])
    photo_messages.append(message.message_id)
    await state.update_data(photo_messages=photo_messages)

    deal_photos_dir = f"{PHOTOS_DIR}/{deal_id}"
    os.makedirs(deal_photos_dir, exist_ok=True)
    logger.info(f"Сохраняем фото в: {deal_photos_dir}")

    # Получаем список существующих фото
    existing_photos = os.listdir(deal_photos_dir)
    photo_index = len(existing_photos) + 1

    photo = message.photo[-1]
    file_path = f"{deal_photos_dir}/photo_{photo_index:03d}.jpg"  # Используем 03d для правильной сортировки

    file = await bot.get_file(photo.file_id)
    await bot.download_file(file.file_path, file_path)
    logger.info(f"Фото сохранено: {file_path}")

    # Обновляем счетчик в сообщении
    if admin_msg_id:
        try:
            total_photos = len(os.listdir(deal_photos_dir))
            logger.info(f"Всего фото в папке: {total_photos}")
            await bot.edit_message_text(
                f"📸 <b>Загрузка фото товара</b>\n"
                f"Заказ #{deal_id}\n\n"
                f"✅ Загружено фото: {total_photos}\n\n"
                f"Отправьте еще фото или нажмите кнопку:",
                chat_id=message.chat.id,
                message_id=admin_msg_id,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Готово", callback_data="admin_photos_done")],
                    [InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin_deal_{deal_id}")]
                ]),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка обновления сообщения: {e}")


@dp.callback_query(F.data == "admin_photos_done")
async def admin_photos_done(callback: CallbackQuery, state: FSMContext):
    """Завершение загрузки фото через кнопку"""
    data = await state.get_data()
    deal_id = data['deal_id']
    photo_messages = data.get('photo_messages', [])

    # Удаляем все сообщения с фото
    for msg_id in photo_messages:
        try:
            await bot.delete_message(callback.message.chat.id, msg_id)
        except:
            pass

    # Очищаем список сообщений с фото
    await state.update_data(photo_messages=[])

    # Подсчитываем загруженные фото
    photos_dir = f"{PHOTOS_DIR}/{deal_id}"
    photo_count = 0
    if os.path.exists(photos_dir):
        photo_count = len(os.listdir(photos_dir))

    # Автоматическое уведомление клиента
    await notify_on_document_upload(deal_id, "photos", callback.from_user.id)

    # Обновляем сообщение с подтверждением и возвращаемся к меню заказа
    deal = await get_deal_details(deal_id)
    if deal:
        title = deal.get('TITLE', 'Без названия')
        has_invoice = os.path.exists(f"{INVOICES_DIR}/{deal_id}.pdf")

        text = (
            f"✅ <b>Фото успешно загружены!</b>\n\n"
            f"📦 <b>Заказ #{deal_id}</b>\n"
            f"📌 {title}\n\n"
            f"📄 Накладная: {'✅ Загружена' if has_invoice else '❌ Отсутствует'}\n"
            f"📸 Фото: ✅ Загружено {photo_count} шт.\n\n"
            f"Клиент получил уведомление.\n\n"
            f"Выберите действие:"
        )

        keyboard = []
        if has_invoice:
            keyboard.append([
                InlineKeyboardButton(text="👁 Просмотр накладной", callback_data=f"admin_view_invoice_{deal_id}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin_delete_invoice_{deal_id}")
            ])
        else:
            keyboard.append([InlineKeyboardButton(text="➕ Добавить накладную", callback_data="admin_add_invoice")])

        keyboard.append([
            InlineKeyboardButton(text=f"👁 Просмотр фото ({photo_count})", callback_data=f"admin_view_photos_{deal_id}"),
            InlineKeyboardButton(text="➕ Добавить еще", callback_data="admin_add_photos")
        ])
        keyboard.append(
            [InlineKeyboardButton(text="🗑 Удалить все фото", callback_data=f"admin_delete_photos_{deal_id}")])
        keyboard.append([InlineKeyboardButton(text="🔙 К списку заказов", callback_data="admin_back_to_deals")])

        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            parse_mode="HTML"
        )

    await callback.answer(f"✅ Загружено {photo_count} фото")


@dp.message(Command("done"))
async def finish_photo_upload(message: Message, state: FSMContext):
    """Завершение загрузки фото"""
    current_state = await state.get_state()
    if current_state != AdminStates.waiting_photos:
        return

    # Удаляем сообщение с командой
    await safe_delete_message(message)

    data = await state.get_data()
    deal_id = data['deal_id']
    admin_msg_id = data.get('admin_message_id')
    photo_messages = data.get('photo_messages', [])

    # Удаляем все сообщения с фото
    for msg_id in photo_messages:
        try:
            await bot.delete_message(message.chat.id, msg_id)
        except:
            pass

    # Очищаем список сообщений с фото
    await state.update_data(photo_messages=[])

    # Подсчитываем загруженные фото
    photos_dir = f"{PHOTOS_DIR}/{deal_id}"
    photo_count = 0
    if os.path.exists(photos_dir):
        photo_count = len(os.listdir(photos_dir))

    # Автоматическое уведомление клиента
    await notify_on_document_upload(deal_id, "photos", message.from_user.id)

    # Возвращаемся к меню заказа
    if admin_msg_id:
        deal = await get_deal_details(deal_id)
        if deal:
            title = deal.get('TITLE', 'Без названия')
            has_invoice = os.path.exists(f"{INVOICES_DIR}/{deal_id}.pdf")

            text = (
                f"✅ <b>Фото успешно загружены!</b>\n\n"
                f"📦 <b>Заказ #{deal_id}</b>\n"
                f"📌 {title}\n\n"
                f"📄 Накладная: {'✅ Загружена' if has_invoice else '❌ Отсутствует'}\n"
                f"📸 Фото: ✅ Загружено {photo_count} шт.\n\n"
                f"Клиент получил уведомление.\n\n"
                f"Выберите действие:"
            )

            keyboard = []
            if not has_invoice:
                keyboard.append([InlineKeyboardButton(text="➕ Добавить накладную", callback_data="admin_add_invoice")])
            else:
                keyboard.append([InlineKeyboardButton(text="🔄 Заменить накладную", callback_data="admin_add_invoice")])
                keyboard.append(
                    [InlineKeyboardButton(text="🗑 Удалить накладную", callback_data=f"admin_delete_invoice_{deal_id}")])

            keyboard.append([InlineKeyboardButton(text="➕ Добавить еще фото", callback_data="admin_add_photos")])
            keyboard.append(
                [InlineKeyboardButton(text="🗑 Удалить все фото", callback_data=f"admin_delete_photos_{deal_id}")])
            keyboard.append([InlineKeyboardButton(text="🔙 К списку заказов", callback_data="admin_back_to_deals")])

            try:
                await bot.edit_message_text(
                    text,
                    chat_id=message.chat.id,
                    message_id=admin_msg_id,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                    parse_mode="HTML"
                )
            except:
                pass


@dp.message(Command("exit"))
async def admin_exit_command(message: Message, state: FSMContext):
    """Быстрый выход из админки командой"""
    current_state = await state.get_state()

    # Проверяем, что мы в админ-состоянии
    if current_state and "AdminStates" in str(current_state):
        await state.clear()
        await message.answer(
            "👋 <b>Вы вышли из админ-панели</b>\n\n"
            "Для возврата используйте /admin\n"
            "Для личного кабинета используйте /start",
            parse_mode="HTML"
        )
    else:
        # Если не в админке, просто игнорируем
        pass


# ====== ОСНОВНЫЕ ОБРАБОТЧИКИ ======

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Старт бота"""
    # ВАЖНО: Очищаем состояние (выход из админки если было)
    await state.clear()

    user_id = message.from_user.id

    if user_id in user_phones:
        await show_main_menu(message)
        return

    await message.answer(
        "🎉 <b>Добро пожаловать в Sunway24!</b>\n\n"
        "Я помогу вам отслеживать ваши заказы и доставки! 📦✨\n\n"
        "Для начала работы мне нужен ваш номер телефона 📱\n"
        "Это необходимо для связи с нашей системой.",
        reply_markup=get_phone_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(RegistrationStates.waiting_for_phone)


@dp.message(RegistrationStates.waiting_for_phone, F.contact)
async def process_phone(message: Message, state: FSMContext):
    """Обработка полученного телефона"""
    phone = message.contact.phone_number
    user_id = message.from_user.id

    await message.answer("⏳ Проверяю ваши данные...", reply_markup=ReplyKeyboardRemove())

    client = await find_client_by_phone(phone)

    if client:
        full_name = format_name(
            client.get('NAME', ''),
            client.get('LAST_NAME', '')
        )

        # Получаем полные данные контакта для email
        email_params = {
            'ID': client['ID']
        }
        contact_data = await bitrix_request('crm.contact.get', email_params)

        email_value = 'Не указан'
        if contact_data and 'EMAIL' in contact_data:
            # EMAIL - это массив, берем первый email
            email_list = contact_data.get('EMAIL', [])
            if email_list and len(email_list) > 0:
                email_value = email_list[0].get('VALUE', 'Не указан')

        user_phones[user_id] = {
            'phone': phone,
            'client_id': client['ID'],
            'name': full_name,
            'email': email_value  # ← Теперь берем из стандартного поля EMAIL
        }

        await message.answer(
            f"✅ <b>Отлично, {user_phones[user_id]['name']}!</b>\n\n"
            "Ваш аккаунт успешно подключен! 🎊\n"
            "Теперь вы можете управлять своими заказами 📦",
            parse_mode="HTML"
        )
        await state.clear()
        await show_main_menu(message)
    else:
        await message.answer(
            "❌ <b>Упс!</b>\n\n"
            "Не могу найти ваш номер в нашей базе данных 😔\n\n"
            "Пожалуйста, свяжитесь с нашим менеджером для регистрации:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💬 Связаться с менеджером", callback_data="consultation")]
            ]),
            parse_mode="HTML"
        )



async def show_main_menu(message: Message):
    """Показать главное меню"""
    user_id = message.from_user.id
    user_data = user_phones.get(user_id)

    if not user_data:
        await message.answer("⚠️ Пожалуйста, начните с команды /start")
        return

    await message.answer(
        f"🏠 <b>Личный кабинет</b>\n\n"
        f"Привет, {user_data['name']}! 👋\n"
        f"Выберите нужный раздел:",
        reply_markup=get_main_menu(),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    """Возврат в главное меню"""
    user_id = callback.from_user.id
    user_data = user_phones.get(user_id)

    await callback.message.edit_text(
        f"🏠 <b>Личный кабинет</b>\n\n"
        f"Привет, {user_data['name']}! 👋\n"
        f"Выберите нужный раздел:",
        reply_markup=get_main_menu(),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(F.data == "current_orders")
async def show_current_orders(callback: CallbackQuery):
    """Текущие заказы с расширенной информацией"""
    user_id = callback.from_user.id
    user_data = user_phones.get(user_id)

    await callback.answer("⏳ Загружаю заказы...")

    orders = await get_active_deals(user_data['client_id'])

    if orders:
        # Подсчитываем документы
        total_orders = len(orders)
        orders_with_docs = 0
        orders_with_photos = 0

        for order in orders:
            order_id = order.get('ID')
            if os.path.exists(f"{INVOICES_DIR}/{order_id}.pdf"):
                orders_with_docs += 1
            if os.path.exists(f"{PHOTOS_DIR}/{order_id}"):
                if os.listdir(f"{PHOTOS_DIR}/{order_id}"):
                    orders_with_photos += 1

        text = (
            f"📦 <b>Текущие заказы</b>\n\n"
            f"📊 Статистика:\n"
            f"• Всего заказов: {total_orders}\n"
            f"• С накладными: {orders_with_docs}/{total_orders}\n"
            f"• С фото: {orders_with_photos}/{total_orders}\n\n"
            f"Выберите заказ для просмотра:"
        )

        await callback.message.edit_text(
            text,
            reply_markup=get_orders_keyboard_with_status(orders, "order"),
            parse_mode="HTML"
        )
    else:
        await callback.message.edit_text(
            "📦 <b>Текущие заказы</b>\n\n"
            "У вас пока нет активных заказов 🤷\n\n"
            "Оформите новый заказ, связавшись с нашим менеджером!",
            reply_markup=get_back_button(),
            parse_mode="HTML"
        )


def parse_bitrix_money(value, default=0.0):
    """Парсит денежное значение из Битрикс в формате '100|RUB'"""
    if value in [None, '', [], {}]:
        return default

    try:
        if '|' in str(value):
            return float(str(value).split('|')[0])
        else:
            clean_value = str(value).replace(' ', '').replace(',', '.')
            return float(clean_value) if clean_value else default
    except (ValueError, TypeError):
        return default

@dp.callback_query(F.data.startswith("order_"))
async def show_order_details(callback: CallbackQuery):
    """Детали заказа"""
    user_id = callback.from_user.id
    user_data = user_phones.get(user_id)

    if not user_data:
        await callback.answer("❌ Пожалуйста, начните с команды /start", show_alert=True)
        return

    order_id = callback.data.split("_")[1]

    await callback.answer("⏳ Загружаю данные...")

    deal = await get_deal_details(order_id)

    if not deal:
        await callback.answer("❌ Ошибка загрузки заказа", show_alert=True)
        return

    def get_field(field_key, default='Н/Д'):
        field_id = BITRIX_FIELDS.get(field_key, '')
        value = deal.get(field_id, default)
        if value in [None, '', [], {}]:
            return default
        return str(value).strip()

    # Название товара из поля TITLE
    title = deal.get('TITLE', 'Без названия')

    # Форматируем текст заказа по новому формату
    text = f"📦 <b>Заказ №{order_id}</b>\n"
    text += f"<b>{title}</b>\n\n"

    # Текущий статус
    stage = deal.get('STAGE_ID', 'UNKNOWN')
    emoji = get_stage_emoji(stage)
    status_name = get_stage_name(stage)
    text += f"<b>Текущий статус:</b> {status_name}\n\n"

    # Основная информация
    product_type_id = get_field('product_category', 'Не указано')
    if product_type_id != 'Не указано':
        product_type = await get_category_name(product_type_id)
    else:
        product_type = 'Не указано'
    text += f"<b>Тип товара:</b> {product_type}\n"

    weight = get_field('weight', 'Н/Д')
    text += f"<b>Вес:</b> {weight} кг\n"

    volume = get_field('volume', 'Н/Д')
    text += f"<b>Объем:</b> {volume} м³\n"

    insurance = get_field('insurance', 'Не указана')
    text += f"<b>Страховка:</b> {insurance}\n\n"

    # Даты
    send_date = format_date(get_field('expected_send_date', ''))
    text += f"<b>Дата выхода груза:</b> {send_date}\n"

    arrival_date = format_date(get_field('expected_arrival_date', ''))
    text += f"<b>Ожидаемая дата прихода:</b> {arrival_date}\n"

    # Новые поля - город прибытия и маркировка груза
    arrival_city = get_field('arrival_city', 'Не указан')
    text += f"<b>Город прибытия:</b> {arrival_city}\n\n"

    cargo_marking = get_field('cargo_marking', 'Не указана')
    text += f"<b>Маркировка груза:</b> {cargo_marking}\n\n"

    # Документы
    text += f"<b>Документы:</b>\n"

    invoice_status = "✅ Загружена" if await has_invoice(order_id) else "⏳ Ожидается"
    text += f"Накладная: {invoice_status}\n"

    photos_dir = f"{PHOTOS_DIR}/{order_id}"
    photo_count = 0
    if os.path.exists(photos_dir):
        photo_count = len(os.listdir(photos_dir))
    photos_status = f"✅ Загружено ({photo_count} шт.)" if photo_count > 0 else "⏳ Ожидаются"
    text += f"Фото: {photos_status}\n\n"


    # Финансы
    # Финансы
    text += f"<b>Финансы:</b>\n"

    # Стоимость товара
    product_cost_raw = get_field('invoice_cost', '0')
    product_cost_value, product_currency = parse_bitrix_money_with_currency(product_cost_raw)
    product_cost_formatted = format_price(product_cost_value, product_currency)
    text += f"Стоимость товара: {product_cost_formatted}\n"

    # Стоимость доставки
    delivery_cost_raw = deal.get('OPPORTUNITY', '0')
    # Получаем валюту сделки
    deal_currency = deal.get('CURRENCY_ID', 'RUB')

    # OPPORTUNITY приходит без валюты, используем валюту сделки
    try:
        delivery_cost_value = float(str(delivery_cost_raw).replace(',', '.'))
    except:
        delivery_cost_value = 0.0

    delivery_cost_formatted = format_price(delivery_cost_value, deal_currency)
    text += f"Стоимость доставки: {delivery_cost_formatted}"


    keyboard = await get_order_details_keyboard(order_id)

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("invoice_"))
async def download_invoice(callback: CallbackQuery):
    """Скачивание накладной"""
    order_id = callback.data.split("_")[1]
    await callback.answer("⏳ Подготавливаю файл...")
    success = await send_invoice_to_client(order_id, callback.from_user.id)
    if success:
        await callback.answer("✅ Накладная отправлена")
    else:
        await callback.answer("❌ Ошибка загрузки накладной. Обратитесь к менеджеру.", show_alert=True)


@dp.callback_query(F.data.startswith("photos_"))
async def show_product_photos(callback: CallbackQuery):
    """Показать фото товара"""
    order_id = callback.data.split("_")[1]
    logger.info(f"Запрос фото для заказа {order_id}")
    await callback.answer("⏳ Загружаю фото...")

    local_photos_dir = f"{PHOTOS_DIR}/{order_id}"
    logger.info(f"Путь к фото: {local_photos_dir}")

    if os.path.exists(local_photos_dir):
        photos = os.listdir(local_photos_dir)
        logger.info(f"Найдено фото: {len(photos)} шт. - {photos}")

        if photos:
            try:
                # Сортируем фото по имени для правильного порядка
                photos = sorted(photos)

                # Отправляем фото как медиа-группу (альбом)
                media = []
                for idx, photo_file in enumerate(photos[:10]):  # Ограничение Telegram - максимум 10 фото в альбоме
                    photo_path = f"{local_photos_dir}/{photo_file}"
                    photo = FSInputFile(photo_path)

                    if idx == 0:
                        # Первое фото с подписью
                        media.append(InputMediaPhoto(
                            media=photo,
                            caption=f"📸 <b>Фото товара на складе</b>\n\nЗаказ #{order_id}\nВсего фото: {len(photos)}",
                            parse_mode="HTML"
                        ))
                    else:
                        media.append(InputMediaPhoto(media=photo))

                # Отправляем альбом
                await bot.send_media_group(
                    callback.from_user.id,
                    media
                )
                logger.info(f"Отправлен альбом из {len(media)} фото")

                # Если фото больше 10, отправляем остальные отдельными альбомами
                if len(photos) > 10:
                    for i in range(10, len(photos), 10):
                        batch = photos[i:i + 10]
                        media = []
                        for photo_file in batch:
                            photo_path = f"{local_photos_dir}/{photo_file}"
                            photo = FSInputFile(photo_path)
                            media.append(InputMediaPhoto(media=photo))

                        await bot.send_media_group(
                            callback.from_user.id,
                            media
                        )

                # Отправляем сообщение с кнопкой возврата
                await bot.send_message(
                    callback.from_user.id,
                    f"✅ Отправлено {len(photos)} фото для заказа #{order_id}",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔙 Вернуться к заказу", callback_data=f"order_{order_id}")]
                    ]),
                    parse_mode="HTML"
                )

                await callback.answer(f"✅ Отправлено {len(photos)} фото")
                return
            except Exception as e:
                logger.error(f"Ошибка отправки фото: {e}")
                await callback.answer("❌ Ошибка при отправке фото", show_alert=True)
                return
    else:
        logger.warning(f"Папка с фото не найдена: {local_photos_dir}")

    await callback.answer("❌ Фото не найдены", show_alert=True)


@dp.callback_query(F.data == "archive_orders")
async def show_archive_orders(callback: CallbackQuery):
    """Архив заказов"""
    user_id = callback.from_user.id
    user_data = user_phones.get(user_id)

    await callback.answer("⏳ Загружаю архив...")

    orders = await get_archived_deals(user_data['client_id'])

    if orders:
        await callback.message.edit_text(
            f"📚 <b>Архив заказов</b>\n\n"
            f"Завершенных заказов: {len(orders)}\n"
            f"Выберите заказ для просмотра:",
            reply_markup=get_orders_keyboard_with_status(orders, "archive"),
            parse_mode="HTML"
        )
    else:
        await callback.message.edit_text(
            "📚 <b>Архив заказов</b>\n\n"
            "Архив пуст 🤷",
            reply_markup=get_back_button(),
            parse_mode="HTML"
        )


@dp.message(Command("post_to_group"))
async def post_to_group(message: Message):
    """Отправка сообщения с кнопкой в группу"""
    if not is_admin(message.from_user.id):
        return

    GROUP_ID = -1001164156941

    text = """📦 SUNWAY24 | ЛИЧНЫЙ КАБИНЕТ

Друзья! У нас отличная новость!

Теперь отслеживать ваши грузы из Китая стало еще проще — запустили личный кабинет прямо в Telegram!

Что доступно в боте:
📊 Текущие заказы с актуальными статусами
📄 Мгновенное получение накладных
📸 Фото вашего товара на складе
📚 Архив всех доставок
💬 Быстрая связь с менеджером через WhatsApp/Telegram

Как подключиться:
1️⃣ Переходите в личный кабинет
2️⃣ Нажимаете START
3️⃣ Отправляете номер телефона
✅ Готово! Все ваши заказы на экране

Забудьте про долгие ожидания информации — все данные обновляются автоматически!

@Sunway_24_bot — ваш персональный помощник в доставке из Китая 🚚"""

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Личный кабинет", url="https://t.me/Sunway_24_bot")]
    ])

    try:
        await bot.send_message(
            GROUP_ID,
            text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        await message.answer("✅ Сообщение отправлено в группу")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@dp.callback_query(F.data.startswith("archive_"))
async def show_archive_details(callback: CallbackQuery):
    """Детали архивного заказа"""
    order_id = callback.data.split("_")[1]
    await callback.answer("⏳ Загружаю данные...")
    deal = await get_deal_details(order_id)
    if deal:
        def get_field(field_key, default='Н/Д'):
            field_id = BITRIX_FIELDS.get(field_key, '')
            value = deal.get(field_id, default)
            if value in [None, '', [], {}]:
                return default
            return str(value).strip()

        title = deal.get('TITLE', 'Без названия')

        text = f"📚 <b>Архив - Заказ #{order_id}</b>\n"
        text += f"📌 <b>{title}</b>\n\n"

        send_date = format_date(get_field('expected_send_date', ''))
        text += f"📅 <b>Дата выхода груза:</b> {send_date}\n"
        weight = get_field('weight', 'Н/Д')
        text += f"⚖️ <b>Вес:</b> {weight} кг\n"
        volume = get_field('volume', 'Н/Д')
        text += f"📦 <b>Объем:</b> {volume} м³\n"
        product_type_id = get_field('product_category', 'Не указано')
        if product_type_id != 'Не указано':
            product_type = await get_category_name(product_type_id)
        else:
            product_type = 'Не указано'
        text += f"🏷️ <b>Тип товара:</b> {product_type}\n"

        # Исправленная секция финансов
        # Стоимость товара
        product_cost_raw = get_field('invoice_cost', '0')
        product_cost_value, product_currency = parse_bitrix_money_with_currency(product_cost_raw)
        product_cost_formatted = format_price(product_cost_value, product_currency)
        text += f"💰 <b>Стоимость товара:</b> {product_cost_formatted}\n"

        # Стоимость доставки
        delivery_cost_raw = deal.get('OPPORTUNITY', '0')
        deal_currency = deal.get('CURRENCY_ID', 'RUB')

        try:
            delivery_cost_value = float(str(delivery_cost_raw).replace(',', '.'))
        except:
            delivery_cost_value = 0.0

        delivery_cost_formatted = format_price(delivery_cost_value, deal_currency)
        text += f"💰 <b>Стоимость доставки:</b> {delivery_cost_formatted}\n"

        date_create = format_date(deal.get('DATE_CREATE', ''))
        text += f"📅 <b>Создан:</b> {date_create}\n"
        date_modify = format_date(deal.get('DATE_MODIFY', ''))
        text += f"✅ <b>Завершен:</b> {date_modify}\n\n"
        text += "🏁 <b>Заказ завершен</b> ✅"

        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 К архиву", callback_data="archive_orders")]
            ]),
            parse_mode="HTML"
        )


@dp.callback_query(F.data == "consultation")
async def consultation(callback: CallbackQuery):
    """Консультация с менеджером"""
    text = (
        "💬 <b>Консультация с менеджером</b>\n\n"
        "Наши менеджеры готовы помочь вам! 🤝\n\n"
        "Выберите удобный способ связи:\n\n"
    )

    keyboard = [
        [InlineKeyboardButton(
            text="💬 WhatsApp",
            url="https://wa.me/79222330619"
        )],
        [InlineKeyboardButton(
            text="✈️ Telegram",
            url="https://t.me/Sunway74"
        )],
        [InlineKeyboardButton(
            text="🔙 Назад в меню",
            callback_data="back_to_menu"
        )]
    ]

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(F.data == "profile")
async def show_profile(callback: CallbackQuery):
    """Профиль клиента"""
    user_id = callback.from_user.id
    user_data = user_phones.get(user_id)

    if not user_data:
        await callback.answer("❌ Данные не найдены", show_alert=True)
        return

    text = f"👤 <b>Профиль клиента</b>\n\n"
    text += f"📝 <b>ФИО:</b> {user_data['name']}\n"
    text += f"📱 <b>Телефон:</b> {user_data['phone']}\n"
    text += f"✉️ <b>Email:</b> {user_data['email']}\n"  # ← Здесь уже используется правильное значение
    text += f"🆔 <b>ID клиента:</b> {user_data['client_id']}\n"

    await callback.message.edit_text(
        text,
        reply_markup=get_back_button(),
        parse_mode="HTML"
    )
    await callback.answer()


async def main():
    logger.info("=" * 60)
    logger.info("🚀 Sunway24 Bot - Финальная версия без ошибок!")
    logger.info(f"📋 Webhook: {BITRIX_WEBHOOK}")
    logger.info(f"👨‍💼 Админ ID: {str(ADMIN_IDS)}")
    logger.info("=" * 60)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
