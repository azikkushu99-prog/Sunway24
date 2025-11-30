import aiohttp

STAGE_NAMES = {
    'NEW': '🆕 Новая заявка',
    'PREPARATION': '📝 Подготовка документов',
    'PREPAYMENT_INVOICE': '💰 Счет выставлен',
    'EXECUTING': '🚚 В работе',
    'UC_RS7UFN': '🛒 Выкуп товара',
    'UC_1BOZ7M': '⏳ Ждем груз от поставщика',
    'UC_Y5IE8J': '🏭 Товар на складе',
    'UC_EWKB0I': '📄 Накладная',
    'UC_VA28QX': '🚚 Логистика в РФ',
    'UC_TOW1NT': '📍 Груз прибыл на склад в РФ',
    'UC_GTV3R4': '📄 Китайская накладная',
    'WON': '✅ Сделка завершена',
    'LOSE': '❌ Отменено'
}

STATUS_EMOJI = {
    'NEW': '🆕',
    'PREPARATION': '📝',
    'PREPAYMENT_INVOICE': '💰',
    'EXECUTING': '🚚',
    'UC_RS7UFN': '🛒',
    'UC_1BOZ7M': '⏳',
    'UC_Y5IE8J': '🏭',
    'UC_EWKB0I': '📄',
    'UC_VA28QX': '🚚',
    'UC_TOW1NT': '📍',
    'UC_GTV3R4': '📄',
    'WON': '✅',
    'LOSE': '❌'
}

# ✅ ИСПРАВЛЕННЫЕ ID ПОЛЕЙ (сопоставлены с реальными данными API)
BITRIX_FIELDS = {
    # Основные поля (НОВЫЕ ID - работают у всех пользователей)
    'weight': 'UF_CRM_1764049517590',  # Общий вес
    'volume': 'UF_CRM_1764049564263',  # Общий объем
    'expected_send_date': 'UF_CRM_1764049614030',  # Ориентировочная дата отправки
    'expected_arrival_date': 'UF_CRM_1764049649086',  # Ориентировочная дата прибытия
    'insurance': 'UF_CRM_1764049805679',  # Страховка
    'cargo_marking': 'UF_CRM_1764049909974',  # Маркировка груза
    'product_category': 'UF_CRM_1764050074878',  # Категория товара
    'invoice_cost': 'UF_CRM_1764050233702',  # Стоимость товара
    'arrival_city': 'UF_CRM_1764050267877',  # Город прибытия

    # Старые поля (оставляем для совместимости, но они могут не работать у всех)
    'client_id': 'UF_CRM_1591163139028',
    'description': 'UF_CRM_5EC95CA4AB01F',
    'product_name': 'UF_CRM_5ED34C9E0DBA1',
    'units_count': 'UF_CRM_1756292836599',
    'expected_customs_date': 'UF_CRM_1756354903725',
    'goods_ready_date': 'UF_CRM_1756355066065',
    'expected_customs_arrival': 'UF_CRM_1758264099921',
    'expenses': 'UF_CRM_1756292847087',
    'profit': 'UF_CRM_1756292927',

    # Файлы
    'invoice_file': 'UF_CRM_1763119515',
    'product_photos': 'UF_CRM_1763119545',

    # Документы
    'commercial_offer': 'UF_CRM_1756295296052',
    'contract': 'UF_CRM_1756295338716',
    'specification': 'UF_CRM_1756295360556',
    'invoice_bill': 'UF_CRM_1756295371638',
    'payment_verification': 'UF_CRM_1756295411588',
    'delivery_invoice': 'UF_CRM_1756295438427',
    'customs_invoice': 'UF_CRM_1756295450237',
    'final_invoice': 'UF_CRM_1756294939310',
    'transport_type': 'UF_CRM_1756293004273',
    'destination': 'UF_CRM_1758265856453',
    'supplier': 'UF_CRM_1758266661240',
}

CONTACT_FIELDS = {
    'client_id': 'UF_CRM_1733595880',
    'city': 'UF_CRM_1732543646',
    'name': 'UF_CRM_1732543667',
    'weight': 'UF_CRM_1731051887',
    'volume': 'UF_CRM_1731051899',
    'description': 'UF_CRM_1732543720',
    'telegram_id': 'UF_CRM_1731051977',
    'telegram_username': 'UF_CRM_1731051966',
    'email': 'UF_CRM_5F02275DA7BD0',
}

CATEGORY_CACHE = {}
BITRIX_WEBHOOK = "https://sunway24.bitrix24.ru/rest/326/fiwux7q90yclt8l1/"


async def get_list_item_name(field_id: str, item_id: str):
    """Получить текстовое название элемента списка"""
    url = f"{BITRIX_WEBHOOK}crm.deal.fields"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url) as response:
                data = await response.json()
                fields = data.get('result', {})

                field_info = fields.get(field_id, {})
                items = field_info.get('items', [])

                for item in items:
                    if str(item.get('ID')) == str(item_id):
                        return item.get('VALUE', item_id)

                return item_id
    except:
        return item_id


async def get_category_name(category_id: str):
    """Получить название категории с кэшированием"""
    if not category_id or category_id == 'Н/Д':
        return 'Не указано'

    if category_id in CATEGORY_CACHE:
        return CATEGORY_CACHE[category_id]

    # Используем новый ID поля категории
    name = await get_list_item_name('UF_CRM_1764050074878', category_id)
    CATEGORY_CACHE[category_id] = name
    return name


def get_stage_name(stage_id: str) -> str:
    """Получить читаемое название статуса"""
    return STAGE_NAMES.get(stage_id, f'❓ {stage_id}')


def get_stage_emoji(stage_id: str) -> str:
    """Получить эмодзи для статуса"""
    return STATUS_EMOJI.get(stage_id, '❓')


def format_date(date_str: str) -> str:
    """Форматировать дату из Битрикс"""
    if not date_str or date_str == 'Н/Д':
        return 'Н/Д'

    try:
        if '.' in date_str and len(date_str) == 10:
            parts = date_str.split('.')
            if len(parts) == 3 and all(p.isdigit() for p in parts):
                return date_str

        if 'T' in date_str:
            date = date_str.split('T')[0]
            year, month, day = date.split('-')
            return f"{day}.{month}.{year}"

        if '-' in date_str and len(date_str) >= 10:
            year, month, day = date_str[:10].split('-')
            return f"{day}.{month}.{year}"

        return date_str
    except:
        return 'Н/Д'


def parse_bitrix_money_with_currency(value, default=0.0):
    """Парсит денежное значение из Битрикс в формате '100|USD' и возвращает кортеж (сумма, валюта)"""
    if value in [None, '', [], {}]:
        return default, 'RUB'

    try:
        if '|' in str(value):
            parts = str(value).split('|')
            amount = float(parts[0])
            currency = parts[1] if len(parts) > 1 else 'RUB'
            return amount, currency
        else:
            clean_value = str(value).replace(' ', '').replace(',', '.')
            return (float(clean_value), 'RUB') if clean_value else (default, 'RUB')
    except (ValueError, TypeError):
        return default, 'RUB'


def format_price(value, currency='RUB') -> str:
    """Форматировать цену с валютой после суммы"""
    currency_symbols = {
        'RUB': '₽',
        'USD': '$',
        'EUR': '€',
        'CNY': '¥'
    }
    symbol = currency_symbols.get(currency, currency)

    try:
        formatted_value = f"{float(value):,.2f}".replace(',', ' ')
        return f"{formatted_value} {symbol}"
    except:
        return f'0.00 {symbol}'


def clean_phone(phone: str) -> str:
    """Очистка номера телефона для поиска в Битрикс"""
    clean = ''.join(filter(str.isdigit, phone))

    if clean.startswith('8'):
        clean = '7' + clean[1:]
    elif clean.startswith('+7'):
        clean = '7' + clean[2:]
    elif clean.startswith('9') and len(clean) == 10:
        clean = '7' + clean

    return clean


def get_file_type(file_url: str) -> str:
    """Определить тип файла по расширению"""
    if not file_url:
        return 'file'

    file_url_lower = file_url.lower()
    if file_url_lower.endswith('.pdf'):
        return 'pdf'
    elif file_url_lower.endswith(('.jpg', '.jpeg', '.png', '.gif')):
        return 'image'
    elif file_url_lower.endswith(('.doc', '.docx')):
        return 'word'
    elif file_url_lower.endswith(('.xls', '.xlsx')):
        return 'excel'
    else:
        return 'file'


def format_name(first_name: str, last_name: str = None) -> str:
    """Форматирование имени без None"""
    first_name = first_name or ''
    last_name = last_name or ''

    first_name = first_name.strip()
    last_name = last_name.strip()

    if first_name and last_name:
        return f"{first_name} {last_name}"
    elif first_name:
        return first_name
    elif last_name:
        return last_name
    else:
        return "Клиент"
