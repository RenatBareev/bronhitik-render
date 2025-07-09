# main.py
import os
from datetime import time, timezone, timedelta
from telegram.ext import Application, CommandHandler, MessageHandler, filters

# Импортируем наши функции и утилиты
from keep_alive import keep_alive
from utils import initialize_firebase_admin_sdk, load_json
from handlers import (
    main_conv, # Единый обработчик для всех диалогов
    start,
    cancel_reminders,
    button_handler,
    send_reminder,
    send_monthly_chart_to_users
)

# --- НАСТРОЙКА ---
# Используем имя переменной 'TOKEN', как было в вашем рабочем коде
TOKEN = os.environ.get('TOKEN') 
REMINDERS_FILE = "reminders.json"
# -----------------

def main() -> None:
    """
    Основная функция для запуска бота на Replit.
    """
    if not TOKEN:
        print("Критическая ошибка: Токен не найден в переменной окружения 'TOKEN'")
        return

    # Инициализируем Firebase
    initialize_firebase_admin_sdk()

    # Запускаем веб-сервер для поддержания активности
    keep_alive()

    application = Application.builder().token(TOKEN).build()

    # Восстановление напоминаний при перезапуске бота
    reminders = load_json(REMINDERS_FILE)
    for chat_id_str, data in reminders.items():
        if 'times' in data:
            for i, t in enumerate(data['times']):
                try:
                    hour, minute = map(int, t.split(':'))
                    job_name = f"reminder_{chat_id_str}_{i}"
                    application.job_queue.run_daily(
                        send_reminder,
                        time=time(hour=hour, minute=minute, tzinfo=timezone(timedelta(hours=3))),
                        chat_id=int(chat_id_str),
                        name=job_name
                    )
                except ValueError:
                    print(f"Ошибка при восстановлении напоминания для {chat_id_str}, время {t}")
                    continue

    # Планируем ежемесячную отправку графика
    application.job_queue.run_daily(
        send_monthly_chart_to_users,
        time=time(hour=0, minute=5, tzinfo=timezone(timedelta(hours=3))),
        name="monthly_chart_sender"
    )

    # Добавляем все обработчики
    application.add_handler(CommandHandler("start", start))

    # Добавляем единый обработчик для всех диалогов (✅ Сделать замер, 📈 График, /profile, /cleardata, /remind)
    application.add_handler(main_conv)

    # Добавляем обработчики для команд и кнопок, НЕ входящих в диалоги
    application.add_handler(CommandHandler("cancelreminders", cancel_reminders))
    # Этот обработчик теперь только для кнопки "Анализ ИИ"
    application.add_handler(MessageHandler(filters.Regex("^(🤖 Анализ ИИ)$"), button_handler))

    print("Финальная версия бота «Помощник Бронхитик» запущена...")
    application.run_polling()

if __name__ == '__main__':
    main()
