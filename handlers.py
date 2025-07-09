# handlers.py
import os
from datetime import datetime, date, time, timezone, timedelta
import json
import google.generativeai as genai
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.constants import ParseMode 
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, filters
import matplotlib.pyplot as plt
import io
import re
import calendar

from utils import load_json_with_firestore_sync, save_json_with_firestore_sync, get_sheet, calculate_age

# --- НАСТРОЙКА ---
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
PROFILES_FILE = "profiles.json"
REMINDERS_FILE = "reminders.json"
CHARTS_SENT_FILE = "charts_sent.json"

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# Состояния диалогов
(
    GET_PEAKFLOW, GET_BREATHING, GET_COUGH, GET_SPUTUM, GET_MEDS,
    SET_PROFILE, GET_GENDER, SET_REMINDER,
    CONFIRM_CLEAR_DATA, GET_CHART_MONTH
) = range(10)

# --- Главное меню и проверки ---
async def show_main_menu(update: Update, text: str):
    reply_keyboard = [["✅ Сделать замер"], ["📈 График", "🤖 Анализ ИИ"]]
    await update.message.reply_text(text, reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True))

async def check_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat_id = str(update.effective_chat.id)
    profiles = load_json_with_firestore_sync(PROFILES_FILE, telegram_chat_id="global_data")
    reminders = load_json_with_firestore_sync(REMINDERS_FILE, telegram_chat_id="global_data")
    user_profile = profiles.get(chat_id)

    if not user_profile or not user_profile.get('dob') or not user_profile.get('sex'):
        await update.effective_message.reply_text("Сначала нужно настроить профиль! Пожалуйста, используйте команду /profile.", reply_markup=ReplyKeyboardRemove())
        return False

    if chat_id not in reminders:
        await update.effective_message.reply_text("Отлично! Профиль настроен. Теперь нужно настроить напоминания. Используйте команду /remind.", reply_markup=ReplyKeyboardRemove())
        return False 
    return True

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await show_main_menu(update, "Действие отменено.")
    return ConversationHandler.END

# --- Функции-обработчики ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_setup(update, context):
        return
    await show_main_menu(update, f'Привет, {update.effective_user.first_name}! Я помощник Бронхитик.')

async def start_logging(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_setup(update, context):
        return ConversationHandler.END
    reply_keyboard = [["Отмена"]]
    await update.message.reply_text("Давай запишем показания. Какое число показал прибор?", reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=True))
    return GET_PEAKFLOW

async def get_peakflow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text == "Отмена": return await cancel(update, context)
    try:
        context.user_data['peakflow'] = int(update.message.text)
        reply_keyboard = [["Да", "Нет"], ["Отмена"]]
        await update.message.reply_text("Дышать было трудно?", reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True))
        return GET_BREATHING
    except ValueError:
        await update.message.reply_text("Это не похоже на число. Попробуй еще раз.")
        return GET_PEAKFLOW 

async def get_breathing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text == "Отмена": return await cancel(update, context)
    context.user_data['breathing'] = update.message.text
    reply_keyboard = [["Да", "Нет"], ["Отмена"]]
    await update.message.reply_text("А кашель был?", reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True))
    return GET_COUGH

async def get_cough(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text == "Отмена": return await cancel(update, context)
    context.user_data['cough'] = update.message.text
    reply_keyboard = [["Да", "Нет"], ["Отмена"]]
    await update.message.reply_text("Мокрота была?", reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True))
    return GET_SPUTUM

async def get_sputum(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text == "Отмена": return await cancel(update, context)
    context.user_data['sputum'] = update.message.text
    reply_keyboard = [["Базисная терапия"], ["Назначения при болезни"], ["Нет"], ["Отмена"]]
    await update.message.reply_text("Какие-то лекарства принимал(а)?", reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True))
    return GET_MEDS

async def get_meds_and_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text == "Отмена": return await cancel(update, context)
    context.user_data['meds'] = update.message.text
    await update.message.reply_text("Спасибо! Сейчас все запишу...")

    chat_id = str(update.effective_chat.id)
    profiles = load_json_with_firestore_sync(PROFILES_FILE, telegram_chat_id="global_data")
    user_profile = profiles.get(chat_id, {})
    dob_str = user_profile.get('dob')
    sex = user_profile.get('sex', 'н/д')
    age = calculate_age(dob_str) if dob_str else 'н/д'

    sheet = get_sheet()
    if not sheet:
        await show_main_menu(update, "❌ Ошибка! Не смог подключиться к таблице.")
        return ConversationHandler.END

    try:
        all_records = sheet.get_all_records()
        next_record_number = len(all_records) + 1
    except Exception as e:
        print(f"Ошибка получения записей из таблицы: {e}")
        all_records = []
        next_record_number = 1

    now_moscow = datetime.now(timezone(timedelta(hours=3)))
    measurement_type = "утро" if now_moscow.hour < 15 else "вечер"

    row_to_save = [
        next_record_number, now_moscow.strftime("%d.%m.%Y"), now_moscow.strftime("%H:%M:%S"),
        measurement_type, context.user_data.get('peakflow'), context.user_data.get('breathing'),
        context.user_data.get('cough'), context.user_data.get('sputum'), context.user_data.get('meds'),
        age, sex
    ]
    try:
        sheet.append_row(row_to_save, value_input_option='USER_ENTERED')
        await show_main_menu(update, "✅ Готово! Все записала. Молодец!")
    except Exception as e:
        print(f"Ошибка записи в Google Sheets: {e}")
        await show_main_menu(update, "❌ Ой, не смогла сохранить данные.")

    context.user_data.clear()
    return ConversationHandler.END

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reply_keyboard = [["Отмена"]]
    await update.message.reply_text("Давайте настроим профиль. Введите дату рождения ребенка (ДД.ММ.ГГГГ).", reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=True))
    return SET_PROFILE

async def set_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text == "Отмена": return await cancel(update, context)
    try:
        dob_str = update.message.text.strip()
        datetime.strptime(dob_str, "%d.%m.%Y")
        context.user_data['dob_to_save'] = dob_str 
        reply_keyboard = [["Мужской", "Женский"], ["Отмена"]]
        await update.message.reply_text("Отлично! Теперь выберите пол ребенка:", reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True))
        return GET_GENDER
    except ValueError:
        await update.message.reply_text("Неверный формат. Попробуйте еще раз (ДД.ММ.ГГГГ).")
        return SET_PROFILE

async def get_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text == "Отмена": return await cancel(update, context)
    sex_raw = update.message.text.strip().lower()
    if sex_raw not in ['мужской', 'женский']:
        await update.message.reply_text("Пожалуйста, используйте кнопки.")
        return GET_GENDER
    dob_str = context.user_data.get('dob_to_save')
    if not dob_str:
        await update.message.reply_text("Ой, что-то пошло не так. Давайте начнем настройку профиля заново.")
        return await profile_command(update, context)
    chat_id = str(update.effective_chat.id)
    first_name = update.effective_user.first_name
    profiles = load_json_with_firestore_sync(PROFILES_FILE, telegram_chat_id="global_data")
    profiles[chat_id] = {'dob': dob_str, 'sex': sex_raw, 'first_name': first_name}
    save_json_with_firestore_sync(profiles, PROFILES_FILE, telegram_chat_id="global_data")
    await update.message.reply_text("Профиль успешно сохранен! Теперь давайте настроим напоминания.")
    return await remind_command(update, context)

async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reply_keyboard = [["Отмена"]]
    await update.message.reply_text("Введите время для напоминаний (например: 08:00 20:30).", reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=True))
    return SET_REMINDER

async def set_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text == "Отмена": return await cancel(update, context)
    chat_id = str(update.message.chat_id)
    try:
        times = update.message.text.split()
        if len(times) != 2: raise ValueError("Требуется два времени.")
        for t_str in times:
            datetime.strptime(t_str, '%H:%M')
        reminders = load_json_with_firestore_sync(REMINDERS_FILE, telegram_chat_id="global_data")
        if chat_id in reminders and 'jobs' in reminders.get(chat_id, {}):
            for job_name in reminders[chat_id]['jobs']:
                for job in context.job_queue.get_jobs_by_name(job_name):
                    job.schedule_removal()
        job_names = []
        for i, t_str in enumerate(times):
            t = datetime.strptime(t_str, '%H:%M').time()
            job_name = f"reminder_{chat_id}_{i}"
            job_names.append(job_name)
            context.job_queue.run_daily(send_reminder, time=t.replace(tzinfo=timezone(timedelta(hours=3))), chat_id=int(chat_id), name=job_name)
        reminders[chat_id] = {'times': times, 'jobs': job_names}
        save_json_with_firestore_sync(reminders, REMINDERS_FILE, telegram_chat_id="global_data")
        await show_main_menu(update, "Отлично! Напоминания установлены. Теперь все готово к работе!")
        return ConversationHandler.END
    except Exception as e:
        print(f"Ошибка установки напоминаний: {e}")
        await update.message.reply_text("Неверный формат. Пожалуйста, введите два времени (например, 08:00 20:30).")
        return SET_REMINDER

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=context.job.chat_id, text="На незабудке сделать замер! 🌸")

async def cancel_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.message.chat_id)
    reminders = load_json_with_firestore_sync(REMINDERS_FILE, telegram_chat_id="global_data")
    if chat_id in reminders:
        if 'jobs' in reminders.get(chat_id, {}):
            for job_name in reminders[chat_id]['jobs']:
                for job in context.job_queue.get_jobs_by_name(job_name):
                    job.schedule_removal()
        del reminders[chat_id]
        save_json_with_firestore_sync(reminders, REMINDERS_FILE, telegram_chat_id="global_data")
        await update.message.reply_text("Все ваши напоминания отменены.")
    else:
        await update.message.reply_text("У вас нет активных напоминаний.")
    await show_main_menu(update, "Главное меню:")

def parse_rgb_string(rgb_string):
    match = re.match(r'rgb\((\d+),\s*(\d+),\s*(\d+)(?:,\s*[\d\.]+)?\)', rgb_string)
    if match:
        r, g, b = int(match.group(1)), int(match.group(2)), int(match.group(3))
        return (r / 255, g / 255, b / 255)
    raise ValueError(f"Неверный формат RGB строки: {rgb_string}")

async def _generate_chart_image(chat_id: int, user_first_name: str, sheet_records: list, target_year: int = None, target_month: int = None):
    today_date = date.today()
    current_year = target_year if target_year is not None else today_date.year
    current_month = target_month if target_month is not None else today_date.month

    num_days_in_month = calendar.monthrange(current_year, current_month)[1]

    daily_raw_data = {day: {'morning': [], 'evening': []} for day in range(1, num_days_in_month + 1)}

    for record in sheet_records:
        try:
            record_date_str = record.get('Дата')
            if not record_date_str: continue

            record_datetime_obj = datetime.strptime(record_date_str, "%d.%m.%Y")

            if record_datetime_obj.year == current_year and record_datetime_obj.month == current_month:
                day = record_datetime_obj.day
                value = int(record.get('Пикфлоуметр', 0))
                time_of_day = record.get('Время суток', '').strip().lower()

                if time_of_day == 'утро':
                    daily_raw_data[day]['morning'].append(value)
                elif time_of_day == 'вечер':
                    daily_raw_data[day]['evening'].append(value)
        except (ValueError, TypeError) as e:
            print(f"Ошибка обработки записи для графика: {e}, Запись: {record}")
            continue

    labels = list(range(1, num_days_in_month + 1))
    morning_data = [max(daily_raw_data[d]['morning']) if daily_raw_data[d]['morning'] else None for d in labels]
    evening_data = [max(daily_raw_data[d]['evening']) if daily_raw_data[d]['evening'] else None for d in labels]

    if not any(morning_data) and not any(evening_data):
        return None

    fig, ax = plt.subplots(figsize=(18, 8))

    morning_color = parse_rgb_string('rgb(54, 162, 235)')
    evening_color = parse_rgb_string('rgb(255, 99, 132)')

    ax.plot(labels, morning_data, label='Утро', color=morning_color, marker='o', linestyle='-', markersize=8, mfc='white')
    ax.plot(labels, evening_data, label='Вечер', color=evening_color, marker='o', linestyle='-', markersize=8, mfc='white')

    for i, val in enumerate(morning_data):
        if val is not None: ax.annotate(str(val), (labels[i], val), textcoords="offset points", xytext=(0,10), ha='center')
    for i, val in enumerate(evening_data):
        if val is not None: ax.annotate(str(val), (labels[i], val), textcoords="offset points", xytext=(0,-20), ha='center')

    ax.set_xlabel('День месяца')
    ax.set_ylabel('Пикфлоуметр (л/мин)')
    ax.set_title(f'Дневник пикфлоуметрии за {date(current_year, current_month, 1).strftime("%B %Y")}')
    ax.set_xticks(labels)
    ax.set_ylim(50, 500)
    ax.set_yticks(range(50, 501, 50))
    ax.grid(True, linestyle='--', alpha=0.6)
    ax.legend()
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150)
    buf.seek(0)
    plt.close(fig)
    return buf

async def chart_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_setup(update, context):
        return ConversationHandler.END

    await update.message.reply_text("Собираю данные для выбора периода...")

    sheet = get_sheet()
    if not sheet:
        await show_main_menu(update, "❌ Не могу получить доступ к данным.")
        return ConversationHandler.END

    try:
        all_records = sheet.get_all_records()
        if not all_records:
            await show_main_menu(update, "В таблице пока нет данных для построения графика.")
            return ConversationHandler.END
    except Exception as e:
        print(f"Ошибка получения записей из таблицы для графика: {e}")
        await show_main_menu(update, "❌ Произошла ошибка при чтении данных из таблицы.")
        return ConversationHandler.END

    available_months = set()
    for record in all_records:
        date_str = record.get('Дата')
        if date_str:
            try:
                dt_obj = datetime.strptime(date_str, "%d.%m.%Y")
                month_key = dt_obj.strftime("%Y-%m")
                available_months.add(month_key)
            except ValueError:
                continue

    if not available_months:
        await show_main_menu(update, "Не нашла данных с корректными датами для построения графика.")
        return ConversationHandler.END

    sorted_months = sorted(list(available_months), reverse=True)

    month_names = {
        "01": "Январь", "02": "Февраль", "03": "Март", "04": "Апрель",
        "05": "Май", "06": "Июнь", "07": "Июль", "08": "Август",
        "09": "Сентябрь", "10": "Октябрь", "11": "Ноябрь", "12": "Декабрь"
    }

    keyboard_buttons = []
    for month_key in sorted_months:
        year, month_num = month_key.split('-')
        month_name = month_names.get(month_num, "")
        display_text = f"{month_name} {year}"
        keyboard_buttons.append([display_text])

    keyboard_buttons.append(["Отмена"])

    reply_markup = ReplyKeyboardMarkup(keyboard_buttons, one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text("Пожалуйста, выберите месяц для построения графика:", reply_markup=reply_markup)

    return GET_CHART_MONTH

async def generate_chart_for_month(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text == "Отмена":
        return await cancel(update, context)

    selected_month_str = update.message.text

    month_map_reverse = {
        "январь": 1, "февраль": 2, "март": 3, "апрель": 4,
        "май": 5, "июнь": 6, "июль": 7, "август": 8,
        "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12
    }

    try:
        parts = selected_month_str.lower().split()
        month_name = parts[0]
        year_str = parts[1]

        target_month = month_map_reverse[month_name]
        target_year = int(year_str)
    except (ValueError, KeyError, IndexError):
        await update.message.reply_text("Не удалось распознать месяц. Пожалуйста, попробуйте еще раз, используя кнопки.")
        return GET_CHART_MONTH

    await update.message.reply_text(f"Готовлю график за {selected_month_str}...")

    sheet = get_sheet()
    if not sheet:
        await show_main_menu(update, "❌ Не могу получить доступ к данным.")
        return ConversationHandler.END

    all_records = sheet.get_all_records()
    user_first_name = update.effective_user.first_name or "Пользователь"
    chat_id = update.effective_chat.id

    chart_image_buffer = await _generate_chart_image(
        chat_id, user_first_name, all_records, target_year, target_month
    )

    if chart_image_buffer:
        try:
            await context.bot.send_photo(chat_id=chat_id, photo=chart_image_buffer)
            await show_main_menu(update, "Вот твой график!")
        except Exception as e:
            print(f"Ошибка отправки графика: {e}")
            await show_main_menu(update, "❌ Не удалось отправить график.")
    else:
        await show_main_menu(update, f"Нет данных за {selected_month_str} для построения графика.")

    return ConversationHandler.END

async def send_monthly_chart_to_users(context: ContextTypes.DEFAULT_TYPE):
    today = date.today()
    if today.day != 1: return

    first_day_of_current_month = today.replace(day=1)
    last_day_of_previous_month = first_day_of_current_month - timedelta(days=1)
    target_year = last_day_of_previous_month.year
    target_month = last_day_of_previous_month.month

    charts_sent_data = load_json_with_firestore_sync(CHARTS_SENT_FILE, telegram_chat_id="global_data")
    all_profiles_data = load_json_with_firestore_sync(PROFILES_FILE, telegram_chat_id="global_data")
    sheet = get_sheet()
    if not sheet: return

    all_records_from_sheet = sheet.get_all_records()

    for chat_id_str, profile_data in all_profiles_data.items():
        month_key = f"{target_year}-{target_month:02d}"
        if month_key in charts_sent_data.get(chat_id_str, []):
            continue

        user_first_name = profile_data.get('first_name', 'Пользователь')
        chart_image_buffer = await _generate_chart_image(
            int(chat_id_str), user_first_name, all_records_from_sheet, target_year, target_month
        )

        if chart_image_buffer:
            try:
                await context.bot.send_photo(
                    chat_id=int(chat_id_str),
                    photo=chart_image_buffer,
                    caption=f"Привет, {user_first_name}! Вот твой дневник за {date(target_year, target_month, 1).strftime('%B %Y')}."
                )
                if chat_id_str not in charts_sent_data:
                    charts_sent_data[chat_id_str] = []
                charts_sent_data[chat_id_str].append(month_key)
            except Exception as e:
                print(f"Ошибка отправки ежемесячного графика пользователю {chat_id_str}: {e}")

    save_json_with_firestore_sync(charts_sent_data, CHARTS_SENT_FILE, telegram_chat_id="global_data")

async def ai_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_setup(update, context): return

    if not GEMINI_API_KEY:
        await show_main_menu(update, "Функция ИИ-анализа не настроена.")
        return

    await update.message.reply_text("🤖 Минутку, отправляю данные на анализ Искусственному Интеллекту...")

    sheet = get_sheet()
    if not sheet:
        await show_main_menu(update, "❌ Не могу получить доступ к данным.")
        return

    all_records = sheet.get_all_records()
    two_weeks_ago = datetime.now() - timedelta(days=14)
    recent_data = [
        rec for rec in all_records
        if 'Дата' in rec and rec.get('Дата') and datetime.strptime(rec['Дата'], "%d.%m.%Y") >= two_weeks_ago
    ]

    if not recent_data:
        await show_main_menu(update, "Недостаточно данных за последние 2 недели для анализа.")
        return

    chat_id = str(update.effective_chat.id)
    profiles = load_json_with_firestore_sync(PROFILES_FILE, telegram_chat_id="global_data")
    user_profile = profiles.get(chat_id, {})
    age = calculate_age(user_profile.get('dob')) if user_profile.get('dob') else 'не указан'
    sex = user_profile.get('sex', 'н/д')

    initial_prompt = f"""Ты — заботливый ИИ-врач, ассистент по имени Бронхитик. Проанализируй данные из дневника здоровья ребенка. Профиль ребенка: возраст {age}, пол {sex}. Данные за последние две недели: {str(recent_data)}. Твоя задача: 1. Кратко оцени общую динамику пикфлоуметрии (стабильная, падает, растет). Обрати внимание на разницу между утром и вечером. 2. Посмотри, есть ли дни с низкими показателями. Если есть, проверь, были ли в эти дни симптомы (кашель, затрудненное дыхание). 3. Сформулируй выводы в 2-3 коротких и понятных предложениях. 4. Дай одну главную, ободряющую рекомендацию. Пиши в дружелюбной и поддерживающей манере, обращаясь к родителю."""

    try:
        model = genai.GenerativeModel('gemini-1.5-flash-latest')
        response = model.generate_content(initial_prompt)

        await update.message.reply_text(response.text, reply_markup=ReplyKeyboardRemove())
        await show_main_menu(update, "ИИ-анализ завершен.")
    except Exception as e:
        print(f"Ошибка Gemini API: {e}")
        await show_main_menu(update, "❌ Не удалось получить ответ от ИИ. Попробуйте позже.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text
    if text == "🤖 Анализ ИИ":
        await ai_report(update, context)
    return ConversationHandler.END

async def clear_data_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reply_keyboard = [["Да", "Нет"], ["Отмена"]]
    await update.message.reply_text("ВНИМАНИЕ! Вы собираетесь полностью удалить все данные. Это действие необратимо. Вы уверены?", reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True))
    return CONFIRM_CLEAR_DATA

async def confirm_clear_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    response = update.message.text.lower()
    if response == "да":
        await update.message.reply_text("Начинаю очистку данных...")
        try:
            sheet = get_sheet()
            if sheet and sheet.row_count > 1:
                sheet.delete_rows(2, sheet.row_count)
            save_json_with_firestore_sync({}, PROFILES_FILE, telegram_chat_id="global_data")
            save_json_with_firestore_sync({}, REMINDERS_FILE, telegram_chat_id="global_data")
            save_json_with_firestore_sync({}, CHARTS_SENT_FILE, telegram_chat_id="global_data")
            await update.message.reply_text("✅ Все данные успешно очищены! Теперь давайте настроим ваш профиль.")
            return await profile_command(update, context)
        except Exception as e:
            print(f"Ошибка при очистке данных: {e}")
            await update.message.reply_text("❌ Произошла ошибка при очистке данных.")
            return ConversationHandler.END
    elif response in ["нет", "отмена"]:
        return await cancel(update, context)
    await update.message.reply_text("Пожалуйста, ответьте 'Да' или 'Нет'.")
    return CONFIRM_CLEAR_DATA

# --- Единый обработчик диалогов ---
main_conv = ConversationHandler(
    entry_points=[
        MessageHandler(filters.Regex("^✅ Сделать замер$"), start_logging),
        MessageHandler(filters.Regex("^📈 График$"), chart_start),
        CommandHandler("cleardata", clear_data_command),
        CommandHandler("profile", profile_command),
        CommandHandler("remind", remind_command)
    ],
    states={
        GET_PEAKFLOW: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_peakflow)],
        GET_BREATHING: [MessageHandler(filters.Regex("^(Да|Нет)$"), get_breathing)],
        GET_COUGH: [MessageHandler(filters.Regex("^(Да|Нет)$"), get_cough)],
        GET_SPUTUM: [MessageHandler(filters.Regex("^(Да|Нет)$"), get_sputum)],
        GET_MEDS: [MessageHandler(filters.Regex("^(Базисная терапия|Назначения при болезни|Нет)$"), get_meds_and_save)],
        CONFIRM_CLEAR_DATA: [MessageHandler(filters.Regex("^(Да|Нет)$"), confirm_clear_data)],
        SET_PROFILE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_profile)],
        GET_GENDER: [MessageHandler(filters.Regex("^(Мужской|Женский)$"), get_gender)],
        SET_REMINDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_reminder)],
        GET_CHART_MONTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, generate_chart_for_month)],
    },
    fallbacks=[CommandHandler("cancel", cancel), MessageHandler(filters.Regex("^Отмена$"), cancel)],
    allow_reentry=True
)

log_conv = main_conv
profile_conv = main_conv
remind_conv = main_conv
clear_data_conv = main_conv
