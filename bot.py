import asyncio
import sqlite3
import logging
from datetime import datetime
from typing import Dict, List, Optional
import json
import threading
import random

from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class Database:
    """Thread-safe database"""

    def __init__(self, db_name='auto_poster.db'):
        self.db_name = db_name
        self.lock = threading.Lock()
        self.init_database()

    def get_connection(self):
        conn = sqlite3.connect(self.db_name, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def init_database(self):
        with self.lock:
            conn = self.get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    phone TEXT,
                    session_string TEXT,
                    is_admin INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # is_admin ustunini tekshirish va qo'shish
            cursor.execute("PRAGMA table_info(users)")
            user_columns = [col[1] for col in cursor.fetchall()]
            if 'is_admin' not in user_columns:
                cursor.execute('ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0')
                logger.info("✅ is_admin ustuni qo'shildi")

            # Eski jadval mavjudligini tekshirish
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='auto_posts'")
            table_exists = cursor.fetchone()

            if table_exists:
                # Eski jadvalni yangilash
                cursor.execute("PRAGMA table_info(auto_posts)")
                columns = [col[1] for col in cursor.fetchall()]

                if 'chat_ids' not in columns:
                    # Eski jadval - yangi nom bilan saqlash va qayta yaratish
                    cursor.execute('ALTER TABLE auto_posts RENAME TO auto_posts_old')

                    cursor.execute('''
                        CREATE TABLE auto_posts (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            user_id INTEGER,
                            chat_ids TEXT,
                            chat_titles TEXT,
                            message_text TEXT,
                            interval_minutes INTEGER,
                            is_active INTEGER DEFAULT 1,
                            last_sent TIMESTAMP,
                            total_sent INTEGER DEFAULT 0,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    ''')

                    # Eski ma'lumotlarni ko'chirish (agar mavjud bo'lsa)
                    try:
                        cursor.execute('''
                            INSERT INTO auto_posts (id, user_id, chat_ids, chat_titles, message_text, 
                                                   interval_minutes, is_active, last_sent, total_sent, created_at)
                            SELECT id, user_id, 
                                   '["' || chat_id || '"]', 
                                   '["' || chat_title || '"]',
                                   message_text, interval_minutes, is_active, 
                                   last_sent, total_sent, created_at
                            FROM auto_posts_old
                        ''')
                    except:
                        pass

                    cursor.execute('DROP TABLE auto_posts_old')
            else:
                # Yangi jadval yaratish
                cursor.execute('''
                    CREATE TABLE auto_posts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        chat_ids TEXT,
                        chat_titles TEXT,
                        message_text TEXT,
                        interval_minutes INTEGER,
                        is_active INTEGER DEFAULT 1,
                        last_sent TIMESTAMP,
                        total_sent INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_chats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    chat_id TEXT,
                    chat_title TEXT,
                    chat_type TEXT,
                    is_selected INTEGER DEFAULT 0,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, chat_id)
                )
            ''')

            # user_chats jadvalida is_selected ustunini tekshirish va qo'shish
            cursor.execute("PRAGMA table_info(user_chats)")
            columns = [col[1] for col in cursor.fetchall()]

            if 'is_selected' not in columns:
                # is_selected ustuni yo'q bo'lsa, qo'shamiz
                cursor.execute('ALTER TABLE user_chats ADD COLUMN is_selected INTEGER DEFAULT 0')
                logger.info("✅ is_selected ustuni qo'shildi")

            conn.commit()
            conn.close()

    def save_user(self, user_id, username, phone, session_string):
        with self.lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO users (user_id, username, phone, session_string)
                VALUES (?, ?, ?, ?)
            ''', (user_id, username, phone, session_string))
            conn.commit()
            conn.close()

    def get_user_session(self, user_id):
        with self.lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT session_string FROM users WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            conn.close()
            return result['session_string'] if result else None

    def save_user_chat(self, user_id, chat_id, chat_title, chat_type):
        with self.lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO user_chats (user_id, chat_id, chat_title, chat_type)
                VALUES (?, ?, ?, ?)
            ''', (user_id, chat_id, chat_title, chat_type))
            conn.commit()
            conn.close()

    def get_user_chats(self, user_id):
        with self.lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT chat_id, chat_title, chat_type, is_selected 
                FROM user_chats WHERE user_id = ? ORDER BY chat_title
            ''', (user_id,))
            results = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return results

    def toggle_chat_selection(self, user_id, chat_id):
        with self.lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE user_chats SET is_selected = 1 - is_selected 
                WHERE user_id = ? AND chat_id = ?
            ''', (user_id, chat_id))
            conn.commit()
            conn.close()

    def clear_all_selections(self, user_id):
        with self.lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('UPDATE user_chats SET is_selected = 0 WHERE user_id = ?', (user_id,))
            conn.commit()
            conn.close()

    def get_selected_chats(self, user_id):
        with self.lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT chat_id, chat_title, chat_type 
                FROM user_chats WHERE user_id = ? AND is_selected = 1
            ''', (user_id,))
            results = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return results

    def create_auto_post(self, user_id, chat_ids, chat_titles, message_text, interval_minutes):
        with self.lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO auto_posts (user_id, chat_ids, chat_titles, message_text, interval_minutes)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, json.dumps(chat_ids), json.dumps(chat_titles), message_text, interval_minutes))
            post_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return post_id

    def get_active_posts(self, user_id=None):
        with self.lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            if user_id:
                cursor.execute('''
                    SELECT id, user_id, chat_ids, chat_titles, message_text, 
                           interval_minutes, last_sent, total_sent, is_active
                    FROM auto_posts WHERE user_id = ?
                ''', (user_id,))
            else:
                cursor.execute('''
                    SELECT id, user_id, chat_ids, chat_titles, message_text, 
                           interval_minutes, last_sent, total_sent, is_active
                    FROM auto_posts WHERE is_active = 1
                ''')
            results = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return results

    def update_post_sent(self, post_id):
        with self.lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE auto_posts 
                SET last_sent = CURRENT_TIMESTAMP, total_sent = total_sent + 1
                WHERE id = ?
            ''', (post_id,))
            conn.commit()
            conn.close()

    def toggle_post_status(self, post_id):
        with self.lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('UPDATE auto_posts SET is_active = 1 - is_active WHERE id = ?', (post_id,))
            conn.commit()
            conn.close()

    def delete_post(self, post_id):
        with self.lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('DELETE FROM auto_posts WHERE id = ?', (post_id,))
            conn.commit()
            conn.close()

    def is_admin(self, user_id):
        """Admin ekanligini tekshirish"""
        with self.lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT is_admin FROM users WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            conn.close()
            return result and result['is_admin'] == 1

    def get_all_users(self):
        """Barcha foydalanuvchilar"""
        with self.lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT user_id, username, phone, created_at FROM users ORDER BY created_at DESC')
            results = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return results

    def get_stats(self):
        """Umumiy statistika"""
        with self.lock:
            conn = self.get_connection()
            cursor = conn.cursor()

            cursor.execute('SELECT COUNT(*) as count FROM users')
            users_count = cursor.fetchone()['count']

            cursor.execute('SELECT COUNT(*) as count FROM auto_posts WHERE is_active = 1')
            active_posts = cursor.fetchone()['count']

            cursor.execute('SELECT SUM(total_sent) as total FROM auto_posts')
            total_sent = cursor.fetchone()['total'] or 0

            conn.close()
            return {
                'users': users_count,
                'active_posts': active_posts,
                'total_sent': total_sent
            }


class AutoPosterBot:
    """Asosiy bot"""

    def __init__(self, api_id, api_hash, bot_token, required_channel="@UySotishBot"):
        self.api_id = api_id
        self.api_hash = api_hash
        # Bot uchun har doim yangi session - eski session bilan konflikt bo'lmasligi uchun
        self.bot = TelegramClient(StringSession(), api_id, api_hash)
        self.bot_token = bot_token
        self.db = Database()
        self.temp_data = {}
        self.user_clients = {}
        self.posting_tasks = {}
        self.required_channel = required_channel  # Majburiy obuna kanali

    async def start(self):
        await self.bot.start(bot_token=self.bot_token)
        logger.info("✅ Bot ishga tushdi!")
        self.register_handlers()
        await self.restart_active_posts()
        await self.bot.run_until_disconnected()

    async def check_subscription(self, user_id):
        """Majburiy obuna tekshirish"""
        try:
            member = await self.bot(GetParticipantRequest(
                channel=self.required_channel,
                participant=user_id
            ))
            return True
        except:
            return False

    async def show_subscription_required(self, event):
        """Obuna kerak xabari"""
        text = (
            "⚠️ Botdan foydalanish uchun kanalimizga obuna bo'ling!\n\n"
            f"📢 Kanal: {self.required_channel}\n\n"
            "Obuna bo'lgandan keyin 'Tekshirish ✅' tugmasini bosing!"
        )
        await event.respond(
            text,
            buttons=[
                [Button.url("📢 Obuna bo'lish", f"https://t.me/{self.required_channel.replace('@', '')}")],
                [Button.inline("Tekshirish ✅", b"check_subscription")]
            ]
        )

    def register_handlers(self):

        @self.bot.on(events.NewMessage(pattern='/start'))
        async def start_handler(event):
            await self.cmd_start(event)

        @self.bot.on(events.CallbackQuery())
        async def callback_handler(event):
            await self.handle_callback(event)

        @self.bot.on(
            events.NewMessage(incoming=True, func=lambda e: e.is_private and e.text and not e.text.startswith('/')))
        async def message_handler(event):
            await self.handle_message(event)

    async def cmd_start(self, event):
        # MAJBURIY OBUNA TEKSHIRISH
        user_id = event.sender_id
        is_subscribed = await self.check_subscription(user_id)

        if not is_subscribed:
            await self.show_subscription_required(event)
            return

        text = """
🤖 **Auto Poster Bot v2.0**

✨ Bir xabar → Ko'p guruhga!

⚠️ DIQQAT: Spam oldini olish uchun:
• Minimal interval: 2 daqiqa
• Guruhlar orasida: 15-30 soniya kutish
• Xavfsiz ishlatish tavsiya etiladi!

Boshlash:
"""
        buttons = [
            [Button.inline("🔗 Profil Ulash", b"connect")],
            [Button.inline("📊 Profilim", b"profile")],
            [Button.inline("📝 Postlar", b"posts")],
            [Button.inline("❓ Yordam", b"help")]
        ]

        # Admin bo'lsa admin panel tugmasini qo'shamiz
        if self.db.is_admin(user_id):
            buttons.append([Button.inline("👑 Admin Panel", b"admin")])

        await event.reply(text, buttons=buttons)

    async def handle_callback(self, event):
        user_id = event.sender_id
        data = event.data.decode('utf-8')

        # check_subscription tugmasi uchun tekshirishni o'tkazib yuborish
        if data != "check_subscription":
            # MAJBURIY OBUNA TEKSHIRISH
            is_subscribed = await self.check_subscription(user_id)
            if not is_subscribed:
                await event.answer("⚠️ Avval kanalga obuna bo'ling!", alert=True)
                await self.show_subscription_required(event)
                return

        try:
            if data == "check_subscription":
                # Obunani tekshirish
                is_subscribed = await self.check_subscription(user_id)
                if is_subscribed:
                    await event.answer("✅ Obuna tasdiqlandi!", alert=True)
                    await self.cmd_start(event)
                else:
                    await event.answer("❌ Siz hali obuna bo'lmagansiz!", alert=True)
            elif data == "main":
                await self.cmd_start(event)
            elif data == "connect":
                await self.start_connection(event)
            elif data == "profile":
                await self.show_profile(event)
            elif data == "posts":
                await self.show_posts(event)
            elif data == "help":
                await self.show_help(event)
            elif data == "load":
                await self.load_chats(event)
            elif data == "create":
                await self.show_chat_selection(event)
            elif data.startswith("toggle_chat:"):
                chat_id = data.split(":", 1)[1]
                self.db.toggle_chat_selection(user_id, chat_id)
                await self.show_chat_selection(event)
            elif data == "confirm":
                await self.ask_message(event)
            elif data.startswith("int:"):
                minutes = float(data.split(":")[1])
                await self.create_post(event, minutes)
            elif data.startswith("tog:"):
                post_id = int(data.split(":")[1])
                await self.toggle_post(event, post_id)
            elif data.startswith("del:"):
                post_id = int(data.split(":")[1])
                await self.delete_post(event, post_id)
            elif data == "admin":
                await self.show_admin_panel(event)
            elif data == "admin_users":
                await self.show_admin_users(event)
            elif data == "admin_stats":
                await self.show_admin_stats(event)
        except Exception as e:
            logger.error(f"Callback error: {e}")
            try:
                await event.answer(f"❌ Xato: {str(e)}", alert=True)
            except:
                pass

    async def start_connection(self, event):
        user_id = event.sender_id
        self.temp_data[user_id] = {'step': 'phone'}
        await event.respond(
            "📱 Telefon raqam (+998...):",
            buttons=[[Button.inline("« Orqaga", b"main")]]
        )
        try:
            await event.delete()
        except:
            pass

    async def handle_message(self, event):
        user_id = event.sender_id
        if user_id not in self.temp_data:
            return

        step = self.temp_data[user_id].get('step')

        if step == 'phone':
            await self.process_phone(event)
        elif step == 'code':
            await self.process_code(event)
        elif step == '2fa':
            await self.process_2fa(event)
        elif step == 'message':
            await self.process_message_text(event)

    async def process_phone(self, event):
        user_id = event.sender_id
        phone = event.text.strip()

        if not phone.startswith('+'):
            await event.reply("❌ + bilan boshlang!")
            return

        msg = await event.reply("⏳ Kod yuborilmoqda...")

        try:
            # Yangi session yaratish
            client = TelegramClient(StringSession(), self.api_id, self.api_hash)
            await client.connect()
            result = await client.send_code_request(phone)

            self.temp_data[user_id].update({
                'step': 'code',
                'phone': phone,
                'phone_code_hash': result.phone_code_hash,
                'client': client
            })

            await msg.edit(f"✅ Kod yuborildi!\n\n{phone}\n\nKodni nuqta bilan yuboring misol: 12.345")
        except Exception as e:
            logger.error(f"Phone error: {e}")
            await msg.edit(f"❌ Xato: {str(e)}")
            if user_id in self.temp_data:
                del self.temp_data[user_id]

    async def process_code(self, event):
        user_id = event.sender_id
        code = event.text.strip()
        temp = self.temp_data.get(user_id, {})

        client = temp.get('client')
        phone = temp.get('phone')
        phone_code_hash = temp.get('phone_code_hash')

        msg = await event.reply("⏳ Tekshirilmoqda...")

        try:
            await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
            session_string = client.session.save()
            me = await client.get_me()

            self.db.save_user(user_id, me.username or me.first_name, phone, session_string)
            self.user_clients[user_id] = client

            await msg.edit(
                f"✅ Muvaffaqiyat!\n\n👤 {me.first_name}\n\nGuruhlarni yuklang:",
                buttons=[[Button.inline("📂 Yuklash", b"load")]]
            )

            del self.temp_data[user_id]
        except SessionPasswordNeededError:
            self.temp_data[user_id]['step'] = '2fa'
            await msg.edit("🔐 2FA parol:")
        except PhoneCodeInvalidError:
            await msg.edit("❌ Noto'g'ri kod!")
        except Exception as e:
            logger.error(f"Code error: {e}")
            await msg.edit(f"❌ Xato: {str(e)}")

    async def process_2fa(self, event):
        user_id = event.sender_id
        password = event.text.strip()
        temp = self.temp_data.get(user_id, {})

        client = temp.get('client')
        phone = temp.get('phone')

        msg = await event.reply("⏳...")

        try:
            await client.sign_in(password=password)
            session_string = client.session.save()
            me = await client.get_me()

            self.db.save_user(user_id, me.username or me.first_name, phone, session_string)
            self.user_clients[user_id] = client

            await msg.edit(
                f"✅ {me.first_name}",
                buttons=[[Button.inline("📂 Yuklash", b"load")]]
            )

            del self.temp_data[user_id]
        except Exception as e:
            logger.error(f"2FA error: {e}")
            await msg.edit(f"❌ Xato: {str(e)}")

    async def show_profile(self, event):
        user_id = event.sender_id
        session = self.db.get_user_session(user_id)

        if not session:
            await event.respond("❌ Ulang!", buttons=[[Button.inline("🔗 Ulash", b"connect")]])
            try:
                await event.delete()
            except:
                pass
            return

        try:
            client = await self.get_user_client(user_id)

            # TO'G'RI TEKSHIRISH: Client mavjud va authorized ekanligini tasdiqlash
            if not client or not await client.is_user_authorized():
                await event.respond(
                    "❌ Profil sessiyasi yaroqsiz!\n\nQaytadan ulang:",
                    buttons=[[Button.inline("🔗 Qayta ulash", b"connect")]]
                )
                try:
                    await event.delete()
                except:
                    pass
                return

            me = await client.get_me()
            chats = self.db.get_user_chats(user_id)

            text = f"✅ {me.first_name}\n📊 Guruhlar: {len(chats)}"

            await event.respond(
                text,
                buttons=[
                    [Button.inline("📂 Yuklash", b"load")],
                    [Button.inline("➕ Post", b"create")],
                    [Button.inline("🏠 Menyu", b"main")]
                ]
            )

            try:
                await event.delete()
            except:
                pass
        except Exception as e:
            logger.error(f"Profile error: {e}")
            await event.respond(
                "❌ Profil ulanmagan yoki sessiya yaroqsiz!\n\nQaytadan ulang:",
                buttons=[[Button.inline("🔗 Qayta ulash", b"connect")]]
            )

    async def load_chats(self, event):
        user_id = event.sender_id
        msg = await event.respond("⏳ Yuklanmoqda...")

        try:
            client = await self.get_user_client(user_id)
            if not client:
                await msg.edit("❌ Avval ulang!")
                return

            # TO'G'RI TEKSHIRISH: Client authorized ekanligini tasdiqlash
            if not await client.is_user_authorized():
                await msg.edit("❌ Sessiya yaroqsiz! Qaytadan ulang:")
                return

            # Avval eski guruhlarni tozalash
            with self.db.lock:
                conn = self.db.get_connection()
                cursor = conn.cursor()
                cursor.execute('DELETE FROM user_chats WHERE user_id = ?', (user_id,))
                conn.commit()
                conn.close()

            dialogs = await client.get_dialogs()
            count = 0
            seen_ids = set()

            for dialog in dialogs:
                # ANIQROQ FILTR: Faqat guruhlar va yozish huquqi bor joylar
                if dialog.is_group and str(dialog.id) not in seen_ids:
                    try:
                        # Guruhga yozish huquqini tekshirish
                        entity = dialog.entity

                        # Supergroup/channel uchun
                        if hasattr(entity, 'default_banned_rights'):
                            perms = entity.default_banned_rights
                            can_send = perms is None or not perms.send_messages
                        else:
                            # Oddiy guruhlar uchun
                            can_send = True

                        # Admin yoki yozish huquqi bor bo'lsa qo'shamiz
                        if can_send:
                            self.db.save_user_chat(
                                user_id, str(dialog.id), dialog.title, 'group'
                            )
                            seen_ids.add(str(dialog.id))
                            count += 1
                            logger.info(f"✅ Qo'shildi: {dialog.title}")
                        else:
                            logger.info(f"⏭ O'tkazildi (huquq yo'q): {dialog.title}")

                    except Exception as e:
                        logger.error(f"Guruh tekshirish xatosi {dialog.title}: {e}")
                        # Xato bo'lsa ham qo'shib qo'yamiz (ehtiyot chorasi)
                        if str(dialog.id) not in seen_ids:
                            self.db.save_user_chat(
                                user_id, str(dialog.id), dialog.title, 'group'
                            )
                            seen_ids.add(str(dialog.id))
                            count += 1

            await msg.edit(
                f"✅ {count} guruh yuklandi!\n\n💡 Faqat yozish huquqi bor guruhlar ko'rsatilgan.",
                buttons=[
                    [Button.inline("➕ Post", b"create")],
                    [Button.inline("🏠 Menyu", b"main")]
                ]
            )
        except Exception as e:
            logger.error(f"Load error: {e}")
            await msg.edit(f"❌ Xato: {str(e)}\n\nQaytadan ulang!")

    async def show_chat_selection(self, event):
        user_id = event.sender_id
        chats = self.db.get_user_chats(user_id)

        if not chats:
            try:
                await event.edit("❌ Avval yuklang!", buttons=[[Button.inline("📂 Yuklash", b"load")]])
            except:
                await event.respond("❌ Avval yuklang!", buttons=[[Button.inline("📂 Yuklash", b"load")]])
                try:
                    await event.delete()
                except:
                    pass
            return

        selected_count = sum(1 for c in chats if c['is_selected'])

        text = f"📂 Guruhlar ({selected_count} tanlandi)\n\nGuruhlarni tanlang:"
        buttons = []

        # Ko'proq guruh ko'rsatish - 50 tagacha
        for chat in chats[:50]:
            icon = "✅" if chat['is_selected'] else "⬜"
            emoji = "👥"  # Faqat guruhlar
            # Uzun nomlarni qisqartirish
            title = chat['chat_title'][:30]
            buttons.append([Button.inline(
                f"{icon} {emoji} {title}",
                f"toggle_chat:{chat['chat_id']}".encode()
            )])

        if len(chats) > 50:
            buttons.append([Button.inline(f"ℹ️ Ko'proq: {len(chats) - 50} ta", b"info")])

        if selected_count > 0:
            buttons.append([Button.inline(f"➡️ Davom ({selected_count})", b"confirm")])

        buttons.append([Button.inline("🔄 Yangilash", b"load")])
        buttons.append([Button.inline("🏠 Menyu", b"main")])

        try:
            # Eski xabarni tahrirlash (yangi xabar emas!)
            await event.edit(text, buttons=buttons)
        except:
            # Agar edit ishlamasa, yangi xabar yuboramiz
            await event.respond(text, buttons=buttons)
            try:
                await event.delete()
            except:
                pass

    async def ask_message(self, event):
        user_id = event.sender_id
        selected = self.db.get_selected_chats(user_id)

        if not selected:
            await event.answer("❌ Tanlanmagan!", alert=True)
            return

        text = f"📝 Xabar yozing\n\nTanlangan: {len(selected)} guruh"
        self.temp_data[user_id] = {'step': 'message'}

        try:
            await event.edit(text, buttons=[[Button.inline("« Orqaga", b"create")]])
        except:
            await event.respond(text, buttons=[[Button.inline("« Orqaga", b"create")]])
            try:
                await event.delete()
            except:
                pass

    async def process_message_text(self, event):
        user_id = event.sender_id
        message_text = event.text
        self.temp_data[user_id]['message_text'] = message_text

        text = f"⏱ Interval\n\n{message_text[:50]}...\n\nHar necha daqiqada?\n\n⚠️ Minimal: 2 daqiqa (spam oldini olish)"

        buttons = [
            [
                Button.inline("2 daq", b"int:2"),
                Button.inline("5 daq", b"int:5"),
                Button.inline("10 daq", b"int:10")
            ],
            [
                Button.inline("15 daq", b"int:15"),
                Button.inline("30 daq", b"int:30"),
                Button.inline("1 soat", b"int:60")
            ],
            [
                Button.inline("2 soat", b"int:120"),
                Button.inline("6 soat", b"int:360"),
                Button.inline("12 soat", b"int:720")
            ],
            [Button.inline("« Orqaga", b"create")]
        ]

        await event.reply(text, buttons=buttons)

    async def create_post(self, event, interval_minutes):
        user_id = event.sender_id
        temp = self.temp_data.get(user_id, {})
        message_text = temp.get('message_text')

        if not message_text:
            await event.answer("❌ Xato!", alert=True)
            return

        selected = self.db.get_selected_chats(user_id)
        if not selected:
            await event.answer("❌ Tanlanmagan!", alert=True)
            return

        chat_ids = [c['chat_id'] for c in selected]
        chat_titles = [c['chat_title'] for c in selected]

        post_id = self.db.create_auto_post(user_id, chat_ids, chat_titles, message_text, interval_minutes)
        self.db.clear_all_selections(user_id)

        task = asyncio.create_task(
            self.auto_posting_task(post_id, user_id, chat_ids, message_text, interval_minutes)
        )
        self.posting_tasks[post_id] = task

        if user_id in self.temp_data:
            del self.temp_data[user_id]

        await event.respond(
            f"✅ Post yaratildi!\n\n📢 {len(selected)} guruh\n⏱ {interval_minutes} daq\n\nBot ishlayapti!",
            buttons=[
                [Button.inline("📝 Postlar", b"posts")],
                [Button.inline("🏠 Menyu", b"main")]
            ]
        )

        try:
            await event.delete()
        except:
            pass

        logger.info(f"✅ Post: {post_id}, {len(selected)} guruh, {interval_minutes}min")

    async def show_posts(self, event):
        user_id = event.sender_id
        posts = self.db.get_active_posts(user_id)

        if not posts:
            await event.respond(
                "📝 Postlar yo'q",
                buttons=[
                    [Button.inline("➕ Yaratish", b"create")],
                    [Button.inline("🏠 Menyu", b"main")]
                ]
            )
            try:
                await event.delete()
            except:
                pass
            return

        text = "📝 Postlar:\n\n"
        buttons = []

        for post in posts:
            post_id = post['id']
            chat_ids = json.loads(post['chat_ids'])
            interval = post['interval_minutes']
            total = post['total_sent']
            is_active = post['is_active']

            is_running = post_id in self.posting_tasks
            status = "▶️" if (is_active and is_running) else "⏸"

            text += f"{status} #{post_id}\n"
            text += f"📢 {len(chat_ids)} guruh\n"
            text += f"⏱ {interval}d | 📊 {total}x\n\n"

            buttons.append([
                Button.inline(f"{status} #{post_id}", f"tog:{post_id}".encode()),
                Button.inline("🗑", f"del:{post_id}".encode())
            ])

        buttons.append([Button.inline("➕ Yangi", b"create")])
        buttons.append([Button.inline("🏠 Menyu", b"main")])

        await event.respond(text, buttons=buttons)
        try:
            await event.delete()
        except:
            pass

    async def toggle_post(self, event, post_id):
        if post_id in self.posting_tasks:
            self.posting_tasks[post_id].cancel()
            del self.posting_tasks[post_id]
            self.db.toggle_post_status(post_id)
            await event.answer("⏸ To'xtatildi!")
        else:
            posts = self.db.get_active_posts()
            post_data = next((p for p in posts if p['id'] == post_id), None)

            if post_data:
                chat_ids = json.loads(post_data['chat_ids'])
                message = post_data['message_text']
                interval = post_data['interval_minutes']
                user_id = post_data['user_id']

                task = asyncio.create_task(
                    self.auto_posting_task(post_id, user_id, chat_ids, message, interval)
                )
                self.posting_tasks[post_id] = task
                self.db.toggle_post_status(post_id)
                await event.answer("▶️ Boshlandi!")

        await self.show_posts(event)

    async def delete_post(self, event, post_id):
        if post_id in self.posting_tasks:
            self.posting_tasks[post_id].cancel()
            del self.posting_tasks[post_id]

        self.db.delete_post(post_id)
        await event.answer("🗑 O'chirildi!")
        await self.show_posts(event)

    async def show_help(self, event):
        text = """
❓ YORDAM

1. Profil ulash
2. Guruhlar yuklash
3. Ko'p guruh tanlash
4. Xabar yozish
5. Interval tanlash

Admin: @jovohircoder

✅ Tayyor!
"""
        await event.respond(text, buttons=[[Button.inline("🏠 Menyu", b"main")]])
        try:
            await event.delete()
        except:
            pass

    async def show_admin_panel(self, event):
        """Admin panel"""
        user_id = event.sender_id

        if not self.db.is_admin(user_id):
            await event.answer("❌ Ruxsat yo'q!", alert=True)
            return

        stats = self.db.get_stats()

        text = f"""
👑 ADMIN PANEL

📊 Statistika:
👥 Foydalanuvchilar: {stats['users']}
📝 Aktiv postlar: {stats['active_posts']}
📤 Jami yuborilgan: {stats['total_sent']}
"""

        buttons = [
            [Button.inline("👥 Foydalanuvchilar", b"admin_users")],
            [Button.inline("📊 To'liq statistika", b"admin_stats")],
            [Button.inline("🏠 Menyu", b"main")]
        ]

        await event.respond(text, buttons=buttons)
        try:
            await event.delete()
        except:
            pass

    async def show_admin_users(self, event):
        """Foydalanuvchilar ro'yxati"""
        user_id = event.sender_id

        if not self.db.is_admin(user_id):
            await event.answer("❌ Ruxsat yo'q!", alert=True)
            return

        users = self.db.get_all_users()

        text = "👥 FOYDALANUVCHILAR\n\n"

        for i, user in enumerate(users[:20], 1):
            username = user['username'] or "yo'q"
            phone = user['phone'] or "yo'q"
            text += f"{i}. @{username}\n📞 {phone}\n\n"

        if len(users) > 20:
            text += f"\n... va yana {len(users) - 20} ta"

        await event.respond(
            text,
            buttons=[[Button.inline("« Orqaga", b"admin")]]
        )
        try:
            await event.delete()
        except:
            pass

    async def show_admin_stats(self, event):
        """To'liq statistika"""
        user_id = event.sender_id

        if not self.db.is_admin(user_id):
            await event.answer("❌ Ruxsat yo'q!", alert=True)
            return

        stats = self.db.get_stats()
        users = self.db.get_all_users()
        all_posts = self.db.get_active_posts()

        text = f"""
📊 TO'LIQ STATISTIKA

👥 Foydalanuvchilar: {stats['users']}
📝 Jami postlar: {len(all_posts)}
✅ Aktiv postlar: {stats['active_posts']}
📤 Jami yuborilgan: {stats['total_sent']}

📈 Oxirgi 5 foydalanuvchi:
"""

        for i, user in enumerate(users[:5], 1):
            username = user['username'] or "yo'q"
            text += f"{i}. @{username}\n"

        await event.respond(
            text,
            buttons=[[Button.inline("« Orqaga", b"admin")]]
        )
        try:
            await event.delete()
        except:
            pass

    async def get_user_client(self, user_id):
        if user_id in self.user_clients:
            return self.user_clients[user_id]

        session_string = self.db.get_user_session(user_id)
        if not session_string:
            return None

        try:
            # Session string dan to'g'ri yuklash
            client = TelegramClient(
                StringSession(session_string),
                self.api_id,
                self.api_hash
            )
            await client.connect()

            # Tekshirish - client haqiqatan ulanganmi?
            if not await client.is_user_authorized():
                logger.error(f"User {user_id} not authorized")
                return None

            self.user_clients[user_id] = client
            return client
        except Exception as e:
            logger.error(f"Client error: {e}")
            return None

    async def auto_posting_task(self, post_id, user_id, chat_ids, message, interval_minutes):
        while True:
            try:
                await asyncio.sleep(interval_minutes * 60)

                client = await self.get_user_client(user_id)
                if not client:
                    break

                # Xabarga reklama qo'shamiz
                advertisement = "\n\n━━━━━━━━━━━━━━\n📢 @Autoxabaryuboruvchibot orqali yuborldi "
                full_message = message + advertisement

                for i, chat_id in enumerate(chat_ids):
                    try:
                        await client.send_message(int(chat_id), full_message)
                        # XAVFSIZLIK: Guruhlar orasida 15-30 soniya kutish
                        wait_time = random.randint(15, 30)
                        logger.info(f"Waiting {wait_time}s before next group...")
                        await asyncio.sleep(wait_time)
                    except Exception as e:
                        logger.error(f"Send error {chat_id}: {e}")
                        # Xato bo'lsa ham kutamiz
                        await asyncio.sleep(10)

                self.db.update_post_sent(post_id)
                logger.info(f"✅ Sent: {post_id}, {len(chat_ids)} chats")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Task error: {e}")
                await asyncio.sleep(60)

    async def restart_active_posts(self):
        posts = self.db.get_active_posts()

        for post in posts:
            post_id = post['id']
            user_id = post['user_id']
            chat_ids = json.loads(post['chat_ids'])
            message = post['message_text']
            interval = post['interval_minutes']

            task = asyncio.create_task(
                self.auto_posting_task(post_id, user_id, chat_ids, message, interval)
            )
            self.posting_tasks[post_id] = task
            logger.info(f"✅ Restarted: {post_id}")


async def main():
    # ⚠️ KONFIGURATSIYA
    API_ID = 37841426
    API_HASH = "5329711a1d0b79cecb6f2edf68d93469"
    BOT_TOKEN = "8604995011:AAE9nC9Jf4drCmMdhbbzgyCnEg7JOhpQMrg"
    REQUIRED_CHANNEL =   # Majburiy obuna kanali

    # 👑 ADMIN IDlar
    ADMIN_IDS = [8201674543]  # Admin user ID

    import os
    os.makedirs('sessions', exist_ok=True)

    bot = AutoPosterBot(API_ID, API_HASH, BOT_TOKEN, REQUIRED_CHANNEL)

    # Adminlarni belgilash
    for admin_id in ADMIN_IDS:
        try:
            with bot.db.lock:
                conn = bot.db.get_connection()
                cursor = conn.cursor()
                cursor.execute('UPDATE users SET is_admin = 1 WHERE user_id = ?', (admin_id,))
                # Agar user yo'q bo'lsa, qo'shamiz
                cursor.execute('INSERT OR IGNORE INTO users (user_id, is_admin) VALUES (?, 1)', (admin_id,))
                conn.commit()
                conn.close()
            logger.info(f"✅ Admin: {admin_id}")
        except Exception as e:
            logger.error(f"Admin set error: {e}")

    await bot.start()


if __name__ == '__main__':
    asyncio.run(main())