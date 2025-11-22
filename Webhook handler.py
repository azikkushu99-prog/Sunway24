"""
Webhook handler для Bitrix24
Этот скрипт обрабатывает входящие вебхуки от Битрикс при изменении статуса сделок
и отправляет уведомления клиентам через Telegram бота
"""

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import logging
import asyncio
from bot import (
    bot,
    notify_stage_change,
    send_invoice_to_client,
    send_warehouse_photos,
    get_deal_details,
    user_phones,
    bitrix_request
)

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Создаем FastAPI приложение
app = FastAPI(title="Sunway24 Webhook Handler")

# Словарь для хранения последних статусов сделок (чтобы отслеживать изменения)
deal_stages = {}


@app.post("/webhook/deal_update")
async def handle_deal_update(request: Request):
    """
    Обработчик вебхука для обновления сделки
    Битрикс должен отправлять сюда POST запросы при изменении сделки
    """
    try:
        # Получаем данные из запроса
        data = await request.json()
        logger.info(f"Received webhook data: {data}")

        # Извлекаем информацию о сделке
        deal_id = data.get('data', {}).get('FIELDS', {}).get('ID')
        if not deal_id:
            deal_id = data.get('FIELDS', {}).get('ID')

        if not deal_id:
            logger.error("No deal ID in webhook data")
            return JSONResponse({"status": "error", "message": "No deal ID"}, status_code=400)

        # Получаем полные данные сделки из Битрикс
        deal = await get_deal_details(deal_id)
        if not deal:
            logger.error(f"Could not fetch deal {deal_id} details")
            return JSONResponse({"status": "error", "message": "Deal not found"}, status_code=404)

        new_stage = deal.get('STAGE_ID')
        contact_id = deal.get('CONTACT_ID')

        if not new_stage or not contact_id:
            logger.error(f"Missing stage or contact for deal {deal_id}")
            return JSONResponse({"status": "error", "message": "Missing data"}, status_code=400)

        # Проверяем, изменился ли статус
        old_stage = deal_stages.get(deal_id)

        if old_stage != new_stage:
            logger.info(f"Deal {deal_id} stage changed: {old_stage} -> {new_stage}")
            deal_stages[deal_id] = new_stage

            # Находим telegram_id клиента
            client_telegram_id = None
            for user_id, user_data in user_phones.items():
                if user_data.get('client_id') == str(contact_id):
                    client_telegram_id = user_id
                    break

            if client_telegram_id:
                # Отправляем уведомление о смене статуса
                await notify_stage_change(deal_id, new_stage)

                # Автоматически отправляем накладную при переходе на стадию "Накладная"
                if new_stage == 'UC_EWKB0I':
                    logger.info(f"Auto-sending invoice for deal {deal_id}")
                    await asyncio.sleep(2)  # Небольшая задержка
                    invoice_sent = await send_invoice_to_client(deal_id, client_telegram_id)
                    if invoice_sent:
                        logger.info(f"Invoice sent successfully for deal {deal_id}")

                # Автоматически отправляем фото при переходе на стадию "Товар на складе"
                elif new_stage == 'UC_Y5IE8J':
                    logger.info(f"Auto-sending photos for deal {deal_id}")
                    await asyncio.sleep(2)  # Небольшая задержка
                    photos_sent = await send_warehouse_photos(deal_id, client_telegram_id)
                    if photos_sent:
                        logger.info(f"Photos sent successfully for deal {deal_id}")

                return JSONResponse({
                    "status": "success",
                    "message": f"Notification sent for deal {deal_id}",
                    "stage": new_stage
                })
            else:
                logger.info(f"No Telegram user found for contact {contact_id}")
                return JSONResponse({
                    "status": "warning",
                    "message": f"No Telegram user for contact {contact_id}"
                })
        else:
            logger.info(f"Deal {deal_id} stage unchanged: {new_stage}")
            return JSONResponse({
                "status": "info",
                "message": "Stage unchanged"
            })

    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        return JSONResponse(
            {"status": "error", "message": str(e)},
            status_code=500
        )


@app.post("/webhook/invoice_uploaded")
async def handle_invoice_upload(request: Request):
    """
    Специальный вебхук для обработки загрузки накладной
    Срабатывает когда в поле накладной добавляется файл
    """
    try:
        data = await request.json()
        logger.info(f"Invoice upload webhook: {data}")

        deal_id = data.get('deal_id') or data.get('FIELDS', {}).get('ID')
        if not deal_id:
            return JSONResponse({"status": "error", "message": "No deal ID"}, status_code=400)

        # Получаем данные сделки
        deal = await get_deal_details(deal_id)
        if not deal:
            return JSONResponse({"status": "error", "message": "Deal not found"}, status_code=404)

        contact_id = deal.get('CONTACT_ID')

        # Находим telegram_id клиента
        client_telegram_id = None
        for user_id, user_data in user_phones.items():
            if user_data.get('client_id') == str(contact_id):
                client_telegram_id = user_id
                break

        if client_telegram_id:
            # Отправляем накладную клиенту
            invoice_sent = await send_invoice_to_client(deal_id, client_telegram_id)

            if invoice_sent:
                # Также отправляем уведомление
                await bot.send_message(
                    client_telegram_id,
                    f"📄 <b>Накладная готова!</b>\n\n"
                    f"Для вашего заказа #{deal_id} подготовлена накладная.\n"
                    f"Документ отправлен вам выше.",
                    parse_mode="HTML"
                )

                return JSONResponse({
                    "status": "success",
                    "message": f"Invoice sent for deal {deal_id}"
                })
            else:
                return JSONResponse({
                    "status": "error",
                    "message": "Failed to send invoice"
                }, status_code=500)
        else:
            return JSONResponse({
                "status": "warning",
                "message": f"No Telegram user for contact {contact_id}"
            })

    except Exception as e:
        logger.error(f"Error processing invoice webhook: {e}", exc_info=True)
        return JSONResponse(
            {"status": "error", "message": str(e)},
            status_code=500
        )


@app.post("/webhook/photos_uploaded")
async def handle_photos_upload(request: Request):
    """
    Специальный вебхук для обработки загрузки фото товара
    Срабатывает когда в поле фото добавляются файлы
    """
    try:
        data = await request.json()
        logger.info(f"Photos upload webhook: {data}")

        deal_id = data.get('deal_id') or data.get('FIELDS', {}).get('ID')
        if not deal_id:
            return JSONResponse({"status": "error", "message": "No deal ID"}, status_code=400)

        # Получаем данные сделки
        deal = await get_deal_details(deal_id)
        if not deal:
            return JSONResponse({"status": "error", "message": "Deal not found"}, status_code=404)

        # Проверяем, что сделка на стадии "Товар на складе"
        if deal.get('STAGE_ID') != 'UC_Y5IE8J':
            return JSONResponse({
                "status": "info",
                "message": "Deal not in warehouse stage"
            })

        contact_id = deal.get('CONTACT_ID')

        # Находим telegram_id клиента
        client_telegram_id = None
        for user_id, user_data in user_phones.items():
            if user_data.get('client_id') == str(contact_id):
                client_telegram_id = user_id
                break

        if client_telegram_id:
            # Отправляем фото клиенту
            photos_sent = await send_warehouse_photos(deal_id, client_telegram_id)

            if photos_sent:
                # Также отправляем уведомление
                await bot.send_message(
                    client_telegram_id,
                    f"📸 <b>Фото товара доступны!</b>\n\n"
                    f"Ваш товар (заказ #{deal_id}) прибыл на склад.\n"
                    f"Фотографии отправлены вам выше.",
                    parse_mode="HTML"
                )

                return JSONResponse({
                    "status": "success",
                    "message": f"Photos sent for deal {deal_id}"
                })
            else:
                return JSONResponse({
                    "status": "error",
                    "message": "Failed to send photos"
                }, status_code=500)
        else:
            return JSONResponse({
                "status": "warning",
                "message": f"No Telegram user for contact {contact_id}"
            })

    except Exception as e:
        logger.error(f"Error processing photos webhook: {e}", exc_info=True)
        return JSONResponse(
            {"status": "error", "message": str(e)},
            status_code=500
        )


@app.get("/")
async def root():
    """Главная страница для проверки работоспособности"""
    return {
        "status": "active",
        "service": "Sunway24 Webhook Handler",
        "endpoints": [
            "/webhook/deal_update",
            "/webhook/invoice_uploaded",
            "/webhook/photos_uploaded"
        ]
    }


@app.get("/health")
async def health_check():
    """Проверка состояния сервиса"""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn

    # Запускаем сервер на порту 8001 (бот работает отдельно)
    uvicorn.run(app, host="0.0.0.0", port=8001)
