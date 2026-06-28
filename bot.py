#!/usr/bin/env python3
# =============================================================================
# BOOTSTRAP — auto-create venv and install dependencies
# =============================================================================

import os
import subprocess
import sys
from pathlib import Path

_BASE = Path(__file__).parent.resolve()
_VENV = _BASE / ".venv"

if sys.prefix != str(_VENV) and len(sys.argv) > 0 and sys.argv[0] and not sys.argv[0].startswith("-"):
    if not _VENV.exists():
        print("Creating virtual environment...")
        subprocess.check_call([sys.executable, "-m", "venv", str(_VENV)])
    pip = _VENV / "bin" / "pip"
    print("Installing dependencies...")
    subprocess.check_call([str(pip), "install", "-q", "python-telegram-bot", "httpx", "flask", "cryptography"])
    os.execv(str(_VENV / "bin" / "python"), [str(_VENV / "bin" / "python"), *sys.argv])

# =============================================================================
# IMPORTS
# =============================================================================

import asyncio
import base64
import hashlib
import json
import logging
import re
import secrets
import shutil
import signal
import time
from datetime import datetime, timezone
from typing import Optional

try:
    from cryptography.fernet import Fernet
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


BASE_DIR = Path(__file__).parent.resolve()
DIRS = {
    "history": BASE_DIR / "history",
    "memory": BASE_DIR / "memory",
    "logs": BASE_DIR / "logs",
    "exports": BASE_DIR / "exports",
    "backups": BASE_DIR / "backups",
}
FILES = {
    "config": BASE_DIR / "config.json",
    "apikeys": BASE_DIR / "apikeys.json",
    "users": BASE_DIR / "users.json",
    "prompt": BASE_DIR / "prompt.txt",
}

for d in DIRS.values():
    d.mkdir(exist_ok=True)
for f in FILES.values():
    if not f.exists() and f.suffix == ".txt":
        f.write_text("")
    elif not f.exists():
        f.write_text("{}" if f.name != "apikeys.json" else "[]")

sys.path.insert(0, str(BASE_DIR))

# =============================================================================
# CONFIG
# =============================================================================

def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}

def save_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

class Config:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self):
        raw = load_json(FILES["config"], {})
        self.bot_token = raw.get("bot_token", "")
        self.current_model = raw.get("current_model", "auto")
        self.temperature = raw.get("temperature", 0.7)
        self.top_p = raw.get("top_p", 0.9)
        self.max_tokens = raw.get("max_tokens", 2048)
        self.history_length = raw.get("history_length", 20)
        self.summary_length = raw.get("summary_length", 512)
        self.typing_animation = raw.get("typing_animation", True)
        self.request_timeout = raw.get("request_timeout", 60)
        self.openrouter_base_url = raw.get("openrouter_base_url", "https://openrouter.ai/api/v1")
        self.free_models = raw.get("free_models", [])
        self.webhook_url = raw.get("webhook_url", "")
        self.admins = raw.get("admins", [])
        self.admin_ui_lang = raw.get("admin_ui_lang", "ru")

    def save(self):
        data = {
            "bot_token": self.bot_token,
            "current_model": self.current_model,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
            "history_length": self.history_length,
            "summary_length": self.summary_length,
            "typing_animation": self.typing_animation,
            "request_timeout": self.request_timeout,
            "openrouter_base_url": self.openrouter_base_url,
            "free_models": self.free_models,
            "webhook_url": self.webhook_url,
            "admins": self.admins,
            "admin_ui_lang": self.admin_ui_lang,
        }
        save_json(FILES["config"], data)

    def reload(self):
        self._load()

config = Config()


def _get_fernet():
    if not HAS_CRYPTOGRAPHY:
        return None
    master = os.environ.get("API_ENCRYPTION_KEY", "")
    if not master:
        master = config.bot_token if config.bot_token else "default-master-key"
    key = base64.urlsafe_b64encode(hashlib.sha256(master.encode()).digest())
    return Fernet(key)


# =============================================================================
# ADMIN UI LOCALIZATION
# =============================================================================

ADMIN_STRINGS = {
    "control_panel": {"ru": "🤖 Панель управления", "en": "🤖 Control Panel"},
    "ai": {"ru": "🤖 ИИ", "en": "🤖 AI"},
    "api_manager": {"ru": "🔑 Управление API", "en": "🔑 API Manager"},
    "users": {"ru": "👥 Пользователи", "en": "👥 Users"},
    "prompt_manager": {"ru": "📝 Управление промптом", "en": "📝 Prompt Manager"},
    "config": {"ru": "⚙️ Настройки", "en": "⚙️ Config"},
    "stats": {"ru": "📊 Статистика", "en": "📊 Stats"},
    "exit": {"ru": "❌ Выход", "en": "❌ Exit"},
    "lang_toggle": {"ru": "🌐 Язык: Русский", "en": "🌐 Language: English"},
    "admin_panel_closed": {"ru": "Панель администратора закрыта.", "en": "Admin panel closed."},
    "ai_engine": {"ru": "🤖 Движок ИИ\n\n", "en": "🤖 AI Engine\n\n"},
    "current_model": {"ru": "Текущая модель", "en": "Current model"},
    "auto_fallback": {"ru": "авто (цепочка запасных)", "en": "auto (fallback chain)"},
    "temperature": {"ru": "Температура", "en": "Temperature"},
    "top_p": {"ru": "Top P", "en": "Top P"},
    "max_tokens": {"ru": "Макс. токенов", "en": "Max tokens"},
    "available_free_models": {"ru": "Доступные бесплатные модели", "en": "Available free models"},
    "api_keys_title": {"ru": "🔑 Ключи API\n", "en": "🔑 API Keys\n"},
    "no_keys": {"ru": "🔑 Ключи API\n\nНет настроенных ключей.", "en": "🔑 API Keys\n\nNo keys configured."},
    "add_key_btn": {"ru": "➕ Добавить ключ", "en": "➕ Add Key"},
    "remove_key_btn": {"ru": "🗑 Удалить ключ", "en": "🗑 Remove Key"},
    "reload_btn": {"ru": "🔄 Перезагрузить", "en": "🔄 Reload"},
    "back_btn": {"ru": "🔙 Назад", "en": "🔙 Back"},
    "send_api_key": {"ru": "Отправьте API ключ для добавления.", "en": "Send me the API key to add."},
    "no_keys_to_remove": {"ru": "Нет ключей для удаления.", "en": "No keys to remove."},
    "select_key_remove": {"ru": "Выберите ключ для удаления:", "en": "Select a key to remove:"},
    "key_removed": {"ru": "Ключ удалён.", "en": "Key removed."},
    "keys_reloaded": {"ru": "API ключи перезагружены.", "en": "API keys reloaded."},
    "current_prompt_title": {"ru": "📝 Текущий промпт", "en": "📝 Current Prompt"},
    "edit_prompt_btn": {"ru": "✏️ Редактировать промпт", "en": "✏️ Edit Prompt"},
    "send_edited_prompt": {"ru": "Отправьте отредактированный промпт или используйте кнопку Edit.", "en": "Send edited prompt or use Edit button."},
    "send_new_prompt": {"ru": "Отправьте новый промпт сообщением.", "en": "Send the new prompt as a message."},
    "config_settings": {"ru": "⚙️ Настройки конфигурации", "en": "⚙️ Config Settings"},
    "typing_animation": {"ru": "Анимация печати", "en": "Typing Animation"},
    "timeout": {"ru": "Таймаут", "en": "Timeout"},
    "history_length": {"ru": "Длина истории", "en": "History Length"},
    "summary_length": {"ru": "Длина сводки", "en": "Summary Length"},
    "bot_statistics": {"ru": "📊 Статистика бота\n\n", "en": "📊 Bot Statistics\n\n"},
    "users_count": {"ru": "Пользователей", "en": "Users"},
    "api_keys_count": {"ru": "Ключей API", "en": "API Keys"},
    "healthy": {"ru": "здоровых", "en": "healthy"},
    "models_count": {"ru": "Моделей", "en": "Models"},
    "recent_logs": {"ru": "Последние логи", "en": "Recent logs"},
    "no_logs": {"ru": "Логов пока нет.", "en": "No logs yet."},
    "no_users": {"ru": "Пользователей пока нет.", "en": "No users yet."},
    "select_user": {"ru": "Выберите пользователя:", "en": "Select a user:"},
    "user_title": {"ru": "👥 Пользователи", "en": "👥 Users"},
    "no_chat_history": {"ru": "Истории чата нет.", "en": "No chat history."},
    "chat_history_btn": {"ru": "📋 История чата", "en": "📋 Chat History"},
    "key_added": {"ru": "API ключ добавлен.", "en": "API key added."},
    "prompt_updated": {"ru": "Промпт обновлён.", "en": "Prompt updated."},
    "config_updated": {"ru": "Настройка {key} обновлена.", "en": "Config {key} updated."},
    "invalid_value": {"ru": "Неверное значение. Отправьте JSON значение (например, 0.8, 100, \"текст\").", "en": "Invalid value. Send a valid JSON value (e.g. 0.8, 100, \"text\")."},
    "session_expired": {"ru": "Сессия истекла.", "en": "Session expired."},
    "status": {"ru": "Статус", "en": "Status"},
    "errors": {"ru": "Ошибок", "en": "Errors"},
    "latency": {"ru": "Задержка", "en": "Latency"},
    "new_user_notification": {"ru": "🆕 Новый пользователь: {name} (ID: {id})\nВсего пользователей: {total}", "en": "🆕 New user: {name} (ID: {id})\nTotal users: {total}"},
    "lang_changed": {"ru": "Язык изменён на русский.", "en": "Language changed to English."},
    "alerts": {"ru": "🔔 Оповещения", "en": "🔔 Alerts"},
    "alert_settings": {"ru": "🔔 Настройки оповещений\n\nID администраторов для уведомлений:", "en": "🔔 Alert Settings\n\nAdmin IDs to notify:"},
    "no_alerts_configured": {"ru": "Нет ID для оповещений.", "en": "No admin IDs configured."},
    "add_alert_btn": {"ru": "➕ Добавить ID", "en": "➕ Add ID"},
    "remove_alert_btn": {"ru": "🗑 Удалить ID", "en": "🗑 Remove ID"},
    "send_admin_id": {"ru": "Отправьте Telegram ID администратора (число).", "en": "Send the Telegram admin ID (number)."},
    "select_admin_remove": {"ru": "Выберите ID для удаления:", "en": "Select an ID to remove:"},
    "admin_id_added": {"ru": "ID администратора добавлен.", "en": "Admin ID added."},
    "admin_id_removed": {"ru": "ID администратора удалён.", "en": "Admin ID removed."},
    "invalid_admin_id": {"ru": "Неверный ID. Отправьте числовой Telegram ID.", "en": "Invalid ID. Send a numeric Telegram ID."},
    "messages": {"ru": "Сообщений", "en": "Messages"},
    "first_seen": {"ru": "Впервые", "en": "First seen"},
    "last_seen": {"ru": "Последний раз", "en": "Last seen"},
    "question_answered": {"ru": "✅ Вы выбрали: {value}", "en": "✅ You selected: {value}"},
}

def t(key: str, **kwargs) -> str:
    lang = config.admin_ui_lang
    entry = ADMIN_STRINGS.get(key, {})
    text = entry.get(lang, entry.get("en", key))
    if kwargs:
        text = text.format(**kwargs)
    return text

# =============================================================================
# LOGGER
# =============================================================================

LOG_FILE = DIRS["logs"] / f"bot_{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("bot")

_shutdown_event = asyncio.Event()

# =============================================================================
# FILES (User DB)
# =============================================================================

class UserDB:
    def __init__(self):
        self._data = load_json(FILES["users"], {})
        self._new_user_queue: list[int] = []

    def save(self):
        save_json(FILES["users"], self._data)

    def get(self, user_id: int) -> dict:
        uid = str(user_id)
        if uid not in self._data:
            self._data[uid] = {
                "id": user_id,
                "username": "",
                "language": "auto",
                "first_seen": datetime.now(timezone.utc).isoformat(),
                "last_seen": datetime.now(timezone.utc).isoformat(),
                "message_count": 0,
                "memory_id": uid,
                "history_id": uid,
            }
            self._new_user_queue.append(user_id)
            self.save()
        return self._data[uid]

    def pop_new_users(self) -> list[int]:
        result = self._new_user_queue[:]
        self._new_user_queue.clear()
        return result

    def update(self, user_id: int, **kwargs):
        uid = str(user_id)
        if uid in self._data:
            self._data[uid].update(kwargs)
        else:
            entry = self.get(user_id)
            entry.update(kwargs)
        self._data[uid]["last_seen"] = datetime.now(timezone.utc).isoformat()
        self.save()

    def increment_messages(self, user_id: int):
        uid = str(user_id)
        self.get(user_id)
        self._data[uid]["message_count"] = self._data[uid].get("message_count", 0) + 1
        self.save()

    @property
    def all_users(self) -> list:
        return list(self._data.values())

user_db = UserDB()

# =============================================================================
# API MANAGER
# =============================================================================

class APIKey:
    def __init__(self, key: str, label: str = ""):
        self._raw_key = key
        self.label = label or self._mask_key(key)
        self.status = "healthy"
        self.latency = 0.0
        self.errors = 0
        self.last_used = None

    @staticmethod
    def _mask_key(key: str) -> str:
        if len(key) > 8:
            return key[:8] + "..."
        return key

    @property
    def key(self) -> str:
        return self._raw_key

    def to_dict(self):
        fernet = _get_fernet()
        if fernet:
            encrypted = fernet.encrypt(self._raw_key.encode()).decode()
        else:
            encrypted = self._raw_key
        return {
            "key_encrypted": encrypted,
            "label": self.label,
            "status": self.status,
            "latency": self.latency,
            "errors": self.errors,
            "last_used": self.last_used,
        }

    @classmethod
    def from_dict(cls, d: dict):
        encrypted = d.get("key_encrypted", "")
        if encrypted:
            fernet = _get_fernet()
            if fernet:
                try:
                    raw_key = fernet.decrypt(encrypted.encode()).decode()
                except Exception:
                    raw_key = encrypted
            else:
                raw_key = encrypted
        else:
            raw_key = d.get("key", "")  # old format fallback
        obj = cls(raw_key, d.get("label", ""))
        obj.status = d.get("status", "healthy")
        obj.latency = d.get("latency", 0.0)
        obj.errors = d.get("errors", 0)
        obj.last_used = d.get("last_used")
        return obj

class APIManager:
    def __init__(self):
        self.keys: list[APIKey] = []
        self._load()

    def _load(self):
        raw = load_json(FILES["apikeys"], [])
        self.keys = [APIKey.from_dict(k) for k in raw]

    def save(self):
        save_json(FILES["apikeys"], [k.to_dict() for k in self.keys])

    def add_key(self, key: str, label: str = ""):
        self.keys.append(APIKey(key, label))
        self.save()

    def remove_key(self, index: int):
        if 0 <= index < len(self.keys):
            self.keys.pop(index)
            self.save()

    def get_healthy_key(self) -> Optional[APIKey]:
        healthy = [k for k in self.keys if k.status == "healthy"]
        if not healthy:
            all_keys = [k for k in self.keys if k.status != "dead"]
            if not all_keys:
                return None
            healthy = all_keys
        healthy.sort(key=lambda k: k.errors)
        return healthy[0]

    def mark_status(self, key: APIKey, status: str, latency: float = 0.0):
        key.status = status
        key.latency = latency
        key.last_used = datetime.now(timezone.utc).isoformat()
        if status == "error":
            key.errors += 1
        elif status == "healthy":
            key.errors = 0
        self.save()

    def get_stats(self) -> list[dict]:
        return [k.to_dict() for k in self.keys]

api_manager = APIManager()

# =============================================================================
# MODEL MANAGER
# =============================================================================

class ModelManager:
    def __init__(self):
        self.models = list(config.free_models)

    def reload(self):
        self.models = list(config.free_models)

    def get_next(self, current: Optional[str] = None) -> Optional[str]:
        if not self.models:
            return None
        if current is None or current not in self.models:
            return self.models[0]
        idx = self.models.index(current)
        if idx + 1 < len(self.models):
            return self.models[idx + 1]
        return None

    def get_fallback_chain(self, start: Optional[str] = None) -> list[str]:
        if not self.models:
            return []
        if start is None or start not in self.models:
            return self.models
        idx = self.models.index(start)
        return self.models[idx:] + self.models[:idx]

model_manager = ModelManager()

# =============================================================================
# MEMORY
# =============================================================================

def _memory_path(user_id: int) -> Path:
    return DIRS["memory"] / f"{user_id}.json"

def _history_path(user_id: int) -> Path:
    return DIRS["history"] / f"{user_id}.json"

class MemorySystem:
    def get_memory(self, user_id: int) -> dict:
        path = _memory_path(user_id)
        return load_json(path, {})

    def save_memory(self, user_id: int, data: dict):
        save_json(_memory_path(user_id), data)

    def update_memory(self, user_id: int, **kwargs):
        mem = self.get_memory(user_id)
        mem.update(kwargs)
        mem["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.save_memory(user_id, mem)

    def get_history(self, user_id: int) -> list:
        path = _history_path(user_id)
        return load_json(path, [])

    def save_history(self, user_id: int, messages: list):
        save_json(_history_path(user_id), messages)

    def add_message(self, user_id: int, role: str, content: str):
        history = self.get_history(user_id)
        history.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        max_len = config.history_length
        if len(history) > max_len + 10:
            history = history[-max_len:]
        self.save_history(user_id, history)

    def get_recent(self, user_id: int, limit: int = None) -> list:
        history = self.get_history(user_id)
        if limit is None:
            limit = config.history_length
        return history[-limit:]

    def summarize_history(self, user_id: int) -> str:
        history = self.get_history(user_id)
        if len(history) <= 2:
            return ""
        text_parts = []
        for msg in history[:-2]:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            text_parts.append(f"{role}: {content}")
        summary_text = "\n".join(text_parts)
        max_chars = config.summary_length * 4
        if len(summary_text) > max_chars:
            summary_text = summary_text[-max_chars:]
        summary = f"[Previous conversation summary:\n{summary_text}\n]"
        mem = self.get_memory(user_id)
        mem["session_summary"] = summary
        mem["summarized_at"] = datetime.now(timezone.utc).isoformat()
        self.save_memory(user_id, mem)
        keep = history[-2:]
        self.save_history(user_id, keep)
        return summary

memory_system = MemorySystem()

# =============================================================================
# OPENROUTER (AI ENGINE)
# =============================================================================

class AIEngine:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=config.request_timeout, trust_env=False)

    async def _call_openrouter(self, api_key: str, model: str, messages: list) -> dict:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/telegram-ai-bot",
            "X-Title": "Telegram AI Bot",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": config.temperature,
            "top_p": config.top_p,
            "max_tokens": config.max_tokens,
        }
        start = time.monotonic()
        try:
            resp = await self.client.post(
                f"{config.openrouter_base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            latency = time.monotonic() - start
            data = resp.json()
            if resp.status_code != 200:
                error_msg = data.get("error", {}).get("message", str(resp.status_code))
                return {
                    "success": False,
                    "error": error_msg,
                    "latency": latency,
                    "status_code": resp.status_code,
                }
            choice = data.get("choices", [{}])[0]
            finish_reason = choice.get("finish_reason", "")
            usage = data.get("usage", {})
            return {
                "success": True,
                "content": choice.get("message", {}).get("content", ""),
                "model": data.get("model", model),
                "finish_reason": finish_reason,
                "latency": latency,
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }
        except httpx.TimeoutException:
            return {"success": False, "error": "timeout", "latency": config.request_timeout}
        except Exception as e:
            return {"success": False, "error": str(e), "latency": 0}

    async def generate(self, user_id: int, user_message: str) -> dict:
        user_info = user_db.get(user_id)
        memory = memory_system.get_memory(user_id)
        prompt_text = FILES["prompt"].read_text(encoding="utf-8").strip()
        question_instr = (
            "\n\nYou can ask the user a question with inline buttons. "
            "To do this include the exact JSON below in your response (replace the example text):\n"
            '【{"question":{"text":"Your question here","options":[{"text":"Button 1","value":"option1"},{"text":"Button 2","value":"option2"}]}}】\n'
            "The bot will automatically render the buttons. Use this ONLY when you need a structured answer."
        )
        messages = [{"role": "system", "content": prompt_text + question_instr}]

        profile = memory.get("profile", "")
        preferences = memory.get("preferences", "")
        facts = memory.get("facts", "")
        long_term = memory.get("long_term_summary", "")
        session_summary = memory.get("session_summary", "")

        context_parts = []
        if profile:
            context_parts.append(f"User profile: {profile}")
        if preferences:
            context_parts.append(f"User preferences: {preferences}")
        if facts:
            context_parts.append(f"Known facts: {facts}")
        if long_term:
            context_parts.append(f"Long-term context: {long_term}")
        if session_summary:
            context_parts.append(f"Session summary: {session_summary}")
        if context_parts:
            messages.append({"role": "system", "content": "\n".join(context_parts)})

        recent = memory_system.get_recent(user_id)
        for msg in recent[-config.history_length:]:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": user_message})

        chosen_model = config.current_model
        if chosen_model == "auto":
            fallback_chain = model_manager.get_fallback_chain()
        else:
            fallback_chain = model_manager.get_fallback_chain(chosen_model)

        if not fallback_chain:
            return {"success": False, "error": "No models configured", "reply": "No models in free_models list. Add one via /pathfinder → Config."}

        last_error = None
        for model in fallback_chain:
            api_key_obj = api_manager.get_healthy_key()
            if not api_key_obj:
                return {"success": False, "error": "No API keys available", "reply": "No working API keys. Contact admin."}

            result = await self._call_openrouter(api_key_obj.key, model, messages)
            if result["success"]:
                api_manager.mark_status(api_key_obj, "healthy", result["latency"])
                result["model_used"] = model
                result["api_label"] = api_key_obj.label
                reply_text = result.get("content", "").strip()
                # Parse question JSON from response
                q_match = re.search(r'【\s*(\{.*?\})\s*】', reply_text, re.DOTALL)
                if q_match:
                    try:
                        q_data = json.loads(q_match.group(1))
                        if "question" in q_data and "options" in q_data["question"]:
                            result["question"] = q_data["question"]
                            reply_text = reply_text.replace(q_match.group(0), "").strip()
                    except (json.JSONDecodeError, KeyError):
                        pass
                result["reply"] = reply_text
                return result
            else:
                last_error = result.get("error", "unknown")
                api_manager.mark_status(api_key_obj, "error", result["latency"])
                logger.warning(f"Model {model} failed on key {api_key_obj.label}: {last_error}. Trying next...")
                await asyncio.sleep(0.5)

        return {"success": False, "error": last_error or "All models failed", "reply": "AI request failed after trying all models and keys."}

    async def close(self):
        await self.client.aclose()

ai_engine = AIEngine()

# =============================================================================
# PROMPTS (Prompt Manager)
# =============================================================================

class PromptManager:
    @staticmethod
    def get_prompt() -> str:
        return FILES["prompt"].read_text(encoding="utf-8")

    @staticmethod
    def set_prompt(text: str):
        FILES["prompt"].write_text(text, encoding="utf-8")

    @staticmethod
    def get_prompt_preview(length: int = 200) -> str:
        text = PromptManager.get_prompt()
        if len(text) > length:
            return text[:length] + "..."
        return text

prompt_manager = PromptManager()

# =============================================================================
# TELEGRAM (Message Router)
# =============================================================================

class TelegramBot:
    def __init__(self, app=None):
        if app:
            self.application = app
        else:
            self.application = Application.builder().token(config.bot_token).build()
        self._register_handlers()
        self.admin_sessions: dict[int, str] = {}
        self._active_questions: dict[str, dict] = {}

    async def _notify_admins_new_user(self, user_id: int, data: dict):
        if not config.admins:
            return
        name = data.get("username", "") or str(user_id)
        total = len(user_db.all_users)
        text = t("new_user_notification", name=name, id=user_id, total=total)
        for admin_id in config.admins:
            try:
                await self.application.bot.send_message(chat_id=admin_id, text=text)
            except Exception as e:
                logger.warning(f"Failed to notify admin {admin_id}: {e}")

    def _register_handlers(self):
        self.application.add_handler(CommandHandler("start", self.cmd_start))
        self.application.add_handler(CommandHandler("pathfinder", self.cmd_pathfinder))
        self.application.add_handler(CommandHandler("export", self.cmd_export))
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_db.get(user.id)
        user_db.update(user.id, username=user.username or user.full_name)
        for uid in user_db.pop_new_users():
            await self._notify_admins_new_user(uid, user_db._data[str(uid)])
        await update.message.reply_text(
            f"Hello {user.full_name}! I'm your AI assistant.\n"
            f"Send me any message and I'll reply."
        )

    async def cmd_export(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        history = memory_system.get_history(user_id)
        mem = memory_system.get_memory(user_id)
        export_data = {
            "user": user_db.get(user_id),
            "memory": mem,
            "history": history,
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }
        export_path = DIRS["exports"] / f"user_{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        save_json(export_path, export_data)
        await update.message.reply_document(
            document=open(export_path, "rb"),
            filename=export_path.name,
            caption="Your data export"
        )

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        text = update.message.text.strip()

        if not text:
            return

        user_db.increment_messages(user_id)
        memory_system.add_message(user_id, "user", text)

        for uid in user_db.pop_new_users():
            await self._notify_admins_new_user(uid, user_db._data[str(uid)])

        # Check if in admin session
        if chat_id in self.admin_sessions:
            await self._handle_admin_input(update, context)
            return

        await self._handle_normal_chat(update, context)

    async def _handle_normal_chat(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        message = update.message

        if config.typing_animation:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        result = await ai_engine.generate(user_id, message.text)

        if result["success"]:
            reply = result["reply"]
            memory_system.add_message(user_id, "assistant", reply)

            log_data = {
                "user_id": user_id,
                "api": result.get("api_label", "?"),
                "model": result.get("model_used", "?"),
                "latency": round(result.get("latency", 0), 2),
                "tokens": result.get("total_tokens", 0),
                "status": "ok",
            }
            logger.info(f"AI OK | user={log_data['user_id']} model={log_data['model']} latency={log_data['latency']}s tokens={log_data['tokens']}")

            question_data = result.get("question")
            if question_data:
                qid = secrets.token_hex(4)
                options_map = {opt["value"]: opt["text"] for opt in question_data.get("options", [])}
                self._active_questions[qid] = {
                    "user_id": user_id,
                    "text": question_data["text"],
                    "options_map": options_map,
                }
                keyboard = []
                for opt in question_data.get("options", []):
                    keyboard.append([
                        InlineKeyboardButton(opt["text"], callback_data=f"cq_{qid}_{opt['value']}")
                    ])
                await message.reply_text(reply, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await message.reply_text(reply)
        else:
            error_msg = result.get("error", "unknown")
            logger.error(f"AI FAIL | user={user_id} error={error_msg}")
            await message.reply_text(f"Sorry, I couldn't process that. Error: {error_msg}")

    async def _handle_admin_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        text = update.message.text.strip()
        session = self.admin_sessions.get(chat_id, "")

        if session == "awaiting_key":
            api_manager.add_key(text)
            self.admin_sessions.pop(chat_id, None)
            await update.message.reply_text(t("key_added"), reply_markup=self._admin_main_menu())
        elif session == "awaiting_admin_id":
            try:
                admin_id = int(text.strip())
                if admin_id not in config.admins:
                    config.admins.append(admin_id)
                    config.save()
                self.admin_sessions.pop(chat_id, None)
                await update.message.reply_text(t("admin_id_added"), reply_markup=self._admin_main_menu())
            except (ValueError, TypeError):
                await update.message.reply_text(t("invalid_admin_id"))
        elif session == "awaiting_prompt":
            prompt_manager.set_prompt(text)
            self.admin_sessions.pop(chat_id, None)
            await update.message.reply_text(t("prompt_updated"), reply_markup=self._admin_main_menu())
        elif session.startswith("awaiting_config:"):
            key = session.split(":", 1)[1]
            try:
                val = json.loads(text)
                setattr(config, key, val)
                config.save()
                self.admin_sessions.pop(chat_id, None)
                await update.message.reply_text(t("config_updated", key=key), reply_markup=self._admin_main_menu())
            except (json.JSONDecodeError, ValueError, TypeError):
                await update.message.reply_text(t("invalid_value"))
        else:
            self.admin_sessions.pop(chat_id, None)
            await update.message.reply_text(t("session_expired"), reply_markup=self._admin_main_menu())

    async def cmd_pathfinder(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            t("control_panel"),
            reply_markup=self._admin_main_menu()
        )

    def _admin_main_menu(self) -> InlineKeyboardMarkup:
        keys = [
            [InlineKeyboardButton(t("ai"), callback_data="admin_ai")],
            [InlineKeyboardButton(t("api_manager"), callback_data="admin_api")],
            [InlineKeyboardButton(t("users"), callback_data="admin_users")],
            [InlineKeyboardButton(t("prompt_manager"), callback_data="admin_prompt")],
            [InlineKeyboardButton(t("config"), callback_data="admin_config")],
            [InlineKeyboardButton(t("stats"), callback_data="admin_stats")],
            [InlineKeyboardButton(t("alerts"), callback_data="admin_alerts")],
            [InlineKeyboardButton(t("lang_toggle"), callback_data="admin_toggle_lang")],
            [InlineKeyboardButton(t("exit"), callback_data="admin_exit")],
        ]
        return InlineKeyboardMarkup(keys)

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data

        if data == "admin_exit":
            chat_id = update.effective_chat.id
            self.admin_sessions.pop(chat_id, None)
            await query.edit_message_text(t("admin_panel_closed"))
            return

        if data == "admin_toggle_lang":
            config.admin_ui_lang = "en" if config.admin_ui_lang == "ru" else "ru"
            config.save()
            await query.edit_message_text(t("lang_changed"), reply_markup=self._admin_main_menu())
            return

        if data == "admin_ai":
            model_status = config.current_model if config.current_model != "auto" else t("auto_fallback")
            text = (
                t("ai_engine")
                + f"{t('current_model')}: {model_status}\n"
                + f"{t('temperature')}: {config.temperature}\n"
                + f"{t('top_p')}: {config.top_p}\n"
                + f"{t('max_tokens')}: {config.max_tokens}\n\n"
                + f"{t('available_free_models')}:\n" + "\n".join(f"• {m}" for m in config.free_models)
            )
            await query.edit_message_text(text, reply_markup=self._admin_main_menu())

        elif data == "admin_api":
            stats = api_manager.get_stats()
            if not stats:
                text = t("no_keys")
            else:
                lines = [t("api_keys_title")]
                for i, k in enumerate(stats):
                    lines.append(f"{i+1}. {k['label']}")
                    lines.append(f"   {t('status')}: {k['status']} | {t('errors')}: {k['errors']} | {t('latency')}: {k['latency']:.2f}s")
                    lines.append("")
                text = "\n".join(lines)
            keyboard = [
                [InlineKeyboardButton(t("add_key_btn"), callback_data="admin_api_add")],
                [InlineKeyboardButton(t("remove_key_btn"), callback_data="admin_api_remove")],
                [InlineKeyboardButton(t("reload_btn"), callback_data="admin_api_reload")],
                [InlineKeyboardButton(t("back_btn"), callback_data="admin_back_main")],
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

        elif data == "admin_api_add":
            chat_id = update.effective_chat.id
            self.admin_sessions[chat_id] = "awaiting_key"
            await query.edit_message_text(t("send_api_key"))

        elif data == "admin_api_remove":
            stats = api_manager.get_stats()
            if not stats:
                await query.edit_message_text(t("no_keys_to_remove"), reply_markup=self._admin_main_menu())
                return
            keyboard = []
            for i, k in enumerate(stats):
                keyboard.append([InlineKeyboardButton(f"🗑 {k['label']}", callback_data=f"admin_api_del_{i}")])
            keyboard.append([InlineKeyboardButton(t("back_btn"), callback_data="admin_back_main")])
            await query.edit_message_text(t("select_key_remove"), reply_markup=InlineKeyboardMarkup(keyboard))

        elif data.startswith("admin_api_del_"):
            idx = int(data.split("_")[-1])
            api_manager.remove_key(idx)
            await query.edit_message_text(t("key_removed"), reply_markup=self._admin_main_menu())

        elif data == "admin_api_reload":
            api_manager._load()
            await query.edit_message_text(t("keys_reloaded"), reply_markup=self._admin_main_menu())

        elif data == "admin_prompt":
            preview = prompt_manager.get_prompt_preview(300)
            keyboard = [
                [InlineKeyboardButton(t("edit_prompt_btn"), callback_data="admin_prompt_edit")],
                [InlineKeyboardButton(t("back_btn"), callback_data="admin_back_main")],
            ]
            await query.edit_message_text(
                f"{t('current_prompt_title')}:\n\n{preview}\n\n"
                f"{t('send_edited_prompt')}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        elif data == "admin_prompt_edit":
            chat_id = update.effective_chat.id
            self.admin_sessions[chat_id] = "awaiting_prompt"
            await query.edit_message_text(
                f"Current prompt:\n\n{prompt_manager.get_prompt()}\n\n"
                f"{t('send_new_prompt')}"
            )

        elif data == "admin_config":
            keyboard = [
                [InlineKeyboardButton(f"{t('temperature')} ({config.temperature})", callback_data="admin_config_temperature")],
                [InlineKeyboardButton(f"{t('top_p')} ({config.top_p})", callback_data="admin_config_top_p")],
                [InlineKeyboardButton(f"{t('max_tokens')} ({config.max_tokens})", callback_data="admin_config_max_tokens")],
                [InlineKeyboardButton(f"{t('history_length')} ({config.history_length})", callback_data="admin_config_history_length")],
                [InlineKeyboardButton(f"{t('summary_length')} ({config.summary_length})", callback_data="admin_config_summary_length")],
                [InlineKeyboardButton(f"{t('typing_animation')} ({config.typing_animation})", callback_data="admin_config_typing")],
                [InlineKeyboardButton(f"{t('timeout')} ({config.request_timeout}s)", callback_data="admin_config_timeout")],
                [InlineKeyboardButton(t("back_btn"), callback_data="admin_back_main")],
            ]
            await query.edit_message_text(t("config_settings"), reply_markup=InlineKeyboardMarkup(keyboard))

        elif data.startswith("admin_config_"):
            key = data.split("admin_config_", 1)[1]
            chat_id = update.effective_chat.id
            self.admin_sessions[chat_id] = f"awaiting_config:{key}"
            current = getattr(config, key, "?")
            await query.edit_message_text(
                f"Current {key} = {current}\nSend the new value as a JSON value."
            )

        elif data == "admin_alerts":
            admins = config.admins
            if not admins:
                text = t("no_alerts_configured")
            else:
                text = t("alert_settings") + "\n" + "\n".join(f"• {uid}" for uid in admins)
            keyboard = [
                [InlineKeyboardButton(t("add_alert_btn"), callback_data="admin_alerts_add")],
                [InlineKeyboardButton(t("remove_alert_btn"), callback_data="admin_alerts_remove")],
                [InlineKeyboardButton(t("back_btn"), callback_data="admin_back_main")],
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

        elif data == "admin_alerts_add":
            chat_id = update.effective_chat.id
            self.admin_sessions[chat_id] = "awaiting_admin_id"
            await query.edit_message_text(t("send_admin_id"))

        elif data == "admin_alerts_remove":
            admins = config.admins
            if not admins:
                await query.edit_message_text(t("no_alerts_configured"), reply_markup=self._admin_main_menu())
                return
            keyboard = []
            for uid in admins:
                keyboard.append([InlineKeyboardButton(f"🗑 {uid}", callback_data=f"admin_alerts_del_{uid}")])
            keyboard.append([InlineKeyboardButton(t("back_btn"), callback_data="admin_back_main")])
            await query.edit_message_text(t("select_admin_remove"), reply_markup=InlineKeyboardMarkup(keyboard))

        elif data.startswith("admin_alerts_del_"):
            uid_str = data.split("admin_alerts_del_", 1)[1]
            try:
                config.admins = [a for a in config.admins if str(a) != uid_str]
                config.save()
            except (ValueError, TypeError):
                pass
            await query.edit_message_text(t("admin_id_removed"), reply_markup=self._admin_main_menu())

        elif data.startswith("cq_"):
            parts = data.split("_", 2)
            if len(parts) < 3:
                return
            _, qid, value = parts
            question = self._active_questions.pop(qid, None)
            if not question:
                await query.edit_message_text("Question expired.")
                return
            user_id = update.effective_user.id
            if question.get("user_id") != user_id:
                await query.answer("This question is not for you.", show_alert=True)
                return
            answer_text = question.get("options_map", {}).get(value, value)
            user_db.increment_messages(user_id)
            memory_system.add_message(user_id, "user", answer_text)
            await query.edit_message_text(
                t("question_answered", value=answer_text)
            )
            result = await ai_engine.generate(user_id, answer_text)
            if result["success"]:
                reply = result["reply"]
                memory_system.add_message(user_id, "assistant", reply)
                q_data = result.get("question")
                if q_data:
                    qid = secrets.token_hex(4)
                    omap = {o["value"]: o["text"] for o in q_data.get("options", [])}
                    self._active_questions[qid] = {"user_id": user_id, "text": q_data["text"], "options_map": omap}
                    kb = [[InlineKeyboardButton(o["text"], callback_data=f"cq_{qid}_{o['value']}")] for o in q_data.get("options", [])]
                    await context.bot.send_message(chat_id=update.effective_chat.id, text=reply, reply_markup=InlineKeyboardMarkup(kb))
                else:
                    await context.bot.send_message(chat_id=update.effective_chat.id, text=reply)
            return

        elif data == "admin_stats":
            total_users = len(user_db.all_users)
            total_keys = len(api_manager.get_stats())
            healthy_keys = len([k for k in api_manager.get_stats() if k["status"] == "healthy"])
            log_lines = []
            if LOG_FILE.exists():
                with open(LOG_FILE, encoding="utf-8") as f:
                    log_lines = f.readlines()[-10:]
            recent_logs = "".join(log_lines[-10:]) if log_lines else t("no_logs")
            await query.edit_message_text(
                f"{t('bot_statistics')}"
                f"{t('users_count')}: {total_users}\n"
                f"{t('api_keys_count')}: {total_keys} ({healthy_keys} {t('healthy')})\n"
                f"{t('models_count')}: {len(config.free_models)}\n"
                f"{t('recent_logs')}:\n{recent_logs}",
                reply_markup=self._admin_main_menu()
            )

        elif data.startswith("admin_users"):
            all_users = user_db.all_users
            if not all_users:
                await query.edit_message_text(t("no_users"), reply_markup=self._admin_main_menu())
                return
            per_page = 10
            page = 0
            if data.startswith("admin_users_page_"):
                page = int(data.split("_")[-1])
            total_pages = (len(all_users) + per_page - 1) // per_page
            start = page * per_page
            end = start + per_page
            keyboard = []
            for u in all_users[start:end]:
                label = u.get("username", "") or f"ID {u['id']}"
                keyboard.append([InlineKeyboardButton(f"👤 {label}", callback_data=f"admin_user_{u['id']}")])
            nav = []
            if page > 0:
                nav.append(InlineKeyboardButton("◀️", callback_data=f"admin_users_page_{page-1}"))
            if page < total_pages - 1:
                nav.append(InlineKeyboardButton("▶️", callback_data=f"admin_users_page_{page+1}"))
            if nav:
                keyboard.append(nav)
            keyboard.append([InlineKeyboardButton(t("back_btn"), callback_data="admin_back_main")])
            await query.edit_message_text(
                f"{t('user_title')} ({len(all_users)})\n\n{t('select_user')}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        elif data.startswith("admin_user_"):
            rest = data.split("admin_user_", 1)[1]
            if rest.startswith("history_"):
                parts = rest.split("_")
                user_id = int(parts[1])
                page = int(parts[2]) if len(parts) > 2 else 0
                history = memory_system.get_history(user_id)
                info = user_db.get(user_id)
                username = info.get("username", str(user_id))
                if not history:
                    await query.edit_message_text(
                        t("no_chat_history"),
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("back_btn"), callback_data=f"admin_user_{user_id}")]])
                    )
                    return
                per_page = 5
                total_pages = (len(history) + per_page - 1) // per_page
                start_idx = max(0, len(history) - (page + 1) * per_page)
                end_idx = len(history) - page * per_page
                messages_slice = history[start_idx:end_idx]
                lines = [f"📋 {username} (p.{page+1}/{total_pages})\n"]
                for msg in messages_slice:
                    role = msg.get("role", "?")
                    content = msg.get("content", "")
                    icon = "👤" if role == "user" else "🤖"
                    if len(content) > 200:
                        content = content[:200] + "..."
                    lines.append(f"{icon} {content}")
                text = "\n".join(lines)
                if len(text) > 4000:
                    text = text[:4000] + "\n...(truncated)"
                keyboard = []
                nav = []
                if page > 0:
                    nav.append(InlineKeyboardButton("◀️", callback_data=f"admin_user_history_{user_id}_{page-1}"))
                if page < total_pages - 1:
                    nav.append(InlineKeyboardButton("▶️", callback_data=f"admin_user_history_{user_id}_{page+1}"))
                if nav:
                    keyboard.append(nav)
                keyboard.append([InlineKeyboardButton(t("back_btn"), callback_data=f"admin_user_{user_id}")])
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                user_id = int(rest)
                info = user_db.get(user_id)
                username = info.get("username", "?")
                msgs = info.get("message_count", 0)
                first_seen = info.get("first_seen", "?")[:10]
                last_seen = info.get("last_seen", "?")[:10]
                text = (
                    f"👤 {username}\n"
                    f"ID: {user_id}\n"
                    f"{t('messages')}: {msgs}\n"
                    f"{t('first_seen')}: {first_seen}\n"
                    f"{t('last_seen')}: {last_seen}"
                )
                keyboard = [
                    [InlineKeyboardButton(t("chat_history_btn"), callback_data=f"admin_user_history_{user_id}_0")],
                    [InlineKeyboardButton(t("back_btn"), callback_data="admin_users")],
                ]
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

        elif data == "admin_back_main":
            await query.edit_message_text(
                t("control_panel"),
                reply_markup=self._admin_main_menu()
            )

    async def run_polling(self):
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()
        logger.info("Bot started polling.")
        try:
            await _shutdown_event.wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await ai_engine.close()
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()

    async def run_webhook(self, url: str):
        await self.application.initialize()
        await self.application.start()
        await self.application.bot.set_webhook(url=url)
        logger.info(f"Webhook set to {url}")
        # Keep running — Flask serves, this just keeps app alive
        try:
            await _shutdown_event.wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await ai_engine.close()
            await self.application.stop()
            await self.application.shutdown()

# =============================================================================
# WEBHOOK (PythonAnywhere)
# =============================================================================

import flask

webapp = flask.Flask(__name__)
_webhook_bot: Optional[TelegramBot] = None

def _get_webhook_bot() -> TelegramBot:
    global _webhook_bot
    if _webhook_bot is None:
        app = Application.builder().token(config.bot_token).updater(None).build()
        _webhook_bot = TelegramBot(app=app)
    return _webhook_bot

@webapp.route("/webhook", methods=["POST"])
def handle_webhook():
    bot = _get_webhook_bot()
    update = Update.de_json(flask.request.get_json(), bot.application.bot)
    asyncio.run(bot.application.process_update(update))
    return "", 200

@webapp.route("/")
def index():
    return "Bot is running.", 200

# =============================================================================
# MAIN LOOP
# =============================================================================

def _prompt_config():
    changed = False
    if not config.bot_token or config.bot_token == "YOUR_BOT_TOKEN_HERE":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            try:
                token = input("Enter Telegram bot token (from @BotFather): ").strip()
            except EOFError:
                token = ""
        if token:
            config.bot_token = token
            changed = True
    if changed:
        config.save()
        print("Config saved.\n")

async def _setup_webhook():
    _prompt_config()
    if not config.bot_token:
        print("ERROR: Bot token is required.")
        sys.exit(1)
    url = input("Enter your webhook URL (e.g. https://your-app.onrender.com/webhook): ").strip()
    if not url:
        print("URL required.")
        sys.exit(1)
    config.webhook_url = url
    config.save()
    bot = TelegramBot()
    await bot.application.initialize()
    await bot.application.start()
    await bot.application.bot.set_webhook(url=url)
    print(f"Webhook set to {url}")
    info = await bot.application.bot.get_webhook_info()
    print(f"Webhook info: {info.url}")
    await bot.application.stop()
    await bot.application.shutdown()

async def main():
    _prompt_config()
    loop = asyncio.get_event_loop()
    try:
        loop.add_signal_handler(signal.SIGTERM, _shutdown_event.set)
    except NotImplementedError:
        pass

    if not config.bot_token:
        print("ERROR: Bot token is required.")
        sys.exit(1)

    if config.webhook_url:
        logger.info(f"Starting in webhook mode: {config.webhook_url}")
        bot = TelegramBot()
        await bot.run_webhook(config.webhook_url)
    else:
        backup_time = time.time()
        backup_interval = 86400
        bot = TelegramBot()
        asyncio.create_task(_backup_loop(backup_time, backup_interval))
        await bot.run_polling()

async def _backup_loop(last_backup: float, interval: int):
    while True:
        now = time.time()
        if now - last_backup >= interval:
            _create_backup()
            last_backup = now
        await asyncio.sleep(3600)

def _create_backup():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = DIRS["backups"] / f"backup_{ts}"
    backup_dir.mkdir(exist_ok=True)
    for f in FILES.values():
        if f.exists():
            shutil.copy2(f, backup_dir / f.name)
    for name, d in DIRS.items():
        if name in ("logs", "backups"):
            continue
        dest = backup_dir / name
        dest.mkdir(exist_ok=True)
        for file in d.iterdir():
            if file.is_file():
                shutil.copy2(file, dest / file.name)
    shutil.copy2(BASE_DIR / "bot.py", backup_dir / "bot.py")
    logger.info(f"Backup created: {backup_dir.name}")

def _run_webhook_server(port: int):
    """Start Flask webhook server (for Render / Replit / cloud platforms)."""
    if not config.bot_token:
        print("ERROR: Bot token is required. Run --setup-webhook first.")
        sys.exit(1)
    print(f"Starting webhook server on port {port}")
    # Detect public URL from platform env vars or --webhook-url arg
    public_url = (
        os.environ.get("RENDER_EXTERNAL_URL", "")
        or os.environ.get("REPLIT_DEV_DOMAIN", "")
        or os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    )
    if public_url and not public_url.startswith("http"):
        public_url = f"https://{public_url}"
    webhook_url = None
    for arg in sys.argv:
        if arg.startswith("--webhook-url="):
            webhook_url = arg.split("=", 1)[1]
    webhook_url = webhook_url or public_url or config.webhook_url
    if webhook_url:
        webhook_full = f"{webhook_url.rstrip('/')}/webhook"
        asyncio.run(_set_webhook_once(webhook_full))
    webapp.run(host="0.0.0.0", port=port)

async def _set_webhook_once(url: str):
    """Set Telegram webhook URL."""
    app = Application.builder().token(config.bot_token).updater(None).build()
    await app.initialize()
    await app.start()
    await app.bot.set_webhook(url=url)
    info = await app.bot.get_webhook_info()
    logger.info(f"Webhook set to {info.url}")
    await app.stop()
    await app.shutdown()

if __name__ == "__main__":
    if "--setup-webhook" in sys.argv:
        asyncio.run(_setup_webhook())
    elif "--webhook-port" in sys.argv:
        try:
            idx = sys.argv.index("--webhook-port")
            port = int(sys.argv[idx + 1])
        except (ValueError, IndexError):
            port = int(os.environ.get("PORT", 10000))
        _run_webhook_server(port)
    else:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            logger.info("Shutting down.")
