# coding: utf-8
import asyncio
import glob
import json
import logging
import uuid
import os
import stat
import threading
import time
from datetime import datetime
import pytz

# MongoDB imports
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, PyMongoError

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

# ===================== DATABASE CONFIGURATION ======================
# MongoDB bağlantı URL'si
os.environ["MONGODB_URL"] = "mongodb://mongo:IvzLvbUGBxZrNVUYiJKbObQYBTXlriTK@zephyr.proxy.rlwy.net:31725"

MONGODB_URL = os.environ["MONGODB_URL"]

# Türkiye timezone'u
TURKEY_TZ = pytz.timezone('Europe/Istanbul')

# MongoDB baglantisi
try:
    mongo_client = MongoClient(MONGODB_URL)
    mongo_db = mongo_client.telegram_bot
    print("MongoDB baglantisi basarili")
except ConnectionFailure as e:
    print("MongoDB baglanti hatasi: " + str(e))
    mongo_client = None
    mongo_db = None

logger = logging.getLogger(__name__)

# ===================== MAIN BOT CLASS ======================
class TelegramBot:
    # ===================== BOT INITIALIZATION =======================
    def __init__(self):
        # BotFather token
        self.BOT_TOKEN = "8188644646:AAFcaiJKZKtnXJo5DZSWLUORq4f_Dj_W-Nc"
        
        # YÖNETİCİ ID'Sİ
        self.ADMIN_ID = 6615127610
        
        # Klasörler
        self.users_dir = 'bot_users'
        self.ensure_users_directory()
        
        # Bot durumları
        self.user_states = {}
        self.telethon_clients = {}
        self.active_tasks = {}
        self.app = None
        
        # Onaylı kullanıcılar listesi
        self.approved_users = set()
        self.load_approved_users()
        
        # Thread lock dosya işlemleri için
        self.file_lock = threading.Lock()
        self.session_locks = {}
        
        # Mevcut session'ları yükle
        self.load_existing_sessions()

    # ===================== MONGODB DATA MANAGEMENT =======================
    def save_user_data(self, user_id, data):
        """Kullanıcı verilerini MongoDB'ye kaydet"""
        if mongo_client is None:
            print("❌ MongoDB bağlantısı yok")
            return
        try:
            collection = mongo_db.users
            data['user_id'] = user_id
            data['last_updated'] = self.get_turkey_time()  # Türkiye saati
            collection.replace_one({'user_id': user_id}, data, upsert=True)
        except PyMongoError as e:
            print(f"❌ MongoDB kaydetme hatası: {e}")

    def load_user_data(self, user_id):
        """Kullanıcı verilerini MongoDB'den yükle"""
        if mongo_client is None:
            print("❌ MongoDB bağlantısı yok")
            return {}
        try:
            collection = mongo_db.users
            user_data = collection.find_one({'user_id': user_id})
            if user_data:
                user_data.pop('_id', None)
                return user_data
            return {}
        except PyMongoError as e:
            print(f"❌ MongoDB yükleme hatası: {e}")
            return {}

    # ===================== UTILITY FUNCTIONS =======================
    def get_turkey_time(self):
        """Türkiye saatini al"""
        return datetime.now(TURKEY_TZ)
    
    def format_turkey_time(self, dt=None, format_str='%d.%m.%Y %H:%M'):
        """Türkiye saatini formatla"""
        if dt is None:
            dt = self.get_turkey_time()
        elif dt.tzinfo is None:
            # Naive datetime'ı UTC olarak kabul et ve Türkiye saatine çevir
            dt = pytz.UTC.localize(dt).astimezone(TURKEY_TZ)
        elif dt.tzinfo != TURKEY_TZ:
            # Farklı timezone'dan Türkiye saatine çevir
            dt = dt.astimezone(TURKEY_TZ)
        return dt.strftime(format_str)
    
    def ensure_users_directory(self):
        """Kullanıcı klasörünü oluştur"""
        if not os.path.exists(self.users_dir):
            os.makedirs(self.users_dir)
    
    def get_approved_users_file(self):
        """Onaylı kullanıcılar dosya yolu"""
        return os.path.join(self.users_dir, 'approved_users.json')
    
    def load_approved_users(self):
        """Onaylı kullanıcıları yükle"""
        approved_file = self.get_approved_users_file()
        if os.path.exists(approved_file):
            try:
                with open(approved_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.approved_users = set(data.get('approved_users', []))
            except:
                self.approved_users = set()
        else:
            self.approved_users = set()
        
        self.approved_users.add(self.ADMIN_ID)
    
    def save_approved_users(self):
        """Onaylı kullanıcıları kaydet"""
        approved_file = self.get_approved_users_file()
        data = {
            'approved_users': list(self.approved_users),
            'last_updated': self.get_turkey_time().isoformat()
        }
        with open(approved_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_existing_sessions(self):
        """Mevcut session dosyalarını yükle"""
        try:
            session_files = glob.glob(os.path.join(self.users_dir, "session_*.session"))
            for session_file in session_files:
                try:
                    # Dosya adından user_id'yi çıkar
                    filename = os.path.basename(session_file)
                    user_id_str = filename.replace("session_", "").replace(".session", "")
                    user_id = int(user_id_str)
                    
                    # Kullanıcı verilerini kontrol et
                    user_data = self.load_user_data(user_id)
                    if user_data.get('session_active') and user_data.get('api_id') and user_data.get('api_hash'):
                        print(f"📱 Session bulundu: Kullanıcı {user_id}")
                    
                except Exception as e:
                    print(f"⚠️ Session yükleme hatası {session_file}: {e}")
                    
        except Exception as e:
            print(f"❌ Session dosyaları yüklenemedi: {e}")

    # ===================== USER APPROVAL SYSTEM =======================
    def is_user_approved(self, user_id):
        """Kullanıcının onaylı olup olmadığını kontrol et"""
        user_data = self.load_user_data(user_id)
        if user_data.get('approval_status') == 'banned':
            return False
        return user_id in self.approved_users

    def is_user_banned(self, user_id):
        """Kullanıcının yasaklı olup olmadığını kontrol et"""
        user_data = self.load_user_data(user_id)
        return user_data.get('approval_status') == 'banned'
    
    def approve_user(self, user_id):
        """Kullanıcıyı onayla"""
        self.approved_users.add(user_id)
        self.save_approved_users()
        
        user_data = self.load_user_data(user_id)
        user_data['approved_date'] = self.get_turkey_time().isoformat()
        user_data['approval_status'] = 'approved'
        self.save_user_data(user_id, user_data)
    
    def reject_user(self, user_id):
        """Kullanıcı onayını reddet"""
        if user_id in self.approved_users:
            self.approved_users.remove(user_id)
            self.save_approved_users()
        
        user_data = self.load_user_data(user_id)
        user_data['rejected_date'] = self.get_turkey_time().isoformat()
        user_data['approval_status'] = 'rejected'
        self.save_user_data(user_id, user_data)

    def ban_user(self, user_id):
        """Kullanıcıyı banla"""
        if user_id in self.approved_users:
            self.approved_users.remove(user_id)
            self.save_approved_users()
        
        user_data = self.load_user_data(user_id)
        user_data['banned_date'] = self.get_turkey_time().isoformat()
        user_data['approval_status'] = 'banned'
        self.save_user_data(user_id, user_data)

    def unban_user(self, user_id):
        """Kullanıcının banını kaldır"""
        self.approved_users.add(user_id)
        self.save_approved_users()
        
        user_data = self.load_user_data(user_id)
        user_data['unbanned_date'] = self.get_turkey_time().isoformat()
        user_data['approval_status'] = 'approved'
        self.save_user_data(user_id, user_data)

    # ===================== HELPER FUNCTIONS =======================
    async def safe_edit_message(self, query, text, reply_markup=None):
        """Güvenli mesaj düzenleme"""
        try:
            await query.edit_message_text(text=text, reply_markup=reply_markup)
            return True
        except Exception as e:
            error_msg = str(e)
            if any(err in error_msg for err in [
                "Query is too old", 
                "response timeout expired", 
                "query id is invalid",
                "Message is not modified",
                "message content and reply markup are exactly the same"
            ]):
                print(f"⚠️ Mesaj düzenleme atlandı: {error_msg[:70]}...")
                return False
            else:
                print(f"❌ Mesaj düzenleme hatası: {error_msg}")
                return False

    # ===================== ADMIN FUNCTIONS =======================
    async def notify_admin_new_user(self, user_id, user_name):
        """Yöneticiye yeni kullanıcı bildirimi gönder"""
        try:
            if not self.is_user_approved(user_id):
                message = f"""🔔 YENİ KULLANICI TALEBİ

👤 Kullanıcı: {user_name}
🆔 ID: {user_id}
⏰ Tarih: {self.format_turkey_time()}

Bu kullanıcının bot'u kullanmasını onaylıyor musunuz?"""
                
                keyboard = [
                    [InlineKeyboardButton("✅ Onayla", callback_data=f"approve_{user_id}"),
                     InlineKeyboardButton("❌ Reddet", callback_data=f"reject_{user_id}")],
                    [InlineKeyboardButton("👤 Kullanıcı Bilgileri", callback_data=f"user_info_{user_id}")]
                ]
                
                await self.app.bot.send_message(
                    chat_id=self.ADMIN_ID,
                    text=message,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        except Exception as e:
            print(f"❌ Yönetici bildirimi hatası: {str(e)}")

    # ===================== BOT COMMANDS =======================
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Ana komut"""
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name
        
        print(f"🚀 /start - Kullanıcı: {user_id} ({user_name})")
        
        # Ban kontrolü
        if user_id != self.ADMIN_ID and self.is_user_banned(user_id):
            message = f"""🚫 HESAP YASAKLANDI

Merhaba {user_name},

❌ Hesabınız yasaklanmıştır.
📞 İtiraz için yöneticiye başvurun: {self.ADMIN_ID}"""
            
            await update.message.reply_text(message)
            return
        
        # Yönetici kontrolü
        if user_id == self.ADMIN_ID:
            message = f"""🔑 YÖNETİCİ PANELİ

Merhaba {user_name}! 👋

✨ Yönetici Özellikleri:
📱 Telegram hesabınızı bağlayın
📋 Hedef grupları ekleyin  
📝 Mesaj içeriği belirleyin
🚀 Anlık mesaj gönderimi
⏰ Otomatik gönderim
👥 Kullanıcı onayları"""
            
            keyboard = [
                [InlineKeyboardButton("🔧 Kurulum Başlat", callback_data="setup")],
                [InlineKeyboardButton("📊 Ayarlarım", callback_data="settings")],
                [InlineKeyboardButton("👥 Bekleyen Onaylar", callback_data="pending_approvals")],
                [InlineKeyboardButton("📋 Onaylı Kullanıcılar", callback_data="approved_users")],
                [InlineKeyboardButton("🚫 Yasaklı Kullanıcılar", callback_data="banned_users")],
                [InlineKeyboardButton("❓ Yardım", callback_data="help")]
            ]
        
        # Onaylı kullanıcı kontrolü
        elif self.is_user_approved(user_id):
            message = f"""🤖 Telegram Mesaj Gönderici Bot

Merhaba {user_name}! 👋

✅ Hesabınız onaylandı!

✨ Özellikler:
📱 Telegram hesabınızı bağlayın
📋 Hedef grupları ekleyin  
📝 Mesaj içeriği belirleyin
🚀 Anlık mesaj gönderimi
⏰ Otomatik gönderim"""
            
            keyboard = [
                [InlineKeyboardButton("🔧 Kurulum Başlat", callback_data="setup")],
                [InlineKeyboardButton("📊 Ayarlarım", callback_data="settings")],
                [InlineKeyboardButton("❓ Yardım", callback_data="help")]
            ]
        
        # Onaysız kullanıcı
        else:
            message = f"""🔒 ERİŞİM BEKLEMEDE

Merhaba {user_name}! 👋

⏳ Hesabınız henüz onaylanmamış.
📝 Kullanıcı ID'niz: {user_id}

🔔 Yöneticiye onay talebi gönderildi.
⏰ Lütfen onay bekleyin."""
            
            keyboard = [
                [InlineKeyboardButton("🔄 Durumu Kontrol Et", callback_data="check_approval")],
                [InlineKeyboardButton("📞 Yöneticiye Mesaj", callback_data="contact_admin")]
            ]
            
            await self.notify_admin_new_user(user_id, user_name)
        
        await update.message.reply_text(
            message, 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # ===================== BUTTON CALLBACKS =======================
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Buton tıklamaları"""
        query = update.callback_query
        
        try:
            await query.answer()
        except Exception as e:
            error_msg = str(e)
            if "Query is too old" in error_msg:
                return
        
        user_id = query.from_user.id
        data = query.data
        
        print(f"🔘 Buton: {data} - Kullanıcı: {user_id}")
        
        try:
            # Yönetici işlemleri
            if data.startswith("approve_"):
                await self.handle_user_approval(query, data)
            elif data.startswith("reject_"):
                await self.handle_user_rejection(query, data)
            elif data.startswith("ban_"):
                await self.handle_user_ban(query, data)
            elif data.startswith("unban_"):
                await self.handle_user_unban(query, data)
            elif data.startswith("user_info_"):
                await self.show_user_info(query, data)
            elif data == "pending_approvals":
                await self.show_pending_approvals(query, user_id)
            elif data == "approved_users":
                await self.show_approved_users(query, user_id)
            elif data == "banned_users":
                await self.show_banned_users(query, user_id)
            elif data == "check_approval":
                await self.check_user_approval_status(query, user_id)
            elif data == "contact_admin":
                await self.show_admin_contact(query, user_id)
            
            # Telegram kod giriş butonları
            elif data.startswith("code_digit_"):
                await self.handle_code_digit(query, user_id, data)
            elif data == "code_delete":
                await self.handle_code_delete(query, user_id)
            elif data == "code_submit":
                await self.handle_code_submit(query, user_id)
            elif data == "resend_code":
                await self.resend_telegram_code(query, user_id)
            
            # Normal kullanıcı işlemleri
            elif self.is_user_approved(user_id) or user_id == self.ADMIN_ID:
                if data == "setup":
                    await self.setup_start(query, user_id)
                elif data == "settings":
                    await self.show_settings(query, user_id)
                elif data == "help":
                    await self.show_help(query)
                elif data == "back":
                    await self.start_command_callback(query)
                elif data == "add_api":
                    await self.request_api(query, user_id)
                elif data == "add_groups":
                    await self.request_groups(query, user_id)
                elif data == "add_message":
                    await self.request_message(query, user_id)
                elif data == "add_interval":
                    await self.request_interval(query, user_id)
                elif data == "start_auto_send":
                    await self.start_auto_send(query, user_id)
                elif data == "confirm_start_auto":
                    await self.confirm_start_auto_send(query, user_id)
                elif data == "stop_auto_send":
                    await self.stop_auto_send(query, user_id)
                elif data == "send_status":
                    await self.show_send_status(query, user_id)
            else:
                await query.edit_message_text(
                    "🔒 Bu işlemi yapmak için yönetici onayı gerekiyor.\n\n⏳ Lütfen onay bekleyin."
                )
        except Exception as e:
            print(f"❌ Button callback hatası - {user_id}: {str(e)}")

    # ===================== ADMIN OPERATIONS =======================
    async def handle_user_approval(self, query, data):
        """Kullanıcı onayını işle"""
        if query.from_user.id != self.ADMIN_ID:
            await query.answer("❌ Bu işlemi sadece yönetici yapabilir!", show_alert=True)
            return
        
        user_id = int(data.split("_")[1])
        self.approve_user(user_id)
        
        await self.safe_edit_message(
            query,
            f"✅ Kullanıcı onaylandı!\n\n👤 ID: {user_id}\n⏰ Tarih: {self.format_turkey_time()}"
        )
        
        try:
            await self.app.bot.send_message(
                chat_id=user_id,
                text="🎉 Hesabınız onaylandı!\n\n✅ Artık bot'u kullanabilirsiniz.\n📱 /start yazarak başlayabilirsiniz."
            )
        except:
            pass
    
    async def handle_user_rejection(self, query, data):
        """Kullanıcı reddini işle"""
        if query.from_user.id != self.ADMIN_ID:
            await query.answer("❌ Bu işlemi sadece yönetici yapabilir!", show_alert=True)
            return
        
        user_id = int(data.split("_")[1])
        self.reject_user(user_id)
        
        await self.safe_edit_message(
            query,
            f"❌ Kullanıcı reddedildi!\n\n👤 ID: {user_id}\n⏰ Tarih: {self.format_turkey_time()}"
        )

    async def handle_user_ban(self, query, data):
        """Kullanıcı banını işle"""
        if query.from_user.id != self.ADMIN_ID:
            await query.answer("❌ Bu işlemi sadece yönetici yapabilir!", show_alert=True)
            return
        
        user_id = int(data.split("_")[1])
        
        if user_id == self.ADMIN_ID:
            await query.answer("❌ Yöneticiyi banlayamazsınız!", show_alert=True)
            return
        
        self.ban_user(user_id)
        
        await self.safe_edit_message(
            query,
            f"🚫 Kullanıcı banlandı!\n\n👤 ID: {user_id}\n⏰ Tarih: {self.format_turkey_time()}"
        )

    async def handle_user_unban(self, query, data):
        """Kullanıcı ban kaldırma"""
        if query.from_user.id != self.ADMIN_ID:
            await query.answer("❌ Bu işlemi sadece yönetici yapabilir!", show_alert=True)
            return
        
        user_id = int(data.split("_")[1])
        self.unban_user(user_id)
        
        await query.edit_message_text(
            f"✅ Kullanıcının banı kaldırıldı!\n\n👤 ID: {user_id}\n⏰ Tarih: {self.format_turkey_time()}"
        )

    async def show_user_info(self, query, data):
        """Kullanıcı bilgilerini göster"""
        if query.from_user.id != self.ADMIN_ID:
            await query.answer("❌ Bu işlemi sadece yönetici yapabilir!", show_alert=True)
            return
        
        user_id = int(data.split("_")[2])
        
        try:
            user_info = await self.app.bot.get_chat(user_id)
            user_data = self.load_user_data(user_id)
            
            message = f"""👤 KULLANICI BİLGİLERİ

🆔 ID: {user_id}
👤 Ad: {user_info.first_name or 'Bilinmiyor'}
👤 Soyad: {user_info.last_name or '-'}
🔗 Kullanıcı adı: @{user_info.username or 'Yok'}
✅ Onaylı: {'Evet' if self.is_user_approved(user_id) else 'Hayır'}

📊 Bot Verileri:
📱 API: {'✅' if user_data.get('api_id') else '❌'}
📞 Telefon: {'✅' if user_data.get('phone_number') else '❌'}
📋 Grup: {'✅' if user_data.get('groups') else '❌'}
📝 Mesaj: {'✅' if user_data.get('message') else '❌'}"""
            
            keyboard = [
                [InlineKeyboardButton("✅ Onayla", callback_data=f"approve_{user_id}"),
                 InlineKeyboardButton("❌ Reddet", callback_data=f"reject_{user_id}")],
                [InlineKeyboardButton("🔙 Geri", callback_data="pending_approvals")]
            ]
            
            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            await query.edit_message_text(f"❌ Kullanıcı bilgisi alınamadı: {str(e)}")

    async def show_pending_approvals(self, query, user_id):
        """Bekleyen onayları göster"""
        if user_id != self.ADMIN_ID:
            await query.answer("❌ Bu işlemi sadece yönetici yapabilir!", show_alert=True)
            return
        
        # MongoDB'den bekleyen kullanıcıları bul
        pending_users = []
        if mongo_client is not None:
            try:
                collection = mongo_db.users
                all_users = collection.find({})
                for user_doc in all_users:
                    uid = user_doc.get('user_id')
                    if uid and not self.is_user_approved(uid) and uid != self.ADMIN_ID:
                        pending_users.append(uid)
            except Exception as e:
                print(f"❌ MongoDB sorgu hatası: {e}")
        
        if not pending_users:
            message = "📋 Bekleyen onay talebi yok."
            keyboard = [[InlineKeyboardButton("🔙 Ana Menü", callback_data="back")]]
        else:
            message = f"⏳ BEKLEYEN ONAYLAR ({len(pending_users)})\n\n"
            keyboard = []
            
            for uid in pending_users[:10]:
                try:
                    user_info = await self.app.bot.get_chat(uid)
                    name = user_info.first_name or f"ID: {uid}"
                    message += f"👤 {name} (ID: {uid})\n"
                    keyboard.append([InlineKeyboardButton(f"👤 {name}", callback_data=f"user_info_{uid}")])
                except:
                    message += f"👤 ID: {uid}\n"
                    keyboard.append([InlineKeyboardButton(f"👤 ID: {uid}", callback_data=f"user_info_{uid}")])
            
            if len(pending_users) > 10:
                message += f"\n... ve {len(pending_users)-10} kullanıcı daha"
            
            keyboard.append([InlineKeyboardButton("🔙 Ana Menü", callback_data="back")])
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def show_approved_users(self, query, user_id):
        """Onaylı kullanıcıları göster"""
        if user_id != self.ADMIN_ID:
            await query.answer("❌ Bu işlemi sadece yönetici yapabilir!", show_alert=True)
            return
        
        approved_list = list(self.approved_users)
        
        if len(approved_list) <= 1:
            message = "📋 Henüz onaylı kullanıcı yok."
            keyboard = [[InlineKeyboardButton("🔙 Ana Menü", callback_data="back")]]
        else:
            message = f"✅ ONAYLI KULLANICILAR ({len(approved_list)})\n\n"
            keyboard = []
            
            for uid in approved_list[:10]:
                try:
                    if uid == self.ADMIN_ID:
                        message += f"🔑 Yönetici (ID: {uid})\n\n"
                    else:
                        user_info = await self.app.bot.get_chat(uid)
                        name = user_info.first_name or f"ID: {uid}"
                        message += f"👤 {name} (ID: {uid})\n\n"
                        keyboard.append([InlineKeyboardButton(f"🚫 {name} - Ban", callback_data=f"ban_{uid}")])
                except:
                    message += f"👤 ID: {uid}\n\n"
                    keyboard.append([InlineKeyboardButton(f"🚫 ID: {uid} - Ban", callback_data=f"ban_{uid}")])
            
            keyboard.append([InlineKeyboardButton("🔙 Ana Menü", callback_data="back")])
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def show_banned_users(self, query, user_id):
        """Yasaklı kullanıcıları göster"""
        if user_id != self.ADMIN_ID:
            await query.answer("❌ Bu işlemi sadece yönetici yapabilir!", show_alert=True)
            return
        
        # MongoDB'den yasaklı kullanıcıları bul
        banned_users = []
        if mongo_client is not None:
            try:
                collection = mongo_db.users
                banned_docs = collection.find({'approval_status': 'banned'})
                for user_doc in banned_docs:
                    uid = user_doc.get('user_id')
                    if uid:
                        banned_users.append(uid)
            except Exception as e:
                print(f"❌ MongoDB sorgu hatası: {e}")
        
        if not banned_users:
            message = "🚫 Yasaklı kullanıcı yok."
            keyboard = [[InlineKeyboardButton("🔙 Ana Menü", callback_data="back")]]
        else:
            message = f"🚫 YASAKLI KULLANICILAR ({len(banned_users)})\n\n"
            keyboard = []
            
            for uid in banned_users[:10]:
                try:
                    user_info = await self.app.bot.get_chat(uid)
                    name = user_info.first_name or f"ID: {uid}"
                    message += f"👤 {name} (ID: {uid})\n\n"
                    keyboard.append([InlineKeyboardButton(f"✅ {name} - Affet", callback_data=f"unban_{uid}")])
                except:
                    message += f"👤 ID: {uid}\n\n"
                    keyboard.append([InlineKeyboardButton(f"✅ ID: {uid} - Affet", callback_data=f"unban_{uid}")])
            
            keyboard.append([InlineKeyboardButton("🔙 Ana Menü", callback_data="back")])
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def check_user_approval_status(self, query, user_id):
        """Kullanıcının onay durumunu kontrol et"""
        if self.is_user_approved(user_id):
            await query.edit_message_text(
                "🎉 Hesabınız onaylandı!\n\n✅ Artık bot'u kullanabilirsiniz.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Bot'u Kullan", callback_data="back")]])
            )
        else:
            await query.edit_message_text(
                f"⏳ Hesabınız henüz onaylanmamış.\n\n📝 Kullanıcı ID'niz: {user_id}\n⏰ Lütfen onay bekleyin.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Tekrar Kontrol Et", callback_data="check_approval")],
                    [InlineKeyboardButton("📞 Yöneticiye Mesaj", callback_data="contact_admin")]
                ])
            )

    async def show_admin_contact(self, query, user_id):
        """Yönetici iletişim bilgilerini göster"""
        message = f"""📞 YÖNETİCİ İLETİŞİM

🔑 Yönetici ID: {self.ADMIN_ID}

📝 Onay talebiniz için bu bilgileri yöneticiye gönderin:

👤 Adınız: {query.from_user.first_name}
🆔 ID'niz: {user_id}
📅 Tarih: {self.format_turkey_time()}"""
        
        keyboard = [
            [InlineKeyboardButton("🔄 Durumu Kontrol Et", callback_data="check_approval")],
            [InlineKeyboardButton("🔙 Geri", callback_data="back")]
        ]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def setup_start(self, query, user_id):
        """Kurulum başlat"""
        message = """🔧 KURULUM

Adım adım kurulum yapacağız:

1️⃣ API Bilgileri - Telegram API
2️⃣ Telefon Numarası - Giriş hesabı
3️⃣ Hedef Gruplar - Mesaj gönderilecek gruplar
4️⃣ Mesaj / Resim - Gönderilecek içerik
5️⃣ Gönderim Aralığı - Kaç dakikada bir

İlk adım: API bilgileri"""
        
        keyboard = [
            [InlineKeyboardButton("📱 API Bilgilerini Gir", callback_data="add_api")],
            [InlineKeyboardButton("🔙 Ana Menü", callback_data="back")]
        ]
        
        await query.edit_message_text(
            message, 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def show_settings(self, query, user_id):
        """Ayarları göster"""
        try:
            user_data = self.load_user_data(user_id)
            
            if not user_data:
                message = "❌ Henüz kurulum yapmadınız."
                keyboard = [[InlineKeyboardButton("🔧 Kurulum Başlat", callback_data="setup")]]
            else:
                api_status = "✅" if user_data.get('api_id') else "❌"
                phone_status = "✅" if user_data.get('phone_number') else "❌"
                
                groups = user_data.get('groups', [])
                groups_count = len(groups) if isinstance(groups, list) else 0
                
                message_status = "✅" if user_data.get('message') else "❌"
                image_path = user_data.get('image_path')
                image_status = "✅" if image_path and os.path.exists(image_path) else "➖"
                
                # Telegram session durumu
                session_active = user_data.get('session_active', False)
                session_status = "✅" if session_active else "❌"
                session_text = "Aktif" if session_active else "Bağlanmamış"
                
                # Mesaj aralığı durumu
                interval_minutes = user_data.get('send_interval_minutes', 0)
                interval_status = "✅" if interval_minutes > 0 else "❌"
                interval_text = f"{interval_minutes} dakika" if interval_minutes > 0 else "Belirtilmemiş"
                
                # Son güncelleme tarihini güvenli şekilde al
                last_updated = user_data.get('last_updated', 'Bilinmiyor')
                if hasattr(last_updated, 'strftime'):
                    # DateTime object ise Türkiye saatine çevir
                    last_updated_str = self.format_turkey_time(last_updated)
                elif isinstance(last_updated, str):
                    last_updated_str = last_updated[:16] if len(last_updated) > 16 else last_updated
                else:
                    last_updated_str = 'Bilinmiyor'
                
                # Tüm ayarların tamamlanıp tamamlanmadığını kontrol et
                all_ready = (user_data.get('api_id') and 
                           user_data.get('phone_number') and 
                           user_data.get('session_active') and
                           user_data.get('groups') and 
                           user_data.get('message') and 
                           interval_minutes > 0)
                
                message = f"""📊 MEVCUT AYARLAR

🔐 API Bilgileri: {api_status}
📱 Telefon: {phone_status}
📞 Telegram Oturumu: {session_status} ({session_text})
📋 Grup Sayısı: {groups_count}
📝 Mesaj: {message_status}
🖼️ Resim: {image_status}
⏰ Gönderim Aralığı: {interval_status} ({interval_text})

📅 Son Güncelleme: {last_updated_str}

{"🎉 Tüm ayarlar tamamlandı! Otomatik gönderimi başlatabilirsiniz." if all_ready else "⚠️ Bazı ayarlar eksik. Tüm ayarları tamamlayın."}"""
                
                keyboard = [
                    [InlineKeyboardButton("📱 API Güncelle", callback_data="add_api"), 
                     InlineKeyboardButton("📋 Grupları Güncelle", callback_data="add_groups")],
                    [InlineKeyboardButton("🖼️ Mesaj / Resim Güncelle", callback_data="add_message"),
                     InlineKeyboardButton("⏰ Aralığı Güncelle", callback_data="add_interval")]
                ]
                
                # Otomatik başlatma butonu - sadece tüm ayarlar tamamsa göster
                if all_ready:
                    keyboard.append([InlineKeyboardButton("� Otomatik Gönderimi Başlat", callback_data="start_auto_send")])
                
                keyboard.append([InlineKeyboardButton("🔙 Ana Menü", callback_data="back")])
            
            await query.edit_message_text(
                message, 
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            print(f"❌ show_settings hatası: {e}")
            await query.edit_message_text(
                "❌ Ayarlar yüklenirken hata oluştu.\n\n🔧 Kurulum yapmayı deneyin.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔧 Kurulum", callback_data="setup")]])
            )

    async def show_help(self, query):
        """Yardım"""
        message = """❓ YARDIM

🤖 Nasıl Çalışır?
1️⃣ API bilgilerinizi girin
2️⃣ Telefon numaranızı ekleyin
3️⃣ Hedef grupları belirleyin
4️⃣ Mesaj içeriğini yazın
5️⃣ Gönderim aralığını ayarlayın

📱 API Bilgileri Nasıl Alınır?
1. https://my.telegram.org
2. Telefon ile giriş yapın
3. "API development tools"
4. Yeni app oluşturun
5. api_id ve api_hash kopyalayın

🛡️ Güvenlik:
✅ Verileriniz güvenle saklanır
✅ API bilgileri şifrelidir
✅ Açık kaynak kodlu"""
        
        keyboard = [[InlineKeyboardButton("🔙 Ana Menü", callback_data="back")]]
        
        await query.edit_message_text(
            message, 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def start_command_callback(self, query):
        """Ana menüye dön"""
        user_name = query.from_user.first_name
        user_id = query.from_user.id
        
        if user_id == self.ADMIN_ID:
            message = f"""🔑 YÖNETİCİ PANELİ

Merhaba {user_name}! 👋

✨ Yönetici Özellikleri:
📱 Telegram hesabınızı bağlayın
📋 Hedef grupları ekleyin  
📝 Mesaj içeriği belirleyin
🚀 Anlık mesaj gönderimi
⏰ Otomatik gönderim
👥 Kullanıcı onayları"""
            
            keyboard = [
                [InlineKeyboardButton("🔧 Kurulum Başlat", callback_data="setup")],
                [InlineKeyboardButton("📊 Ayarlarım", callback_data="settings")],
                [InlineKeyboardButton("👥 Bekleyen Onaylar", callback_data="pending_approvals")],
                [InlineKeyboardButton("📋 Onaylı Kullanıcılar", callback_data="approved_users")],
                [InlineKeyboardButton("🚫 Yasaklı Kullanıcılar", callback_data="banned_users")],
                [InlineKeyboardButton("❓ Yardım", callback_data="help")]
            ]
        
        elif self.is_user_approved(user_id):
            message = f"""🤖 Telegram Mesaj Gönderici Bot

Merhaba {user_name}! 👋

✅ Hesabınız onaylandı!"""
            
            keyboard = [
                [InlineKeyboardButton("🔧 Kurulum Başlat", callback_data="setup")],
                [InlineKeyboardButton("📊 Ayarlarım", callback_data="settings")],
                [InlineKeyboardButton("❓ Yardım", callback_data="help")]
            ]
        
        else:
            message = f"""🔒 ERİŞİM BEKLEMEDE

Merhaba {user_name}! 👋

⏳ Hesabınız henüz onaylanmamış."""
            
            keyboard = [
                [InlineKeyboardButton("🔄 Durumu Kontrol Et", callback_data="check_approval")],
                [InlineKeyboardButton("📞 Yöneticiye Mesaj", callback_data="contact_admin")]
            ]
        
        await self.safe_edit_message(query, message, InlineKeyboardMarkup(keyboard))

    # ===================== SETUP OPERATIONS =======================
    async def request_api(self, query, user_id):
        """API bilgilerini iste"""
        self.user_states[user_id] = "waiting_api_id"
        
        message = """📱 API BİLGİLERİ

Telegram API bilgilerinizi almanız gerekiyor:

1. https://my.telegram.org
2. Telefon numaranızla giriş yapın
3. "API development tools"
4. Yeni uygulama oluşturun
5. api_id ve api_hash alın

Şimdi api_id numaranızı gönderin:
(Örnek: 12345678)"""
        
        await query.edit_message_text(message)

    async def request_groups(self, query, user_id):
        """Grup listesi iste"""
        self.user_states[user_id] = "waiting_groups"
        
        message = """📋 HEDEF GRUPLAR

Mesaj gönderilecek grupları ekleyin:

📝 Desteklenen Formatlar:
• @grupadi (Örnek: @bitcoin_tr)
• -1001234567890 (Grup ID'si)
• -1002234567890 (Supergroup ID'si)
• https://t.me/grupadi
• t.me/grupadi

💡 İpucu: Grup ID'sini öğrenmek için:
1. Grupta @userinfobot kullanın
2. Telegram Desktop'ta grup bilgilerini kontrol edin

Her satıra bir grup yazın:"""
        
        await query.edit_message_text(message)

    async def request_message(self, query, user_id):
        """Mesaj ve isteğe bağlı resim içeriği iste"""
        self.user_states[user_id] = "waiting_message"
        
        message = """🖼️ MESAJ + RESİM

Gruplara gönderilecek içeriği gönderin:

✅ Sadece metin gönderebilirsiniz
✅ Bir fotoğraf seçip açıklama (caption) kısmına mesajınızı yazabilirsiniz
✅ Çok satırlı mesaj ve emoji kullanabilirsiniz

📌 Resim + mesaj birlikte göndermek için fotoğrafı ve açıklamasını TEK mesaj olarak gönderin."""
        
        await query.edit_message_text(message)

    async def request_interval(self, query, user_id):
        """Mesaj aralığını iste"""
        self.user_states[user_id] = "waiting_interval"
        
        message = """⏰ MESAJ ARALIĞI

Kaç dakika aralıklarla mesaj göndermek istiyorsunuz?

💡 Öneriler:
• 30 dakika = Normal hızlı
• 60 dakika = Orta hızlı (varsayılan)
• 120 dakika = Yavaş gönderim

Sadece sayı girin (dakika cinsinden):
Örnek: 60"""
        
        await query.edit_message_text(message)

    # ===================== AUTO SEND MANAGEMENT =======================
    async def start_auto_send(self, query, user_id):
        """Otomatik mesaj gönderimini başlat"""
        try:
            user_data = self.load_user_data(user_id)
            
            # Tüm ayarların tamamlanıp tamamlanmadığını kontrol et
            if not all([
                user_data.get('api_id'),
                user_data.get('api_hash'),
                user_data.get('phone_number'),
                user_data.get('groups'),
                user_data.get('message'),
                user_data.get('send_interval_minutes', 0) > 0
            ]):
                await query.edit_message_text(
                    "❌ Eksik ayarlar!\n\n🔧 Önce tüm ayarları tamamlayın:",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⚙️ Ayarlara Git", callback_data="settings")],
                        [InlineKeyboardButton("🔙 Geri", callback_data="back")]
                    ])
                )
                return
            
            # Kullanıcının zaten aktif görevi var mı kontrol et
            if user_id in self.active_tasks:
                await query.edit_message_text(
                    "⚠️ Zaten aktif bir gönderim işleminiz var!\n\n🛑 Önce mevcut işlemi durdurun.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🛑 Gönderimi Durdur", callback_data="stop_auto_send")],
                        [InlineKeyboardButton("📊 Durum", callback_data="send_status")],
                        [InlineKeyboardButton("🔙 Ayarlar", callback_data="settings")]
                    ])
                )
                return
            
            # Ayarları özetle ve onay iste
            interval = user_data.get('send_interval_minutes')
            groups_count = len(user_data.get('groups', []))
            message_preview = user_data.get('message', '')[:50] + "..." if len(user_data.get('message', '')) > 50 else user_data.get('message', '')
            
            confirmation_message = f"""🚀 OTOMATIK GÖNDERİM BAŞLATMA

📋 Ayar Özeti:
🎯 Grup Sayısı: {groups_count}
📝 Mesaj Önizleme: "{message_preview}"
⏰ Gönderim Aralığı: {interval} dakika

⚠️ DİKKAT:
• Gönderim başladıktan sonra sürekli çalışacak
• Sadece siz durdurabilirsiniz
• Telegram API limitlerini aşmamaya dikkat edin

Bu ayarlarla otomatik gönderimi başlatmak istediğinizden emin misiniz?"""
            
            keyboard = [
                [InlineKeyboardButton("✅ EVET, BAŞLAT", callback_data="confirm_start_auto")],
                [InlineKeyboardButton("❌ Hayır, İptal", callback_data="settings")],
                [InlineKeyboardButton("⚙️ Ayarları Düzenle", callback_data="settings")]
            ]
            
            await query.edit_message_text(
                confirmation_message,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
        except Exception as e:
            print(f"❌ start_auto_send hatası: {e}")
            await query.edit_message_text(
                "❌ Otomatik gönderim başlatılırken hata oluştu.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Ayarlar", callback_data="settings")]])
            )

    async def confirm_start_auto_send(self, query, user_id):
        """Otomatik gönderimi onaylanmış olarak başlat"""
        try:
            user_data = self.load_user_data(user_id)
            
            # Telegram session kontrolü
            session_client = await self.ensure_telegram_session(user_id)
            if not session_client:
                await query.edit_message_text(
                    "❌ Telegram oturumu bulunamadı!\n\n🔧 Lütfen önce API ayarlarını yapın.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Ayarlar", callback_data="settings")]])
                )
                return
            
            await query.edit_message_text(
                """🎉 OTOMATIK GÖNDERİM BAŞLATILDI!

🚀 İlk mesaj gönderiliyor...
🔄 Sonra belirlenen aralıklarla devam edecek

📊 Kontrol seçenekleri:""",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📊 Durum", callback_data="send_status")],
                    [InlineKeyboardButton("🛑 Durdur", callback_data="stop_auto_send")],
                    [InlineKeyboardButton("⚙️ Ayarlar", callback_data="settings")]
                ])
            )
            
            # Aktif görevler listesine ekle
            self.active_tasks[user_id] = {
                'status': 'running',
                'started_at': self.get_turkey_time(),  # Türkiye saati
                'messages_sent': 0,
                'last_send_time': None,
                'session_client': session_client
            }
            
            # İlk mesajı hemen gönder
            await self.send_message_to_groups(user_id)
            
            # Otomatik gönderim döngüsünü başlat
            asyncio.create_task(self.auto_send_loop(user_id))
            
        except Exception as e:
            print(f"❌ confirm_start_auto_send hatası: {e}")
            await query.edit_message_text(
                "❌ Gönderim başlatılırken hata oluştu.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Ayarlar", callback_data="settings")]])
            )

    async def stop_auto_send(self, query, user_id):
        """Otomatik gönderimi durdur"""
        try:
            if user_id in self.active_tasks:
                # Session client'ı temizle
                task_info = self.active_tasks[user_id]
                if 'session_client' in task_info:
                    try:
                        await task_info['session_client'].disconnect()
                    except:
                        pass
                
                del self.active_tasks[user_id]
                await query.edit_message_text(
                    "🛑 OTOMATIK GÖNDERİM DURDURULDU\n\n✅ Sistem başarıyla durduruldu.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🚀 Yeniden Başlat", callback_data="start_auto_send")],
                        [InlineKeyboardButton("⚙️ Ayarlar", callback_data="settings")]
                    ])
                )
            else:
                await query.edit_message_text(
                    "ℹ️ Zaten aktif bir gönderim yok.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Ayarlar", callback_data="settings")]])
                )
        except Exception as e:
            print(f"❌ stop_auto_send hatası: {e}")

    async def show_send_status(self, query, user_id):
        """Gönderim durumunu göster"""
        try:
            if user_id in self.active_tasks:
                task_info = self.active_tasks[user_id]
                started_at = task_info.get('started_at')
                messages_sent = task_info.get('messages_sent', 0)
                last_send_time = task_info.get('last_send_time')
                
                if started_at:
                    current_turkey_time = self.get_turkey_time()
                    runtime = current_turkey_time - started_at
                    runtime_str = str(runtime).split('.')[0]  # Saniye hassasiyetini kaldır
                else:
                    runtime_str = "Bilinmiyor"
                
                # Son mesaj gönderim zamanı
                last_send_str = "Henüz gönderilmedi"
                if last_send_time:
                    last_send_str = self.format_turkey_time(last_send_time, '%H:%M:%S')
                
                # Güncel zaman damgası ekle
                current_time = self.format_turkey_time(format_str='%H:%M:%S')
                
                status_message = f"""📊 GÖNDERİM DURUMU

🟢 Durum: Aktif
⏰ Başlama: {self.format_turkey_time(started_at) if started_at else 'Bilinmiyor'}
🕐 Çalışma Süresi: {runtime_str}
📨 Gönderilen Mesaj: {messages_sent}
🕒 Son Gönderim: {last_send_str}
🔄 Son Güncelleme: {current_time}

🔄 Sistem düzenli olarak mesajları gönderiyor."""
                
                keyboard = [
                    [InlineKeyboardButton("🛑 Durdur", callback_data="stop_auto_send")],
                    [InlineKeyboardButton("🔄 Yenile", callback_data="send_status")],
                    [InlineKeyboardButton("⚙️ Ayarlar", callback_data="settings")]
                ]
            else:
                status_message = "ℹ️ Şu anda aktif bir otomatik gönderim yok."
                keyboard = [
                    [InlineKeyboardButton("🚀 Başlat", callback_data="start_auto_send")],
                    [InlineKeyboardButton("⚙️ Ayarlar", callback_data="settings")]
                ]
            
            # Güvenli mesaj düzenleme kullan
            success = await self.safe_edit_message(query, status_message, InlineKeyboardMarkup(keyboard))
            
            if not success:
                # Eğer mesaj düzenlenemezse, kullanıcıya bilgi ver
                await query.answer("ℹ️ Durum bilgisi güncellendi", show_alert=False)
                
        except Exception as e:
            print(f"❌ show_send_status hatası: {e}")
            await query.answer("❌ Durum yüklenirken hata oluştu", show_alert=True)

    # ===================== TELEGRAM SESSION MANAGEMENT =======================
    def get_session_file_path(self, user_id):
        """Kullanıcının session dosya yolunu al"""
        return os.path.join(self.users_dir, f"session_{user_id}.session")
    
    async def start_telegram_session(self, update, user_id, user_data):
        """Telegram oturumunu başlat"""
        try:
            api_id = user_data.get('api_id')
            api_hash = user_data.get('api_hash')
            phone_number = user_data.get('phone_number')
            
            if not all([api_id, api_hash, phone_number]):
                await update.message.reply_text("❌ API bilgileri eksik!")
                return
            
            session_file = self.get_session_file_path(user_id)
            
            # Eğer session dosyası varsa, oturum zaten aktif
            if os.path.exists(session_file):
                try:
                    client = TelegramClient(session_file, api_id, api_hash)
                    await client.connect()
                    
                    if await client.is_user_authorized():
                        self.telethon_clients[user_id] = client
                        user_data['session_active'] = True
                        self.save_user_data(user_id, user_data)
                        del self.user_states[user_id]
                        
                        keyboard = [
                            [InlineKeyboardButton("📋 Grupları Ekle", callback_data="add_groups")],
                            [InlineKeyboardButton("📊 Ayarları Görüntüle", callback_data="settings")]
                        ]
                        
                        await update.message.reply_text(
                            "✅ Telegram oturumu zaten aktif!\n\n🎉 API ayarları tamamlandı!\n\nSıradaki adım: Grupları ekleyin",
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                        return
                    else:
                        await client.disconnect()
                except Exception as e:
                    print(f"⚠️ Mevcut session hatası: {e}")
            
            # Yeni oturum başlat
            client = TelegramClient(session_file, api_id, api_hash)
            await client.connect()
            
            # Telefon numarasına doğrulama kodu gönder
            try:
                result = await client.send_code_request(phone_number)
                self.telethon_clients[user_id] = client
                self.user_states[user_id] = "waiting_telegram_code"
                
                # Session bilgilerini geçici olarak sakla
                user_data['temp_phone_code_hash'] = result.phone_code_hash
                self.save_user_data(user_id, user_data)
                
                # Sayı butonları oluştur
                keyboard = []
                for i in range(0, 10, 3):  # 3'erli satırlar halinde
                    row = []
                    for j in range(3):
                        if i + j < 10:
                            num = i + j
                            if num == 0:  # 0'ı en son ekle
                                continue
                            row.append(InlineKeyboardButton(str(num), callback_data=f"code_digit_{num}"))
                    if row:
                        keyboard.append(row)
                
                # 0 ve silme butonları
                keyboard.append([
                    InlineKeyboardButton("0", callback_data="code_digit_0"),
                    InlineKeyboardButton("🔙 Sil", callback_data="code_delete")
                ])
                
                # Kontrol butonları
                keyboard.append([
                    InlineKeyboardButton("✅ Kodu Gönder", callback_data="code_submit"),
                    InlineKeyboardButton("🔄 Yeni Kod İste", callback_data="resend_code")
                ])
                
                # Kullanıcının girdiği kodu saklamak için
                user_data['temp_entered_code'] = ""
                self.save_user_data(user_id, user_data)
                
                await update.message.reply_text(
                    f"📱 TELEGRAM DOĞRULAMA\n\n📨 {phone_number} numarasına doğrulama kodu gönderildi.\n\n🔢 Aşağıdaki butonlarla 5 haneli kodu girin:\n\n⏰ DİKKAT: Kod 2-3 dakika içinde süresini dolduracak!\n\n� Girilen kod: {user_data.get('temp_entered_code', '')}",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                
            except Exception as e:
                await client.disconnect()
                if user_id in self.telethon_clients:
                    del self.telethon_clients[user_id]
                await update.message.reply_text(f"❌ Doğrulama kodu gönderilemedi: {str(e)}")
                
        except Exception as e:
            print(f"❌ start_telegram_session hatası: {e}")
            await update.message.reply_text("❌ Telegram oturumu başlatılamadı. Lütfen API bilgilerini kontrol edin.")
    
    async def handle_telegram_code(self, update, user_id, code):
        """Telegram doğrulama kodunu işle"""
        try:
            user_data = self.load_user_data(user_id)
            phone_number = user_data.get('phone_number')
            phone_code_hash = user_data.get('temp_phone_code_hash')
            
            if user_id not in self.telethon_clients:
                await update.message.reply_text("❌ Oturum bulunamadı. Lütfen tekrar başlayın.")
                return
            
            client = self.telethon_clients[user_id]
            
            try:
                # Doğrulama kodunu gönder
                await client.sign_in(phone_number, code, phone_code_hash=phone_code_hash)
                
                # Başarılı giriş
                user_data['session_active'] = True
                user_data['session_created'] = self.get_turkey_time().isoformat()
                # Geçici verileri temizle
                user_data.pop('temp_phone_code_hash', None)
                self.save_user_data(user_id, user_data)
                del self.user_states[user_id]
                
                keyboard = [
                    [InlineKeyboardButton("📋 Grupları Ekle", callback_data="add_groups")],
                    [InlineKeyboardButton("📊 Ayarları Görüntüle", callback_data="settings")]
                ]
                
                await update.message.reply_text(
                    "🎉 TELEGRAM OTURUMU BAŞARILI!\n\n✅ Hesabınız bağlandı ve kaydedildi\n🔄 Bundan sonra otomatik giriş yapacak\n\nSıradaki adım: Grupları ekleyin",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                
            except SessionPasswordNeededError:
                # 2FA aktif, şifre gerekli
                self.user_states[user_id] = "waiting_telegram_password"
                await update.message.reply_text("🔐 İKİ FAKTÖRLÜ DOĞRULAMA\n\n🔑 Hesabınızda 2FA aktif. Lütfen Telegram 2FA şifrenizi girin:\n\n💡 Bu, Telegram'da Settings > Privacy and Security > Two-Step Verification bölümünde ayarladığınız şifredir.")
                
            except Exception as e:
                error_msg = str(e)
                if "PHONE_CODE_INVALID" in error_msg:
                    await update.message.reply_text("❌ Geçersiz doğrulama kodu! Lütfen tekrar deneyin:")
                elif "PHONE_CODE_EXPIRED" in error_msg or "confirmation code has expired" in error_msg.lower():
                    # Sayı butonları oluştur
                    keyboard = []
                    for i in range(0, 10, 3):  # 3'erli satırlar halinde
                        row = []
                        for j in range(3):
                            if i + j < 10:
                                num = i + j
                                if num == 0:  # 0'ı en son ekle
                                    continue
                                row.append(InlineKeyboardButton(str(num), callback_data=f"code_digit_{num}"))
                        if row:
                            keyboard.append(row)
                    
                    # 0 ve silme butonları
                    keyboard.append([
                        InlineKeyboardButton("0", callback_data="code_digit_0"),
                        InlineKeyboardButton("🔙 Sil", callback_data="code_delete")
                    ])
                    
                    # Kontrol butonları
                    keyboard.append([
                        InlineKeyboardButton("✅ Kodu Gönder", callback_data="code_submit"),
                        InlineKeyboardButton("� Yeni Kod İste", callback_data="resend_code")
                    ])
                    
                    # Kullanıcının girdiği kodu temizle
                    user_data['temp_entered_code'] = ""
                    self.save_user_data(user_id, user_data)
                    
                    await update.message.reply_text(
                        f"❌ Doğrulama kodu süresi doldu!\n\n🔢 Aşağıdaki butonlarla yeni kodu girin:\n\n📱 Girilen kod: {user_data.get('temp_entered_code', '')}",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                elif "flood" in error_msg.lower():
                    await update.message.reply_text("⏳ Çok fazla deneme yaptınız. Lütfen birkaç dakika bekleyin.")
                else:
                    await update.message.reply_text(f"❌ Giriş hatası: {error_msg}\n\n🔄 Tekrar denemek için API ayarlarını yenileyin.")
                    if user_id in self.telethon_clients:
                        await self.telethon_clients[user_id].disconnect()
                        del self.telethon_clients[user_id]
                    del self.user_states[user_id]
                
        except Exception as e:
            print(f"❌ handle_telegram_code hatası: {e}")
            await update.message.reply_text("❌ Doğrulama kodu işlenemedi.")
    
    async def handle_telegram_password(self, update, user_id, password):
        """2FA şifresini işle"""
        try:
            if user_id not in self.telethon_clients:
                await update.message.reply_text("❌ Oturum bulunamadı. Lütfen tekrar başlayın.")
                return
            
            client = self.telethon_clients[user_id]
            user_data = self.load_user_data(user_id)
            
            try:
                # 2FA şifresi ile giriş
                await client.sign_in(password=password)
                
                # Başarılı giriş
                user_data['session_active'] = True
                user_data['session_created'] = self.get_turkey_time().isoformat()
                user_data.pop('temp_phone_code_hash', None)
                self.save_user_data(user_id, user_data)
                del self.user_states[user_id]
                
                keyboard = [
                    [InlineKeyboardButton("📋 Grupları Ekle", callback_data="add_groups")],
                    [InlineKeyboardButton("📊 Ayarları Görüntüle", callback_data="settings")]
                ]
                
                await update.message.reply_text(
                    "🎉 TELEGRAM OTURUMU BAŞARILI!\n\n✅ Hesabınız bağlandı ve kaydedildi\n🔄 Bundan sonra otomatik giriş yapacak\n\nSıradaki adım: Grupları ekleyin",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                
            except Exception as e:
                error_msg = str(e)
                if "PASSWORD_HASH_INVALID" in error_msg:
                    await update.message.reply_text("❌ Geçersiz 2FA şifresi! Lütfen tekrar deneyin:")
                elif "password" in error_msg.lower() and "invalid" in error_msg.lower():
                    await update.message.reply_text("❌ Yanlış 2FA şifresi! Lütfen doğru şifrenizi girin:")
                elif "flood" in error_msg.lower():
                    await update.message.reply_text("⏳ Çok fazla yanlış deneme. Lütfen birkaç dakika bekleyin.")
                else:
                    await update.message.reply_text(f"❌ 2FA Şifre hatası: {error_msg}")
                    # Çok fazla hata varsa session'ı temizle
                    if user_id in self.telethon_clients:
                        await self.telethon_clients[user_id].disconnect()
                        del self.telethon_clients[user_id]
                    del self.user_states[user_id]
                    
                    keyboard = [
                        [InlineKeyboardButton("🔄 Tekrar Başla", callback_data="add_api")],
                        [InlineKeyboardButton("📊 Ayarlar", callback_data="settings")]
                    ]
                    
                    await update.message.reply_text(
                        "🔄 Tekrar API ayarlarından başlayın:",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                    
        except Exception as e:
            print(f"❌ handle_telegram_password hatası: {e}")
            await update.message.reply_text("❌ Şifre işlenemedi.")
    
    async def ensure_telegram_session(self, user_id):
        """Kullanıcının Telegram oturumunun aktif olduğundan emin ol"""
        try:
            # Mevcut client'ı kontrol et
            if user_id in self.telethon_clients:
                client = self.telethon_clients[user_id]
                try:
                    # Bağlantı durumunu kontrol et
                    if client.is_connected() and await client.is_user_authorized():
                        return client
                    else:
                        # Bağlantı kopuksa, yeniden bağlan
                        if not client.is_connected():
                            await client.connect()
                        
                        if await client.is_user_authorized():
                            return client
                        else:
                            # Yetkilendirme geçersizse, client'ı temizle
                            await client.disconnect()
                            del self.telethon_clients[user_id]
                except Exception as e:
                    print(f"⚠️ Mevcut client kontrolü hatası {user_id}: {e}")
                    # Hatalı client'ı temizle
                    try:
                        await client.disconnect()
                    except:
                        pass
                    del self.telethon_clients[user_id]
            
            # Session dosyasından yeniden yükle
            user_data = self.load_user_data(user_id)
            api_id = user_data.get('api_id')
            api_hash = user_data.get('api_hash')
            session_file = self.get_session_file_path(user_id)
            
            if api_id and api_hash and os.path.exists(session_file):
                client = TelegramClient(session_file, api_id, api_hash)
                await client.connect()
                
                if await client.is_user_authorized():
                    self.telethon_clients[user_id] = client
                    print(f"✅ Kullanıcı {user_id}: Session yeniden yüklendi")
                    return client
                else:
                    await client.disconnect()
                    print(f"❌ Kullanıcı {user_id}: Session yetkilendirmesi geçersiz")
            
            return None
            
        except Exception as e:
            print(f"❌ ensure_telegram_session hatası: {e}")
            return None

    async def send_message_to_groups(self, user_id):
        """Kullanıcının tüm gruplarına mesaj gönder"""
        try:
            user_data = self.load_user_data(user_id)
            groups = user_data.get('groups', [])
            message_text = user_data.get('message', '')
            image_path = user_data.get('image_path')
            has_image = bool(image_path)
            if has_image and not os.path.isfile(image_path):
                logger.error("Kayıtlı fotoğraf bulunamadı; kullanıcı %s", user_id)
                await self.app.bot.send_message(
                    chat_id=user_id,
                    text="❌ Kayıtlı fotoğraf bulunamadı. Mesaj Ekle bölümünden fotoğrafı açıklamasıyla tekrar yükleyin."
                )
                return 0
            
            if not groups or not message_text:
                print(f"❌ Kullanıcı {user_id}: Grup veya mesaj eksik")
                return 0
            
            # Session client'ı al - her seferinde yeniden kontrol et
            client = await self.ensure_telegram_session(user_id)
            
            if not client:
                print(f"❌ Kullanıcı {user_id}: Telegram session bulunamadı")
                return 0
            
            # Active task'ta client'ı güncelle
            if user_id in self.active_tasks:
                self.active_tasks[user_id]['session_client'] = client
            
            sent_count = 0
            failed_groups = []
            
            for group in groups:
                try:
                    # Bağlantı durumunu kontrol et
                    if not client.is_connected():
                        print(f"⚠️ Bağlantı kopuk, yeniden bağlanıyor...")
                        await client.connect()
                    
                    # Grup formatını düzenle
                    group = group.strip()
                    if group.startswith('https://t.me/'):
                        group = group.replace('https://t.me/', '@')
                    elif group.startswith('t.me/'):
                        group = group.replace('t.me/', '@')
                    
                    # Grup ID'si kontrolü - negatif sayılar için int'e çevir
                    if group.startswith('-') and group[1:].isdigit():
                        group = int(group)  # -1002681523669 -> integer olarak kullan
                    
                    # Resim varsa mesajı fotoğraf açıklaması olarak, yoksa normal metin olarak gönder
                    if has_image:
                        await client.send_file(group, image_path, caption=message_text, parse_mode=None)
                        print(f"✅ Kullanıcı {user_id}: {group} grubuna resim + mesaj gönderildi")
                    else:
                        await client.send_message(group, message_text)
                        print(f"✅ Kullanıcı {user_id}: {group} grubuna mesaj gönderildi")
                    sent_count += 1
                    
                    # Telegram API rate limit için kısa bekleme
                    await asyncio.sleep(1)
                    
                except Exception as e:
                    error_msg = str(e)
                    failed_groups.append(group)
                    print(f"❌ Kullanıcı {user_id}: {group} grubuna mesaj gönderilemedi - {error_msg}")
                    
                    # Eğer bağlantı sorunuysa, bir sonraki gruba geçmeden önce yeniden bağlan
                    if "disconnected" in error_msg.lower() or "connection" in error_msg.lower():
                        try:
                            print(f"🔄 Bağlantı sorunu tespit edildi, yeniden bağlanıyor...")
                            await client.disconnect()
                            await asyncio.sleep(2)
                            await client.connect()
                            if not await client.is_user_authorized():
                                print(f"❌ Yeniden bağlantı başarısız, session geçersiz")
                                break
                        except Exception as reconnect_error:
                            print(f"❌ Yeniden bağlantı hatası: {reconnect_error}")
                            break
            
            # Aktif task bilgilerini güncelle
            if user_id in self.active_tasks:
                self.active_tasks[user_id]['messages_sent'] += sent_count
                self.active_tasks[user_id]['last_send_time'] = self.get_turkey_time()
            
            # Başarı raporu
            if sent_count > 0:
                print(f"📊 Kullanıcı {user_id}: {sent_count}/{len(groups)} gruba mesaj gönderildi")
            
            # Başarısız gruplar varsa admin'e bildir
            if failed_groups and (has_image or user_id == self.ADMIN_ID):
                try:
                    await self.app.bot.send_message(
                        chat_id=user_id,
                        text=f"⚠️ Bazı gruplara mesaj gönderilemedi:\n\n" + "\n".join(map(str, failed_groups[:5]))
                    )
                except:
                    pass
            
            return sent_count
            
        except Exception as e:
            print(f"❌ send_message_to_groups hatası - Kullanıcı {user_id}: {e}")
            return 0

    async def auto_send_loop(self, user_id):
        """Otomatik mesaj gönderim döngüsü"""
        try:
            user_data = self.load_user_data(user_id)
            interval_minutes = user_data.get('send_interval_minutes', 60)
            interval_seconds = interval_minutes * 60
            
            print(f"🔄 Kullanıcı {user_id}: Otomatik gönderim döngüsü başlatıldı ({interval_minutes} dakika aralıklarla)")
            
            while user_id in self.active_tasks:
                try:
                    # Belirlenen aralığı bekle
                    await asyncio.sleep(interval_seconds)
                    
                    # Hala aktif mi kontrol et
                    if user_id not in self.active_tasks:
                        break
                    
                    # Mesaj gönder
                    sent_count = await self.send_message_to_groups(user_id)
                    
                    if sent_count > 0:
                        print(f"🚀 Kullanıcı {user_id}: Otomatik gönderim tamamlandı - {sent_count} grup")
                    
                except asyncio.CancelledError:
                    print(f"🛑 Kullanıcı {user_id}: Otomatik gönderim döngüsü iptal edildi")
                    break
                except Exception as e:
                    print(f"❌ Kullanıcı {user_id}: Otomatik gönderim döngüsü hatası - {e}")
                    await asyncio.sleep(60)  # Hata durumunda 1 dakika bekle
            
            print(f"🏁 Kullanıcı {user_id}: Otomatik gönderim döngüsü sonlandırıldı")
            
        except Exception as e:
            print(f"❌ auto_send_loop hatası - Kullanıcı {user_id}: {e}")
            # Hata durumunda task'ı temizle
            if user_id in self.active_tasks:
                del self.active_tasks[user_id]

    async def resend_telegram_code(self, query, user_id):
        """Telegram doğrulama kodunu tekrar gönder"""
        try:
            user_data = self.load_user_data(user_id)
            phone_number = user_data.get('phone_number')
            
            if user_id in self.telethon_clients:
                # Mevcut client'ı kapat
                await self.telethon_clients[user_id].disconnect()
                del self.telethon_clients[user_id]
            
            # Yeni session başlat
            await self.start_telegram_session_from_query(query, user_id, user_data)
            
        except Exception as e:
            print(f"❌ resend_telegram_code hatası: {e}")
            await query.edit_message_text(
                "❌ Kod tekrar gönderilemedi. Lütfen API ayarlarını yenileyin.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 API Ayarları", callback_data="add_api")]])
            )

    async def start_telegram_session_from_query(self, query, user_id, user_data):
        """Query'den Telegram oturumu başlat"""
        try:
            api_id = user_data.get('api_id')
            api_hash = user_data.get('api_hash')
            phone_number = user_data.get('phone_number')
            
            if not all([api_id, api_hash, phone_number]):
                await query.edit_message_text("❌ API bilgileri eksik!")
                return
            
            session_file = self.get_session_file_path(user_id)
            
            # Yeni oturum başlat
            client = TelegramClient(session_file, api_id, api_hash)
            await client.connect()
            
            # Telefon numarasına doğrulama kodu gönder
            try:
                result = await client.send_code_request(phone_number)
                self.telethon_clients[user_id] = client
                self.user_states[user_id] = "waiting_telegram_code"
                
                # Session bilgilerini geçici olarak sakla
                user_data['temp_phone_code_hash'] = result.phone_code_hash
                user_data['temp_entered_code'] = ""
                self.save_user_data(user_id, user_data)
                
                # Sayı butonları oluştur
                keyboard = []
                for i in range(0, 10, 3):  # 3'erli satırlar halinde
                    row = []
                    for j in range(3):
                        if i + j < 10:
                            num = i + j
                            if num == 0:  # 0'ı en son ekle
                                continue
                            row.append(InlineKeyboardButton(str(num), callback_data=f"code_digit_{num}"))
                    if row:
                        keyboard.append(row)
                
                # 0 ve silme butonları
                keyboard.append([
                    InlineKeyboardButton("0", callback_data="code_digit_0"),
                    InlineKeyboardButton("🔙 Sil", callback_data="code_delete")
                ])
                
                # Kontrol butonları
                keyboard.append([
                    InlineKeyboardButton("✅ Kodu Gönder", callback_data="code_submit"),
                    InlineKeyboardButton("🔄 Yeni Kod İste", callback_data="resend_code")
                ])
                
                await query.edit_message_text(
                    f"📱 YENİ DOĞRULAMA KODU\n\n📨 {phone_number} numarasına yeni kod gönderildi.\n\n🔢 Aşağıdaki butonlarla 5 haneli kodu girin:\n\n⏰ DİKKAT: Kod 2-3 dakika içinde süresini dolduracak!\n\n� Girilen kod: {user_data.get('temp_entered_code', '')}",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                
            except Exception as e:
                await client.disconnect()
                if user_id in self.telethon_clients:
                    del self.telethon_clients[user_id]
                await query.edit_message_text(f"❌ Doğrulama kodu gönderilemedi: {str(e)}")
                
        except Exception as e:
            print(f"❌ start_telegram_session_from_query hatası: {e}")
            await query.edit_message_text("❌ Telegram oturumu başlatılamadı.")

    async def handle_code_digit(self, query, user_id, data):
        """Kod rakamı butonunu işle"""
        try:
            digit = data.split("_")[2]  # code_digit_1 -> 1
            user_data = self.load_user_data(user_id)
            current_code = user_data.get('temp_entered_code', '')
            
            # Maksimum 5 rakam
            if len(current_code) < 5:
                current_code += digit
                user_data['temp_entered_code'] = current_code
                self.save_user_data(user_id, user_data)
                
                # Mesajı güncelle
                await self.update_code_input_message(query, user_id, user_data)
                
        except Exception as e:
            print(f"❌ handle_code_digit hatası: {e}")
    
    async def handle_code_delete(self, query, user_id):
        """Son rakamı sil"""
        try:
            user_data = self.load_user_data(user_id)
            current_code = user_data.get('temp_entered_code', '')
            
            if current_code:
                current_code = current_code[:-1]  # Son karakteri sil
                user_data['temp_entered_code'] = current_code
                self.save_user_data(user_id, user_data)
                
                # Mesajı güncelle
                await self.update_code_input_message(query, user_id, user_data)
                
        except Exception as e:
            print(f"❌ handle_code_delete hatası: {e}")
    
    async def handle_code_submit(self, query, user_id):
        """Girilen kodu gönder"""
        try:
            user_data = self.load_user_data(user_id)
            entered_code = user_data.get('temp_entered_code', '')
            
            if len(entered_code) != 5:
                await query.answer("❌ 5 haneli kod gerekli!", show_alert=True)
                return
            
            # Kodu handle_telegram_code fonksiyonuna gönder
            phone_number = user_data.get('phone_number')
            phone_code_hash = user_data.get('temp_phone_code_hash')
            
            if user_id not in self.telethon_clients:
                await query.edit_message_text("❌ Oturum bulunamadı. Lütfen tekrar başlayın.")
                return
            
            client = self.telethon_clients[user_id]
            
            try:
                # Doğrulama kodunu gönder
                await client.sign_in(phone_number, entered_code, phone_code_hash=phone_code_hash)
                
                # Başarılı giriş
                user_data['session_active'] = True
                user_data['session_created'] = self.get_turkey_time().isoformat()
                # Geçici verileri temizle
                user_data.pop('temp_phone_code_hash', None)
                user_data.pop('temp_entered_code', None)
                self.save_user_data(user_id, user_data)
                del self.user_states[user_id]
                
                keyboard = [
                    [InlineKeyboardButton("📋 Grupları Ekle", callback_data="add_groups")],
                    [InlineKeyboardButton("📊 Ayarları Görüntüle", callback_data="settings")]
                ]
                
                await query.edit_message_text(
                    "🎉 TELEGRAM OTURUMU BAŞARILI!\n\n✅ Hesabınız bağlandı ve kaydedildi\n🔄 Bundan sonra otomatik giriş yapacak\n\nSıradaki adım: Grupları ekleyin",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                
            except SessionPasswordNeededError:
                # 2FA aktif, şifre gerekli
                self.user_states[user_id] = "waiting_telegram_password"
                user_data.pop('temp_entered_code', None)  # Kodu temizle
                self.save_user_data(user_id, user_data)
                
                await query.edit_message_text("🔐 İKİ FAKTÖRLÜ DOĞRULAMA\n\n🔑 Hesabınızda 2FA aktif. Lütfen Telegram 2FA şifrenizi yazın:\n\n💡 Bu, Telegram'da Settings > Privacy and Security > Two-Step Verification bölümünde ayarladığınız şifredir.")
                
            except Exception as e:
                error_msg = str(e)
                if "PHONE_CODE_INVALID" in error_msg:
                    await query.answer("❌ Geçersiz doğrulama kodu!", show_alert=True)
                    # Kodu temizle
                    user_data['temp_entered_code'] = ""
                    self.save_user_data(user_id, user_data)
                    await self.update_code_input_message(query, user_id, user_data)
                elif "PHONE_CODE_EXPIRED" in error_msg or "confirmation code has expired" in error_msg.lower():
                    # Kod süresi doldu, yeni kod isteme butonları göster
                    keyboard = []
                    for i in range(0, 10, 3):
                        row = []
                        for j in range(3):
                            if i + j < 10:
                                num = i + j
                                if num == 0:
                                    continue
                                row.append(InlineKeyboardButton(str(num), callback_data=f"code_digit_{num}"))
                        if row:
                            keyboard.append(row)
                    
                    keyboard.append([
                        InlineKeyboardButton("0", callback_data="code_digit_0"),
                        InlineKeyboardButton("🔙 Sil", callback_data="code_delete")
                    ])
                    
                    keyboard.append([
                        InlineKeyboardButton("✅ Kodu Gönder", callback_data="code_submit"),
                        InlineKeyboardButton("🔄 Yeni Kod İste", callback_data="resend_code")
                    ])
                    
                    user_data['temp_entered_code'] = ""
                    self.save_user_data(user_id, user_data)
                    
                    await query.edit_message_text(
                        f"❌ Doğrulama kodu süresi doldu!\n\n🔢 Aşağıdaki butonlarla yeni kodu girin:\n\n📱 Girilen kod: {user_data.get('temp_entered_code', '')}",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                elif "flood" in error_msg.lower():
                    await query.answer("⏳ Çok fazla deneme. Birkaç dakika bekleyin.", show_alert=True)
                else:
                    await query.edit_message_text(f"❌ Giriş hatası: {error_msg}\n\n🔄 Tekrar denemek için API ayarlarını yenileyin.")
                    if user_id in self.telethon_clients:
                        await self.telethon_clients[user_id].disconnect()
                        del self.telethon_clients[user_id]
                    del self.user_states[user_id]
                
        except Exception as e:
            print(f"❌ handle_code_submit hatası: {e}")
            await query.answer("❌ Kod gönderilemedi!", show_alert=True)
    
    async def update_code_input_message(self, query, user_id, user_data):
        """Kod giriş mesajını güncelle"""
        try:
            current_code = user_data.get('temp_entered_code', '')
            phone_number = user_data.get('phone_number', '')
            
            # Sayı butonları oluştur
            keyboard = []
            for i in range(0, 10, 3):  # 3'erli satırlar halinde
                row = []
                for j in range(3):
                    if i + j < 10:
                        num = i + j
                        if num == 0:  # 0'ı en son ekle
                            continue
                        row.append(InlineKeyboardButton(str(num), callback_data=f"code_digit_{num}"))
                if row:
                    keyboard.append(row)
            
            # 0 ve silme butonları
            keyboard.append([
                InlineKeyboardButton("0", callback_data="code_digit_0"),
                InlineKeyboardButton("🔙 Sil", callback_data="code_delete")
            ])
            
            # Kontrol butonları
            keyboard.append([
                InlineKeyboardButton("✅ Kodu Gönder", callback_data="code_submit"),
                InlineKeyboardButton("🔄 Yeni Kod İste", callback_data="resend_code")
            ])
            
            # Kod görselleştirme
            code_display = ""
            for i in range(5):
                if i < len(current_code):
                    code_display += current_code[i] + " "
                else:
                    code_display += "_ "
            
            await query.edit_message_text(
                f"📱 TELEGRAM DOĞRULAMA\n\n📨 {phone_number} numarasına doğrulama kodu gönderildi.\n\n🔢 Aşağıdaki butonlarla 5 haneli kodu girin:\n\n⏰ DİKKAT: Kod 2-3 dakika içinde süresini dolduracak!\n\n📱 Girilen kod: {code_display.strip()}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
        except Exception as e:
            print(f"❌ update_code_input_message hatası: {e}")

    # ===================== MESSAGE HANDLING =======================
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Kullanıcı mesajlarını işle"""
        message = update.effective_message
        if message is None or update.effective_user is None:
            return
        user_id = update.effective_user.id
        message_text = message.text or message.caption or ""
        
        if not self.is_user_approved(user_id) and user_id != self.ADMIN_ID:
            await update.message.reply_text(
                "🔒 Bu botu kullanmak için yönetici onayı gerekiyor.\n\n⏳ Lütfen onay bekleyin."
            )
            return
        
        if user_id not in self.user_states:
            await update.message.reply_text("❓ /start ile başlayabilirsiniz.")
            return
        
        user_state = self.user_states[user_id]
        if message.photo and user_state != "waiting_message":
            await message.reply_text("❌ Fotoğraf göndermek için önce Mesaj Ekle seçeneğini açın.")
            return
        user_data = self.load_user_data(user_id)
        
        if user_state == "waiting_api_id":
            try:
                api_id = int(message_text)
                user_data['api_id'] = api_id
                self.save_user_data(user_id, user_data)
                self.user_states[user_id] = "waiting_api_hash"
                await update.message.reply_text("✅ API ID kaydedildi!\n\n🔑 Şimdi api_hash değerinizi gönderin:")
            except ValueError:
                await update.message.reply_text("❌ Geçersiz API ID! Sadece sayı girin.")
        
        elif user_state == "waiting_api_hash":
            user_data['api_hash'] = message_text
            self.save_user_data(user_id, user_data)
            self.user_states[user_id] = "waiting_phone"
            await update.message.reply_text("✅ API Hash kaydedildi!\n\n📱 Telefon numaranızı gönderin:\n(Örnek: +905xxxxxxxxx)")
        
        elif user_state == "waiting_phone":
            if message_text.startswith('+') and len(message_text) >= 10:
                user_data['phone_number'] = message_text
                self.save_user_data(user_id, user_data)
                
                # Telegram oturumunu başlat
                await self.start_telegram_session(update, user_id, user_data)
            else:
                await update.message.reply_text("❌ Geçersiz telefon numarası! + ile başlayın.")
        
        elif user_state == "waiting_telegram_code":
            # Artık kod girişi butonlarla yapılıyor
            await update.message.reply_text("🔢 Lütfen yukarıdaki butonlarla doğrulama kodunuzu girin.")
        
        elif user_state == "waiting_telegram_password":
            # 2FA şifresi varsa işle
            await self.handle_telegram_password(update, user_id, message_text)
        
        elif user_state == "waiting_groups":
            groups = [line.strip() for line in message_text.split('\n') if line.strip()]
            
            if groups:
                user_data['groups'] = groups
                self.save_user_data(user_id, user_data)
                del self.user_states[user_id]
                
                keyboard = [
                    [InlineKeyboardButton("📝 Mesaj Ekle", callback_data="add_message")],
                    [InlineKeyboardButton("📊 Ayarları Görüntüle", callback_data="settings")]
                ]
                
                await update.message.reply_text(
                    f"✅ {len(groups)} grup kaydedildi!\n\nSıradaki adım: Mesaj içeriğini belirleyin",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await update.message.reply_text("❌ Geçerli grup bulunamadı!")
        
        elif user_state == "waiting_message":
            if update.message.photo:
                if not message_text.strip():
                    await update.message.reply_text(
                        "❌ Fotoğrafın açıklama kısmına gönderilecek mesajı da yazın.\n\n"
                        "📌 Fotoğraf + mesajı tek seferde gönderin."
                    )
                    return

                # Her gönderi ayrı dosyada tutulur; devam eden gönderimler etkilenmez.
                photo_path = os.path.abspath(os.path.join(
                    self.users_dir, f"message_photo_{user_id}_{uuid.uuid4().hex}.jpg"
                ))
                try:
                    os.makedirs(self.users_dir, exist_ok=True)
                    photo = message.photo[-1]
                    telegram_file = await context.bot.get_file(photo.file_id)
                    await telegram_file.download_to_drive(custom_path=photo_path)
                except Exception:
                    logger.exception("Fotoğraf indirilemedi; kullanıcı %s", user_id)
                    try:
                        if os.path.exists(photo_path):
                            os.remove(photo_path)
                    except OSError:
                        logger.exception("Yarım fotoğraf dosyası silinemedi")
                    await message.reply_text(
                        "❌ Fotoğraf kaydedilemedi. Lütfen fotoğrafı açıklamasıyla tekrar gönderin."
                    )
                    return

                user_data['image_path'] = photo_path
                user_data['message'] = message_text
                saved_text = "✅ Resim + mesaj kaydedildi!"
            else:
                if not message_text.strip():
                    await update.message.reply_text("❌ Mesaj boş olamaz.")
                    return
                user_data['message'] = message_text
                user_data.pop('image_path', None)
                saved_text = "✅ Mesaj kaydedildi!"

            self.save_user_data(user_id, user_data)
            del self.user_states[user_id]
            
            keyboard = [
                [InlineKeyboardButton("⏰ Gönderim Aralığını Belirle", callback_data="add_interval")],
                [InlineKeyboardButton("📊 Ayarları Kontrol Et", callback_data="settings")]
            ]
            
            await update.message.reply_text(
                saved_text + "\n\n⏰ Son adım: Gönderim aralığını belirleyin",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        elif user_state == "waiting_interval":
            try:
                interval_minutes = int(message_text)
                if interval_minutes < 5:
                    await update.message.reply_text("❌ En az 5 dakika olmalı!")
                    return
                elif interval_minutes > 1440:
                    await update.message.reply_text("❌ En fazla 1440 dakika (24 saat) olabilir!")
                    return
                
                user_data['send_interval_minutes'] = interval_minutes
                self.save_user_data(user_id, user_data)
                del self.user_states[user_id]
                
                keyboard = [
                    [InlineKeyboardButton("📊 Ayarları Kontrol Et", callback_data="settings")]
                ]
                
                await update.message.reply_text(
                    f"✅ Gönderim aralığı kaydedildi!\n\n⏰ Aralık: {interval_minutes} dakika\n\n🎉 Kurulum tamamlandı!",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except ValueError:
                await update.message.reply_text("❌ Geçersiz sayı! Sadece sayı girin.")

# ===================== MAIN EXECUTION =======================
async def main():
    """Ana program"""
    # MongoDB bağlantısını kontrol et
    if mongo_client is None:
        print("❌ MongoDB bağlantısı başarısız!")
        return
    
    print("✅ MongoDB bağlantısı hazır")
    
    bot = TelegramBot()
    
    try:
        # Application oluştur
        bot.app = Application.builder().token(bot.BOT_TOKEN).build()
        
        # Handler'ları ekle
        bot.app.add_handler(CommandHandler("start", bot.start_command))
        bot.app.add_handler(CallbackQueryHandler(bot.button_callback))
        bot.app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, bot.handle_message))
        
        print("🚀 Bot başlatılıyor...")
        print(f"🔑 Admin ID: {bot.ADMIN_ID}")
        print("✅ Bot hazır! /start yazın")
        
        # Bot'u başlat
        await bot.app.initialize()
        await bot.app.start()
        await bot.app.updater.start_polling()
        
        # Sürekli çalıştır
        import signal
        stop_signals = (signal.SIGTERM, signal.SIGINT)
        for sig in stop_signals:
            signal.signal(sig, lambda: None)
        
        # Sonsuz döngü
        while True:
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        print("\n🛑 Bot durduruldu")
    except Exception as e:
        print(f"❌ Bot hatası: {str(e)}")
    finally:
        if bot.app:
            await bot.app.stop()
            await bot.app.shutdown()
        
        # Telegram client'ları kapat
        for user_id, client in bot.telethon_clients.items():
            try:
                await client.disconnect()
                print(f"📱 Telegram oturumu kapatıldı: {user_id}")
            except:
                pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Program sonlandırıldı")
