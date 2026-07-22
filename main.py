import asyncio
import logging
import os
import json
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
import sqlite3
import html
import re

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    WebAppInfo
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
import aiohttp
from aiohttp import web

# ============================================
# НАСТРОЙКИ
# ============================================

TOKEN = os.getenv("TELEGRAM_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
WEB_USERNAME = os.getenv("WEB_USERNAME", "admin")
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "admin123")
LOGS_GROUP_ID = int(os.getenv("LOGS_GROUP_ID", "0"))
REVIEWS_GROUP_ID = int(os.getenv("REVIEWS_GROUP_ID", "0"))
PROMOTIONS_GROUP_ID = int(os.getenv("PROMOTIONS_GROUP_ID", "0"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================
# БАЗА ДАННЫХ
# ============================================

class Database:
    def __init__(self, db_path="relax.db"):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    role_id TEXT DEFAULT '',
                    prefix TEXT DEFAULT '',
                    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reputation INTEGER DEFAULT 0,
                    days_in_team INTEGER DEFAULT 0,
                    tickets_count INTEGER DEFAULT 0,
                    rating REAL DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    is_banned INTEGER DEFAULT 0,
                    is_muted INTEGER DEFAULT 0,
                    mute_until TIMESTAMP,
                    ban_reason TEXT
                );
                
                CREATE TABLE IF NOT EXISTS roles (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    prefix TEXT,
                    level INTEGER,
                    permissions TEXT,
                    can_see_logs INTEGER DEFAULT 0,
                    can_manage_roles INTEGER DEFAULT 0,
                    can_ban INTEGER DEFAULT 0,
                    can_mute INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_by INTEGER
                );
                
                CREATE TABLE IF NOT EXISTS tickets (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    user_name TEXT,
                    admin_id INTEGER DEFAULT 0,
                    admin_name TEXT DEFAULT '',
                    type TEXT,
                    category TEXT DEFAULT '',
                    status TEXT DEFAULT 'waiting',
                    message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    closed_at TIMESTAMP,
                    rating INTEGER,
                    review_text TEXT,
                    is_active INTEGER DEFAULT 1
                );
                
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id TEXT,
                    from_id INTEGER,
                    to_id INTEGER,
                    text TEXT,
                    is_admin INTEGER DEFAULT 0,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS promotion_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    user_id INTEGER,
                    user_name TEXT,
                    old_role TEXT,
                    new_role TEXT,
                    old_prefix TEXT,
                    new_prefix TEXT,
                    reason TEXT,
                    promoted_by INTEGER,
                    promoted_by_name TEXT,
                    action TEXT
                );
                
                CREATE TABLE IF NOT EXISTS ban_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    user_id INTEGER,
                    user_name TEXT,
                    action TEXT,
                    duration TEXT,
                    reason TEXT,
                    admin_id INTEGER,
                    admin_name TEXT
                );
                
                CREATE TABLE IF NOT EXISTS technical_requests (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    username TEXT,
                    category TEXT,
                    description TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reviewed_at TIMESTAMP,
                    reviewed_by INTEGER
                );
                
                CREATE TABLE IF NOT EXISTS web_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE,
                    password TEXT,
                    role TEXT DEFAULT 'viewer',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    expires_at TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS user_states (
                    user_id INTEGER PRIMARY KEY,
                    step TEXT,
                    ticket_id TEXT,
                    technical_type TEXT,
                    temp_message TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            
            # Создаем дефолтные роли
            default_roles = [
                ('owner', 'Владелец', '👑', 100, '["all"]', 1, 1, 1, 1),
                ('technical_admin', 'Технический администратор', '🛠', 80, '["view_logs","manage_technical","view_users"]', 1, 0, 0, 0),
                ('senior_admin', 'Главный администратор', '⭐', 70, '["view_logs","manage_tickets","view_users","manage_team"]', 1, 0, 1, 1),
                ('support_admin', 'Администратор поддержки', '💬', 50, '["manage_tickets","view_users"]', 0, 0, 0, 0),
                ('helper', 'Помощник', '🌱', 30, '["view_tickets"]', 0, 0, 0, 0),
            ]
            
            for role in default_roles:
                conn.execute("""
                    INSERT OR IGNORE INTO roles (id, name, prefix, level, permissions, can_see_logs, can_manage_roles, can_ban, can_mute)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, role)
            
            # Создаем веб-пользователя
            password_hash = hashlib.sha256(WEB_PASSWORD.encode()).hexdigest()
            conn.execute("""
                INSERT OR IGNORE INTO web_users (username, password, role)
                VALUES (?, ?, ?)
            """, (WEB_USERNAME, password_hash, 'owner'))
    
    def get_user(self, user_id: int) -> Optional[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            result = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return dict(result) if result else None
    
    def save_user(self, user: Dict):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO users (id, username, first_name, last_name, role_id, prefix, 
                    registered_at, reputation, days_in_team, tickets_count, rating, is_active, 
                    is_banned, is_muted, mute_until, ban_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user.get('id'), user.get('username'), user.get('first_name'), user.get('last_name'),
                user.get('role_id', ''), user.get('prefix', ''), user.get('registered_at', datetime.now()),
                user.get('reputation', 0), user.get('days_in_team', 0), user.get('tickets_count', 0),
                user.get('rating', 0), user.get('is_active', 1), user.get('is_banned', 0),
                user.get('is_muted', 0), user.get('mute_until'), user.get('ban_reason')
            ))
    
    def get_role(self, role_id: str) -> Optional[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            result = conn.execute("SELECT * FROM roles WHERE id = ?", (role_id,)).fetchone()
            return dict(result) if result else None
    
    def get_all_roles(self) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            results = conn.execute("SELECT * FROM roles ORDER BY level DESC").fetchall()
            return [dict(r) for r in results]
    
    def save_role(self, role: Dict):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO roles (id, name, prefix, level, permissions, can_see_logs, 
                    can_manage_roles, can_ban, can_mute, created_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                role.get('id'), role.get('name'), role.get('prefix'), role.get('level'),
                role.get('permissions', '[]'), role.get('can_see_logs', 0), role.get('can_manage_roles', 0),
                role.get('can_ban', 0), role.get('can_mute', 0), role.get('created_at', datetime.now()),
                role.get('created_by', 0)
            ))
    
    def delete_role(self, role_id: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM roles WHERE id = ?", (role_id,))
    
    def get_all_admins(self) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            results = conn.execute("""
                SELECT * FROM users WHERE role_id != '' AND is_active = 1 AND is_banned = 0
            """).fetchall()
            return [dict(r) for r in results]
    
    def get_all_users(self) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            results = conn.execute("SELECT * FROM users").fetchall()
            return [dict(r) for r in results]
    
    def save_ticket(self, ticket: Dict):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO tickets (id, user_id, user_name, admin_id, admin_name, type, 
                    category, status, message, created_at, closed_at, rating, review_text, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ticket.get('id'), ticket.get('user_id'), ticket.get('user_name'), 
                ticket.get('admin_id', 0), ticket.get('admin_name', ''),
                ticket.get('type'), ticket.get('category', ''), ticket.get('status', 'waiting'),
                ticket.get('message'), ticket.get('created_at', datetime.now()),
                ticket.get('closed_at'), ticket.get('rating'), ticket.get('review_text', ''),
                ticket.get('is_active', 1)
            ))
    
    def get_ticket(self, ticket_id: str) -> Optional[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            result = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
            return dict(result) if result else None
    
    def get_active_tickets(self) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            results = conn.execute("""
                SELECT * FROM tickets WHERE is_active = 1 AND status != 'closed'
            """).fetchall()
            return [dict(r) for r in results]
    
    def get_user_tickets(self, user_id: int) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            results = conn.execute("""
                SELECT * FROM tickets WHERE user_id = ? ORDER BY created_at DESC
            """, (user_id,)).fetchall()
            return [dict(r) for r in results]
    
    def save_message(self, msg: Dict):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO messages (ticket_id, from_id, to_id, text, is_admin, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                msg.get('ticket_id'), msg.get('from_id'), msg.get('to_id'),
                msg.get('text'), msg.get('is_admin', 0), msg.get('timestamp', datetime.now())
            ))
    
    def get_ticket_messages(self, ticket_id: str) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            results = conn.execute("""
                SELECT * FROM messages WHERE ticket_id = ? ORDER BY timestamp
            """, (ticket_id,)).fetchall()
            return [dict(r) for r in results]
    
    def save_technical_request(self, req: Dict):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO technical_requests (id, user_id, username, category, 
                    description, status, created_at, reviewed_at, reviewed_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                req.get('id'), req.get('user_id'), req.get('username'), req.get('category'),
                req.get('description'), req.get('status', 'pending'), req.get('created_at', datetime.now()),
                req.get('reviewed_at'), req.get('reviewed_by', 0)
            ))
    
    def get_technical_request(self, req_id: str) -> Optional[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            result = conn.execute("SELECT * FROM technical_requests WHERE id = ?", (req_id,)).fetchone()
            return dict(result) if result else None
    
    def get_pending_technical_requests(self) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            results = conn.execute("""
                SELECT * FROM technical_requests WHERE status = 'pending'
            """).fetchall()
            return [dict(r) for r in results]
    
    def add_promotion_log(self, log: Dict):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO promotion_logs (user_id, user_name, old_role, new_role, old_prefix, 
                    new_prefix, reason, promoted_by, promoted_by_name, action)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                log.get('user_id'), log.get('user_name'), log.get('old_role', ''),
                log.get('new_role', ''), log.get('old_prefix', ''), log.get('new_prefix', ''),
                log.get('reason', ''), log.get('promoted_by', 0), log.get('promoted_by_name', ''),
                log.get('action', '')
            ))
    
    def get_promotion_logs(self) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            results = conn.execute("""
                SELECT * FROM promotion_logs ORDER BY timestamp DESC
            """).fetchall()
            return [dict(r) for r in results]
    
    def add_ban_log(self, log: Dict):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO ban_logs (user_id, user_name, action, duration, reason, admin_id, admin_name)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                log.get('user_id'), log.get('user_name'), log.get('action'),
                log.get('duration', ''), log.get('reason', ''), log.get('admin_id', 0),
                log.get('admin_name', '')
            ))
    
    def get_ban_logs(self) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            results = conn.execute("SELECT * FROM ban_logs ORDER BY timestamp DESC").fetchall()
            return [dict(r) for r in results]
    
    def get_user_state(self, user_id: int) -> Dict:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            result = conn.execute(
                "SELECT * FROM user_states WHERE user_id = ?", (user_id,)
            ).fetchone()
            if result:
                return dict(result)
            return {'user_id': user_id, 'step': 'start', 'ticket_id': '', 'technical_type': '', 'temp_message': ''}
    
    def set_user_state(self, user_id: int, state: Dict):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO user_states (user_id, step, ticket_id, technical_type, temp_message, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                user_id, state.get('step', 'start'), state.get('ticket_id', ''),
                state.get('technical_type', ''), state.get('temp_message', ''), datetime.now()
            ))
    
    def clear_user_state(self, user_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,))
    
    def get_web_user(self, username: str) -> Optional[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            result = conn.execute("SELECT * FROM web_users WHERE username = ?", (username,)).fetchone()
            return dict(result) if result else None
    
    def set_session(self, session_id: str, user_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO sessions (id, user_id, expires_at)
                VALUES (?, ?, ?)
            """, (session_id, user_id, datetime.now() + timedelta(days=1)))
    
    def get_session(self, session_id: str) -> Optional[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            result = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if result:
                session = dict(result)
                if datetime.fromisoformat(session['expires_at']) > datetime.now():
                    return session
            return None
    
    def delete_session(self, session_id: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    
    def generate_ticket_id(self) -> str:
        with sqlite3.connect(self.db_path) as conn:
            result = conn.execute("SELECT MAX(CAST(id AS INTEGER)) FROM tickets").fetchone()
            next_id = (result[0] or 0) + 1000
            return str(next_id)
    
    def generate_technical_id(self) -> str:
        with sqlite3.connect(self.db_path) as conn:
            result = conn.execute("SELECT MAX(CAST(id AS INTEGER)) FROM technical_requests").fetchone()
            next_id = (result[0] or 0) + 500
            return str(next_id)

db = Database()

# ============================================
# СОСТОЯНИЯ
# ============================================

class UserStates(StatesGroup):
    start = State()
    waiting_message = State()
    waiting_technical = State()
    chatting = State()
    writing_review = State()

# ============================================
# КЛАВИАТУРЫ
# ============================================

def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💬 Написать администратору"), KeyboardButton(text="📞 Техническая поддержка")],
            [KeyboardButton(text="📂 Мои обращения"), KeyboardButton(text="ℹ️ Информация")]
        ],
        resize_keyboard=True
    )

def get_admin_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Активные обращения"), KeyboardButton(text="👥 Команда")],
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="👤 Мой профиль")],
            [KeyboardButton(text="🧑‍💻 Админ панель"), KeyboardButton(text="📜 История действий")]
        ],
        resize_keyboard=True
    )

def get_owner_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="👑 Управление ролями")],
            [KeyboardButton(text="📜 Логи"), KeyboardButton(text="📊 Полная статистика")]
        ],
        resize_keyboard=True
    )

def get_technical_categories_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👨‍💻 Заявка на администратора"), KeyboardButton(text="🐛 Ошибка")],
            [KeyboardButton(text="💡 Предложение"), KeyboardButton(text="🤝 Сотрудничество")],
            [KeyboardButton(text="❓ Другое")],
            [KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True
    )

# ============================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================

def is_admin(user_id: int) -> bool:
    user = db.get_user(user_id)
    return user and user.get('role_id') and user.get('is_active', 0) == 1

def is_owner(user_id: int) -> bool:
    user = db.get_user(user_id)
    return user and user.get('role_id') == 'owner'

def get_role_name(role_id: str) -> str:
    role = db.get_role(role_id)
    return role.get('name', 'Пользователь') if role else 'Пользователь'

# ============================================
# ОСНОВНОЙ БОТ
# ============================================

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ============================================
# ОБРАБОТЧИКИ КОМАНД
# ============================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    user = db.get_user(user_id)
    if not user:
        db.save_user({
            'id': user_id,
            'username': message.from_user.username,
            'first_name': message.from_user.first_name,
            'last_name': message.from_user.last_name,
            'role_id': '',
            'prefix': '',
            'registered_at': datetime.now(),
            'is_active': 1
        })
    
    await state.clear()
    
    keyboard = get_main_keyboard()
    if is_admin(user_id):
        keyboard = get_admin_keyboard()
    if is_owner(user_id):
        keyboard = get_owner_keyboard()
    
    await message.answer(
        f"🌿 Relax\n\nПривет, {message.from_user.first_name}! 👋\n"
        f"Добро пожаловать в бот поддержки Relax.\n\n"
        f"Наши администраторы готовы помочь тебе 💚",
        reply_markup=keyboard
    )

@dp.message(Command("profile"))
async def cmd_profile(message: types.Message):
    user_id = message.from_user.id
    user = db.get_user(user_id)
    
    if not user:
        await message.answer("❌ Профиль не найден.")
        return
    
    role_name = get_role_name(user.get('role_id', ''))
    
    text = (
        f"🌿 Relax Profile\n\n"
        f"{user.get('prefix', '')} {user.get('first_name')}\n\n"
        f"🛡 Ранг: {role_name}\n"
        f"⭐ Репутация: {user.get('reputation', 0)}\n"
        f"📅 Дней: {user.get('days_in_team', 0)}\n"
        f"🎫 Обращений: {user.get('tickets_count', 0)}\n"
        f"⭐ Оценка: {user.get('rating', 0):.1f}/5"
    )
    
    keyboard = get_main_keyboard()
    if is_admin(user_id):
        keyboard = get_admin_keyboard()
    if is_owner(user_id):
        keyboard = get_owner_keyboard()
    
    await message.answer(text, reply_markup=keyboard)

# ============================================
# ОБРАБОТЧИКИ ТЕКСТА
# ============================================

@dp.message(F.text == "💬 Написать администратору")
async def start_support(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    # Проверка на бан
    user = db.get_user(user_id)
    if user and user.get('is_banned', 0):
        await message.answer(f"❌ Вы забанены. Причина: {user.get('ban_reason', 'Не указана')}")
        return
    
    # Проверка на мут
    if user and user.get('is_muted', 0):
        mute_until = user.get('mute_until')
        if mute_until and datetime.fromisoformat(mute_until) > datetime.now():
            await message.answer(f"🔇 Вы замучены до {mute_until}")
            return
    
    await state.set_state(UserStates.waiting_message)
    await message.answer("💬 Опишите вашу проблему или вопрос:", reply_markup=types.ReplyKeyboardRemove())

@dp.message(F.text == "📞 Техническая поддержка")
async def start_technical(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    user = db.get_user(user_id)
    if user and user.get('is_banned', 0):
        await message.answer(f"❌ Вы забанены. Причина: {user.get('ban_reason', 'Не указана')}")
        return
    
    await state.set_state(UserStates.waiting_technical)
    await message.answer("📞 Выберите категорию технической поддержки:", reply_markup=get_technical_categories_keyboard())

@dp.message(F.text == "📂 Мои обращения")
async def my_tickets(message: types.Message):
    user_id = message.from_user.id
    tickets = db.get_user_tickets(user_id)
    
    if not tickets:
        await message.answer("📂 У вас нет обращений.", reply_markup=get_main_keyboard())
        return
    
    text = "📂 Ваши обращения:\n\n"
    for ticket in tickets[:10]:
        status = "🟡 В обработке"
        if ticket.get('status') == 'closed':
            status = "✅ Закрыто"
        text += f"🎫 #{ticket.get('id')} - {status}\n"
    
    if len(tickets) > 10:
        text += f"\nи ещё {len(tickets) - 10}..."
    
    await message.answer(text, reply_markup=get_main_keyboard())

@dp.message(F.text == "ℹ️ Информация")
async def info(message: types.Message):
    await message.answer(
        "🌿 Relax\n\n"
        "Версия: 2.0.0 (Python)\n"
        "Бот поддержки с веб-панелью.\n\n"
        "💚 Поддержка, которая рядом",
        reply_markup=get_main_keyboard()
    )

@dp.message(F.text == "🔙 Назад")
async def back(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await state.clear()
    
    keyboard = get_main_keyboard()
    if is_admin(user_id):
        keyboard = get_admin_keyboard()
    if is_owner(user_id):
        keyboard = get_owner_keyboard()
    
    await message.answer("🔙 Возврат в главное меню.", reply_markup=keyboard)

# ============================================
# АДМИНСКИЕ ОБРАБОТЧИКИ
# ============================================

@dp.message(F.text == "📋 Активные обращения")
async def active_tickets(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    tickets = db.get_active_tickets()
    if not tickets:
        await message.answer("📋 Активных обращений нет", reply_markup=get_admin_keyboard())
        return
    
    text = "📋 Активные обращения:\n\n"
    for ticket in tickets:
        status = "🟡 Ожидание"
        if ticket.get('status') == 'in_progress':
            status = f"🟢 В работе ({ticket.get('admin_name', 'Неизвестно')})"
        text += f"🎫 #{ticket.get('id')} - {status}\n"
    
    await message.answer(text, reply_markup=get_admin_keyboard())

@dp.message(F.text == "👥 Команда")
async def team_list(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    admins = db.get_all_admins()
    if not admins:
        await message.answer("👥 В команде нет администраторов", reply_markup=get_admin_keyboard())
        return
    
    text = "👥 Команда Relax:\n\n"
    for admin in admins:
        role_name = get_role_name(admin.get('role_id', ''))
        text += f"{admin.get('prefix', '')} {admin.get('first_name')} - {role_name}\n"
    
    await message.answer(text, reply_markup=get_admin_keyboard())

@dp.message(F.text == "📊 Статистика")
async def stats(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    user = db.get_user(message.from_user.id)
    text = (
        f"📊 Статистика администратора\n\n"
        f"⭐ Репутация: {user.get('reputation', 0)}\n"
        f"🎫 Обращений: {user.get('tickets_count', 0)}\n"
        f"⭐ Оценка: {user.get('rating', 0):.1f}/5\n"
        f"📅 Дней: {user.get('days_in_team', 0)}"
    )
    await message.answer(text, reply_markup=get_admin_keyboard())

@dp.message(F.text == "👤 Мой профиль")
async def profile_button(message: types.Message):
    await cmd_profile(message)

@dp.message(F.text == "🧑‍💻 Админ панель")
async def admin_panel(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎫 Обращения", callback_data="admin_panel_tickets"),
        InlineKeyboardButton(text="👥 Команда", callback_data="admin_panel_team")
    )
    builder.row(
        InlineKeyboardButton(text="📊 Статистика", callback_data="admin_panel_stats"),
        InlineKeyboardButton(text="👤 Профиль", callback_data="admin_panel_profile")
    )
    
    await message.answer("🧑‍💻 Админ панель:", reply_markup=builder.as_markup())

@dp.message(F.text == "📜 История действий")
async def logs(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    logs = db.get_promotion_logs()
    if not logs:
        await message.answer("📜 Логов пока нет", reply_markup=get_admin_keyboard())
        return
    
    text = "📜 Последние логи:\n\n"
    for log in logs[:10]:
        text += f"{log.get('timestamp')[:16]}: {log.get('action')}\n"
        if log.get('action') == 'promotion':
            text += f"  {log.get('old_role', '')} → {log.get('new_role', '')}\n"
    
    await message.answer(text, reply_markup=get_admin_keyboard())

@dp.message(F.text == "⚙️ Настройки")
async def settings(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    
    await message.answer(
        "⚙️ Настройки Relax\n\n"
        "Для изменения настроек используйте веб-панель.",
        reply_markup=get_owner_keyboard()
    )

@dp.message(F.text == "👑 Управление ролями")
async def manage_roles(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    
    roles = db.get_all_roles()
    text = "👑 Управление ролями\n\n"
    for role in roles:
        text += f"{role.get('prefix')} {role.get('name')} (Уровень {role.get('level')})\n"
    
    text += "\nУправляйте ролями в веб-панели."
    await message.answer(text, reply_markup=get_owner_keyboard())

@dp.message(F.text == "📜 Логи")
async def logs_owner(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    await logs(message)

@dp.message(F.text == "📊 Полная статистика")
async def full_stats(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    
    users = db.get_all_users()
    admins = db.get_all_admins()
    tickets = db.get_active_tickets()
    
    text = (
        f"📊 Полная статистика\n\n"
        f"👥 Пользователей: {len(users)}\n"
        f"🧑‍💻 Администраторов: {len(admins)}\n"
        f"🎫 Активных обращений: {len(tickets)}"
    )
    await message.answer(text, reply_markup=get_owner_keyboard())

# ============================================
# ОБРАБОТЧИКИ CALLBACK
# ============================================

@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    data = callback.data
    
    if data.startswith("take_"):
        await take_ticket(callback)
    elif data.startswith("skip_"):
        await skip_ticket(callback)
    elif data.startswith("close_"):
        await close_ticket(callback)
    elif data.startswith("rate_"):
        await rate_ticket(callback)
    elif data.startswith("tech_approve_"):
        await approve_technical(callback)
    elif data.startswith("tech_reject_"):
        await reject_technical(callback)
    elif data.startswith("tech_open_"):
        await open_technical(callback)
    elif data == "admin_panel_tickets":
        await admin_tickets_list(callback)
    elif data == "admin_panel_team":
        await admin_team_list(callback)
    elif data == "admin_panel_stats":
        await admin_stats(callback)
    elif data == "admin_panel_profile":
        await admin_profile(callback)
    
    await callback.answer()

async def take_ticket(callback: types.CallbackQuery):
    ticket_id = callback.data.replace("take_", "")
    admin_id = callback.from_user.id
    admin_name = callback.from_user.first_name
    
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        await callback.answer("❌ Тикет не найден")
        return
    
    if ticket.get('status') != 'waiting':
        await callback.answer("❌ Это обращение уже взято")
        return
    
    ticket['admin_id'] = admin_id
    ticket['admin_name'] = admin_name
    ticket['status'] = 'in_progress'
    db.save_ticket(ticket)
    
    admin = db.get_user(admin_id)
    if admin:
        admin['tickets_count'] = admin.get('tickets_count', 0) + 1
        db.save_user(admin)
    
    await bot.send_message(
        ticket['user_id'],
        f"✅ Ваше обращение принято.\n\nАдминистратор: {admin_name}\nОжидайте ответа 💚"
    )
    
    state = {'step': 'chatting', 'ticket_id': ticket_id}
    db.set_user_state(ticket['user_id'], state)
    db.set_user_state(admin_id, state)
    
    await callback.message.edit_text("✅ Обращение взято")
    await callback.answer("Вы взяли обращение")

async def skip_ticket(callback: types.CallbackQuery):
    ticket_id = callback.data.replace("skip_", "")
    
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        await callback.answer("❌ Тикет не найден")
        return
    
    ticket['status'] = 'waiting'
    ticket['admin_id'] = 0
    ticket['admin_name'] = ''
    db.save_ticket(ticket)
    
    await bot.send_message(
        ticket['user_id'],
        "🌿 Мы уже ищем свободного администратора.\nПожалуйста, ожидайте 💚"
    )
    
    await callback.message.edit_text("❌ Пропущено")
    await callback.answer("Обращение пропущено")

async def close_ticket(callback: types.CallbackQuery):
    ticket_id = callback.data.replace("close_", "")
    
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        await callback.answer("❌ Тикет не найден")
        return
    
    ticket['status'] = 'closed'
    ticket['closed_at'] = datetime.now().isoformat()
    ticket['is_active'] = 0
    db.save_ticket(ticket)
    
    db.clear_user_state(ticket['user_id'])
    db.clear_user_state(ticket['admin_id'])
    
    builder = InlineKeyboardBuilder()
    for i in range(1, 6):
        builder.add(InlineKeyboardButton(text=f"⭐ {i}", callback_data=f"rate_{i}_{ticket_id}"))
    builder.adjust(3, 2)
    
    await bot.send_message(
        ticket['user_id'],
        "✅ Обращение закрыто.\n\nСпасибо! 💚\nОцените работу:",
        reply_markup=builder.as_markup()
    )
    
    await callback.message.edit_text("🔒 Закрыто")
    await callback.answer("Обращение закрыто")

async def rate_ticket(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    rating = int(parts[1])
    ticket_id = parts[2]
    
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        await callback.answer("❌ Ошибка")
        return
    
    await callback.answer("Спасибо! Напишите отзыв:")
    
    state = {
        'step': 'writing_review',
        'ticket_id': ticket_id,
        'temp_message': str(rating)
    }
    db.set_user_state(callback.from_user.id, state)
    
    ticket['rating'] = rating
    db.save_ticket(ticket)
    
    await bot.send_message(
        callback.from_user.id,
        "✍️ Напишите текстовый отзыв о работе администратора:"
    )

async def approve_technical(callback: types.CallbackQuery):
    tech_id = callback.data.replace("tech_approve_", "")
    
    req = db.get_technical_request(tech_id)
    if not req:
        await callback.answer("❌ Ошибка")
        return
    
    req['status'] = 'approved'
    req['reviewed_at'] = datetime.now().isoformat()
    req['reviewed_by'] = callback.from_user.id
    db.save_technical_request(req)
    
    await bot.send_message(
        req['user_id'],
        f"✅ Ваша заявка #{tech_id} одобрена! 💚"
    )
    
    await callback.message.edit_text("✅ Одобрено")
    await callback.answer("Заявка одобрена")

async def reject_technical(callback: types.CallbackQuery):
    tech_id = callback.data.replace("tech_reject_", "")
    
    req = db.get_technical_request(tech_id)
    if not req:
        await callback.answer("❌ Ошибка")
        return
    
    req['status'] = 'rejected'
    req['reviewed_at'] = datetime.now().isoformat()
    req['reviewed_by'] = callback.from_user.id
    db.save_technical_request(req)
    
    await bot.send_message(
        req['user_id'],
        f"❌ Ваша заявка #{tech_id} отклонена."
    )
    
    await callback.message.edit_text("❌ Отклонено")
    await callback.answer("Заявка отклонена")

async def open_technical(callback: types.CallbackQuery):
    tech_id = callback.data.replace("tech_open_", "")
    
    req = db.get_technical_request(tech_id)
    if not req:
        await callback.answer("❌ Ошибка")
        return
    
    text = (
        f"📞 Заявка #{tech_id}\n\n"
        f"Тип: {req.get('category')}\n"
        f"От: {req.get('username')}\n"
        f"Описание: {req.get('description')}\n"
        f"Статус: {req.get('status')}"
    )
    
    await callback.message.answer(text)
    await callback.answer()

async def admin_tickets_list(callback: types.CallbackQuery):
    tickets = db.get_active_tickets()
    if not tickets:
        await callback.answer("Нет активных обращений")
        return
    
    text = "🎫 Активные обращения:\n\n"
    for ticket in tickets:
        text += f"#{ticket.get('id')} - {ticket.get('user_name')}\n"
    
    await callback.message.answer(text)

async def admin_team_list(callback: types.CallbackQuery):
    admins = db.get_all_admins()
    text = "👥 Команда:\n\n"
    for admin in admins:
        role_name = get_role_name(admin.get('role_id', ''))
        text += f"{admin.get('prefix', '')} {admin.get('first_name')} - {role_name}\n"
    
    await callback.message.answer(text)

async def admin_stats(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    text = (
        f"📊 Статистика\n\n"
        f"⭐ Репутация: {user.get('reputation', 0)}\n"
        f"🎫 Обращений: {user.get('tickets_count', 0)}\n"
        f"⭐ Оценка: {user.get('rating', 0):.1f}/5"
    )
    await callback.message.answer(text)

async def admin_profile(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    role_name = get_role_name(user.get('role_id', ''))
    text = (
        f"👤 Профиль\n\n"
        f"Имя: {user.get('first_name')}\n"
        f"Ранг: {role_name}\n"
        f"Префикс: {user.get('prefix', '')}\n"
        f"⭐ Репутация: {user.get('reputation', 0)}"
    )
    await callback.message.answer(text)

# ============================================
# ОСНОВНЫЕ ОБРАБОТЧИКИ СООБЩЕНИЙ
# ============================================

@dp.message(UserStates.waiting_message)
async def handle_support_message(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text
    
    ticket_id = db.generate_ticket_id()
    user = db.get_user(user_id)
    
    ticket = {
        'id': ticket_id,
        'user_id': user_id,
        'user_name': user.get('first_name', 'Пользователь'),
        'type': 'support',
        'status': 'waiting',
        'message': text,
        'created_at': datetime.now().isoformat(),
        'is_active': 1
    }
    db.save_ticket(ticket)
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Взять обращение", callback_data=f"take_{ticket_id}"),
        InlineKeyboardButton(text="❌ Пропустить", callback_data=f"skip_{ticket_id}")
    )
    
    admins = db.get_all_admins()
    for admin in admins:
        try:
            await bot.send_message(
                admin['id'],
                f"🌿 Relax Panel\n\n"
                f"Новое обращение!\n\n"
                f"👤 Пользователь: {user.get('first_name')}\n"
                f"📌 Тип: Поддержка\n"
                f"💬 Сообщение: {text[:200]}\n\n"
                f"🟡 Статус: Ожидание\n"
                f"🎫 #{ticket_id}",
                reply_markup=builder.as_markup()
            )
        except:
            pass
    
    await message.answer(
        f"✅ Ваше обращение принято.\n\nОжидайте ответа 💚\n🎫 #{ticket_id}",
        reply_markup=get_main_keyboard()
    )
    await state.clear()

@dp.message(UserStates.waiting_technical)
async def handle_technical_message(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text
    state_data = await state.get_data()
    
    if text == "🔙 Назад":
        await state.clear()
        await message.answer("Возврат в главное меню.", reply_markup=get_main_keyboard())
        return
    
    categories = ["👨‍💻 Заявка на администратора", "🐛 Ошибка", "💡 Предложение", "🤝 Сотрудничество", "❓ Другое"]
    
    if text not in categories:
        await message.answer("Пожалуйста, выберите категорию из списка.")
        return
    
    await state.update_data(technical_type=text)
    await state.set_state("waiting_technical_description")
    await message.answer("📝 Опишите вашу проблему подробно:")

@dp.message(lambda m: m.text and m.text not in ["🔙 Назад", "👨‍💻 Заявка на администратора", "🐛 Ошибка", "💡 Предложение", "🤝 Сотрудничество", "❓ Другое"])
async def handle_technical_description(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text
    state_data = await state.get_data()
    technical_type = state_data.get('technical_type', '')
    
    if not technical_type:
        await message.answer("Пожалуйста, выберите категорию сначала.")
        return
    
    tech_id = db.generate_technical_id()
    user = db.get_user(user_id)
    
    req = {
        'id': tech_id,
        'user_id': user_id,
        'username': user.get('first_name', 'Пользователь'),
        'category': technical_type,
        'description': text,
        'status': 'pending',
        'created_at': datetime.now().isoformat()
    }
    db.save_technical_request(req)
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="👀 Открыть", callback_data=f"tech_open_{tech_id}"),
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"tech_approve_{tech_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"tech_reject_{tech_id}")
    )
    
    admins = db.get_all_admins()
    for admin in admins:
        role = db.get_role(admin.get('role_id', ''))
        if role and role.get('name') == 'Технический администратор':
            try:
                await bot.send_message(
                    admin['id'],
                    f"🌿 Relax Technical Panel\n\n"
                    f"Новая заявка!\n"
                    f"📞 #{tech_id}\n"
                    f"Тип: {technical_type}\n"
                    f"От: {user.get('first_name')}\n"
                    f"Описание: {text[:200]}",
                    reply_markup=builder.as_markup()
                )
            except:
                pass
    
    await message.answer(
        f"✅ Ваша заявка принята.\n\n"
        f"📞 #{tech_id}\n"
        f"Тип: {technical_type}\n"
        f"Статус: Рассмотрение",
        reply_markup=get_main_keyboard()
    )
    await state.clear()

@dp.message(UserStates.writing_review)
async def handle_review(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    state_data = await state.get_data()
    
    ticket_id = state_data.get('ticket_id')
    rating = int(state_data.get('temp_message', 0))
    review_text = message.text
    
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        await message.answer("❌ Ошибка: обращение не найдено")
        await state.clear()
        return
    
    ticket['review_text'] = review_text
    db.save_ticket(ticket)
    
    admin = db.get_user(ticket['admin_id'])
    if admin:
        admin['tickets_count'] = admin.get('tickets_count', 0) + 1
        admin['rating'] = (admin.get('rating', 0) * (admin.get('tickets_count', 0) - 1) + rating) / admin['tickets_count']
        admin['reputation'] = admin.get('reputation', 0) + rating
        db.save_user(admin)
    
    if REVIEWS_GROUP_ID:
        try:
            await bot.send_message(
                REVIEWS_GROUP_ID,
                f"🌟 НОВЫЙ ОТЗЫВ\n\n"
                f"👤 Пользователь: {ticket.get('user_name')}\n"
                f"💬 Отзыв: \"{review_text}\"\n"
                f"⭐ Оценка: {rating}/5\n"
                f"👨‍💼 Админ: {ticket.get('admin_name', 'Неизвестно')}\n"
                f"🎫 #{ticket_id}"
            )
        except:
            pass
    
    await message.answer(
        f"✅ Спасибо! Оценка: {rating}/5\nОтзыв: \"{review_text}\"",
        reply_markup=get_main_keyboard()
    )
    await state.clear()

@dp.message(UserStates.chatting)
async def handle_chat_message(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text
    state_data = await state.get_data()
    ticket_id = state_data.get('ticket_id')
    
    if not ticket_id:
        await message.answer("❌ Ошибка: не найден активный диалог")
        await state.clear()
        return
    
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        await message.answer("❌ Ошибка: обращение не найдено")
        await state.clear()
        return
    
    if is_admin(user_id):
        # Админ пишет пользователю
        await bot.send_message(
            ticket['user_id'],
            f"💬 Сообщение от администратора:\n\n{text}"
        )
        
        db.save_message({
            'ticket_id': ticket_id,
            'from_id': user_id,
            'to_id': ticket['user_id'],
            'text': text,
            'is_admin': 1,
            'timestamp': datetime.now().isoformat()
        })
        
        await message.answer("✅ Сообщение отправлено пользователю.")
        
    else:
        # Пользователь пишет админу
        if ticket.get('admin_id'):
            admin_msg = f"💬 Сообщение от пользователя:\n\n{text}"
            
            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(text="🔒 Закрыть", callback_data=f"close_{ticket_id}")
            )
            
            await bot.send_message(
                ticket['admin_id'],
                admin_msg,
                reply_markup=builder.as_markup()
            )
            
            db.save_message({
                'ticket_id': ticket_id,
                'from_id': user_id,
                'to_id': ticket['admin_id'],
                'text': text,
                'is_admin': 0,
                'timestamp': datetime.now().isoformat()
            })
            
            await message.answer("✅ Сообщение отправлено администратору.")

# ============================================
# AI ПОМОЩНИК
# ============================================

@dp.message(Command("ask"))
async def ai_help(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    query = message.text.replace("/ask", "").strip()
    if not query:
        await message.answer("🤖 Использование: /ask [вопрос]")
        return
    
    # Простой ответ без DeepSeek API (можно добавить позже)
    await message.answer(
        "🤖 Relax AI\n\n"
        "Возможный ответ:\n"
        "Здравствуйте! Опишите подробнее вашу проблему, "
        "и мы постараемся помочь."
    )

# ============================================
# ВЕБ-СЕРВЕР
# ============================================

routes = web.RouteTableDef()

@routes.get('/')
async def web_root(request):
    return web.HTTPFound('/dashboard')

@routes.get('/login')
async def web_login_get(request):
    html = '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>🌿 Relax - Вход</title>
        <style>
            *{margin:0;padding:0;box-sizing:border-box}
            body{font-family:'Segoe UI',sans-serif;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);height:100vh;display:flex;justify-content:center;align-items:center}
            .container{background:white;padding:40px;border-radius:16px;box-shadow:0 20px 60px rgba(0,0,0,0.3);width:400px}
            h1{color:#2d3748;margin-bottom:8px}
            .subtitle{color:#718096;margin-bottom:30px}
            input{width:100%;padding:12px;border:2px solid #e2e8f0;border-radius:8px;margin-bottom:16px;font-size:16px}
            input:focus{outline:none;border-color:#667eea}
            button{width:100%;padding:12px;background:linear-gradient(135deg,#667eea,#764ba2);color:white;border:none;border-radius:8px;font-size:16px;font-weight:600;cursor:pointer}
            button:hover{transform:translateY(-2px)}
            .error{color:#e53e3e;margin-top:12px}
            .logo{font-size:48px;margin-bottom:16px}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="logo">🌿</div>
            <h1>Relax Panel</h1>
            <p class="subtitle">Войдите в панель управления</p>
            <form method="POST">
                <input type="text" name="username" placeholder="Логин" required>
                <input type="password" name="password" placeholder="Пароль" required>
                <button type="submit">Войти</button>
            </form>
        </div>
    </body>
    </html>
    '''
    return web.Response(text=html, content_type='text/html')

@routes.post('/login')
async def web_login_post(request):
    data = await request.post()
    username = data.get('username')
    password = data.get('password')
    
    web_user = db.get_web_user(username)
    if not web_user:
        return web.HTTPFound('/login?error=1')
    
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    if web_user['password'] != password_hash:
        return web.HTTPFound('/login?error=1')
    
    session_id = secrets.token_urlsafe(32)
    db.set_session(session_id, web_user['id'])
    
    response = web.HTTPFound('/dashboard')
    response.set_cookie('session_id', session_id, max_age=86400)
    return response

@routes.get('/logout')
async def web_logout(request):
    session_id = request.cookies.get('session_id')
    if session_id:
        db.delete_session(session_id)
    response = web.HTTPFound('/login')
    response.del_cookie('session_id')
    return response

@routes.get('/dashboard')
async def web_dashboard(request):
    session_id = request.cookies.get('session_id')
    if not session_id:
        return web.HTTPFound('/login')
    
    session = db.get_session(session_id)
    if not session:
        return web.HTTPFound('/login')
    
    user = db.get_user(session['user_id'])
    users = db.get_all_users()
    admins = db.get_all_admins()
    tickets = db.get_active_tickets()
    
    html = f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>🌿 Relax - Панель</title>
        <style>
            *{{margin:0;padding:0;box-sizing:border-box}}
            body{{font-family:'Segoe UI',sans-serif;background:#f7fafc}}
            .header{{background:white;padding:20px 40px;box-shadow:0 2px 4px rgba(0,0,0,0.1);display:flex;justify-content:space-between;align-items:center}}
            .header h1{{color:#2d3748}}
            .header a{{color:#667eea;text-decoration:none;margin-left:20px}}
            .container{{padding:40px;max-width:1400px;margin:0 auto}}
            .stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:20px;margin-bottom:40px}}
            .stat{{background:white;padding:24px;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.08)}}
            .stat .num{{font-size:32px;font-weight:700;color:#2d3748}}
            .stat .label{{color:#718096;font-size:14px}}
            .menu{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:20px}}
            .menu a{{background:white;padding:30px;border-radius:12px;text-align:center;text-decoration:none;color:#2d3748;box-shadow:0 2px 8px rgba(0,0,0,0.08);transition:0.2s}}
            .menu a:hover{{transform:translateY(-4px);box-shadow:0 4px 16px rgba(0,0,0,0.12)}}
            .menu .icon{{font-size:40px;display:block;margin-bottom:12px}}
            .menu .title{{font-weight:600}}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>🌿 Relax Panel</h1>
            <div>
                <span>{user.get('first_name', 'Admin')}</span>
                <a href="/logout">Выйти</a>
            </div>
        </div>
        <div class="container">
            <div class="stats">
                <div class="stat"><div class="num">{len(users)}</div><div class="label">Пользователей</div></div>
                <div class="stat"><div class="num">{len(tickets)}</div><div class="label">Активных обращений</div></div>
                <div class="stat"><div class="num">{len(admins)}</div><div class="label">Администраторов</div></div>
            </div>
            <div class="menu">
                <a href="/logs"><span class="icon">📜</span><div class="title">Логи</div></a>
                {'<a href="/console"><span class="icon">🖥️</span><div class="title">Консоль</div></a>' if user.get('role_id') == 'owner' else ''}
            </div>
        </div>
    </body>
    </html>
    '''
    return web.Response(text=html, content_type='text/html')

@routes.get('/logs')
async def web_logs(request):
    session_id = request.cookies.get('session_id')
    if not session_id:
        return web.HTTPFound('/login')
    
    session = db.get_session(session_id)
    if not session:
        return web.HTTPFound('/login')
    
    user = db.get_user(session['user_id'])
    role = db.get_role(user.get('role_id', ''))
    
    if not role or not role.get('can_see_logs'):
        return web.Response(text="Доступ запрещен", status=403)
    
    logs = db.get_promotion_logs()
    ban_logs = db.get_ban_logs()
    
    html_rows = ""
    for log in logs[:50]:
        html_rows += f'''
        <tr>
            <td>{log.get('timestamp', '')[:16]}</td>
            <td><span class="badge promotion">Повышение</span></td>
            <td>{log.get('user_name')} → {log.get('new_role')}</td>
            <td>{log.get('promoted_by_name')}</td>
        </tr>
        '''
    
    for log in ban_logs[:50]:
        badge = "ban" if log.get('action') == 'ban' else "mute"
        label = "Бан" if log.get('action') == 'ban' else "Мут"
        html_rows += f'''
        <tr>
            <td>{log.get('timestamp', '')[:16]}</td>
            <td><span class="badge {badge}">{label}</span></td>
            <td>{log.get('user_name')} - {log.get('reason', '')}</td>
            <td>{log.get('admin_name')}</td>
        </tr>
        '''
    
    html = f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>🌿 Relax - Логи</title>
        <style>
            *{{margin:0;padding:0;box-sizing:border-box}}
            body{{font-family:'Segoe UI',sans-serif;background:#f7fafc}}
            .header{{background:white;padding:20px 40px;box-shadow:0 2px 4px rgba(0,0,0,0.1);display:flex;justify-content:space-between;align-items:center}}
            .header a{{color:#667eea;text-decoration:none}}
            .container{{padding:40px;max-width:1400px;margin:0 auto}}
            table{{width:100%;background:white;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08)}}
            th{{background:#f7fafc;padding:12px 16px;text-align:left;font-weight:600}}
            td{{padding:12px 16px;border-top:1px solid #e2e8f0}}
            .badge{{padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600;color:white}}
            .promotion{{background:#48bb78}}
            .ban{{background:#fc8181}}
            .mute{{background:#ed8936}}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>📜 Логи системы</h1>
            <a href="/dashboard">← Назад</a>
        </div>
        <div class="container">
            <table>
                <thead>
                    <tr><th>Время</th><th>Тип</th><th>Действие</th><th>Кем</th></tr>
                </thead>
                <tbody>
                    {html_rows}
                </tbody>
            </table>
        </div>
    </body>
    </html>
    '''
    return web.Response(text=html, content_type='text/html')

@routes.get('/console')
async def web_console(request):
    session_id = request.cookies.get('session_id')
    if not session_id:
        return web.HTTPFound('/login')
    
    session = db.get_session(session_id)
    if not session:
        return web.HTTPFound('/login')
    
    user = db.get_user(session['user_id'])
    if not user or user.get('role_id') != 'owner':
        return web.Response(text="Доступ запрещен. Только для владельца", status=403)
    
    users = db.get_all_users()
    roles = db.get_all_roles()
    
    user_rows = ""
    for u in users[:50]:
        role_name = get_role_name(u.get('role_id', ''))
        banned = "🔴 Забанен" if u.get('is_banned') else ""
        muted = "🔇 Замучен" if u.get('is_muted') else ""
        user_rows += f'''
        <div class="user-item">
            <div>
                {u.get('prefix', '')} <strong>{u.get('first_name')}</strong> ({role_name})
                {banned} {muted}
            </div>
            <div>
                <button onclick="promoteUser({u.get('id')})">⬆</button>
                {'<button onclick="banUser('+str(u.get('id'))+')">🔨</button>' if not u.get('is_banned') else '<button onclick="unbanUser('+str(u.get('id'))+')">🔓</button>'}
                {'<button onclick="muteUser('+str(u.get('id'))+')">🔇</button>' if not u.get('is_muted') else '<button onclick="unmuteUser('+str(u.get('id'))+')">🔊</button>'}
            </div>
        </div>
        '''
    
    role_options = ""
    for role in roles:
        role_options += f'<option value="{role.get("id")}">{role.get("prefix")} {role.get("name")}</option>'
    
    html = f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>🌿 Relax - Консоль</title>
        <style>
            *{{margin:0;padding:0;box-sizing:border-box}}
            body{{font-family:'Segoe UI',sans-serif;background:#1a202c;color:#e2e8f0}}
            .header{{background:#2d3748;padding:20px 40px;display:flex;justify-content:space-between;align-items:center}}
            .header h1{{color:#48bb78}}
            .header a{{color:#a0aec0;text-decoration:none}}
            .container{{padding:40px;max-width:1400px;margin:0 auto;display:grid;grid-template-columns:1fr 1fr;gap:30px}}
            .panel{{background:#2d3748;border-radius:12px;padding:24px;border:1px solid #4a5568}}
            .panel h2{{color:#48bb78;margin-bottom:16px;border-bottom:1px solid #4a5568;padding-bottom:12px}}
            .user-item{{padding:12px;border-bottom:1px solid #4a5568;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}}
            .user-item:hover{{background:#4a5568}}
            button{{padding:6px 14px;border:none;border-radius:6px;cursor:pointer;font-weight:600}}
            button:hover{{transform:scale(1.05)}}
            .form-group{{margin-bottom:16px}}
            .form-group label{{display:block;color:#a0aec0;margin-bottom:4px;font-size:14px}}
            .form-group input,.form-group select{{width:100%;padding:10px;background:#1a202c;border:1px solid #4a5568;border-radius:6px;color:#e2e8f0}}
            .form-group input:focus{{outline:none;border-color:#48bb78}}
            .btn-submit{{width:100%;padding:12px;background:#48bb78;color:#1a202c;border:none;border-radius:6px;font-weight:600;cursor:pointer}}
            .btn-submit:hover{{background:#38a169}}
            .status{{padding:12px;border-radius:6px;margin-bottom:16px}}
            .status-success{{background:#276749;color:#48bb78}}
            .status-error{{background:#742a2a;color:#fc8181}}
            .role-item{{padding:8px 12px;background:#1a202c;border-radius:6px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center}}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>🖥️ Консоль управления</h1>
            <a href="/dashboard">← Назад</a>
        </div>
        <div class="container">
            <div class="panel">
                <h2>👥 Пользователи</h2>
                <div id="userList">
                    {user_rows}
                </div>
            </div>
            <div>
                <div class="panel">
                    <h2>⚡ Действия</h2>
                    <div id="statusMessages"></div>
                    <div class="form-group">
                        <label>Пользователь</label>
                        <select id="actionUser">
                            {''.join([f'<option value="{u.get("id")}">{u.get("prefix", "")} {u.get("first_name")}</option>' for u in users[:50]])}
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Действие</label>
                        <select id="actionType" onchange="toggleFields()">
                            <option value="promote">Повысить</option>
                            <option value="ban">Забанить</option>
                            <option value="mute">Замутить</option>
                            <option value="unban">Разбанить</option>
                            <option value="unmute">Размутить</option>
                        </select>
                    </div>
                    <div id="roleFields">
                        <div class="form-group">
                            <label>Новая роль</label>
                            <select id="newRole">{role_options}</select>
                        </div>
                        <div class="form-group">
                            <label>Префикс</label>
                            <input type="text" id="newPrefix" placeholder="#Префикс">
                        </div>
                    </div>
                    <div class="form-group">
                        <label>Причина</label>
                        <input type="text" id="reason" placeholder="Причина">
                    </div>
                    <button class="btn-submit" onclick="executeAction()">▶ Выполнить</button>
                </div>
                <div class="panel" style="margin-top:20px;">
                    <h2>👑 Роли</h2>
                    {''.join([f'<div class="role-item"><div>{r.get("prefix")} {r.get("name")} (Ур. {r.get("level")})</div><button onclick="deleteRole(\'{r.get("id")}\')">🗑️</button></div>' for r in roles])}
                    <div style="margin-top:16px;">
                        <h3 style="color:#a0aec0;font-size:14px;margin-bottom:8px;">Новая роль</h3>
                        <div class="form-group"><input type="text" id="newRoleName" placeholder="Название"></div>
                        <div class="form-group"><input type="text" id="newRolePrefix" placeholder="Префикс (👑)"></div>
                        <div class="form-group"><input type="number" id="newRoleLevel" value="40"></div>
                        <button class="btn-submit" onclick="createRole()">➕ Создать</button>
                    </div>
                </div>
            </div>
        </div>
        <script>
            function toggleFields() {{
                const type = document.getElementById('actionType').value;
                document.getElementById('roleFields').style.display = type === 'promote' ? 'block' : 'none';
            }}
            
            function showStatus(msg, type) {{
                const el = document.getElementById('statusMessages');
                el.innerHTML = `<div class="status status-${{type}}">${{msg}}</div>`;
                setTimeout(() => el.innerHTML = '', 5000);
            }}
            
            async function executeAction() {{
                const userId = document.getElementById('actionUser').value;
                const action = document.getElementById('actionType').value;
                const reason = document.getElementById('reason').value || 'Не указана';
                const newRole = document.getElementById('newRole')?.value || '';
                const newPrefix = document.getElementById('newPrefix')?.value || '';
                
                if (!userId) {{ showStatus('Выберите пользователя', 'error'); return; }}
                
                try {{
                    const resp = await fetch('/api/' + action, {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{user_id: parseInt(userId), reason, role_id: newRole, prefix: newPrefix}})
                    }});
                    const data = await resp.json();
                    if (data.success) {{
                        showStatus('✅ ' + data.message, 'success');
                        setTimeout(() => window.location.reload(), 1500);
                    }} else {{
                        showStatus('❌ ' + data.message, 'error');
                    }}
                }} catch(e) {{
                    showStatus('❌ ' + e, 'error');
                }}
            }}
            
            function promoteUser(id) {{ document.getElementById('actionUser').value = id; document.getElementById('actionType').value = 'promote'; document.getElementById('reason').value = 'Повышение за работу'; toggleFields(); executeAction(); }}
            function banUser(id) {{ document.getElementById('actionUser').value = id; document.getElementById('actionType').value = 'ban'; document.getElementById('reason').value = 'Нарушение правил'; executeAction(); }}
            function unbanUser(id) {{ document.getElementById('actionUser').value = id; document.getElementById('actionType').value = 'unban'; document.getElementById('reason').value = 'Разбан'; executeAction(); }}
            function muteUser(id) {{ document.getElementById('actionUser').value = id; document.getElementById('actionType').value = 'mute'; document.getElementById('reason').value = 'Нарушение'; executeAction(); }}
            function unmuteUser(id) {{ document.getElementById('actionUser').value = id; document.getElementById('actionType').value = 'unmute'; document.getElementById('reason').value = 'Размут'; executeAction(); }}
            
            async function createRole() {{
                const name = document.getElementById('newRoleName').value;
                const prefix = document.getElementById('newRolePrefix').value;
                const level = parseInt(document.getElementById('newRoleLevel').value);
                if (!name) {{ showStatus('Введите название', 'error'); return; }}
                
                try {{
                    const resp = await fetch('/api/roles', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{name, prefix, level}})
                    }});
                    const data = await resp.json();
                    if (data.success) {{
                        showStatus('✅ Роль создана', 'success');
                        setTimeout(() => window.location.reload(), 1500);
                    }} else {{
                        showStatus('❌ ' + data.message, 'error');
                    }}
                }} catch(e) {{
                    showStatus('❌ ' + e, 'error');
                }}
            }}
            
            async function deleteRole(id) {{
                if (!confirm('Удалить роль?')) return;
                try {{
                    const resp = await fetch('/api/roles/' + id, {{method: 'DELETE'}});
                    const data = await resp.json();
                    if (data.success) {{
                        showStatus('✅ Роль удалена', 'success');
                        setTimeout(() => window.location.reload(), 1500);
                    }} else {{
                        showStatus('❌ ' + data.message, 'error');
                    }}
                }} catch(e) {{
                    showStatus('❌ ' + e, 'error');
                }}
            }}
            
            toggleFields();
        </script>
    </body>
    </html>
    '''
    return web.Response(text=html, content_type='text/html')

# ============================================
# API ОБРАБОТЧИКИ
# ============================================

@routes.post('/api/promote')
async def api_promote(request):
    data = await request.json()
    user_id = data.get('user_id')
    role_id = data.get('role_id')
    prefix = data.get('prefix', '')
    reason = data.get('reason', '')
    
    user = db.get_user(user_id)
    if not user:
        return web.json_response({'success': False, 'message': 'Пользователь не найден'})
    
    old_role = user.get('role_id', '')
    old_prefix = user.get('prefix', '')
    
    user['role_id'] = role_id
    if prefix:
        user['prefix'] = prefix
    db.save_user(user)
    
    role = db.get_role(role_id)
    db.add_promotion_log({
        'user_id': user_id,
        'user_name': user.get('first_name'),
        'old_role': old_role,
        'new_role': role.get('name') if role else 'Неизвестно',
        'old_prefix': old_prefix,
        'new_prefix': user.get('prefix', ''),
        'reason': reason,
        'promoted_by': 0,
        'promoted_by_name': 'System',
        'action': 'promotion'
    })
    
    if PROMOTIONS_GROUP_ID:
        try:
            await bot.send_message(
                PROMOTIONS_GROUP_ID,
                f"🎉 ПОВЫШЕНИЕ\n\n"
                f"👤 {user.get('first_name')}\n"
                f"📈 {role.get('name') if role else 'Неизвестно'}\n"
                f"📝 {reason}"
            )
        except:
            pass
    
    return web.json_response({'success': True, 'message': 'Пользователь повышен'})

@routes.post('/api/ban')
async def api_ban(request):
    data = await request.json()
    user_id = data.get('user_id')
    reason = data.get('reason', '')
    
    user = db.get_user(user_id)
    if not user:
        return web.json_response({'success': False, 'message': 'Пользователь не найден'})
    
    user['is_banned'] = 1
    user['ban_reason'] = reason
    db.save_user(user)
    
    db.add_ban_log({
        'user_id': user_id,
        'user_name': user.get('first_name'),
        'action': 'ban',
        'duration': 'Навсегда',
        'reason': reason,
        'admin_id': 0,
        'admin_name': 'System'
    })
    
    if LOGS_GROUP_ID:
        try:
            await bot.send_message(LOGS_GROUP_ID, f"🔨 БАН\n\n👤 {user.get('first_name')}\n📝 {reason}")
        except:
            pass
    
    return web.json_response({'success': True, 'message': 'Пользователь забанен'})

@routes.post('/api/unban')
async def api_unban(request):
    data = await request.json()
    user_id = data.get('user_id')
    
    user = db.get_user(user_id)
    if not user:
        return web.json_response({'success': False, 'message': 'Пользователь не найден'})
    
    user['is_banned'] = 0
    user['ban_reason'] = ''
    db.save_user(user)
    
    return web.json_response({'success': True, 'message': 'Пользователь разбанен'})

@routes.post('/api/mute')
async def api_mute(request):
    data = await request.json()
    user_id = data.get('user_id')
    reason = data.get('reason', '')
    
    user = db.get_user(user_id)
    if not user:
        return web.json_response({'success': False, 'message': 'Пользователь не найден'})
    
    user['is_muted'] = 1
    user['mute_until'] = (datetime.now() + timedelta(hours=1)).isoformat()
    db.save_user(user)
    
    db.add_ban_log({
        'user_id': user_id,
        'user_name': user.get('first_name'),
        'action': 'mute',
        'duration': '1 час',
        'reason': reason,
        'admin_id': 0,
        'admin_name': 'System'
    })
    
    return web.json_response({'success': True, 'message': 'Пользователь замучен'})

@routes.post('/api/unmute')
async def api_unmute(request):
    data = await request.json()
    user_id = data.get('user_id')
    
    user = db.get_user(user_id)
    if not user:
        return web.json_response({'success': False, 'message': 'Пользователь не найден'})
    
    user['is_muted'] = 0
    user['mute_until'] = None
    db.save_user(user)
    
    return web.json_response({'success': True, 'message': 'Пользователь размучен'})

@routes.post('/api/roles')
async def api_create_role(request):
    data = await request.json()
    name = data.get('name')
    prefix = data.get('prefix', '')
    level = data.get('level', 40)
    
    if not name:
        return web.json_response({'success': False, 'message': 'Введите название'})
    
    role_id = f"role_{secrets.token_urlsafe(8)}"
    db.save_role({
        'id': role_id,
        'name': name,
        'prefix': prefix,
        'level': level,
        'permissions': '[]',
        'can_see_logs': 0,
        'can_manage_roles': 0,
        'can_ban': 0,
        'can_mute': 0,
        'created_at': datetime.now().isoformat(),
        'created_by': 0
    })
    
    return web.json_response({'success': True, 'message': 'Роль создана', 'role': {'id': role_id, 'name': name}})

@routes.delete('/api/roles/{role_id}')
async def api_delete_role(request):
    role_id = request.match_info.get('role_id')
    db.delete_role(role_id)
    return web.json_response({'success': True, 'message': 'Роль удалена'})

# ============================================
# ЗАПУСК
# ============================================

async def start_bot():
    logger.info("🚀 Запуск Relax Bot...")
    await dp.start_polling(bot)

async def start_web():
    logger.info(f"🌐 Веб-сервер на порту {WEB_PORT}")
    app = web.Application()
    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', WEB_PORT)
    await site.start()
    logger.info(f"🔗 Откройте: http://localhost:{WEB_PORT}")
    logger.info(f"🔑 Логин: {WEB_USERNAME} | Пароль: {WEB_PASSWORD}")

async def main():
    await asyncio.gather(
        start_bot(),
        start_web()
    )

if __name__ == "__main__":
    if not TOKEN:
        logger.error("❌ TELEGRAM_TOKEN не задан!")
        exit(1)
    
    asyncio.run(main())
