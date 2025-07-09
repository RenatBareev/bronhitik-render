# utils.py
import json
import os
from datetime import datetime, date, timezone, timedelta
import gspread
from google.oauth2.service_account import Credentials
import firebase_admin
from firebase_admin import credentials, firestore

# --- Глобальные переменные ---
db_firestore = None
app_id_global = None

def initialize_firebase_admin_sdk():
    """Инициализирует Firebase Admin SDK."""
    global db_firestore, app_id_global
    if firebase_admin._apps:
        return

    firebase_key = os.environ.get('FIREBASE_SERVICE_ACCOUNT_KEY')
    canvas_app_id = os.environ.get('__app_id', 'default-app-id')

    if not firebase_key:
        print("Критическая ошибка: Секрет FIREBASE_SERVICE_ACCOUNT_KEY не найден.")
        return

    try:
        creds_dict = json.loads(firebase_key)
        cred = credentials.Certificate(creds_dict)
        firebase_admin.initialize_app(cred)
        db_firestore = firestore.client()
        app_id_global = canvas_app_id
        print("Firebase Admin SDK успешно инициализирован.")
    except Exception as e:
        print(f"Критическая ошибка при инициализации Firebase: {e}")

def get_sheet():
    """
    Подключается к Google Таблице и возвращает рабочий лист.
    Использует ключ и URL из переменных окружения.
    """
    try:
        creds_json_str = os.environ.get('GSPREAD_CREDENTIALS')
        spreadsheet_url = os.environ.get('SPREADSHEET_URL')

        if not creds_json_str:
            print("Критическая ошибка: переменная окружения GSPREAD_CREDENTIALS не найдена.")
            return None
        if not spreadsheet_url:
            print("Критическая ошибка: переменная окружения SPREADSHEET_URL не найдена.")
            return None

        creds_dict = json.loads(creds_json_str)
        creds = Credentials.from_service_account_info(creds_dict)
        client = gspread.authorize(creds)

        print("Успешно подключился к Google API (gspread).")
        return client.open_by_url(spreadsheet_url).sheet1
    except Exception as e:
        print(f"Критическая ошибка при подключении к Google Sheets: {e}")
        return None

def load_json_with_firestore_sync(filename: str, telegram_chat_id: str = None) -> dict:
    collection_name = os.path.splitext(filename)[0]
    doc_id = "data"
    if db_firestore:
        user_id_for_path = telegram_chat_id if telegram_chat_id else "global_data"
        doc_ref = db_firestore.collection('artifacts').document(app_id_global).collection('users').document(user_id_for_path).collection(collection_name).document(doc_id)
        try:
            doc = doc_ref.get()
            if doc.exists: return doc.to_dict()
        except Exception as e:
            print(f"Ошибка загрузки из Firestore для {filename}: {e}.")
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_json_with_firestore_sync(data: dict, filename: str, telegram_chat_id: str = None):
    collection_name = os.path.splitext(filename)[0]
    doc_id = "data"
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Ошибка сохранения в локальный файл {filename}: {e}")
    if db_firestore:
        user_id_for_path = telegram_chat_id if telegram_chat_id else "global_data"
        doc_ref = db_firestore.collection('artifacts').document(app_id_global).collection('users').document(user_id_for_path).collection(collection_name).document(doc_id)
        try:
            doc_ref.set(data)
        except Exception as e:
            print(f"Ошибка сохранения в Firestore для {filename}: {e}")

def calculate_age(dob_str: str) -> str | None:
    if not isinstance(dob_str, str): return None
    try:
        birth_date = datetime.strptime(dob_str, "%d.%m.%Y").date()
        today = date.today()
        age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
        if 11 <= age % 100 <= 19: return f"{age} лет"
        if age % 10 == 1: return f"{age} год"
        if 2 <= age % 10 <= 4: return f"{age} года"
        return f"{age} лет"
    except (ValueError, TypeError): return None

def load_json(filename):
    try:
        with open(filename, 'r', encoding='utf-8') as f: return json.load(f)
    except: return {}

def save_json(data, filename):
    with open(filename, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4)

def get_spreadsheet_url():
    # Эта функция больше не нужна, так как URL берется из переменной окружения в get_sheet()
    return os.environ.get('SPREADSHEET_URL')
