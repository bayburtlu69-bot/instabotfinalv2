# =================== panel.py (BAŞI) ===================
import os
import time
import random
import smtplib
import threading
import json
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps

from dotenv import load_dotenv
load_dotenv()  # .env dosyasını yükle

# ------------------ Gizli Anahtarlar / Ortam Değişkenleri ------------------
# PayTR
PAYTR_MERCHANT_ID   = os.getenv("PAYTR_MERCHANT_ID", "")
PAYTR_MERCHANT_KEY  = os.getenv("PAYTR_MERCHANT_KEY", "")
PAYTR_MERCHANT_SALT = os.getenv("PAYTR_MERCHANT_SALT", "")

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")  # .env: TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")    # .env: TELEGRAM_CHAT_ID=...

# ResellersMM
# Öncelik .env, yoksa (geçici olarak) sabit fallback kullan
EXTERNAL_API_URL = os.getenv("EXTERNAL_API_URL", "https://resellersmm.com/api/v2/")
EXTERNAL_API_KEY = os.getenv("EXTERNAL_API_KEY", "6b0e961c4a42155ba44bfd4384915c27").strip()

# ------------------ Flask / DB ------------------
from flask import (
    Flask, session, request, redirect, render_template_string,
    abort, url_for, flash, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

# flask_login - current_user kullanımı için import
try:
    from flask_login import current_user  # Eğer projede LoginManager kuruluysa bunu kullanır
except Exception:
    # Fallback: current_user yoksa güvenli bir placeholder oluştur
    class _AnonUser:
        is_authenticated = False
    current_user = _AnonUser()

import requests  # HTTP istekleri için

app = Flask(__name__)
app.url_map.strict_slashes = False
app.secret_key = os.getenv("SECRET_KEY", "cok-gizli-bir-anahtar")  # .env ile değiştir

# ----- DB URL'yi normalize eden helper -----
def _normalize_db_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return raw

    low = raw.lower()

    # Neon panelinden kopyalanan "psql 'postgresql://...'" formatını ayıkla
    if low.startswith("psql "):
        q = "'" if "'" in raw else ('"' if '"' in raw else None)
        if q:
            i, j = raw.find(q), raw.rfind(q)
            if i != -1 and j != -1 and j > i:
                raw = raw[i + 1 : j].strip()
        else:
            # psql'den sonra kalan kısmı dene
            raw = raw.split(None, 1)[-1].strip()

    # Baş/son tırnakları temizle
    if (raw.startswith("'") and raw.endswith("'")) or (raw.startswith('"') and raw.endswith('"')):
        raw = raw[1:-1].strip()

    # Eski şema düzeltmesi
    if raw.startswith("postgres://"):
        raw = raw.replace("postgres://", "postgresql+psycopg2://", 1)

    # postgresql:// da geçerli; istersek driver ekleyebiliriz ama zorunlu değil.
    return raw

# Neon/Postgres bağlantın (ENV + normalize + güvenli fallback)
_raw_uri = os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URI") or ""
uri = _normalize_db_url(_raw_uri)

if not uri:
    # Production'da boş kalmasın diye buradaki fallback'i çalıştırma ihtimali varsa log basalım
    print("⚠️  DATABASE_URL boş geldi, sqlite fallback'e düşülüyor.", flush=True)
    uri = "sqlite:///data.db"

# SQLAlchemy ayarları
app.config["SQLALCHEMY_DATABASE_URI"] = uri
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Havuz/bağlantı stabilitesi
engine_opts = {"pool_pre_ping": True, "pool_recycle": 280}

# Eğer URL'de sslmode yoksa ve postgres ise güvenli tarafta kal
if uri.startswith("postgres") and "sslmode=" not in uri:
    engine_opts["connect_args"] = {"sslmode": "require"}

app.config["SQLALCHEMY_ENGINE_OPTIONS"] = engine_opts

db = SQLAlchemy(app)

# ------------------ Sabitler ------------------
SABIT_FIYAT = 0.5
EXT_SELECTION_FILE = "ext_selection.json"
EXT_SELECTED_IDS = [6896, 6898, 6899, 6900, 6911, 6901, 6909, 6910, 6904, 6908, 6905]

# Platform override (istersen doldur)
PLATFORM_OVERRIDES = {
    # Örnek: 100000 + 2273: "tiktok",
}

# === PARA HELPER'LARI (dosyada 1 kere olsun) ================================

from decimal import Decimal, ROUND_HALF_UP

TWOPLACES = Decimal("0.01")

def D(val) -> Decimal:
    """Her şeyi güvenli biçimde 2 basamaklı Decimal'e çevir."""
    return Decimal(str(val or 0)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)

def _balance_set(user, new_amount_dec: Decimal):
    """User.balance kolon tipi float/Decimal fark etmeksizin doğru tipte yaz."""
    curr = getattr(user, "balance", 0)
    if isinstance(curr, Decimal):
        user.balance = new_amount_dec
    else:
        user.balance = float(new_amount_dec)

def balance_add(user, amount) -> Decimal:
    new_val = D(getattr(user, "balance", 0)) + D(amount)
    _balance_set(user, new_val)
    return new_val

def balance_sub(user, amount) -> Decimal:
    new_val = D(getattr(user, "balance", 0)) - D(amount)
    _balance_set(user, new_val)
    return new_val

# ------------------ Yardımcı Fonksiyonlar ------------------

TWOPLACES = Decimal("0.01")

def D(val) -> Decimal:
    """Her şeyi güvenli şekilde 2 basamaklı Decimal'e çevir."""
    return Decimal(str(val or 0)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)

def balance_set(user, new_amount_dec: Decimal):
    """User.balance kolon tipi float mı Decimal mi fark etmeksizin doğru tipte yaz."""
    curr = getattr(user, "balance", 0)
    if isinstance(curr, Decimal):
        user.balance = new_amount_dec
    else:
        user.balance = float(new_amount_dec)

def balance_add(user, amount) -> Decimal:
    new_val = D(getattr(user, "balance", 0)) + D(amount)
    balance_set(user, new_val)
    return new_val

def balance_sub(user, amount) -> Decimal:
    new_val = D(getattr(user, "balance", 0)) - D(amount)
    balance_set(user, new_val)
    return new_val

def telegram_mesaj_gonder(mesaj: str) -> bool:
    """Telegram’a mesaj gönderir. .env’den token/chat_id alır."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram uyarı: BOT_TOKEN veya CHAT_ID yok.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mesaj, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, data=payload, timeout=12)
        print("Telegram response:", resp.text)
        return resp.ok
    except Exception as e:
        print("Telegram Hatası:", e)
        return False

def load_selected_ext_ids():
    try:
        with open(EXT_SELECTION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_selected_ext_ids(ids):
    with open(EXT_SELECTION_FILE, "w", encoding="utf-8") as f:
        json.dump(ids, f)

def detect_platform(*parts: str) -> str:
    t = (" ".join([p or "" for p in parts])).lower()
    if any(k in t for k in ["tiktok", "tik tok", "tt ", "douyin"]):
        return "tiktok"
    if any(k in t for k in ["youtube", "yt ", " y.t", "shorts", "abon", "subscriber"]):
        return "youtube"
    return "instagram"

def durum_turkce(status: str) -> str:
    s = (status or "").lower().strip()
    mapping = {
        "completed": "Tamamlandı",
        "pending": "İşlemde",
        "started": "Başladı",
        "in progress": "İşlemde",
        "processing": "Sırada",
        "canceled": "İptal Edildi",
        "cancelled": "İptal Edildi",
        "partial": "Kısmi Tamamlandı",
        "fail": "Sırada",
    }
    return mapping.get(s, status)

def admin_required(f):
    """Session veya flask_login mevcutsa admin kontrolü."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        user_id = session.get("user_id")
        # current_user varsa ve login'liyse onu kullan; yoksa session fallback
        try:
            if getattr(current_user, "is_authenticated", False) and getattr(current_user, "role", None) == "admin":
                return f(*args, **kwargs)
        except Exception:
            pass
        from sqlalchemy import inspect
        # Döngü importlarını önlemek için burada import edeceğiz (User modelin altta)
        try:
            user = User.query.get(user_id) if user_id else None  # noqa: F821 (User daha sonra tanımlı)
        except Exception:
            # Model henüz load edilmediyse sessiz geç
            user = None
        if not user or getattr(user, "role", None) != "admin":
            abort(403)
        return f(*args, **kwargs)
    return wrapper

def sync_services_with_api(api_services):
    """
    api_services: API'den gelen servis objeleri (Service instance) ya da dict listesi.
    DB’de artık bulunmayanları temizler.
    """
    if not api_services:
        return
    if hasattr(api_services[0], "id"):
        api_service_ids = {s.id for s in api_services}
    elif isinstance(api_services[0], dict):
        api_service_ids = {int(s["id"]) for s in api_services if "id" in s}
    else:
        return

    db_services = Service.query.all()  # noqa: F821
    db_service_ids = {s.id for s in db_services}
    to_delete = db_service_ids - api_service_ids
    if to_delete:
        Service.query.filter(Service.id.in_(to_delete)).delete(synchronize_session=False)  # noqa: F821
        db.session.commit()

# ------------------ ResellersMM Servis Çekme ------------------
def fetch_selected_external_services():
    """
    Sadece EXT_SELECTED_IDS’teki ResellersMM servislerini çeker.
    Service modeli daha sonra tanımlı olacağı için fonksiyon tanımlı kalabilir.
    """
    if not EXTERNAL_API_KEY:
        print("❌ EXTERNAL_API_KEY boş. .env'e EXTERNAL_API_KEY ekleyin.")
        return []

    try:
        resp = requests.get(
            EXTERNAL_API_URL,
            params={"key": EXTERNAL_API_KEY, "action": "services"},
            timeout=20
        )
        # API bazen 200 döner ama gövde hata içerir; ikisini de kontrol edelim
        try:
            payload = resp.json()
        except Exception:
            print("❌ Servis listesi JSON parse edilemedi:", resp.text[:300])
            return []

        # API error formatlarını normalize et
        if isinstance(payload, dict) and payload.get("error"):
            print("❌ Harici API hata:", payload.get("error"))
            return []

        data = payload.get("data", []) if isinstance(payload, dict) else payload
        if not isinstance(data, list):
            print("❌ Beklenmeyen servis cevap formatı:", type(data).__name__)
            return []

        filtered = [item for item in data if int(item.get("service", 0)) in EXT_SELECTED_IDS]

        services = []
        for item in filtered:
            # Service modeli çağrı zamanında mevcut olacak
            svc = Service(  # noqa: F821
                id=100000 + int(item["service"]),
                name=item.get("name", "İsim yok"),
                description=item.get("description", item.get("name", "")),
                price=float(item.get("rate", 0) or 0),
                min_amount=int(item.get("min", 1) or 1),
                max_amount=int(item.get("max", 1) or 1),
                active=True
            )
            plat = PLATFORM_OVERRIDES.get(svc.id) or detect_platform(item.get("category", ""), item.get("name", ""))
            setattr(svc, "platform", plat)
            services.append(svc)

        return services

    except requests.Timeout:
        print("❌ fetch_selected_external_services: Zaman aşımı")
        return []
    except Exception as e:
        print("❌ fetch_selected_external_services hata:", e)
        return []

# =================== panel.py (BAŞI SONU) ===================

# --- MODELLER ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(512), nullable=False)
    email = db.Column(db.String(120), unique=True)
    role = db.Column(db.String(16), nullable=False)
    balance = db.Column(db.Float, default=10.0)
    is_verified = db.Column(db.Boolean, default=False)
    last_ad_watch = db.Column(db.DateTime, default=None)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

from datetime import datetime

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    merchant_oid = db.Column(db.String(128), unique=True, index=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount_kurus = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(32), default='pending')  # pending | success | failed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# models.py (örnek)
class Category(db.Model):
    __tablename__ = "category"
    id   = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    icon = db.Column(db.String(16))
    order = db.Column(db.Integer, default=0)

class WalletTransaction(db.Model):
    __tablename__ = "wallet_transaction"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=True, index=True)
    amount = db.Column(db.Float, nullable=False)  # +: yükleme/iadeler, -: harcama
    type = db.Column(db.String(20), nullable=False)  # 'deposit' | 'order' | 'refund' | 'adjustment'
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        # Aynı order için 2. kez refund gelmesin:
        db.UniqueConstraint('order_id', 'type', name='uq_wallet_refund_per_order'),
    )

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(128), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(32), nullable=False, default="pending")
    total_price = db.Column(db.Float, nullable=False)
    service_id = db.Column(db.Integer, nullable=False)
    error = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship("User", backref="orders")
    api_order_id = db.Column(db.String(64), nullable=True)

class BalanceRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(16), default="pending")
    explanation = db.Column(db.String(256), default="")
    reject_reason = db.Column(db.String(256), default="")
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    user = db.relationship("User")

class Service(db.Model):
    __tablename__ = "service"
    __table_args__ = (
        db.UniqueConstraint("name", name="uq_service_name"),  # isme tekillik
    )

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(255), nullable=False)   # unique constraint üstte
    description = db.Column(db.Text)                          # 512 sınırı kalktı, uzun açıklama serbest
    price       = db.Column(db.Numeric(18, 5), nullable=False)
    min_amount  = db.Column(db.Integer, nullable=False, default=1)
    max_amount  = db.Column(db.Integer, nullable=False, default=1000)
    active      = db.Column(db.Boolean, nullable=False, default=True)

    # kategori entegrasyonu
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=True, index=True)
    category    = db.relationship("Category", backref=db.backref("services", lazy="dynamic"))

    def __repr__(self):
        return f"<Service id={self.id} name={self.name!r} price={self.price} active={self.active}>"

class Ticket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    subject = db.Column(db.String(128), nullable=False)
    message = db.Column(db.String(512), nullable=False)
    status = db.Column(db.String(16), default="open")
    response = db.Column(db.String(1024), default="")
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    user = db.relationship("User")

class AdVideo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    embed_url = db.Column(db.String(256), default="https://www.youtube.com/embed/KzJk7e7XF3g")

def _add_wallet_tx(user: User, amount: float, tx_type: str, order: Order | None = None):
    tx = WalletTransaction(
        user_id=user.id,
        order_id=order.id if order else None,
        amount=float(amount),
        type=tx_type,
    )
    db.session.add(tx)
    user.balance = float(user.balance or 0) + float(amount)

def apply_refund(order_id: int, amount: float | None = None) -> bool:
    """
    İdempotent refund: Aynı order için ikinci kez 'refund' yazmaz.
    amount=None ise sipariş toplamı kadar iade eder.
    İç statü: 'canceled' (ekranda TR gösterilecek).
    """
    order = Order.query.get(order_id)
    if not order:
        return False
    user = User.query.get(order.user_id)
    if not user:
        return False

    # Zaten refund var mı? (idempotent)
    exists = WalletTransaction.query.filter_by(order_id=order.id, type='refund').first()
    if exists:
        return False

    refund_amount = float(amount if amount is not None else (order.total_price or 0))
    if refund_amount <= 0:
        return False

    _add_wallet_tx(user, refund_amount, 'refund', order=order)
    order.status = 'canceled'
    db.session.commit()
    return True

from sqlalchemy import MetaData, text

def force_delete_user_everywhere(user_id: int):
    """
    Bu fonksiyon, tüm şemayı yansıtır (reflect) ve aşağıdaki aday sütun adlarına bakarak
    user_id'ye bağlı tüm kayıtları siler. En sonda users tablosundan kullanıcıyı da siler.
    """
    meta = MetaData()
    meta.reflect(bind=db.engine)

    candidate_cols = ('user_id', 'owner_id', 'created_by', 'updated_by', 'assigned_to')
    # Çift uçlu kullanıcı referansları için de ek denemeler (takipçi/arkadaşlık vb.)
    two_user_cols = (('follower_id',), ('followee_id',), ('target_user_id',), ('friend_id',))

    with db.session.begin():
        # 1) user_id vb. sütunları olan tablolardan sil
        for t in meta.sorted_tables:
            cols = t.c.keys()
            match_cols = [c for c in candidate_cols if c in cols]
            if match_cols:
                for col in match_cols:
                    db.session.execute(
                        t.delete().where(getattr(t.c, col) == user_id)
                    )

        # 2) İki uçlu kullanıcı sütunları (varsa)
        for t in meta.sorted_tables:
            cols = set(t.c.keys())
            for col_tuple in two_user_cols:
                for col in col_tuple:
                    if col in cols:
                        db.session.execute(
                            t.delete().where(getattr(t.c, col) == user_id)
                        )

        # 3) En sonda kullanıcıyı sil
        users_table_name = User.__tablename__  # genelde "user" veya "users"
        db.session.execute(
            text(f"DELETE FROM {users_table_name} WHERE id = :uid"),
            {"uid": user_id}
        )

# panel.py (uygun bir yere ekle)
from sqlalchemy import MetaData, Table, inspect

def force_delete_user_by_fk(user_id: int) -> int:
    """
    Users(id)'ye FK ile bağlı tüm tablolardaki satırları siler, en sonda kullanıcıyı siler.
    Dönen değer toplam silinen satır sayısıdır (kullanıcı dahil).
    """
    meta = MetaData()
    meta.reflect(bind=db.engine)

    insp = inspect(db.engine)
    users_t = Table(User.__tablename__, meta, autoload_with=db.engine)

    # Users(id)'ye referans veren FK'leri yakala
    refs = []  # (table, [local_cols])
    for t in meta.sorted_tables:
        for fk in t.foreign_key_constraints:
            # fk.columns: local kolon(lar); fk.elements: remote eşleşmeler
            for elem in fk.elements:
                if elem.column.table.name == users_t.name and elem.column.name == 'id':
                    local_cols = [c.name for c in fk.columns]  # genelde 1 kolon
                    refs.append((t, local_cols))
                    break

    total_deleted = 0

    # 1) FK ile bağlı çocukları (ve onların çocuklarını) defalarca geçerek sil
    changed = True
    while changed:
        changed = False
        for (t, local_cols) in refs:
            if len(local_cols) != 1:
                continue  # composite FK varsa atla (genelde yoktur)
            col = getattr(t.c, local_cols[0])
            res = db.session.execute(t.delete().where(col == user_id))
            rc = res.rowcount or 0
            if rc > 0:
                total_deleted += rc
                changed = True
                print(f"[FORCE-DEL] {t.name}: {rc} satır silindi")

    # 2) FK tanımlamamış ama user_id kolonlu tablolar varsa, ekstra süpür (opsiyonel)
    for t in meta.sorted_tables:
        if t.name != users_t.name and 'user_id' in t.c:
            res = db.session.execute(t.delete().where(t.c.user_id == user_id))
            rc = res.rowcount or 0
            if rc > 0:
                total_deleted += rc
                print(f"[FORCE-DEL] {t.name} (user_id kolonu): {rc} satır silindi")

    # 3) En sonda kullanıcıyı sil
    res = db.session.execute(users_t.delete().where(users_t.c.id == user_id))
    rc = res.rowcount or 0
    total_deleted += rc
    print(f"[FORCE-DEL] users: {rc} satır silindi")

    db.session.commit()
    return total_deleted

# --- SMTP AYARLARI (mail ile ilgili) ---
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_ADDR = "kuzenlertv6996@gmail.com"
SMTP_PASS = "nurkqldoqcaefqwk"
def send_verification_mail(email, code):
    subject = "Kayıt Doğrulama Kodunuz"
    body = f"Merhaba,\n\nKayıt işlemini tamamlamak için doğrulama kodunuz: {code}\n\nbaybayim.com Ekibi"
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_ADDR
    msg["To"] = email
    try:
        smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        smtp.starttls()
        smtp.login(SMTP_ADDR, SMTP_PASS)
        smtp.sendmail(SMTP_ADDR, [email], msg.as_string())
        smtp.quit()
    except Exception as e:
        print("Mail gönderilemedi:", e)

def rolu_turkce(rol):
    return "Yönetici" if rol == "admin" else ("Kullanıcı" if rol == "viewer" else rol)

# --- HTML ŞABLONLAR ---

HTML_USERS = """

<!DOCTYPE html>
<html lang="tr">
<head>
  <link rel="icon" href="{{ url_for('static', filename='favicon.ico') }}" type="image/x-icon">
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kullanıcı Yönetimi</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body {
      margin: 0;
      min-height: 100vh;
      background: linear-gradient(-45deg, #1e1e1e, #2c2f34, #1e1e1e, #000000);
      background-size: 400% 400%;
      animation: gradientBG 15s ease infinite;
      color: #fff;
      overflow: hidden;
      position: relative;
    }
    @keyframes gradientBG {
      0% {background-position: 0% 50%;}
      50% {background-position: 100% 50%;}
      100% {background-position: 0% 50%;}
    }
    .card {
      background-color: #1f1f1f;
      color: #fff;
      border-radius: 18px;
      z-index: 2;
      position: relative;
    }
    .form-control, .form-select {
      background-color: #2e2e2e !important;
      color: #f1f1f1;
      border: 1px solid #444;
      box-shadow: none;
    }
    .form-control:focus, .form-select:focus {
      background-color: #2e2e2e;
      color: #fff;
      border-color: #666;
      box-shadow: none;
    }
    .form-control::placeholder { color: #aaa; }
    input[type=number]::-webkit-inner-spin-button,
    input[type=number]::-webkit-outer-spin-button { -webkit-appearance: none; margin: 0; }
    input[type=number] { -moz-appearance: textfield; appearance: textfield; }

    .table-dark { background-color: #2c2c2c; }
    .table-dark th, .table-dark td { color: #eee; }

    a { color: #8db4ff; }
    a:hover { color: #fff; text-decoration: underline; }
    .btn { font-weight: 500; }

    /* Pagination (dark) */
    .pagination .page-link{background:#1e1e1e;border-color:#444;color:#e6e6e6}
    .pagination .page-link:hover{background:#2a2a2a;color:#fff}
    .pagination .page-item.active .page-link{background:#0ea5e9;border-color:#0ea5e9;color:#fff}
    .pagination .page-item.disabled .page-link{background:#141414;color:#777;border-color:#333}

    /* -- Sosyal medya hareketli arka plan -- */
    .animated-social-bg {
      position: fixed; inset: 0; width: 100vw; height: 100vh; z-index: 0;
      pointer-events: none; overflow: hidden; user-select: none;
    }
    .bg-icon {
      position: absolute; width: 48px; opacity: 0.13;
      filter: blur(0.2px) drop-shadow(0 4px 24px #0008);
      animation-duration: 18s; animation-iteration-count: infinite; animation-timing-function: ease-in-out;
      user-select: none;
    }
    .icon1  { left: 10vw;  top: 13vh; animation-name: float1; }
    .icon2  { left: 72vw;  top: 22vh; animation-name: float2; }
    .icon3  { left: 23vw;  top: 67vh; animation-name: float3; }
    .icon4  { left: 70vw;  top: 75vh; animation-name: float4; }
    .icon5  { left: 48vw;  top: 45vh; animation-name: float5; }
    .icon6  { left: 81vw;  top: 15vh; animation-name: float6; }
    .icon7  { left: 17vw;  top: 40vh; animation-name: float7;}
    .icon8  { left: 61vw;  top: 55vh; animation-name: float8;}
    .icon9  { left: 33vw;  top: 24vh; animation-name: float9;}
    .icon10 { left: 57vw; top: 32vh; animation-name: float10;}
    .icon11 { left: 80vw; top: 80vh; animation-name: float11;}
    .icon12 { left: 8vw;  top: 76vh; animation-name: float12;}
    .icon13 { left: 19vw;  top: 22vh; animation-name: float13;}
    .icon14 { left: 38vw;  top: 18vh; animation-name: float14;}
    .icon15 { left: 27vw;  top: 80vh; animation-name: float15;}
    .icon16 { left: 45vw;  top: 82vh; animation-name: float16;}
    .icon17 { left: 88vw;  top: 55vh; animation-name: float17;}
    .icon18 { left: 89vw;  top: 28vh; animation-name: float18;}
    @keyframes float1  { 0%{transform:translateY(0);} 50%{transform:translateY(-34px) scale(1.09);} 100%{transform:translateY(0);} }
    @keyframes float2  { 0%{transform:translateY(0);} 50%{transform:translateY(20px) scale(0.97);} 100%{transform:translateY(0);} }
    @keyframes float3  { 0%{transform:translateY(0);} 50%{transform:translateY(-27px) scale(1.05);} 100%{transform:translateY(0);} }
    @keyframes float4  { 0%{transform:translateY(0);} 50%{transform:translateY(-20px) scale(0.95);} 100%{transform:translateY(0);} }
    @keyframes float5  { 0%{transform:translateY(0);} 50%{transform:translateY(21px) scale(1.02);} 100%{transform:translateY(0);} }
    @keyframes float6  { 0%{transform:translateY(0);} 50%{transform:translateY(-16px) scale(1.05);} 100%{transform:translateY(0);} }
    @keyframes float7  { 0%{transform:translateY(0);} 50%{transform:translateY(18px) scale(0.98);} 100%{transform:translateY(0);} }
    @keyframes float8  { 0%{transform:translateY(0);} 50%{transform:translateY(-14px) scale(1.04);} 100%{transform:translateY(0);} }
    @keyframes float9  { 0%{transform:translateY(0);} 50%{transform:translateY(24px) scale(1.06);} 100%{transform:translateY(0);} }
    @keyframes float10 { 0%{transform:translateY(0);} 50%{transform:translateY(-22px) scale(1.01);} 100%{transform:translateY(0);} }
    @keyframes float11 { 0%{transform:translateY(0);} 50%{transform:translateY(15px) scale(1.06);} 100%{transform:translateY(0);} }
    @keyframes float12 { 0%{transform:translateY(0);} 50%{transform:translateY(-18px) scale(1.03);} 100%{transform:translateY(0);} }
    @keyframes float13 { 0%{transform:translateY(0);} 50%{transform:translateY(24px) scale(1.04);} 100%{transform:translateY(0);} }
    @keyframes float14 { 0%{transform:translateY(0);} 50%{transform:translateY(-20px) scale(1.07);} 100%{transform:translateY(0);} }
    @keyframes float15 { 0%{transform:translateY(0);} 50%{transform:translateY(11px) scale(0.94);} 100%{transform:translateY(0);} }
    @keyframes float16 { 0%{transform:translateY(0);} 50%{transform:translateY(-19px) scale(1.03);} 100%{transform:translateY(0);} }
    @keyframes float17 { 0%{transform:translateY(0);} 50%{transform:translateY(16px) scale(1.01);} 100%{transform:translateY(0);} }
    @keyframes float18 { 0%{transform:translateY(0);} 50%{transform:translateY(-25px) scale(1.05);} 100%{transform:translateY(0);} }
  </style>
</head>
<body class="text-light">
  <!-- Sosyal medya hareketli arka plan -->
  <div class="animated-social-bg">
    <img src="{{ url_for('static', filename='linkedin.png') }}" class="bg-icon icon1">
    <img src="{{ url_for('static', filename='youtube.png') }}" class="bg-icon icon2">
    <img src="{{ url_for('static', filename='twitter.png') }}" class="bg-icon icon3">
    <img src="{{ url_for('static', filename='9gag.png') }}" class="bg-icon icon4">
    <img src="{{ url_for('static', filename='imo.png') }}" class="bg-icon icon5">
    <img src="{{ url_for('static', filename='discord.png') }}" class="bg-icon icon6">
    <img src="{{ url_for('static', filename='goodreads.png') }}" class="bg-icon icon7">
    <img src="{{ url_for('static', filename='twitch.png') }}" class="bg-icon icon8">
    <img src="{{ url_for('static', filename='wechat.png') }}" class="bg-icon icon9">
    <img src="{{ url_for('static', filename='swift.png') }}" class="bg-icon icon10">
    <img src="{{ url_for('static', filename='vkontakte.png') }}" class="bg-icon icon11">
    <img src="{{ url_for('static', filename='envato.png') }}" class="bg-icon icon12">
    <img src="{{ url_for('static', filename='reddit.png') }}" class="bg-icon icon13">
    <img src="{{ url_for('static', filename='facebook.png') }}" class="bg-icon icon14">
    <img src="{{ url_for('static', filename='instagram.png') }}" class="bg-icon icon15">
    <img src="{{ url_for('static', filename='foursquare.png') }}" class="bg-icon icon16">
    <img src="{{ url_for('static', filename='whatsapp.png') }}" class="bg-icon icon17">
    <img src="{{ url_for('static', filename='klout.png') }}" class="bg-icon icon18">
  </div>
  <div class="container py-4">
    <div class="card p-4 mx-auto" style="max-width:700px;">
      <h3>Kullanıcı Yönetimi</h3>
      <form method="post" class="row g-2 align-items-end mb-4">
        <div class="col"><input name="u" class="form-control" placeholder="Yeni kullanıcı"></div>
        <div class="col"><input name="pw" type="password" class="form-control" placeholder="Parola"></div>
        <div class="col">
          <select name="role" class="form-select">
            <option value="admin">Yönetici</option>
            <option value="viewer">Kullanıcı</option>
          </select>
        </div>
        <div class="col"><button class="btn btn-success">Ekle</button></div>
      </form>
      <hr><h5>Mevcut Kullanıcılar</h5>
      <div class="table-responsive">
        <table class="table table-dark table-striped table-bordered align-middle mb-3">
          <thead>
            <tr>
              <th>#</th><th>Kullanıcı</th><th>Rol</th><th>Bakiye</th><th>İşlem</th>
            </tr>
          </thead>
          <tbody>
            {% for usr in users %}
              <tr>
                <td>{{ start_index + loop.index }}</td>
                <td>{{ usr.username }}</td>
                <td>{{ rolu_turkce(usr.role) }}</td>
                <td>{{ usr.balance }}</td>
                <td>
<a href="{{ url_for('admin_force_delete_user', user_id=usr.id) }}"
   class="btn btn-danger btn-sm"
   onclick="return confirm('“{{ usr.username }}” ve bağlı TÜM veriler KALICI olarak silinsin mi? Bu işlem geri alınamaz!');">
   Kalıcı Sil
</a>
                </td>
              </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>

      {# ---- PAGINATION ---- #}
      {% if total_pages > 1 %}
      <nav aria-label="Sayfalar" class="mt-2">
        <ul class="pagination justify-content-center">
          <li class="page-item {% if page <= 1 %}disabled{% endif %}">
            <a class="page-link" href="?page={{ page-1 }}">Önceki</a>
          </li>

          {% set s = 1 if page-2 < 1 else page-2 %}
          {% set e = total_pages if page+2 > total_pages else page+2 %}

          {% if s > 1 %}
            <li class="page-item"><a class="page-link" href="?page=1">1</a></li>
            {% if s > 2 %}<li class="page-item disabled"><span class="page-link">…</span></li>{% endif %}
          {% endif %}

          {% for p in range(s, e + 1) %}
            {% if p == page %}
              <li class="page-item active"><span class="page-link">{{ p }}</span></li>
            {% else %}
              <li class="page-item"><a class="page-link" href="?page={{ p }}">{{ p }}</a></li>
            {% endif %}
          {% endfor %}

          {% if e < total_pages %}
            {% if e < total_pages - 1 %}<li class="page-item disabled"><span class="page-link">…</span></li>{% endif %}
            <li class="page-item"><a class="page-link" href="?page={{ total_pages }}">{{ total_pages }}</a></li>
          {% endif %}

          <li class="page-item {% if page >= total_pages %}disabled{% endif %}">
            <a class="page-link" href="?page={{ page+1 }}">Sonraki</a>
          </li>
        </ul>
      </nav>
      {% endif %}

      <h5>Bakiye Ekle</h5>
      <form method="post" action="/admin/add-balance" class="row g-2">
        <div class="col"><input name="username" class="form-control" placeholder="Kullanıcı adı"></div>
        <div class="col"><input name="amount" type="number" step="0.01" class="form-control" placeholder="Tutar"></div>
        <div class="col"><button class="btn btn-primary">Bakiye Ekle</button></div>
      </form>
      <div class="mt-3">
        <a href="{{ url_for('panel') }}" class="btn btn-secondary btn-sm">Panel’e Dön</a>
      </div>
    </div>
  </div>
</body>
</html>
"""

HTML_LOGIN = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Baybayim - Sosyal Medya Hizmetleri</title>
  <meta name="description" content="Baybayim SMM Panel: Instagram, TikTok, YouTube ve daha fazlasında takipçi, beğeni, izlenme, yorum. Hızlı, otomatik, güvenli.">
  <link rel="icon" href="{{ url_for('static', filename='favicon.ico') }}" type="image/x-icon">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">

  <!-- Open Graph -->
  <meta property="og:title" content="Baybayim - Sosyal Medya Hizmetleri">
  <meta property="og:description" content="Instagram, TikTok, YouTube vb. tüm sosyal medya hizmetleri tek panelde.">
  <meta property="og:type" content="website">
  <meta property="og:url" content="https://baybayim.com/">
  <meta property="og:image" content="https://baybayim.com/static/logo.png">
  <meta property="og:locale" content="tr_TR">

  <!-- Twitter -->
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="Baybayim - Sosyal Medya Hizmetleri">
  <meta name="twitter:description" content="SMM Panel: takipçi, beğeni, izlenme, yorum. Hızlı ve güvenli.">
  <meta name="twitter:image" content="https://baybayim.com/static/logo.png">

  <style>
    html{ -webkit-text-size-adjust:100% }
    body{
      margin:0;height:100vh;color:#fff;overflow:hidden;position:relative;
      background:linear-gradient(-45deg,#121212,#1e1e1e,#212121,#000);background-size:400% 400%;
      animation:gradientBG 12s ease infinite;
    }
    @supports(height:100dvh){ body{ min-height:100dvh;height:auto } }
    @keyframes gradientBG{0%{background-position:0% 50%}50%{background-position:100% 50%}100%{background-position:0% 50%}}

    .card{background:#1b1b1b;border-radius:16px;color:#fff;z-index:2;position:relative;max-width:980px;width:100%}
    .logo-img{width:62px;height:62px;display:block;margin:0 auto 12px;border-radius:20%;box-shadow:0 4px 16px #0005;object-fit:contain;background:#232323}
    .modern-title{font-family:'Montserrat','Segoe UI',Arial,sans-serif;font-size:2.4rem;font-weight:900;text-transform:uppercase;letter-spacing:.01em;margin:4px 0 14px;background:linear-gradient(92deg,#58a7ff 10%,#b95cff 65%,#2feea3 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;text-shadow:0 4px 24px #000c}
    .form-control,.form-control:focus{background:#2c2c2c;color:#f1f1f1;border:1px solid #555}
    ::placeholder{color:#aaa}
    .alert-custom{background:#1f1f1f;border-left:4px solid #0d6efd;padding:10px 12px;border-radius:6px;font-size:.95rem;margin-bottom:1rem;text-align:center}

    /* Bilgi paneli */
    .info{padding:18px 22px;border-left:1px solid #2a2a2a;background:linear-gradient(180deg,#1a1a1a 0%,#171717 100%)}
    .info h3{font-size:1.28rem;margin-bottom:.6rem}
    .info p{color:#cfcfcf;margin-bottom:.8rem}
    .tiny{font-size:.9rem;color:#cfcfcf}
    .badge-soft{background:#232323;border:1px solid #2f2f2f;border-radius:10px;padding:8px 10px;margin:4px 6px;display:inline-block}
    .step{display:flex;gap:10px;align-items:flex-start;margin-bottom:10px}
    .step .num{width:26px;height:26px;border-radius:7px;background:#0d6efd;display:inline-flex;align-items:center;justify-content:center;font-weight:700}

    /* Form altı: Sayaç + Güven + SSS */
    .divider{height:1px;background:#2a2a2a;margin:14px 0}
    .stat-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:8px}
    .stat-card{background:#232323;border:1px solid #2d2d2d;border-radius:12px;padding:12px;text-align:center}
    .stat-label{font-size:.76rem;color:#bdbdbd}
    .stat-val{font-weight:800;font-size:1.35rem;line-height:1.1;background:linear-gradient(92deg,#58a7ff,#b95cff,#2feea3);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
    .trust-row{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
    .trust-badge{background:#232323;border:1px solid #2f2f2f;border-radius:999px;padding:6px 10px;font-size:.85rem;display:inline-flex;align-items:center;gap:6px}
    .trust-dot{width:8px;height:8px;border-radius:50%;background:#2feea3;display:inline-block;box-shadow:0 0 8px #2feea399}
    .accordion-button{background:#212121;color:#eaeaea}
    .accordion-button:not(.collapsed){background:#262626;color:#fff;box-shadow:none}
    .accordion-body{background:#1f1f1f;color:#cfcfcf;border-top:1px solid #2a2a2a}

    /* Arka plan ikonları */
    .animated-social-bg{position:fixed;inset:0;width:100vw;height:100vh;z-index:0;pointer-events:none;overflow:hidden;user-select:none}
    .bg-icon{position:absolute;width:48px;opacity:.13;filter:blur(.2px) drop-shadow(0 4px 24px #0008);animation:18s ease-in-out infinite}
    .icon1{left:10vw;top:13vh;animation-name:float1}.icon2{left:72vw;top:22vh;animation-name:float2}.icon3{left:23vw;top:67vh;animation-name:float3}
    .icon4{left:70vw;top:75vh;animation-name:float4}.icon5{left:48vw;top:45vh;animation-name:float5}.icon6{left:81vw;top:15vh;animation-name:float6}
    .icon7{left:17vw;top:40vh;animation-name:float7}.icon8{left:61vw;top:55vh;animation-name:float8}.icon9{left:33vw;top:24vh;animation-name:float9}
    .icon10{left:57vw;top:32vh;animation-name:float10}.icon11{left:80vw;top:80vh;animation-name:float11}.icon12{left:8vw;top:76vh;animation-name:float12}
    .icon13{left:19vw;top:22vh;animation-name:float13}.icon14{left:38vw;top:18vh;animation-name:float14}.icon15{left:27vw;top:80vh;animation-name:float15}
    .icon16{left:45vw;top:82vh;animation-name:float16}.icon17{left:88vw;top:55vh;animation-name:float17}.icon18{left:89vw;top:28vh;animation-name:float18}
    @keyframes float1{0%{transform:translateY(0)}50%{transform:translateY(-34px) scale(1.09)}100%{transform:translateY(0)}}
    @keyframes float2{0%{transform:translateY(0)}50%{transform:translateY(20px) scale(.97)}100%{transform:translateY(0)}}
    @keyframes float3{0%{transform:translateY(0)}50%{transform:translateY(-27px) scale(1.05)}100%{transform:translateY(0)}}
    @keyframes float4{0%{transform:translateY(0)}50%{transform:translateY(-20px) scale(.95)}100%{transform:translateY(0)}}
    @keyframes float5{0%{transform:translateY(0)}50%{transform:translateY(21px) scale(1.02)}100%{transform:translateY(0)}}
    @keyframes float6{0%{transform:translateY(0)}50%{transform:translateY(-16px) scale(1.05)}100%{transform:translateY(0)}}
    @keyframes float7{0%{transform:translateY(0)}50%{transform:translateY(18px) scale(.98)}100%{transform:translateY(0)}}
    @keyframes float8{0%{transform:translateY(0)}50%{transform:translateY(-14px) scale(1.04)}100%{transform:translateY(0)}}
    @keyframes float9{0%{transform:translateY(0)}50%{transform:translateY(24px) scale(1.06)}100%{transform:translateY(0)}}
    @keyframes float10{0%{transform:translateY(0)}50%{transform:translateY(-22px) scale(1.01)}100%{transform:translateY(0)}}
    @keyframes float11{0%{transform:translateY(0)}50%{transform:translateY(15px) scale(1.06)}100%{transform:translateY(0)}}
    @keyframes float12{0%{transform:translateY(0)}50%{transform:translateY(-18px) scale(1.03)}100%{transform:translateY(0)}}
    @keyframes float13{0%{transform:translateY(0)}50%{transform:translateY(24px) scale(1.04)}100%{transform:translateY(0)}}
    @keyframes float14{0%{transform:translateY(0)}50%{transform:translateY(-20px) scale(1.07)}100%{transform:translateY(0)}}
    @keyframes float15{0%{transform:translateY(0)}50%{transform:translateY(11px) scale(.94)}100%{transform:translateY(0)}}
    @keyframes float16{0%{transform:translateY(0)}50%{transform:translateY(-19px) scale(1.03)}100%{transform:translateY(0)}}
    @keyframes float17{0%{transform:translateY(0)}50%{transform:translateY(16px) scale(1.01)}100%{transform:translateY(0)}}
    @keyframes float18{0%{transform:translateY(0)}50%{transform:translateY(-25px) scale(1.05)}100%{transform:translateY(0)}}

    /* Yerleşim: formu/infoyu yan yana ve yer değiştirebilir yap */
    .form-first .row{flex-direction:row}
    .info-first .row{flex-direction:row-reverse}

    /* Mobil */
    @media (max-width:575.98px){
      body{overflow-x:hidden;overflow-y:auto;padding:16px 12px calc(16px + env(safe-area-inset-bottom))}
      body.d-flex{align-items:flex-start!important}
      .card{margin:10vh auto 0;max-width:420px}
      .modern-title{font-size:clamp(1.6rem,8vw,2.2rem)}
      .bg-icon{width:36px;opacity:.12}
      .info{border-left:0;border-top:1px solid #2a2a2a;padding:16px}
      .stat-grid{grid-template-columns:repeat(2,1fr)}
    }
  </style>
</head>
<body class="d-flex justify-content-center align-items-center info-first"> <!-- form-first = form solda, info-first = info solda -->
  <!-- Arka plan ikonları -->
  <div class="animated-social-bg">
    <img src="{{ url_for('static', filename='linkedin.png') }}" class="bg-icon icon1" alt="">
    <img src="{{ url_for('static', filename='youtube.png') }}" class="bg-icon icon2" alt="">
    <img src="{{ url_for('static', filename='twitter.png') }}" class="bg-icon icon3" alt="">
    <img src="{{ url_for('static', filename='9gag.png') }}" class="bg-icon icon4" alt="">
    <img src="{{ url_for('static', filename='imo.png') }}" class="bg-icon icon5" alt="">
    <img src="{{ url_for('static', filename='discord.png') }}" class="bg-icon icon6" alt="">
    <img src="{{ url_for('static', filename='goodreads.png') }}" class="bg-icon icon7" alt="">
    <img src="{{ url_for('static', filename='twitch.png') }}" class="bg-icon icon8" alt="">
    <img src="{{ url_for('static', filename='wechat.png') }}" class="bg-icon icon9" alt="">
    <img src="{{ url_for('static', filename='swift.png') }}" class="bg-icon icon10" alt="">
    <img src="{{ url_for('static', filename='vkontakte.png') }}" class="bg-icon icon11" alt="">
    <img src="{{ url_for('static', filename='envato.png') }}" class="bg-icon icon12" alt="">
    <img src="{{ url_for('static', filename='reddit.png') }}" class="bg-icon icon13" alt="">
    <img src="{{ url_for('static', filename='facebook.png') }}" class="bg-icon icon14" alt="">
    <img src="{{ url_for('static', filename='instagram.png') }}" class="bg-icon icon15" alt="">
    <img src="{{ url_for('static', filename='foursquare.png') }}" class="bg-icon icon16" alt="">
    <img src="{{ url_for('static', filename='whatsapp.png') }}" class="bg-icon icon17" alt="">
    <img src="{{ url_for('static', filename='klout.png') }}" class="bg-icon icon18" alt="">
  </div>

  <div class="card shadow p-0">
    <div class="row g-0 align-items-stretch">
      <!-- FORM -->
      <div class="col-12 col-md-6 p-4">
        <img src="{{ url_for('static', filename='logo.png') }}" alt="Logo" class="logo-img">
        <div class="modern-title text-center">BAYBAYİM GİRİŞ</div>

        {% with messages = get_flashed_messages() %}
          {% if messages %}
            <div class="alert-custom">
              {% for message in messages %}{{ message }}<br>{% endfor %}
            </div>
          {% endif %}
        {% endwith %}

        <form method="post">
          <div class="mb-2">
            <label class="form-label">Kullanıcı Adı</label>
            <input name="username" class="form-control" placeholder="">
          </div>
          <div class="mb-3">
            <label class="form-label">Şifre</label>
            <input name="password" type="password" class="form-control" placeholder="">
          </div>
          <button class="btn btn-primary w-100">Giriş</button>
        </form>
        <div class="text-center mt-2">
          <a href="/register" class="btn btn-link btn-sm">Kayıt Ol</a>
        </div>

        <!-- === FORM ALTI: Sayaç + Güven + SSS (Login/Register aynı) === -->
        <div class="divider"></div>

        <div class="stat-grid" aria-label="Panel istatistikleri">
          <div class="stat-card">
            <div class="stat-label">Son 24 saatte sipariş</div>
            <div class="stat-val" data-count="12438"></div>
          </div>
          <div class="stat-card">
            <div class="stat-label">Ortalama başlangıç</div>
            <div class="stat-val" data-count="10" data-suffix=" DK"></div>
          </div>
          <div class="stat-card">
            <div class="stat-label">Aktif Çalışan Servis</div>
            <div class="stat-val" data-count="11"></div>
          </div>
          <div class="stat-card">
            <div class="stat-label">Refill başarı oranı</div>
            <div class="stat-val" data-count="98" data-suffix="%"></div>
          </div>
        </div>

        <div class="trust-row" aria-label="Güven rozetleri">
          <span class="trust-badge"><span class="trust-dot"></span> 7/24 Otomatik</span>
          <span class="trust-badge"><span class="trust-dot"></span> Şifre İstemeyiz</span>
          <span class="trust-badge"><span class="trust-dot"></span> Canlı Destek</span>
        </div>

        <div class="divider"></div>

        <div class="accordion accordion-flush" id="faqAccordion">
          <div class="accordion-item">
            <h2 class="accordion-header" id="q1">
              <button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#a1" aria-expanded="false" aria-controls="a1">
                Düşüş olursa ne oluyor?
              </button>
            </h2>
            <div id="a1" class="accordion-collapse collapse" aria-labelledby="q1" data-bs-parent="#faqAccordion">
              <div class="accordion-body">Refill aktif servislerde otomatik telafi çalışır; eksik kalanlar bakiyeye iade ya da telafiyle tamamlanır.</div>
            </div>
          </div>
        </div>
        <!-- === /FORM ALTI === -->
      </div>

      <!-- BİLGİ PANELİ (GİRİŞ ve KAYIT ile AYNIDIR) -->
      <div class="col-12 col-md-6 info">
        <h3>Neye giriş yapıyorsun?</h3>
        <p>Baybayim; <strong>SMM Panel</strong> altyapısıyla Instagram, TikTok, YouTube, Twitter/X ve daha fazlasında
          <strong>takipçi</strong>, <strong>beğeni</strong>, <strong>izlenme</strong>, <strong>yorum</strong> gibi
          hizmetleri <em>hızlı, otomatik ve güvenli</em> şekilde sunar. Panel <strong>7/24</strong> açıktır; siparişler saniyeler içinde işleme alınır.</p>

        <div class="mb-2">
          <span class="badge-soft">Instagram • Takipçi / Beğeni / Reel izlenme</span>
          <span class="badge-soft">TikTok • İzlenme / Canlı izleyici</span>
          <span class="badge-soft">YouTube • İzlenme / Abone / Yorum</span>
        </div>

        <h3 class="mt-3">Nasıl çalışır?</h3>

        <div class="step"><span class="num">1</span><div><strong>Hesabını oluştur / giriş yap</strong><br><span class="tiny">E-posta doğrulaması ile güvence.</span></div></div>
        <div class="step"><span class="num">2</span><div><strong>Bakiye ekle</strong><br><span class="tiny">Desteklenen yöntemlerle güvenli ödeme.</span></div></div>
        <div class="step"><span class="num">3</span><div><strong>Hizmeti seç & linki gir</strong><br><span class="tiny">Siparişin otomatik başlar; hız ve kapsam açıklamada yazar.</span></div></div>

        <h3 class="mt-3">Neden Baybayim?</h3>
        <ul class="tiny mb-3">
          <li>⚡ Anında otomatik teslimat & 7/24 panel</li>
          <li>🛡️ <strong>Şifreni asla istemeyiz</strong>; gizlilik ve sipariş koruması</li>
          <li>🎯 Yerli/gerçek/karışık gibi kalite seçenekleri</li>
          <li>💬 Canlı destek ve detaylı sipariş takibi</li>
        </ul>

        <h3 class="mt-3">Önemli notlar</h3>
        <ul class="tiny">
          <li>Kullanıcı adı veya içerik bağlantısı yeterlidir; şifre paylaşılmaz.</li>
          <li>Düşüşlerde telafi sunulan servislerde otomatik telafi işler.</li>
        </ul>

        <p class="tiny mt-3">Yeni misin? <a href="/register" class="link-light text-decoration-underline">30 sn’de hesap aç</a> ve ilk sipariş bonusunu kap 🚀</p>
      </div>
    </div>
  </div>

  <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
  <script>
    // Basit sayaç animasyonu (görününce başlar)
    (function(){
      const els = document.querySelectorAll('.stat-val');
      if(!('IntersectionObserver' in window)){ els.forEach(e=>animate(e)); return; }
      const io = new IntersectionObserver((entries)=>{
        entries.forEach(ent=>{
          if(ent.isIntersecting && !ent.target.dataset.done){
            animate(ent.target); ent.target.dataset.done = '1';
          }
        });
      },{threshold:0.6});
      els.forEach(el=>io.observe(el));

      function animate(el){
        const target = parseFloat(el.dataset.count||'0');
        const suffix = el.dataset.suffix || '';
        const duration = 1400;
        let start;
        function step(ts){
          if(!start) start = ts;
          const p = Math.min((ts-start)/duration,1);
          const val = Math.floor(target*p);
          el.textContent = val.toLocaleString('tr-TR') + suffix;
          if(p<1) requestAnimationFrame(step);
        }
        requestAnimationFrame(step);
      }
    })();
  </script>
</body>
</html>
"""

HTML_REGISTER = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Kayıt Ol - Baybayim</title>
  <link rel="icon" href="{{ url_for('static', filename='favicon.ico') }}" type="image/x-icon">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    html{ -webkit-text-size-adjust:100% }
    body{
      margin:0;height:100vh;color:#fff;overflow:hidden;position:relative;
      background:linear-gradient(-45deg,#121212,#1e1e1e,#212121,#000);background-size:400% 400%;
      animation:gradientBG 12s ease infinite;
    }
    @supports(height:100dvh){ body{min-height:100dvh;height:auto} }
    @keyframes gradientBG{0%{background-position:0% 50%}50%{background-position:100% 50%}100%{background-position:0% 50%}}

    .card{background:#1b1b1b;border-radius:16px;color:#fff;z-index:2;position:relative;max-width:980px;width:100%}
    .logo-img{width:62px;height:62px;display:block;margin:0 auto 12px;border-radius:20%;box-shadow:0 4px 16px #0005;object-fit:contain;background:#232323}
    .modern-title{font-family:'Montserrat','Segoe UI',Arial,sans-serif;font-size:2.4rem;font-weight:900;text-transform:uppercase;background:linear-gradient(92deg,#58a7ff 10%,#b95cff 65%,#2feea3 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;text-shadow:0 4px 24px #000c}
    .modern-title-register{font-family:'Montserrat','Segoe UI',Arial,sans-serif;font-size:2.4rem;font-weight:900;text-transform:uppercase;background:linear-gradient(90deg,#14fff1 0%,#4294ff 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;text-shadow:0 2px 10px #00485a55}

    .form-control,.form-control:focus{background:#2c2c2c;color:#f1f1f1;border:1px solid #555}
    .custom-alert{background:#292929;border-left:5px solid #4da3ff;padding:12px 15px;border-radius:6px;color:#fff;font-size:.92rem;margin-bottom:18px;text-align:center}

    .info{padding:18px 22px;border-left:1px solid #2a2a2a;background:linear-gradient(180deg,#1a1a1a 0%,#171717 100%)}
    .info h3{font-size:1.28rem;margin-bottom:.6rem}
    .info p{color:#cfcfcf;margin-bottom:.8rem}
    .tiny{font-size:.9rem;color:#cfcfcf}
    .badge-soft{background:#232323;border:1px solid #2f2f2f;border-radius:10px;padding:8px 10px;margin:4px 6px;display:inline-block}
    .step{display:flex;gap:10px;align-items:flex-start;margin-bottom:10px}
    .step .num{width:26px;height:26px;border-radius:7px;background:#0d6efd;display:inline-flex;align-items:center;justify-content:center;font-weight:700}

    /* Form altı: Sayaç + Güven + SSS (Login ile aynı) */
    .divider{height:1px;background:#2a2a2a;margin:14px 0}
    .stat-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:8px}
    .stat-card{background:#232323;border:1px solid #2d2d2d;border-radius:12px;padding:12px;text-align:center}
    .stat-label{font-size:.76rem;color:#bdbdbd}
    .stat-val{font-weight:800;font-size:1.35rem;line-height:1.1;background:linear-gradient(92deg,#58a7ff,#b95cff,#2feea3);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
    .trust-row{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
    .trust-badge{background:#232323;border:1px solid #2f2f2f;border-radius:999px;padding:6px 10px;font-size:.85rem;display:inline-flex;align-items:center;gap:6px}
    .trust-dot{width:8px;height:8px;border-radius:50%;background:#2feea3;display:inline-block;box-shadow:0 0 8px #2feea399}
    .accordion-button{background:#212121;color:#eaeaea}
    .accordion-button:not(.collapsed){background:#262626;color:#fff;box-shadow:none}
    .accordion-body{background:#1f1f1f;color:#cfcfcf;border-top:1px solid #2a2a2a}

    .animated-social-bg{position:fixed;inset:0;width:100vw;height:100vh;z-index:0;pointer-events:none;overflow:hidden;user-select:none}
    .bg-icon{position:absolute;width:48px;opacity:.13;filter:blur(.2px) drop-shadow(0 4px 24px #0008);animation:18s ease-in-out infinite}
    .icon1{left:10vw;top:13vh;animation-name:float1}.icon2{left:72vw;top:22vh;animation-name:float2}.icon3{left:23vw;top:67vh;animation-name:float3}
    .icon4{left:70vw;top:75vh;animation-name:float4}.icon5{left:48vw;top:45vh;animation-name:float5}.icon6{left:81vw;top:15vh;animation-name:float6}
    .icon7{left:17vw;top:40vh;animation-name:float7}.icon8{left:61vw;top:55vh;animation-name:float8}.icon9{left:33vw;top:24vh;animation-name:float9}
    .icon10{left:57vw;top:32vh;animation-name:float10}.icon11{left:80vw;top:80vh;animation-name:float11}.icon12{left:8vw;top:76vh;animation-name:float12}
    .icon13{left:19vw;top:22vh;animation-name:float13}.icon14{left:38vw;top:18vh;animation-name:float14}.icon15{left:27vw;top:80vh;animation-name:float15}
    .icon16{left:45vw;top:82vh;animation-name:float16}.icon17{left:88vw;top:55vh;animation-name:float17}.icon18{left:89vw;top:28vh;animation-name:float18}
    @keyframes float1{0%{transform:translateY(0)}50%{transform:translateY(-34px) scale(1.09)}100%{transform:translateY(0)}}
    @keyframes float2{0%{transform:translateY(0)}50%{transform:translateY(20px) scale(.97)}100%{transform:translateY(0)}}
    @keyframes float3{0%{transform:translateY(0)}50%{transform:translateY(-27px) scale(1.05)}100%{transform:translateY(0)}}
    @keyframes float4{0%{transform:translateY(0)}50%{transform:translateY(-20px) scale(.95)}100%{transform:translateY(0)}}
    @keyframes float5{0%{transform:translateY(0)}50%{transform:translateY(21px) scale(1.02)}100%{transform:translateY(0)}}
    @keyframes float6{0%{transform:translateY(0)}50%{transform:translateY(-16px) scale(1.05)}100%{transform:translateY(0)}}
    @keyframes float7{0%{transform:translateY(0)}50%{transform:translateY(18px) scale(.98)}100%{transform:translateY(0)}}
    @keyframes float8{0%{transform:translateY(0)}50%{transform:translateY(-14px) scale(1.04)}100%{transform:translateY(0)}}
    @keyframes float9{0%{transform:translateY(0)}50%{transform:translateY(24px) scale(1.06)}100%{transform:translateY(0)}}
    @keyframes float10{0%{transform:translateY(0)}50%{transform:translateY(-22px) scale(1.01)}100%{transform:translateY(0)}}
    @keyframes float11{0%{transform:translateY(0)}50%{transform:translateY(15px) scale(1.06)}100%{transform:translateY(0)}}
    @keyframes float12{0%{transform:translateY(0)}50%{transform:translateY(-18px) scale(1.03)}100%{transform:translateY(0)}}
    @keyframes float13{0%{transform:translateY(0)}50%{transform:translateY(24px) scale(1.04)}100%{transform:translateY(0)}}
    @keyframes float14{0%{transform:translateY(0)}50%{transform:translateY(-20px) scale(1.07)}100%{transform:translateY(0)}}
    @keyframes float15{0%{transform:translateY(0)}50%{transform:translateY(11px) scale(.94)}100%{transform:translateY(0)}}
    @keyframes float16{0%{transform:translateY(0)}50%{transform:translateY(-19px) scale(1.03)}100%{transform:translateY(0)}}
    @keyframes float17{0%{transform:translateY(0)}50%{transform:translateY(16px) scale(1.01)}100%{transform:translateY(0)}}
    @keyframes float18{0%{transform:translateY(0)}50%{transform:translateY(-25px) scale(1.05)}100%{transform:translateY(0)}}

    .form-first .row{flex-direction:row}
    .info-first .row{flex-direction:row-reverse}

    @media (max-width:575.98px){
      body{overflow-x:hidden;overflow-y:auto;padding:16px 12px calc(16px + env(safe-area-inset-bottom))}
      body.d-flex{align-items:flex-start!important}
      .card{margin:8vh auto 0;max-width:420px}
      .modern-title,.modern-title-register{font-size:clamp(1.6rem,8vw,2.2rem)}
      .bg-icon{width:36px;opacity:.12}
      .info{border-left:0;border-top:1px solid #2a2a2a;padding:16px}
      .stat-grid{grid-template-columns:repeat(2,1fr)}
    }
  </style>
</head>
<body class="d-flex justify-content-center align-items-center form-first"> <!-- form-first = form solda, info-first = info solda -->
  <!-- Arka plan ikonları -->
  <div class="animated-social-bg">
    <img src="{{ url_for('static', filename='linkedin.png') }}" class="bg-icon icon1" alt="">
    <img src="{{ url_for('static', filename='youtube.png') }}" class="bg-icon icon2" alt="">
    <img src="{{ url_for('static', filename='twitter.png') }}" class="bg-icon icon3" alt="">
    <img src="{{ url_for('static', filename='9gag.png') }}" class="bg-icon icon4" alt="">
    <img src="{{ url_for('static', filename='imo.png') }}" class="bg-icon icon5" alt="">
    <img src="{{ url_for('static', filename='discord.png') }}" class="bg-icon icon6" alt="">
    <img src="{{ url_for('static', filename='goodreads.png') }}" class="bg-icon icon7" alt="">
    <img src="{{ url_for('static', filename='twitch.png') }}" class="bg-icon icon8" alt="">
    <img src="{{ url_for('static', filename='wechat.png') }}" class="bg-icon icon9" alt="">
    <img src="{{ url_for('static', filename='swift.png') }}" class="bg-icon icon10" alt="">
    <img src="{{ url_for('static', filename='vkontakte.png') }}" class="bg-icon icon11" alt="">
    <img src="{{ url_for('static', filename='envato.png') }}" class="bg-icon icon12" alt="">
    <img src="{{ url_for('static', filename='reddit.png') }}" class="bg-icon icon13" alt="">
    <img src="{{ url_for('static', filename='facebook.png') }}" class="bg-icon icon14" alt="">
    <img src="{{ url_for('static', filename='instagram.png') }}" class="bg-icon icon15" alt="">
    <img src="{{ url_for('static', filename='foursquare.png') }}" class="bg-icon icon16" alt="">
    <img src="{{ url_for('static', filename='whatsapp.png') }}" class="bg-icon icon17" alt="">
    <img src="{{ url_for('static', filename='klout.png') }}" class="bg-icon icon18" alt="">
  </div>

  <div class="card shadow p-0">
    <div class="row g-0 align-items-stretch">
      <!-- FORM -->
      <div class="col-12 col-md-6 p-4">
        <img src="{{ url_for('static', filename='logo.png') }}" alt="Logo" class="logo-img">
        <div class="text-center">
          <span class="modern-title">BAYBAYİM</span>
          <span class="modern-title-register">KAYIT</span>
        </div>

        {% with messages = get_flashed_messages() %}
          {% if messages %}
            {% for message in messages %}
              <div class="custom-alert">{{ message }}</div>
            {% endfor %}
          {% endif %}
        {% endwith %}

        {% if not sent %}
          <form method="post">
            <div class="mb-2">
              <label class="form-label">Kullanıcı Adı</label>
              <input name="username" class="form-control" required>
            </div>
            <div class="mb-2">
              <label class="form-label">Şifre</label>
              <input name="password" type="password" class="form-control" required>
            </div>
            <div class="mb-3">
              <label class="form-label">E-Posta</label>
              <input name="email" type="email" class="form-control" required>
            </div>
            <button class="btn btn-success w-100 mb-2">Kayıt Ol</button>
          </form>
        {% else %}
          <form method="post">
            <div class="mb-3">
              <label class="form-label">E-postana gelen kod</label>
              <input name="verify_code" class="form-control" required>
            </div>
            <button class="btn btn-primary w-100 mb-2">Kodu Doğrula</button>
          </form>
          <form method="post" action="/reset-registration" class="text-center">
            <button type="submit" class="btn btn-link btn-sm text-decoration-none text-danger">Kayıt İşleminden Vazgeç</button>
          </form>
        {% endif %}
        <div class="text-center mt-2">
          <a href="/" class="btn btn-link btn-sm text-decoration-none">Giriş Yap</a>
        </div>

        <!-- === FORM ALTI: Sayaç + Güven + SSS (Login/Register aynı) === -->
        <div class="divider"></div>

        <div class="stat-grid" aria-label="Panel istatistikleri">
          <div class="stat-card">
            <div class="stat-label">Son 24 saatte sipariş</div>
            <div class="stat-val" data-count="12438"></div>
          </div>
          <div class="stat-card">
            <div class="stat-label">Ortalama başlangıç</div>
            <div class="stat-val" data-count="10" data-suffix=" DK"></div>
          </div>
          <div class="stat-card">
            <div class="stat-label">Aktif Çalışan Servis</div>
            <div class="stat-val" data-count="11"></div>
          </div>
          <div class="stat-card">
            <div class="stat-label">Refill başarı oranı</div>
            <div class="stat-val" data-count="98" data-suffix="%"></div>
          </div>
        </div>

        <div class="trust-row" aria-label="Güven rozetleri">
          <span class="trust-badge"><span class="trust-dot"></span> 7/24 Otomatik</span>
          <span class="trust-badge"><span class="trust-dot"></span> Şifre İstemeyiz</span>
          <span class="trust-badge"><span class="trust-dot"></span> Canlı Destek</span>
        </div>

        <div class="divider"></div>

        <div class="accordion accordion-flush" id="faqAccordionReg">
          <div class="accordion-item">
            <h2 class="accordion-header" id="rq1">
              <button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#ra1" aria-expanded="false" aria-controls="ra1">
                Whatsapp İle İrtabata geçin !
              </button>
            </h2>
            <div id="ra1" class="accordion-collapse collapse" aria-labelledby="rq1" data-bs-parent="#faqAccordionReg">
              <div class="accordion-body">7/24 Canlı desteğimiz var anlık yazabilirsiniz.</div>
            </div>
          </div>
        </div>
        <!-- === /FORM ALTI === -->
      </div>

      <!-- BİLGİ PANELİ (GİRİŞ ile AYNIDIR) -->
      <div class="col-12 col-md-6 info">
        <h3>Neye giriş yapıyorsun?</h3>
        <p>Baybayim; <strong>SMM Panel</strong> altyapısıyla Instagram, TikTok, YouTube, Twitter/X ve daha fazlasında
          <strong>takipçi</strong>, <strong>beğeni</strong>, <strong>izlenme</strong>, <strong>yorum</strong> gibi
          hizmetleri <em>hızlı, otomatik ve güvenli</em> şekilde sunar. Panel <strong>7/24</strong> açıktır; siparişler saniyeler içinde işleme alınır.</p>

        <div class="mb-2">
          <span class="badge-soft">Instagram • Takipçi / Beğeni / Reel izlenme</span>
          <span class="badge-soft">TikTok • İzlenme / Canlı izleyici</span>
          <span class="badge-soft">YouTube • İzlenme / Abone / Yorum</span>
        </div>

        <h3 class="mt-3">Nasıl çalışır?</h3>

        <div class="step"><span class="num">1</span><div><strong>Hesabını oluştur / giriş yap</strong><br><span class="tiny">E-posta doğrulaması ile güvence.</span></div></div>
        <div class="step"><span class="num">2</span><div><strong>Bakiye ekle</strong><br><span class="tiny">Desteklenen yöntemlerle güvenli ödeme.</span></div></div>
        <div class="step"><span class="num">3</span><div><strong>Hizmeti seç & linki gir</strong><br><span class="tiny">Siparişin otomatik başlar; hız ve kapsam açıklamada yazar.</span></div></div>

        <h3 class="mt-3">Neden Baybayim?</h3>
        <ul class="tiny mb-3">
          <li>⚡ Anında otomatik teslimat & 7/24 panel</li>
          <li>🛡️ <strong>Şifreni asla istemeyiz</strong>; gizlilik ve sipariş koruması</li>
          <li>🎯 Yerli/gerçek/karışık gibi kalite seçenekleri</li>
          <li>💬 Canlı destek ve detaylı sipariş takibi</li>
        </ul>

        <h3 class="mt-3">Önemli notlar</h3>
        <ul class="tiny">
          <li>Kullanıcı adı veya içerik bağlantısı yeterlidir; şifre paylaşılmaz.</li>
          <li>Düşüşlerde telafi sunulan servislerde otomatik telafi işler.</li>
        </ul>

        <p class="tiny mt-3">Hesabın varsa <a href="/" class="link-light text-decoration-underline">giriş yap</a>, yoksa 30 sn’de kaydol ve bonusunu kap 🚀</p>
      </div>
    </div>
  </div>

  <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
  <script>
    // Basit sayaç animasyonu (görününce başlar) – Login ile aynı
    (function(){
      const els = document.querySelectorAll('.stat-val');
      if(!('IntersectionObserver' in window)){ els.forEach(e=>animate(e)); return; }
      const io = new IntersectionObserver((entries)=>{
        entries.forEach(ent=>{
          if(ent.isIntersecting && !ent.target.dataset.done){
            animate(ent.target); ent.target.dataset.done = '1';
          }
        });
      },{threshold:0.6});
      els.forEach(el=>io.observe(el));

      function animate(el){
        const target = parseFloat(el.dataset.count||'0');
        const suffix = el.dataset.suffix || '';
        const duration = 1400;
        let start;
        function step(ts){
          if(!start) start = ts;
          const p = Math.min((ts-start)/duration,1);
          const val = Math.floor(target*p);
          el.textContent = val.toLocaleString('tr-TR') + suffix;
          if(p<1) requestAnimationFrame(step);
        }
        requestAnimationFrame(step);
      }
    })();
  </script>
</body>
</html>
"""

HTML_SERVICES_MANAGE = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <link rel="icon" href="{{ url_for('static', filename='favicon.ico') }}" type="image/x-icon">
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Servisleri Yönet</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"/>
  <style>
    /* ---- Temel düzen ---- */
    :root{
      --card-bg:#1f1f1f;
      --field-bg:#2e2e2e;
      --border:#444;
      --thead:#242424;
    }
    html, body { height: 100%; }
    body {
      margin: 0;
      min-height: 100vh;
      background: linear-gradient(-45deg, #1e1e1e, #2c2f34, #1e1e1e, #000000);
      background-size: 400% 400%;
      animation: gradientBG 15s ease infinite;
      color: #fff;
      /* Eskiden overflow:hidden idi — sayfayı taşırıyordu */
      overflow: auto;
      position: relative;
    }
    @keyframes gradientBG {
      0% {background-position: 0% 50%;}
      50% {background-position: 100% 50%;}
      100% {background-position: 0% 50%;}
    }

    .card {
      background-color: var(--card-bg);
      color: #fff;
      border-radius: 18px;
      z-index: 2;
      position: relative;
      /* Tam genişlik: kart artık ekrana sığıyor */
      width: 100%;
      max-width: 100%;
      overflow: visible;
    }

    .form-control, .form-control-sm, .form-select, .form-select-sm {
      background-color: var(--field-bg) !important;
      color: #f1f1f1 !important;
      border: 1px solid var(--border);
      box-shadow: none;
    }
    .form-control:focus, .form-control-sm:focus,
    .form-select:focus, .form-select-sm:focus {
      background-color: var(--field-bg) !important;
      color: #fff !important;
      border-color: #666;
      box-shadow: none;
    }
    .form-control::placeholder, .form-control-sm::placeholder { color: #aaa; }
    .btn { font-weight: 500; }
    a { color: #8db4ff; }
    a:hover { color: #fff; text-decoration: underline; }
    input[type=number]::-webkit-inner-spin-button,
    input[type=number]::-webkit-outer-spin-button { -webkit-appearance: none; margin: 0; }
    input[type=number] { -moz-appearance: textfield; appearance: textfield; }

    /* ---- Tablo düzeni: sığdırma ve kaydırma ---- */
    .table-wrap{
      /* Hem yatay hem dikey scroll: tablo kendi alanında taşsın */
      overflow: auto;
      -webkit-overflow-scrolling: touch;
      /* Kartın içinde ekrana göre yükseklik: */
      max-height: 68vh;
      border: 1px solid var(--border);
      border-radius: 12px;
    }
    .table-dark {
      margin: 0; /* wrap içinde boşluk olmasın */
    }
    .table-dark th, .table-dark td { color: #eee; vertical-align: middle; }
    .table-dark thead th{
      position: sticky;
      top: 0;
      z-index: 3;
      background: var(--thead);
    }
    /* Hücreleri kompakt tutalım */
    .table td, .table th { white-space: nowrap; }
    .table .form-control-sm, .table .form-select-sm{
      padding: .25rem .5rem;
      font-size: .875rem;
      min-width: 90px;
    }
    /* Geniş kolonlara makul bir minimum verelim */
    td[style*="width:220px"] { min-width: 220px; }
    td[style*="width:120px"] { min-width: 120px; }
    td[style*="width:80px"]  { min-width: 80px;  }

    /* ---- Sosyal medya hareketli arka plan ---- */
    .animated-social-bg {
      position: fixed;
      inset: 0;
      width: 100vw;
      height: 100vh;
      z-index: 0;
      pointer-events: none;
      overflow: hidden;
      user-select: none;
    }
    .bg-icon {
      position: absolute;
      width: 48px;
      opacity: 0.13;
      filter: blur(0.2px) drop-shadow(0 4px 24px #0008);
      animation-duration: 18s;
      animation-iteration-count: infinite;
      animation-timing-function: ease-in-out;
      user-select: none;
    }
    .icon1  { left: 10vw;  top: 13vh; animation-name: float1; }
    .icon2  { left: 72vw;  top: 22vh; animation-name: float2; }
    .icon3  { left: 23vw;  top: 67vh; animation-name: float3; }
    .icon4  { left: 70vw;  top: 75vh; animation-name: float4; }
    .icon5  { left: 48vw;  top: 45vh; animation-name: float5; }
    .icon6  { left: 81vw;  top: 15vh; animation-name: float6; }
    .icon7  { left: 17vw;  top: 40vh; animation-name: float7;}
    .icon8  { left: 61vw;  top: 55vh; animation-name: float8;}
    .icon9  { left: 33vw;  top: 24vh; animation-name: float9;}
    .icon10 { left: 57vw; top: 32vh; animation-name: float10;}
    .icon11 { left: 80vw; top: 80vh; animation-name: float11;}
    .icon12 { left: 8vw;  top: 76vh; animation-name: float12;}
    .icon13 { left: 19vw;  top: 22vh; animation-name: float13;}
    .icon14 { left: 38vw;  top: 18vh; animation-name: float14;}
    .icon15 { left: 27vw;  top: 80vh; animation-name: float15;}
    .icon16 { left: 45vw;  top: 82vh; animation-name: float16;}
    .icon17 { left: 88vw;  top: 55vh; animation-name: float17;}
    .icon18 { left: 89vw;  top: 28vh; animation-name: float18;}
    @keyframes float1  { 0%{transform:translateY(0);} 50%{transform:translateY(-34px) scale(1.09);} 100%{transform:translateY(0);} }
    @keyframes float2  { 0%{transform:translateY(0);} 50%{transform:translateY(20px) scale(0.97);} 100%{transform:translateY(0);} }
    @keyframes float3  { 0%{transform:translateY(0);} 50%{transform:translateY(-27px) scale(1.05);} 100%{transform:translateY(0);} }
    @keyframes float4  { 0%{transform:translateY(0);} 50%{transform:translateY(-20px) scale(0.95);} 100%{transform:translateY(0);} }
    @keyframes float5  { 0%{transform:translateY(0);} 50%{transform:translateY(21px) scale(1.02);} 100%{transform:translateY(0);} }
    @keyframes float6  { 0%{transform:translateY(0);} 50%{transform:translateY(-16px) scale(1.05);} 100%{transform:translateY(0);} }
    @keyframes float7  { 0%{transform:translateY(0);} 50%{transform:translateY(18px) scale(0.98);} 100%{transform:translateY(0);} }
    @keyframes float8  { 0%{transform:translateY(0);} 50%{transform:translateY(-14px) scale(1.04);} 100%{transform:translateY(0);} }
    @keyframes float9  { 0%{transform:translateY(0);} 50%{transform:translateY(24px) scale(1.06);} 100%{transform:translateY(0);} }
    @keyframes float10 { 0%{transform:translateY(0);} 50%{transform:translateY(-22px) scale(1.01);} 100%{transform:translateY(0);} }
    @keyframes float11 { 0%{transform:translateY(0);} 50%{transform:translateY(15px) scale(1.06);} 100%{transform:translateY(0);} }
    @keyframes float12 { 0%{transform:translateY(0);} 50%{transform:translateY(-18px) scale(1.03);} 100%{transform:translateY(0);} }
    @keyframes float13 { 0%{transform:translateY(0);} 50%{transform:translateY(24px) scale(1.04);} 100%{transform:translateY(0);} }
    @keyframes float14 { 0%{transform:translateY(0);} 50%{transform:translateY(-20px) scale(1.07);} 100%{transform:translateY(0);} }
    @keyframes float15 { 0%{transform:translateY(0);} 50%{transform:translateY(11px) scale(0.94);} 100%{transform:translateY(0);} }
    @keyframes float16 { 0%{transform:translateY(0);} 50%{transform:translateY(-19px) scale(1.03);} 100%{transform:translateY(0);} }
    @keyframes float17 { 0%{transform:translateY(0);} 50%{transform:translateY(16px) scale(1.01);} 100%{transform:translateY(0);} }
    @keyframes float18 { 0%{transform:translateY(0);} 50%{transform:translateY(-25px) scale(1.05);} 100%{transform:translateY(0);} }

    /* ---- Responsive ince ayar ---- */
    @media (max-width: 1200px){
      .table .form-control-sm, .table .form-select-sm{ min-width: 120px; }
      .table-wrap{ max-height: 60vh; }
    }
    @media (max-width: 768px){
      h3{ font-size:1.25rem; }
      .table-wrap{ max-height: 58vh; }
    }
  </style>
</head>
<body class="text-light">
  <!-- Sosyal medya hareketli arka plan -->
  <div class="animated-social-bg">
    <img src="{{ url_for('static', filename='linkedin.png') }}" class="bg-icon icon1">
    <img src="{{ url_for('static', filename='youtube.png') }}" class="bg-icon icon2">
    <img src="{{ url_for('static', filename='twitter.png') }}" class="bg-icon icon3">
    <img src="{{ url_for('static', filename='9gag.png') }}" class="bg-icon icon4">
    <img src="{{ url_for('static', filename='imo.png') }}" class="bg-icon icon5">
    <img src="{{ url_for('static', filename='discord.png') }}" class="bg-icon icon6">
    <img src="{{ url_for('static', filename='goodreads.png') }}" class="bg-icon icon7">
    <img src="{{ url_for('static', filename='twitch.png') }}" class="bg-icon icon8">
    <img src="{{ url_for('static', filename='wechat.png') }}" class="bg-icon icon9">
    <img src="{{ url_for('static', filename='swift.png') }}" class="bg-icon icon10">
    <img src="{{ url_for('static', filename='vkontakte.png') }}" class="bg-icon icon11">
    <img src="{{ url_for('static', filename='envato.png') }}" class="bg-icon icon12">
    <img src="{{ url_for('static', filename='reddit.png') }}" class="bg-icon icon13">
    <img src="{{ url_for('static', filename='facebook.png') }}" class="bg-icon icon14">
    <img src="{{ url_for('static', filename='instagram.png') }}" class="bg-icon icon15">
    <img src="{{ url_for('static', filename='foursquare.png') }}" class="bg-icon icon16">
    <img src="{{ url_for('static', filename='whatsapp.png') }}" class="bg-icon icon17">
    <img src="{{ url_for('static', filename='klout.png') }}" class="bg-icon icon18">
  </div>

  <div class="container-fluid py-4">
    <div class="card mx-auto">
      <div class="card-body">
        <h3 class="mb-3">Servisleri Yönet</h3>

        <!-- Kategori Oluştur / Listele -->
        <div class="row g-3 mb-3">
          <div class="col-lg-6">
            <div class="card">
              <div class="card-body">
                <h5 class="mb-3">Yeni Kategori Oluştur</h5>
                <form method="post" action="{{ url_for('manage_services') }}">
                  <div class="row g-2 align-items-end">
                    <div class="col-4 col-sm-3">
                      <label class="form-label">İkon/Emoji</label>
                      <input name="new_cat_icon" maxlength="8" class="form-control" placeholder="📁">
                    </div>
                    <div class="col-8 col-sm-6">
                      <label class="form-label">Kategori Adı</label>
                      <input name="new_cat_name" class="form-control" placeholder="Dijital Büyüme">
                    </div>
                    <div class="col-12 col-sm-3 d-grid">
                      <label class="form-label d-none d-sm-block">&nbsp;</label>
                      <button class="btn btn-success" type="submit" name="create_category" value="1">Ekle</button>
                    </div>
                  </div>
                </form>
              </div>
            </div>
          </div>

          <div class="col-lg-6">
            <div class="card">
              <div class="card-body">
                <h5 class="mb-3">Mevcut Kategoriler</h5>
                <ul class="list-group">
                  {% for c in categories %}
                    <li class="list-group-item d-flex justify-content-between align-items-center" style="background:#2e2e2e;color:#fff;border-color:#444;">
                      <span>{{ c.icon or "📁" }} {{ c.name }}</span>
                      <form method="post" action="{{ url_for('manage_services') }}" class="m-0">
                        <button class="btn btn-sm btn-outline-danger" name="delete_category" value="{{ c.id }}" onclick="return confirm('Bu kategoriyi silmek istiyor musun?');">Sil</button>
                      </form>
                    </li>
                  {% else %}
                    <li class="list-group-item" style="background:#2e2e2e;color:#fff;border-color:#444;">Kayıtlı kategori yok.</li>
                  {% endfor %}
                </ul>
              </div>
            </div>
          </div>
        </div>

        <!-- Tablo: responsive + sticky head + kendi içinde scroll -->
        <form method="post" action="{{ url_for('manage_services') }}">
          <div class="table-wrap table-responsive">
            <table class="table table-dark table-striped table-hover align-middle mb-0">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Servis</th>
                  <th>Açıklama</th>
                  <th>Fiyat (TL)</th>
                  <th>Min</th>
                  <th>Max</th>
                  <th>Kategori</th>
                  <th>Kaynak</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
              {% for s in services %}
                <tr>
                  <td>{{ s.id }}</td>
                  <td>
                    <input name="name_{{s.id}}" class="form-control form-control-sm" value="{{ s.name }}" {% if s.id not in local_ids %}readonly{% endif %}>
                  </td>
                  <td>
                    <input name="desc_{{s.id}}" class="form-control form-control-sm" value="{{ s.description }}" {% if s.id not in local_ids %}readonly{% endif %}>
                  </td>
                  <td style="width:120px">
                    <input type="number" step="any" min="0" name="price_{{ s.id }}" class="form-control form-control-sm" value="{{ '{:.5f}'.format(s.price) if s.price is not none else '' }}">
                  </td>
                  <td style="width:80px">
                    {{ s.min_amount }}
                  </td>
                  <td style="width:120px">
                    <input name="max_{{s.id}}" type="number" min="{{ s.min_amount }}" class="form-control form-control-sm" value="{{ s.max_amount }}" {% if s.id not in local_ids %}readonly{% endif %}>
                  </td>
                  <td style="width:220px">
                    <select name="category_{{ s.id }}" class="form-select form-select-sm" {% if s.id not in local_ids %}disabled{% endif %}>
                      <option value="" {% if not s.category_id %}selected{% endif %}>— Kategori yok —</option>
                      {% for c in categories %}
                        <option value="{{ c.id }}" {% if s.category_id == c.id %}selected{% endif %}>
                          {{ c.icon or "📁" }} {{ c.name }}
                        </option>
                      {% endfor %}
                    </select>
                  </td>
                  <td>
                    {% if s.id in local_ids %}
                      <span class="badge bg-success">Local</span>
                    {% else %}
                      <span class="badge bg-warning text-dark">External</span>
                    {% endif %}
                  </td>
                  <td>
                    {% if s.id not in local_ids %}
                      <button type="submit" name="add_external" value="{{ s.id }}" class="btn btn-sm btn-primary">Veritabanına Ekle</button>
                    {% endif %}
                  </td>
                </tr>
              {% endfor %}
              </tbody>
            </table>
          </div>

          <div class="d-grid mt-3">
            <button class="btn btn-success" type="submit" name="save_changes" value="1">Düzenlemeleri Kaydet</button>
          </div>
        </form>

        <div class="mt-3">
          <a href="{{ url_for('panel') }}" class="btn btn-secondary w-100">Panele Dön</a>
        </div>
      </div>
    </div>
  </div>
</body>
</html>
"""

HTML_BAKIYE_YUKLE = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <link rel="icon" href="{{ url_for('static', filename='favicon.ico') }}" type="image/x-icon">
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Bakiye Yükle</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"/>
  <style>
    /* Tutarlı mobil metin ölçekleme */
    html { -webkit-text-size-adjust: 100%; }

    /* Sadece number inputlardaki okları kaldırır */
    input[type="number"]::-webkit-inner-spin-button,
    input[type="number"]::-webkit-outer-spin-button {
      -webkit-appearance: none;
      margin: 0;
    }
    input[type="number"] {
      -moz-appearance: textfield; /* Firefox */
    }

    body {
      margin: 0;
      min-height: 100vh;
      background: linear-gradient(-45deg, #121212, #1e1e1e, #212121, #000000);
      background-size: 400% 400%;
      animation: gradientBG 12s ease infinite;
      color: #fff;
      overflow: hidden;
      position: relative;
    }
    @supports (height: 100dvh) {
      /* Mobil toolbar dalgalanmasına dayanıklı yükseklik */
      body { min-height: 100dvh; }
    }

    @keyframes gradientBG {
      0% {background-position: 0% 50%;}
      50% {background-position: 100% 50%;}
      100% {background-position: 0% 50%;}
    }
    .card {
      background: rgba(20, 20, 20, 0.95);
      border-radius: 14px;
      box-shadow: 0 4px 20px rgba(0,0,0,0.3);
      color: #f1f1f1;
      z-index: 2;
      position: relative;
    }
    h3 {
      color: #fff;
      font-weight: 600;
    }
    .form-label {
      color: #b8d0ee;
      font-weight: 500;
    }
    .form-control {
      background-color: #1e1e1e;
      border-color: #2186eb;
      color: #fff;
    }
    .form-control:focus {
      background-color: #1e1e1e;
      border-color: #62b3ff;
      color: #fff;
      box-shadow: none;
    }
    .form-control::placeholder { color: #aaa; }
    .btn-shopier {
      background: linear-gradient(90deg,#1b56e4,#1db3ff);
      color: #fff;
      font-weight: 600;
      border-radius: 8px;
      box-shadow: 0 2px 16px #1264a966;
      border: none;
      padding: 10px 28px;
      transition: background 0.2s, transform 0.1s;
      font-size: 1.1rem;
      letter-spacing: 0.5px;
    }
    .btn-shopier:hover {
      background: linear-gradient(90deg,#1db3ff,#1b56e4);
      color: #fff;
      transform: scale(1.04);
    }
    .msgbox {
      margin-bottom: 16px;
      padding: 9px 14px;
      border-radius: 8px;
      font-size: 1.05rem;
      background: rgba(0,150,255,0.10);
      border-left: 4px solid #2186eb;
      color: #8ecfff;
      font-weight: 500;
      box-shadow: 0 1px 6px #151a2266;
    }
    /* Sosyal medya hareketli arka plan aynen! */
    .animated-social-bg {
      position: fixed;
      inset: 0;
      width: 100vw;
      height: 100vh;
      z-index: 0;
      pointer-events: none;
      overflow: hidden;
      user-select: none;
    }
    .bg-icon {
      position: absolute;
      width: 48px;
      opacity: 0.13;
      filter: blur(0.2px) drop-shadow(0 4px 24px #0008);
      animation-duration: 18s;
      animation-iteration-count: infinite;
      animation-timing-function: ease-in-out;
      user-select: none;
    }
    .icon1  { left: 10vw;  top: 13vh; animation-name: float1; }
    .icon2  { left: 72vw;  top: 22vh; animation-name: float2; }
    .icon3  { left: 23vw;  top: 67vh; animation-name: float3; }
    .icon4  { left: 70vw;  top: 75vh; animation-name: float4; }
    .icon5  { left: 48vw;  top: 45vh; animation-name: float5; }
    .icon6  { left: 81vw;  top: 15vh; animation-name: float6; }
    .icon7  { left: 17vw;  top: 40vh; animation-name: float7;}
    .icon8  { left: 61vw;  top: 55vh; animation-name: float8;}
    .icon9  { left: 33vw;  top: 24vh; animation-name: float9;}
    .icon10 { left: 57vw; top: 32vh; animation-name: float10;}
    .icon11 { left: 80vw; top: 80vh; animation-name: float11;}
    .icon12 { left: 8vw;  top: 76vh; animation-name: float12;}
    .icon13 { left: 19vw;  top: 22vh; animation-name: float13;}
    .icon14 { left: 38vw;  top: 18vh; animation-name: float14;}
    .icon15 { left: 27vw;  top: 80vh; animation-name: float15;}
    .icon16 { left: 45vw;  top: 82vh; animation-name: float16;}
    .icon17 { left: 88vw;  top: 55vh; animation-name: float17;}
    .icon18 { left: 89vw;  top: 28vh; animation-name: float18;}
    @keyframes float1  { 0%{transform:translateY(0);} 50%{transform:translateY(-34px) scale(1.09);} 100%{transform:translateY(0);} }
    @keyframes float2  { 0%{transform:translateY(0);} 50%{transform:translateY(20px) scale(0.97);} 100%{transform:translateY(0);} }
    @keyframes float3  { 0%{transform:translateY(0);} 50%{transform:translateY(-27px) scale(1.05);} 100%{transform:translateY(0);} }
    @keyframes float4  { 0%{transform:translateY(0);} 50%{transform:translateY(-20px) scale(0.95);} 100%{transform:translateY(0);} }
    @keyframes float5  { 0%{transform:translateY(0);} 50%{transform:translateY(21px) scale(1.02);} 100%{transform:translateY(0);} }
    @keyframes float6  { 0%{transform:translateY(0);} 50%{transform:translateY(-16px) scale(1.05);} 100%{transform:translateY(0);} }
    @keyframes float7  { 0%{transform:translateY(0);} 50%{transform:translateY(18px) scale(0.98);} 100%{transform:translateY(0);} }
    @keyframes float8  { 0%{transform:translateY(0);} 50%{transform:translateY(-14px) scale(1.04);} 100%{transform:translateY(0);} }
    @keyframes float9  { 0%{transform:translateY(0);} 50%{transform:translateY(24px) scale(1.06);} 100%{transform:translateY(0);} }
    @keyframes float10 { 0%{transform:translateY(0);} 50%{transform:translateY(-22px) scale(1.01);} 100%{transform:translateY(0);} }
    @keyframes float11 { 0%{transform:translateY(0);} 50%{transform:translateY(15px) scale(1.06);} 100%{transform:translateY(0);} }
    @keyframes float12 { 0%{transform:translateY(0);} 50%{transform:translateY(-18px) scale(1.03);} 100%{transform:translateY(0);} }
    @keyframes float13 { 0%{transform:translateY(0);} 50%{transform:translateY(24px) scale(1.04);} 100%{transform:translateY(0);} }
    @keyframes float14 { 0%{transform:translateY(0);} 50%{transform:translateY(-20px) scale(1.07);} 100%{transform:translateY(0);} }
    @keyframes float15 { 0%{transform:translateY(0);} 50%{transform:translateY(11px) scale(0.94);} 100%{transform:translateY(0);} }
    @keyframes float16 { 0%{transform:translateY(0);} 50%{transform:translateY(-19px) scale(1.03);} 100%{transform:translateY(0);} }
    @keyframes float17 { 0%{transform:translateY(0);} 50%{transform:translateY(16px) scale(1.01);} 100%{transform:translateY(0);} }
    @keyframes float18 { 0%{transform:translateY(0);} 50%{transform:translateY(-25px) scale(1.05);} 100%{transform:translateY(0);} }
    /* Panele Dön butonu custom */
    .btn-paneldon {
      display: block;
      width: 100%;
      margin-top: 12px;
      padding: 10px 0;
      background: #757b80;
      color: #fff;
      border: none;
      border-radius: 8px;
      font-size: 1.08rem;
      font-weight: 500;
      letter-spacing: 0.2px;
      transition: background 0.2s, transform 0.1s;
      text-align: center;
      text-decoration: none;
      box-shadow: 0 2px 12px #0006;
      cursor: pointer;
    }
    .btn-paneldon:hover {
      background: #5b5e64;
      color: #fff;
      transform: scale(1.02);
      text-decoration: none;
    }

    /* ===== SADECE MOBİL DOKUNUŞLAR ===== */
    @media (max-width: 575.98px) {
      /* Scroll aktif + notch güvenli alan */
      body {
        overflow-x: hidden;
        overflow-y: auto;
        padding: 16px 12px calc(16px + env(safe-area-inset-bottom));
      }
      .container { padding-left: 0; padding-right: 0; }
      .card { margin-top: 10vh; border-radius: 16px; }
      .card .card-body { padding: 1.1rem; }

      /* Başlık ve kontroller akışkan boyutta */
      h3 { font-size: clamp(1.2rem, 6vw, 1.5rem); }
      .form-control { min-height: 44px; font-size: 1rem; }
      .btn-shopier { min-height: 44px; font-size: 1rem; }

      /* Arka plan ikonlarını küçült */
      .bg-icon { width: 36px; opacity: 0.12; }
    }

    /* Kısa ekranlarda üst boşluğu azalt */
    @media (max-height: 640px) and (orientation: portrait) {
      .card { margin-top: 24px; }
    }
  </style>
</head>
<body>
  <div class="animated-social-bg">
    <img src="{{ url_for('static', filename='linkedin.png') }}" class="bg-icon icon1">
    <img src="{{ url_for('static', filename='youtube.png') }}" class="bg-icon icon2">
    <img src="{{ url_for('static', filename='twitter.png') }}" class="bg-icon icon3">
    <img src="{{ url_for('static', filename='9gag.png') }}" class="bg-icon icon4">
    <img src="{{ url_for('static', filename='imo.png') }}" class="bg-icon icon5">
    <img src="{{ url_for('static', filename='discord.png') }}" class="bg-icon icon6">
    <img src="{{ url_for('static', filename='goodreads.png') }}" class="bg-icon icon7">
    <img src="{{ url_for('static', filename='twitch.png') }}" class="bg-icon icon8">
    <img src="{{ url_for('static', filename='wechat.png') }}" class="bg-icon icon9">
    <img src="{{ url_for('static', filename='swift.png') }}" class="bg-icon icon10">
    <img src="{{ url_for('static', filename='vkontakte.png') }}" class="bg-icon icon11">
    <img src="{{ url_for('static', filename='envato.png') }}" class="bg-icon icon12">
    <img src="{{ url_for('static', filename='reddit.png') }}" class="bg-icon icon13">
    <img src="{{ url_for('static', filename='facebook.png') }}" class="bg-icon icon14">
    <img src="{{ url_for('static', filename='instagram.png') }}" class="bg-icon icon15">
    <img src="{{ url_for('static', filename='foursquare.png') }}" class="bg-icon icon16">
    <img src="{{ url_for('static', filename='whatsapp.png') }}" class="bg-icon icon17">
    <img src="{{ url_for('static', filename='klout.png') }}" class="bg-icon icon18">
  </div>
  <div class="container py-5">
    <div class="card mx-auto" style="max-width: 500px;">
      <div class="card-body p-4">
        <h3 class="mb-4 text-center">Bakiye Yükle</h3>
        {% if msg %}
          <div class="msgbox">{{ msg }}</div>
        {% endif %}
        <form method="post">
          <div class="mb-3">
            <label for="amount" class="form-label">Yüklemek istediğin tutar (₺):</label>
            <input type="number" min="1" step="1" class="form-control" id="amount" name="amount" placeholder="" required>
          </div>
          <button type="submit" class="btn btn-shopier w-100 mt-2">
            <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" fill="#fff" style="margin-right:8px;margin-top:-3px" viewBox="0 0 24 24"><path d="M2 5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2zm2 0v2h16V5zm16 14v-8H4v8zm-4-3h-4a1 1 0 1 0 0 2h4a1 1 0 1 0 0-2z"/></svg>
            PayTR ile Öde
          </button>
        </form>
        <div class="mt-4 text-center small text-secondary">
          <span style="color:#8ecfff">⚡️ Bakiye yüklemelerin anında hesabına yansır.</span><br>
        </div>
        <a href="/panel" class="btn-paneldon mt-2">Panele Dön</a>
      </div>
    </div>
  </div>
</body>
</html>
<!-- İletişim ve adres bilgisi eklendi -->
<div class="text-center mt-5" style="font-size: 0.9rem; color: #aaa;">
  <hr style="border-color: #333;">
  <p><strong>İletişim:</strong> 📩 kuzenlertv6996@gmail.com – 📞 +44 7927 573543 - 📸 @baybayimofficial</p>
    <p><strong>📍 Adres:</strong> Mustafa Kemal Paşa Mahallesi, Lale Sokak No:110
</div>
</body>
</html>
"""

HTML_SERVICES = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <link rel="icon" href="{{ url_for('static', filename='favicon.ico') }}" type="image/x-icon">
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Servisler & Fiyat Listesi</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"/>
  <style>
    /* Tutarlı mobil metin ölçekleme */
    html { -webkit-text-size-adjust: 100%; }

    body {
      margin: 0;
      min-height: 100vh;
      background: linear-gradient(-45deg, #121212, #1e1e1e, #212121, #000000);
      background-size: 400% 400%;
      animation: gradientBG 12s ease infinite;
      color: #fff;
      overflow: hidden;
      position: relative;
    }
    @supports (height: 100dvh) {
      /* Mobil toolbar dalgalanmasına dayanıklı yükseklik */
      body { min-height: 100dvh; }
    }

    @keyframes gradientBG {
      0% {background-position: 0% 50%;}
      50% {background-position: 100% 50%;}
      100% {background-position: 0% 50%;}
    }

    .editing .text { display: none; }
    .editing .inp  { display: inline-block !important; width: 100%; }

    .card {
      background: rgba(20, 20, 20, 0.9);
      border-radius: 14px;
      box-shadow: 0 4px 20px rgba(0,0,0,0.3);
      color: #f1f1f1;
      z-index: 2;
      position: relative;
    }

    .card h3 {
      color: #ffffff;
    }

    .form-control, .form-select {
      background-color: #1e1e1e;
      border-color: #444;
      color: #fff;
    }

    .form-control:focus, .form-select:focus {
      background-color: #1e1e1e;
      border-color: #2186eb;
      color: #fff;
      box-shadow: none;
    }

    .form-control::placeholder {
      color: #aaa;
    }

    .table-dark {
      background-color: #1f1f1f;
    }

    .table-dark td, .table-dark th {
      color: #e6e6e6;
    }

    .btn-outline-info {
      color: #8ecfff;
      border-color: #2186eb;
    }

    .btn-outline-info:hover {
      background-color: #2186eb;
      color: white;
    }

    .btn-success, .btn-secondary {
      font-weight: 500;
    }

    input::placeholder {
      color: #bbb;
    }

    /* -- Sosyal medya hareketli arka plan -- */
    .animated-social-bg {
      position: fixed;
      inset: 0;
      width: 100vw;
      height: 100vh;
      z-index: 0;
      pointer-events: none;
      overflow: hidden;
      user-select: none;
    }
    .bg-icon {
      position: absolute;
      width: 48px;
      opacity: 0.13;
      filter: blur(0.2px) drop-shadow(0 4px 24px #0008);
      animation-duration: 18s;
      animation-iteration-count: infinite;
      animation-timing-function: ease-in-out;
      user-select: none;
    }
    /* 18 farklı pozisyon ve animasyon */
    .icon1  { left: 10vw;  top: 13vh; animation-name: float1; }
    .icon2  { left: 72vw;  top: 22vh; animation-name: float2; }
    .icon3  { left: 23vw;  top: 67vh; animation-name: float3; }
    .icon4  { left: 70vw;  top: 75vh; animation-name: float4; }
    .icon5  { left: 48vw;  top: 45vh; animation-name: float5; }
    .icon6  { left: 81vw;  top: 15vh; animation-name: float6; }
    .icon7  { left: 17vw;  top: 40vh; animation-name: float7;}
    .icon8  { left: 61vw;  top: 55vh; animation-name: float8;}
    .icon9  { left: 33vw;  top: 24vh; animation-name: float9;}
    .icon10 { left: 57vw; top: 32vh; animation-name: float10;}
    .icon11 { left: 80vw; top: 80vh; animation-name: float11;}
    .icon12 { left: 8vw;  top: 76vh; animation-name: float12;}
    .icon13 { left: 19vw;  top: 22vh; animation-name: float13;}
    .icon14 { left: 38vw;  top: 18vh; animation-name: float14;}
    .icon15 { left: 27vw;  top: 80vh; animation-name: float15;}
    .icon16 { left: 45vw;  top: 82vh; animation-name: float16;}
    .icon17 { left: 88vw;  top: 55vh; animation-name: float17;}
    .icon18 { left: 89vw;  top: 28vh; animation-name: float18;}
    @keyframes float1  { 0%{transform:translateY(0);} 50%{transform:translateY(-34px) scale(1.09);} 100%{transform:translateY(0);} }
    @keyframes float2  { 0%{transform:translateY(0);} 50%{transform:translateY(20px) scale(0.97);} 100%{transform:translateY(0);} }
    @keyframes float3  { 0%{transform:translateY(0);} 50%{transform:translateY(-27px) scale(1.05);} 100%{transform:translateY(0);} }
    @keyframes float4  { 0%{transform:translateY(0);} 50%{transform:translateY(-20px) scale(0.95);} 100%{transform:translateY(0);} }
    @keyframes float5  { 0%{transform:translateY(0);} 50%{transform:translateY(21px) scale(1.02);} 100%{transform:translateY(0);} }
    @keyframes float6  { 0%{transform:translateY(0);} 50%{transform:translateY(-16px) scale(1.05);} 100%{transform:translateY(0);} }
    @keyframes float7  { 0%{transform:translateY(0);} 50%{transform:translateY(18px) scale(0.98);} 100%{transform:translateY(0);} }
    @keyframes float8  { 0%{transform:translateY(0);} 50%{transform:translateY(-14px) scale(1.04);} 100%{transform:translateY(0);} }
    @keyframes float9  { 0%{transform:translateY(0);} 50%{transform:translateY(24px) scale(1.06);} 100%{transform:translateY(0);} }
    @keyframes float10 { 0%{transform:translateY(0);} 50%{transform:translateY(-22px) scale(1.01);} 100%{transform:translateY(0);} }
    @keyframes float11 { 0%{transform:translateY(0);} 50%{transform:translateY(15px) scale(1.06);} 100%{transform:translateY(0);} }
    @keyframes float12 { 0%{transform:translateY(0);} 50%{transform:translateY(-18px) scale(1.03);} 100%{transform:translateY(0);} }
    @keyframes float13 { 0%{transform:translateY(0);} 50%{transform:translateY(24px) scale(1.04);} 100%{transform:translateY(0);} }
    @keyframes float14 { 0%{transform:translateY(0);} 50%{transform:translateY(-20px) scale(1.07);} 100%{transform:translateY(0);} }
    @keyframes float15 { 0%{transform:translateY(0);} 50%{transform:translateY(11px) scale(0.94);} 100%{transform:translateY(0);} }
    @keyframes float16 { 0%{transform:translateY(0);} 50%{transform:translateY(-19px) scale(1.03);} 100%{transform:translateY(0);} }
    @keyframes float17 { 0%{transform:translateY(0);} 50%{transform:translateY(16px) scale(1.01);} 100%{transform:translateY(0);} }
    @keyframes float18 { 0%{transform:translateY(0);} 50%{transform:translateY(-25px) scale(1.05);} 100%{transform:translateY(0);} }

    /* ===== SADECE MOBİL DOKUNUŞLAR ===== */
    @media (max-width: 575.98px) {
      /* Scroll aktif + notch güvenli alan */
      body {
        overflow-x: hidden;
        overflow-y: auto;
        padding: 16px 12px calc(16px + env(safe-area-inset-bottom));
      }

      .container { padding-left: 0; padding-right: 0; }
      .card { margin-top: 10vh; border-radius: 16px; }
      .card .card-body { padding: 1.1rem; }

      /* Başlık ve arama satırı dikey stacksin */
      .card .d-flex.justify-content-between { 
        flex-direction: column; 
        align-items: stretch !important; 
        gap: .5rem; 
      }
      .card .d-flex.justify-content-between .d-flex { gap: .5rem; }
      #search { width: 100%; min-height: 44px; font-size: 1rem; }
      #editBtn { width: 100%; min-height: 44px; }

      /* Tablo kendi içinde yatay kaydırılabilir olsun */
      #tbl {
        display: block;
        width: 100%;
        overflow-x: auto;
        -webkit-overflow-scrolling: touch;
        white-space: nowrap;
      }
      #tbl thead th, #tbl tbody td { white-space: nowrap; }

      /* Arka plan ikonlarını küçült */
      .bg-icon { width: 36px; opacity: 0.12; }
      
      /* H3 akışkan boyut */
      h3 { font-size: clamp(1.2rem, 6vw, 1.5rem); }
    }

    /* Kısa ekranlarda üst boşluğu azalt */
    @media (max-height: 640px) and (orientation: portrait) {
      .card { margin-top: 24px; }
    }
  </style>
</head>
<body>
  <!-- Sosyal medya hareketli arka plan -->
  <div class="animated-social-bg">
    <img src="{{ url_for('static', filename='linkedin.png') }}" class="bg-icon icon1">
    <img src="{{ url_for('static', filename='youtube.png') }}" class="bg-icon icon2">
    <img src="{{ url_for('static', filename='twitter.png') }}" class="bg-icon icon3">
    <img src="{{ url_for('static', filename='9gag.png') }}" class="bg-icon icon4">
    <img src="{{ url_for('static', filename='imo.png') }}" class="bg-icon icon5">
    <img src="{{ url_for('static', filename='discord.png') }}" class="bg-icon icon6">
    <img src="{{ url_for('static', filename='goodreads.png') }}" class="bg-icon icon7">
    <img src="{{ url_for('static', filename='twitch.png') }}" class="bg-icon icon8">
    <img src="{{ url_for('static', filename='wechat.png') }}" class="bg-icon icon9">
    <img src="{{ url_for('static', filename='swift.png') }}" class="bg-icon icon10">
    <img src="{{ url_for('static', filename='vkontakte.png') }}" class="bg-icon icon11">
    <img src="{{ url_for('static', filename='envato.png') }}" class="bg-icon icon12">
    <img src="{{ url_for('static', filename='reddit.png') }}" class="bg-icon icon13">
    <img src="{{ url_for('static', filename='facebook.png') }}" class="bg-icon icon14">
    <img src="{{ url_for('static', filename='instagram.png') }}" class="bg-icon icon15">
    <img src="{{ url_for('static', filename='foursquare.png') }}" class="bg-icon icon16">
    <img src="{{ url_for('static', filename='whatsapp.png') }}" class="bg-icon icon17">
    <img src="{{ url_for('static', filename='klout.png') }}" class="bg-icon icon18">
  </div>
  <div class="container py-4">
    <div class="card mx-auto" style="max-width:900px">
      <div class="card-body">
        <h3>Servisler & Fiyat Listesi</h3>
        <div class="d-flex justify-content-between align-items-center mb-3">
          <div><strong>Toplam:</strong> {{ servisler|length }} servis</div>
          <div class="d-flex">
            <input id="search" class="form-control form-control-sm me-2" placeholder="Servis ara…">
            {% if user.role=='admin' %}
              <button id="editBtn" class="btn btn-outline-info btn-sm">Edit</button>
            {% endif %}
          </div>
        </div>

        {% if user.role=='admin' %}
        <form method="post" action="{{ url_for('services') }}">
        {% endif %}

          <table id="tbl" class="table table-dark table-striped">
            <thead>
              <tr>
                <th>Servis</th><th>Açıklama</th><th>Fiyat (TL)</th><th>Min</th><th>Max</th>
              </tr>
            </thead>
            <tbody>
            {% for s in servisler %}
              <tr class="{% if user.role=='admin' %}editable{% endif %}">
                <td>
                  <span class="text">{{ s.name }}</span>
                  {% if user.role=='admin' %}
                  <input type="text" name="name_{{s.id}}" value="{{ s.name }}"
                         class="form-control form-control-sm inp" style="display:none">
                  {% endif %}
                </td>
                <td>
                  <span class="text">{{ s.description }}</span>
                  {% if user.role=='admin' %}
                  <input type="text" name="desc_{{s.id}}" value="{{ s.description }}"
                         class="form-control form-control-sm inp" style="display:none">
                  {% endif %}
                </td>
                <td>
                  <span class="text">{{"%.2f"|format(s.price)}}</span>
                  {% if user.role=='admin' %}
                  <input type="number" step="0.01" min="0.01" name="price_{{s.id}}"
                         value="{{"%.2f"|format(s.price)}}"
                         class="form-control form-control-sm inp" style="display:none">
                  {% endif %}
                </td>
                <td>{{ s.min_amount }}</td>
                <td>{{ s.max_amount }}</td>
              </tr>
            {% endfor %}
            </tbody>
          </table>

        {% if user.role=='admin' %}
          <div id="btns" class="mt-2" style="display:none">
            <button type="submit" class="btn btn-success btn-sm me-2">Kaydet</button>
            <button type="button" id="cancel" class="btn btn-secondary btn-sm">İptal</button>
          </div>
        </form>
        {% endif %}

      </div>
    </div>
  </div>

  <script>
    // Arama
    document.getElementById('search').addEventListener('input', function(){
      const q = this.value.toLowerCase();
      document.querySelectorAll('#tbl tbody tr').forEach(tr=>{
        tr.style.display = tr.innerText.toLowerCase().includes(q) ? '' : 'none';
      });
    });

    {% if user.role=='admin' %}
    // Edit modu
    let editing=false;
    const card=document.querySelector('.card'),
          editBtn=document.getElementById('editBtn'),
          cancelBtn=document.getElementById('cancel'),
          btns=document.getElementById('btns');

    editBtn.addEventListener('click', e=>{
      e.preventDefault();
      editing = !editing;
      card.classList.toggle('editing', editing);
      btns.style.display = editing ? 'block' : 'none';
      editBtn.textContent = editing ? 'Stop' : 'Edit';
    });
    cancelBtn.addEventListener('click', ()=>location.reload());
    {% endif %}
  </script>
  <!-- Sosyal medya hareketli arka plan -->
  <div class="animated-social-bg">
    <img src="{{ url_for('static', filename='linkedin.png') }}" class="bg-icon icon1">
    <img src="{{ url_for('static', filename='youtube.png') }}" class="bg-icon icon2">
    <img src="{{ url_for('static', filename='twitter.png') }}" class="bg-icon icon3">
    <img src="{{ url_for('static', filename='9gag.png') }}" class="bg-icon icon4">
    <img src="{{ url_for('static', filename='imo.png') }}" class="bg-icon icon5">
    <img src="{{ url_for('static', filename='discord.png') }}" class="bg-icon icon6">
    <img src="{{ url_for('static', filename='goodreads.png') }}" class="bg-icon icon7">
    <img src="{{ url_for('static', filename='twitch.png') }}" class="bg-icon icon8">
    <img src="{{ url_for('static', filename='wechat.png') }}" class="bg-icon icon9">
    <img src="{{ url_for('static', filename='swift.png') }}" class="bg-icon icon10">
    <img src="{{ url_for('static', filename='vkontakte.png') }}" class="bg-icon icon11">
    <img src="{{ url_for('static', filename='envato.png') }}" class="bg-icon icon12">
    <img src="{{ url_for('static', filename='reddit.png') }}" class="bg-icon icon13">
    <img src="{{ url_for('static', filename='facebook.png') }}" class="bg-icon icon14">
    <img src="{{ url_for('static', filename='instagram.png') }}" class="bg-icon icon15">
    <img src="{{ url_for('static', filename='foursquare.png') }}" class="bg-icon icon16">
    <img src="{{ url_for('static', filename='whatsapp.png') }}" class="bg-icon icon17">
    <img src="{{ url_for('static', filename='klout.png') }}" class="bg-icon icon18">
  </div>
</body>
</html>
"""

HTML_ADMIN_TICKETS = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <link rel="icon" href="{{ url_for('static', filename='favicon.ico') }}" type="image/x-icon">
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Ticket Yönetimi (Admin)</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    /* Tutarlı mobil metin ölçekleme */
    html { -webkit-text-size-adjust: 100%; }

    body {
      margin: 0;
      min-height: 100vh;
      background: linear-gradient(-45deg, #121212, #1e1e1e, #212121, #000000);
      background-size: 400% 400%;
      animation: gradientBG 12s ease infinite;
      color: #fff;
      overflow: hidden;
      position: relative;
    }
    @supports (height: 100dvh) {
      /* Mobil toolbar dalgalanmasına dayanıklı yükseklik */
      body { min-height: 100dvh; }
    }

    @keyframes gradientBG {
      0% {background-position: 0% 50%;}
      50% {background-position: 100% 50%;}
      100% {background-position: 0% 50%;}
    }

    .card {
      background: rgba(20, 20, 20, 0.9);
      border-radius: 14px;
      box-shadow: 0 4px 20px rgba(0,0,0,0.3);
      color: #f1f1f1;
      z-index: 2;
      position: relative;
    }

    .form-control {
      background-color: #1e1e1e;
      border-color: #444;
      color: #fff;
    }

    .form-control:focus {
      background-color: #1e1e1e;
      border-color: #2186eb;
      color: #fff;
      box-shadow: none;
    }

    .form-control::placeholder { color: #aaa; }

    .table-dark { background-color: #1f1f1f; }
    .table-dark td, .table-dark th { color: #e6e6e6; }

    .btn-success, .btn-secondary { font-weight: 500; }
    .text-muted { color: #bbb !important; }

    /* -- Sosyal medya hareketli arka plan -- */
    .animated-social-bg {
      position: fixed;
      inset: 0;
      width: 100vw;
      height: 100vh;
      z-index: 0;
      pointer-events: none;
      overflow: hidden;
      user-select: none;
    }
    .bg-icon {
      position: absolute;
      width: 48px;
      opacity: 0.13;
      filter: blur(0.2px) drop-shadow(0 4px 24px #0008);
      animation-duration: 18s;
      animation-iteration-count: infinite;
      animation-timing-function: ease-in-out;
      user-select: none;
    }
    /* 18 farklı pozisyon ve animasyon */
    .icon1  { left: 10vw;  top: 13vh; animation-name: float1; }
    .icon2  { left: 72vw;  top: 22vh; animation-name: float2; }
    .icon3  { left: 23vw;  top: 67vh; animation-name: float3; }
    .icon4  { left: 70vw;  top: 75vh; animation-name: float4; }
    .icon5  { left: 48vw;  top: 45vh; animation-name: float5; }
    .icon6  { left: 81vw;  top: 15vh; animation-name: float6; }
    .icon7  { left: 17vw;  top: 40vh; animation-name: float7;}
    .icon8  { left: 61vw;  top: 55vh; animation-name: float8;}
    .icon9  { left: 33vw;  top: 24vh; animation-name: float9;}
    .icon10 { left: 57vw; top: 32vh; animation-name: float10;}
    .icon11 { left: 80vw; top: 80vh; animation-name: float11;}
    .icon12 { left: 8vw;  top: 76vh; animation-name: float12;}
    .icon13 { left: 19vw;  top: 22vh; animation-name: float13;}
    .icon14 { left: 38vw;  top: 18vh; animation-name: float14;}
    .icon15 { left: 27vw;  top: 80vh; animation-name: float15;}
    .icon16 { left: 45vw;  top: 82vh; animation-name: float16;}
    .icon17 { left: 88vw;  top: 55vh; animation-name: float17;}
    .icon18 { left: 89vw;  top: 28vh; animation-name: float18;}
    @keyframes float1  { 0%{transform:translateY(0);} 50%{transform:translateY(-34px) scale(1.09);} 100%{transform:translateY(0);} }
    @keyframes float2  { 0%{transform:translateY(0);} 50%{transform:translateY(20px) scale(0.97);} 100%{transform:translateY(0);} }
    @keyframes float3  { 0%{transform:translateY(0);} 50%{transform:translateY(-27px) scale(1.05);} 100%{transform:translateY(0);} }
    @keyframes float4  { 0%{transform:translateY(0);} 50%{transform:translateY(-20px) scale(0.95);} 100%{transform:translateY(0);} }
    @keyframes float5  { 0%{transform:translateY(0);} 50%{transform:translateY(21px) scale(1.02);} 100%{transform:translateY(0);} }
    @keyframes float6  { 0%{transform:translateY(0);} 50%{transform:translateY(-16px) scale(1.05);} 100%{transform:translateY(0);} }
    @keyframes float7  { 0%{transform:translateY(0);} 50%{transform:translateY(18px) scale(0.98);} 100%{transform:translateY(0);} }
    @keyframes float8  { 0%{transform:translateY(0);} 50%{transform:translateY(-14px) scale(1.04);} 100%{transform:translateY(0);} }
    @keyframes float9  { 0%{transform:translateY(0);} 50%{transform:translateY(24px) scale(1.06);} 100%{transform:translateY(0);} }
    @keyframes float10 { 0%{transform:translateY(0);} 50%{transform:translateY(-22px) scale(1.01);} 100%{transform:translateY(0);} }
    @keyframes float11 { 0%{transform:translateY(0);} 50%{transform:translateY(15px) scale(1.06);} 100%{transform:translateY(0);} }
    @keyframes float12 { 0%{transform:translateY(0);} 50%{transform:translateY(-18px) scale(1.03);} 100%{transform:translateY(0);} }
    @keyframes float13 { 0%{transform:translateY(0);} 50%{transform:translateY(24px) scale(1.04);} 100%{transform:translateY(0);} }
    @keyframes float14 { 0%{transform:translateY(0);} 50%{transform:translateY(-20px) scale(1.07);} 100%{transform:translateY(0);} }
    @keyframes float15 { 0%{transform:translateY(0);} 50%{transform:translateY(11px) scale(0.94);} 100%{transform:translateY(0);} }
    @keyframes float16 { 0%{transform:translateY(0);} 50%{transform:translateY(-19px) scale(1.03);} 100%{transform:translateY(0);} }
    @keyframes float17 { 0%{transform:translateY(0);} 50%{transform:translateY(16px) scale(1.01);} 100%{transform:translateY(0);} }
    @keyframes float18 { 0%{transform:translateY(0);} 50%{transform:translateY(-25px) scale(1.05);} 100%{transform:translateY(0);} }

    /* ===== SADECE MOBİL DOKUNUŞLAR ===== */
    @media (max-width: 575.98px) {
      /* Scroll aktif + notch güvenli alan */
      body {
        overflow-x: hidden;
        overflow-y: auto;
        padding: 16px 12px calc(16px + env(safe-area-inset-bottom));
      }
      .container { padding-left: 0; padding-right: 0; }
      .card { margin-top: 10vh; border-radius: 16px; }

      /* Tabloyu yatay kaydırılabilir yap */
      .card .table {
        display: block;
        width: 100%;
        overflow-x: auto;
        -webkit-overflow-scrolling: touch;
      }
      .card .table thead th,
      .card .table tbody td { white-space: nowrap; }

      /* Uzun metinler sarılsın: Konu(4), Mesaj(5), Yanıt(7) */
      .card .table th:nth-child(4),
      .card .table td:nth-child(4),
      .card .table th:nth-child(5),
      .card .table td:nth-child(5),
      .card .table th:nth-child(7),
      .card .table td:nth-child(7) {
        white-space: normal;
        word-break: break-word;
        min-width: 220px; /* okunabilirlik */
      }

      /* Form elemanları ve butonlar rahat dokunulsun */
      .form-control { min-height: 44px; font-size: 1rem; }
      .btn { min-height: 44px; }

      /* Başlık akışkan boyut */
      h2 { font-size: clamp(1.25rem, 6vw, 1.75rem); }

      /* Arka plan ikonlarını küçült */
      .bg-icon { width: 36px; opacity: 0.12; }
    }

    /* Kısa ekranlarda üst boşluğu azalt */
    @media (max-height: 640px) and (orientation: portrait) {
      .card { margin-top: 24px; }
    }
  </style>
</head>
<body>
  <!-- Sosyal medya hareketli arka plan -->
  <div class="animated-social-bg">
    <img src="{{ url_for('static', filename='linkedin.png') }}" class="bg-icon icon1">
    <img src="{{ url_for('static', filename='youtube.png') }}" class="bg-icon icon2">
    <img src="{{ url_for('static', filename='twitter.png') }}" class="bg-icon icon3">
    <img src="{{ url_for('static', filename='9gag.png') }}" class="bg-icon icon4">
    <img src="{{ url_for('static', filename='imo.png') }}" class="bg-icon icon5">
    <img src="{{ url_for('static', filename='discord.png') }}" class="bg-icon icon6">
    <img src="{{ url_for('static', filename='goodreads.png') }}" class="bg-icon icon7">
    <img src="{{ url_for('static', filename='twitch.png') }}" class="bg-icon icon8">
    <img src="{{ url_for('static', filename='wechat.png') }}" class="bg-icon icon9">
    <img src="{{ url_for('static', filename='swift.png') }}" class="bg-icon icon10">
    <img src="{{ url_for('static', filename='vkontakte.png') }}" class="bg-icon icon11">
    <img src="{{ url_for('static', filename='envato.png') }}" class="bg-icon icon12">
    <img src="{{ url_for('static', filename='reddit.png') }}" class="bg-icon icon13">
    <img src="{{ url_for('static', filename='facebook.png') }}" class="bg-icon icon14">
    <img src="{{ url_for('static', filename='instagram.png') }}" class="bg-icon icon15">
    <img src="{{ url_for('static', filename='foursquare.png') }}" class="bg-icon icon16">
    <img src="{{ url_for('static', filename='whatsapp.png') }}" class="bg-icon icon17">
    <img src="{{ url_for('static', filename='klout.png') }}" class="bg-icon icon18">
  </div>
  <div class="container py-4">
    <div class="card p-4 mx-auto" style="max-width:900px;">
      <h2 class="mb-4">Tüm Destek Talepleri</h2>
      <table class="table table-dark table-bordered text-center align-middle">
        <thead>
          <tr>
            <th>ID</th>
            <th>Kullanıcı</th>
            <th>Tarih</th>
            <th>Konu</th>
            <th>Mesaj</th>
            <th>Durum</th>
            <th>Yanıt</th>
            <th>İşlem</th>
          </tr>
        </thead>
        <tbody>
        {% for t in tickets %}
          <tr>
            <td>{{ t.id }}</td>
            <td>{{ t.user.username }}</td>
            <td>{{ t.created_at.strftime('%d.%m.%Y %H:%M') }}</td>
            <td>{{ t.subject }}</td>
            <td>{{ t.message }}</td>
            <td>
              {% if t.status == "open" %}
                <span class="badge bg-warning text-dark">Açık</span>
              {% else %}
                <span class="badge bg-success">Yanıtlandı</span>
              {% endif %}
            </td>
            <td>{{ t.response or "" }}</td>
            <td>
              {% if t.status == "open" %}
                <form method="post" class="d-flex flex-column gap-1">
                  <input type="hidden" name="ticket_id" value="{{ t.id }}">
                  <input type="text" name="response" class="form-control mb-1" placeholder="Yanıt">
                  <button class="btn btn-success btn-sm w-100">Yanıtla & Kapat</button>
                </form>
              {% else %}
                <span class="text-muted">—</span>
              {% endif %}
            </td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
      <a href="/panel" class="btn btn-secondary btn-sm w-100 mt-3">Panele Dön</a>
    </div>
  </div>
</body>
</html>
"""

HTML_EXTERNAL_MANAGE = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <link rel="icon" href="{{ url_for('static', filename='favicon.ico') }}" type="image/x-icon">
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Dış Servis Seçimi</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"/>
  <style>
    /* Tutarlı mobil metin ölçekleme */
    html { -webkit-text-size-adjust: 100%; }

    body {
      margin: 0;
      min-height: 100vh;
      background: linear-gradient(-45deg, #121212, #1e1e1e, #212121, #000000);
      background-size: 400% 400%;
      animation: gradientBG 12s ease infinite;
      color: #fff;
      overflow: hidden;
      position: relative;
    }
    @supports (height: 100dvh) {
      /* Mobil toolbar dalgalanmasına dayanıklı yükseklik */
      body { min-height: 100dvh; }
    }
    @keyframes gradientBG {
      0% {background-position: 0% 50%;}
      50% {background-position: 100% 50%;}
      100% {background-position: 0% 50%;}
    }

    .card, .table {
      background-color: rgba(20, 20, 20, 0.9) !important;
      border-radius: 12px;
      box-shadow: 0 4px 16px rgba(0,0,0,0.3);
      z-index: 2;
      position: relative;
    }
    .form-control, .form-select {
      background-color: #1e1e1e;
      border-color: #444;
      color: #fff;
    }
    .form-control:focus, .form-select:focus {
      background-color: #1e1e1e;
      border-color: #2186eb;
      color: #fff;
      box-shadow: none;
    }
    .form-control::placeholder { color: #aaa; }
    .table-dark { background-color: #1f1f1f; }
    .table-dark td, .table-dark th { color: #e6e6e6; }
    .btn { font-weight: 500; }
    h3 { color: #f8f9fa; }

    /* -- Sosyal medya hareketli arka plan -- */
    .animated-social-bg {
      position: fixed; inset: 0; width: 100vw; height: 100vh; z-index: 0;
      pointer-events: none; overflow: hidden; user-select: none;
    }
    .bg-icon {
      position: absolute; width: 48px; opacity: 0.13;
      filter: blur(0.2px) drop-shadow(0 4px 24px #0008);
      animation-duration: 18s; animation-iteration-count: infinite; animation-timing-function: ease-in-out;
      user-select: none;
    }
    /* 18 farklı pozisyon ve animasyon */
    .icon1  { left: 10vw;  top: 13vh; animation-name: float1; }
    .icon2  { left: 72vw;  top: 22vh; animation-name: float2; }
    .icon3  { left: 23vw;  top: 67vh; animation-name: float3; }
    .icon4  { left: 70vw;  top: 75vh; animation-name: float4; }
    .icon5  { left: 48vw;  top: 45vh; animation-name: float5; }
    .icon6  { left: 81vw;  top: 15vh; animation-name: float6; }
    .icon7  { left: 17vw;  top: 40vh; animation-name: float7;}
    .icon8  { left: 61vw;  top: 55vh; animation-name: float8;}
    .icon9  { left: 33vw;  top: 24vh; animation-name: float9;}
    .icon10 { left: 57vw; top: 32vh; animation-name: float10;}
    .icon11 { left: 80vw; top: 80vh; animation-name: float11;}
    .icon12 { left: 8vw;  top: 76vh; animation-name: float12;}
    .icon13 { left: 19vw;  top: 22vh; animation-name: float13;}
    .icon14 { left: 38vw;  top: 18vh; animation-name: float14;}
    .icon15 { left: 27vw;  top: 80vh; animation-name: float15;}
    .icon16 { left: 45vw;  top: 82vh; animation-name: float16;}
    .icon17 { left: 88vw;  top: 55vh; animation-name: float17;}
    .icon18 { left: 89vw;  top: 28vh; animation-name: float18;}
    @keyframes float1  { 0%{transform:translateY(0);} 50%{transform:translateY(-34px) scale(1.09);} 100%{transform:translateY(0);} }
    @keyframes float2  { 0%{transform:translateY(0);} 50%{transform:translateY(20px) scale(0.97);} 100%{transform:translateY(0);} }
    @keyframes float3  { 0%{transform:translateY(0);} 50%{transform:translateY(-27px) scale(1.05);} 100%{transform:translateY(0);} }
    @keyframes float4  { 0%{transform:translateY(0);} 50%{transform:translateY(-20px) scale(0.95);} 100%{transform:translateY(0);} }
    @keyframes float5  { 0%{transform:translateY(0);} 50%{transform:translateY(21px) scale(1.02);} 100%{transform:translateY(0);} }
    @keyframes float6  { 0%{transform:translateY(0);} 50%{transform:translateY(-16px) scale(1.05);} 100%{transform:translateY(0);} }
    @keyframes float7  { 0%{transform:translateY(0);} 50%{transform:translateY(18px) scale(0.98);} 100%{transform:translateY(0);} }
    @keyframes float8  { 0%{transform:translateY(0);} 50%{transform:translateY(-14px) scale(1.04);} 100%{transform:translateY(0);} }
    @keyframes float9  { 0%{transform:translateY(0);} 50%{transform:translateY(24px) scale(1.06);} 100%{transform:translateY(0);} }
    @keyframes float10 { 0%{transform:translateY(0);} 50%{transform:translateY(-22px) scale(1.01);} 100%{transform:translateY(0);} }
    @keyframes float11 { 0%{transform:translateY(0);} 50%{transform:translateY(15px) scale(1.06);} 100%{transform:translateY(0);} }
    @keyframes float12 { 0%{transform:translateY(0);} 50%{transform:translateY(-18px) scale(1.03);} 100%{transform:translateY(0);} }
    @keyframes float13 { 0%{transform:translateY(0);} 50%{transform:translateY(24px) scale(1.04);} 100%{transform:translateY(0);} }
    @keyframes float14 { 0%{transform:translateY(0);} 50%{transform:translateY(-20px) scale(1.07);} 100%{transform:translateY(0);} }
    @keyframes float15 { 0%{transform:translateY(0);} 50%{transform:translateY(11px) scale(0.94);} 100%{transform:translateY(0);} }
    @keyframes float16 { 0%{transform:translateY(0);} 50%{transform:translateY(-19px) scale(1.03);} 100%{transform:translateY(0);} }
    @keyframes float17 { 0%{transform:translateY(0);} 50%{transform:translateY(16px) scale(1.01);} 100%{transform:translateY(0);} }
    @keyframes float18 { 0%{transform:translateY(0);} 50%{transform:translateY(-25px) scale(1.05);} 100%{transform:translateY(0);} }

    /* ===== SADECE MOBİL DOKUNUŞLAR ===== */
    @media (max-width: 575.98px) {
      /* Scroll aktif + notch güvenli alan */
      body {
        overflow-x: hidden;
        overflow-y: auto;
        padding: 16px 12px calc(16px + env(safe-area-inset-bottom));
      }
      .container { padding-left: 0; padding-right: 0; }
      .card { margin-top: 10vh; border-radius: 16px; }

      /* Tablo kendi içinde yatay kaydırılabilir */
      .card .table {
        display: block;
        width: 100%;
        overflow-x: auto;
        -webkit-overflow-scrolling: touch;
        white-space: nowrap;
      }
      .card .table thead th,
      .card .table tbody td { white-space: nowrap; }

      /* Servis adı okunabilir kalsın (wrap + min-width) */
      .card .table th:nth-child(2),
      .card .table td:nth-child(2) {
        white-space: normal;
        word-break: break-word;
        min-width: 240px;
      }

      /* Checkbox ve butonlar rahat dokunulsun */
      input[type="checkbox"] { width: 22px; height: 22px; }
      .btn { min-height: 44px; }

      /* Alt buton grubu stack */
      .d-flex.justify-content-start.gap-2.mt-3 {
        flex-direction: column;
      }
      .d-flex.justify-content-start.gap-2.mt-3 > * {
        width: 100%;
      }

      /* Başlık akışkan boyut */
      h3 { font-size: clamp(1.2rem, 6vw, 1.5rem); }

      /* Arka plan ikonlarını küçült */
      .bg-icon { width: 36px; opacity: 0.12; }
    }

    /* Kısa ekranlarda üst boşluğu azalt */
    @media (max-height: 640px) and (orientation: portrait) {
      .card { margin-top: 24px; }
    }
  </style>
</head>
<body>
  <!-- Sosyal medya hareketli arka plan -->
  <div class="animated-social-bg">
    <img src="{{ url_for('static', filename='linkedin.png') }}" class="bg-icon icon1">
    <img src="{{ url_for('static', filename='youtube.png') }}" class="bg-icon icon2">
    <img src="{{ url_for('static', filename='twitter.png') }}" class="bg-icon icon3">
    <img src="{{ url_for('static', filename='9gag.png') }}" class="bg-icon icon4">
    <img src="{{ url_for('static', filename='imo.png') }}" class="bg-icon icon5">
    <img src="{{ url_for('static', filename='discord.png') }}" class="bg-icon icon6">
    <img src="{{ url_for('static', filename='goodreads.png') }}" class="bg-icon icon7">
    <img src="{{ url_for('static', filename='twitch.png') }}" class="bg-icon icon8">
    <img src="{{ url_for('static', filename='wechat.png') }}" class="bg-icon icon9">
    <img src="{{ url_for('static', filename='swift.png') }}" class="bg-icon icon10">
    <img src="{{ url_for('static', filename='vkontakte.png') }}" class="bg-icon icon11">
    <img src="{{ url_for('static', filename='envato.png') }}" class="bg-icon icon12">
    <img src="{{ url_for('static', filename='reddit.png') }}" class="bg-icon icon13">
    <img src="{{ url_for('static', filename='facebook.png') }}" class="bg-icon icon14">
    <img src="{{ url_for('static', filename='instagram.png') }}" class="bg-icon icon15">
    <img src="{{ url_for('static', filename='foursquare.png') }}" class="bg-icon icon16">
    <img src="{{ url_for('static', filename='whatsapp.png') }}" class="bg-icon icon17">
    <img src="{{ url_for('static', filename='klout.png') }}" class="bg-icon icon18">
  </div>
  <div class="container py-4">
    <div class="card p-4 mx-auto" style="max-width:850px;">
      <h3 class="mb-4 text-center">Dış Servis Seçimi (ResellersMM)</h3>
      <form method="post">
        <table class="table table-dark table-striped">
          <thead>
            <tr><th>Seç</th><th>Servis Adı</th><th>Min / Max</th></tr>
          </thead>
          <tbody>
          {% for s in all_ext %}
            <tr>
              <td>
                <input type="checkbox" name="ext_{{s.id}}" {% if s.id in selected %}checked{% endif %}>
              </td>
              <td>{{ s.name }}</td>
              <td>{{ s.min_amount }} / {{ s.max_amount }}</td>
            </tr>
          {% endfor %}
          </tbody>
        </table>
        <div class="d-flex justify-content-start gap-2 mt-3">
          <button class="btn btn-success">Kaydet</button>
          <a href="{{ url_for('panel') }}" class="btn btn-secondary">Panele Dön</a>
        </div>
      </form>
    </div>
  </div>
</body>
</html>
"""

HTML_ORDERS_SIMPLE = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <link rel="icon" href="{{ url_for('static', filename='favicon.ico') }}" type="image/x-icon">
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <title>Geçmiş Siparişler</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        /* Tutarlı mobil metin ölçekleme */
        html { -webkit-text-size-adjust: 100%; }

        body {
          margin: 0;
          min-height: 100vh;
          background: linear-gradient(-45deg, #1e1e1e, #2c2f34, #1e1e1e, #000000);
          background-size: 400% 400%;
          animation: gradientBG 15s ease infinite;
          color: #fff;
          overflow: hidden;
          position: relative;
        }
        @supports (height: 100dvh) {
          /* Mobil toolbar dalgalanmasına dayanıklı yükseklik */
          body { min-height: 100dvh; }
        }
        @keyframes gradientBG {
          0% {background-position: 0% 50%;}
          50% {background-position: 100% 50%;}
          100% {background-position: 0% 50%;}
        }
        .container { margin-top: 60px; }
        .table { background: #1f1f1f; color: #eaeaea; border-radius: 16px; min-width: 950px; }
        .table th, .table td { vertical-align: middle; color: #fff; }
        .badge-warning { background: #ffc107; color: #000; }
        .badge-success { background: #28a745; }
        .badge-secondary { background: #6c757d; }
        .badge-danger { background: #dc3545; }
        .badge-info { background: #17a2b8; }
        .badge-dark { background: #222; }
        .orders-card {
          border-radius: 25px;
          box-shadow: 0 4px 24px rgba(0,0,0,0.5);
          padding: 40px;
          background: rgba(33, 37, 41, 0.95);
          max-width: 1200px;
          overflow-x: hidden;
          z-index: 2;
          position: relative;
        }
        .flash-msg { margin-bottom: 24px; }
        .btn-resend, .btn-complete, .btn-cancel {
          margin: 2px 0;
        }
        h1 {
          color: #61dafb;
          text-shadow: 0 2px 16px #000a;
        }
        ::-webkit-scrollbar { width: 0px; height: 0px; background: transparent; }

        /* Pagination (dark) */
        .pagination .page-link{background:#1e1e1e;border-color:#444;color:#e6e6e6}
        .pagination .page-link:hover{background:#2a2a2a;color:#fff}
        .pagination .page-item.active .page-link{background:#0ea5e9;border-color:#0ea5e9;color:#fff}
        .pagination .page-item.disabled .page-link{background:#141414;color:#777;border-color:#333}

        /* -- Sosyal medya hareketli arka plan -- */
        .animated-social-bg {
          position: fixed; inset: 0; width: 100vw; height: 100vh; z-index: 0;
          pointer-events: none; overflow: hidden; user-select: none;
        }
        .bg-icon {
          position: absolute; width: 48px; opacity: 0.13;
          filter: blur(0.2px) drop-shadow(0 4px 24px #0008);
          animation-duration: 18s; animation-iteration-count: infinite; animation-timing-function: ease-in-out;
          user-select: none;
        }
        .icon1  { left: 10vw;  top: 13vh; animation-name: float1; }
        .icon2  { left: 72vw;  top: 22vh; animation-name: float2; }
        .icon3  { left: 23vw;  top: 67vh; animation-name: float3; }
        .icon4  { left: 70vw;  top: 75vh; animation-name: float4; }
        .icon5  { left: 48vw;  top: 45vh; animation-name: float5; }
        .icon6  { left: 81vw;  top: 15vh; animation-name: float6; }
        .icon7  { left: 17vw;  top: 40vh; animation-name: float7;}
        .icon8  { left: 61vw;  top: 55vh; animation-name: float8;}
        .icon9  { left: 33vw;  top: 24vh; animation-name: float9;}
        .icon10 { left: 57vw; top: 32vh; animation-name: float10;}
        .icon11 { left: 80vw; top: 80vh; animation-name: float11;}
        .icon12 { left: 8vw;  top: 76vh; animation-name: float12;}
        .icon13 { left: 19vw;  top: 22vh; animation-name: float13;}
        .icon14 { left: 38vw;  top: 18vh; animation-name: float14;}
        .icon15 { left: 27vw;  top: 80vh; animation-name: float15;}
        .icon16 { left: 45vw;  top: 82vh; animation-name: float16;}
        .icon17 { left: 88vw;  top: 55vh; animation-name: float17;}
        .icon18 { left: 89vw;  top: 28vh; animation-name: float18;}
        @keyframes float1  { 0%{transform:translateY(0);} 50%{transform:translateY(-34px) scale(1.09);} 100%{transform:translateY(0);} }
        @keyframes float2  { 0%{transform:translateY(0);} 50%{transform:translateY(20px) scale(0.97);} 100%{transform:translateY(0);} }
        @keyframes float3  { 0%{transform:translateY(0);} 50%{transform:translateY(-27px) scale(1.05);} 100%{transform:translateY(0);} }
        @keyframes float4  { 0%{transform:translateY(0);} 50%{transform:translateY(-20px) scale(0.95);} 100%{transform:translateY(0);} }
        @keyframes float5  { 0%{transform:translateY(0);} 50%{transform:translateY(21px) scale(1.02);} 100%{transform:translateY(0);} }
        @keyframes float6  { 0%{transform:translateY(0);} 50%{transform:translateY(-16px) scale(1.05);} 100%{transform:translateY(0);} }
        @keyframes float7  { 0%{transform:translateY(0);} 50%{transform:translateY(18px) scale(0.98);} 100%{transform:translateY(0);} }
        @keyframes float8  { 0%{transform:translateY(0);} 50%{transform:translateY(-14px) scale(1.04);} 100%{transform:translateY(0);} }
        @keyframes float9  { 0%{transform:translateY(0);} 50%{transform:translateY(24px) scale(1.06);} 100%{transform:translateY(0);} }
        @keyframes float10 { 0%{transform:translateY(0);} 50%{transform:translateY(-22px) scale(1.01);} 100%{transform:translateY(0);} }
        @keyframes float11 { 0%{transform:translateY(0);} 50%{transform:translateY(15px) scale(1.06);} 100%{transform:translateY(0);} }
        @keyframes float12 { 0%{transform:translateY(0);} 50%{transform:translateY(-18px) scale(1.03);} 100%{transform:translateY(0);} }
        @keyframes float13 { 0%{transform:translateY(0);} 50%{transform:translateY(24px) scale(1.04);} 100%{transform:translateY(0);} }
        @keyframes float14 { 0%{transform:translateY(0);} 50%{transform:translateY(-20px) scale(1.07);} 100%{transform:translateY(0);} }
        @keyframes float15 { 0%{transform:translateY(0);} 50%{transform:translateY(11px) scale(0.94);} 100%{transform:translateY(0);} }
        @keyframes float16 { 0%{transform:translateY(0);} 50%{transform:translateY(-19px) scale(1.03);} 100%{transform:translateY(0);} }
        @keyframes float17 { 0%{transform:translateY(0);} 50%{transform:translateY(16px) scale(1.01);} 100%{transform:translateY(0);} }
        @keyframes float18 { 0%{transform:translateY(0);} 50%{transform:translateY(-25px) scale(1.05);} 100%{transform:translateY(0);} }

        /* ===== SADECE MOBİL DOKUNUŞLAR ===== */
        @media (max-width: 575.98px) {
          /* Scroll aktif + notch güvenli alan */
          body {
            overflow-x: hidden;
            overflow-y: auto;
            padding: 16px 12px calc(16px + env(safe-area-inset-bottom));
          }

          .container { padding-left: 0; padding-right: 0; margin-top: 40px; }

          /* Kartı mobilde daha ferah yap + yatay kaydırma izni */
          .orders-card {
            padding: 16px;
            border-radius: 16px;
            overflow-x: auto; /* tabloda x-scroll */
            -webkit-overflow-scrolling: touch;
          }

          /* Tabloyu kendi içinde yatay kaydırılabilir yap */
          .orders-card .table {
            display: block;
            width: 100%;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            white-space: nowrap;
          }
          .orders-card .table thead th,
          .orders-card .table tbody td { white-space: nowrap; }

          /* Hata vb. uzun metinlerin sarılacağı kolonlar (admin görünümünde 9. sütun) */
          .orders-card .table th:nth-child(9),
          .orders-card .table td:nth-child(9) {
            white-space: normal;
            word-break: break-word;
            min-width: 220px;
          }

          /* Dokunma hedefleri */
          .btn { min-height: 44px; }
          .btn-sm { min-height: 40px; }
          input[type="checkbox"] { width: 20px; height: 20px; }

          /* Başlık akışkan boyut */
          h1 { font-size: clamp(1.25rem, 6vw, 1.75rem); }

          /* Arka plan ikonlarını küçült */
          .bg-icon { width: 36px; opacity: 0.12; }
        }

        /* Kısa ekranlarda üst boşluğu azalt */
        @media (max-height: 640px) and (orientation: portrait) {
          .container { margin-top: 24px; }
        }
    </style>
</head>
<body>
    <!-- Sosyal medya hareketli arka plan -->
    <div class="animated-social-bg">
      <img src="{{ url_for('static', filename='linkedin.png') }}" class="bg-icon icon1">
      <img src="{{ url_for('static', filename='youtube.png') }}" class="bg-icon icon2">
      <img src="{{ url_for('static', filename='twitter.png') }}" class="bg-icon icon3">
      <img src="{{ url_for('static', filename='9gag.png') }}" class="bg-icon icon4">
      <img src="{{ url_for('static', filename='imo.png') }}" class="bg-icon icon5">
      <img src="{{ url_for('static', filename='discord.png') }}" class="bg-icon icon6">
      <img src="{{ url_for('static', filename='goodreads.png') }}" class="bg-icon icon7">
      <img src="{{ url_for('static', filename='twitch.png') }}" class="bg-icon icon8">
      <img src="{{ url_for('static', filename='wechat.png') }}" class="bg-icon icon9">
      <img src="{{ url_for('static', filename='swift.png') }}" class="bg-icon icon10">
      <img src="{{ url_for('static', filename='vkontakte.png') }}" class="bg-icon icon11">
      <img src="{{ url_for('static', filename='envato.png') }}" class="bg-icon icon12">
      <img src="{{ url_for('static', filename='reddit.png') }}" class="bg-icon icon13">
      <img src="{{ url_for('static', filename='facebook.png') }}" class="bg-icon icon14">
      <img src="{{ url_for('static', filename='instagram.png') }}" class="bg-icon icon15">
      <img src="{{ url_for('static', filename='foursquare.png') }}" class="bg-icon icon16">
      <img src="{{ url_for('static', filename='whatsapp.png') }}" class="bg-icon icon17">
      <img src="{{ url_for('static', filename='klout.png') }}" class="bg-icon icon18">
    </div>
    <div class="container d-flex justify-content-center">
        <div class="orders-card w-100">
          <h1 class="mb-4 fw-bold text-center">Geçmiş Siparişler</h1>
            <div id="alert-area"></div>
            {% with messages = get_flashed_messages(with_categories=true) %}
              {% if messages %}
                <div class="flash-msg">
                  {% for category, message in messages %}
                    <div class="alert alert-{{ 'warning' if category=='danger' else category }} text-center mb-2 py-2 px-3" style="border-radius:12px;">{{ message }}</div>
                  {% endfor %}
                </div>
              {% endif %}
            {% endwith %}

            {% if role == 'admin' %}
            <form method="post" action="{{ url_for('delete_orders_bulk') }}" id="bulk-delete-form" onsubmit="return confirm('Seçili siparişleri silmek istediğine emin misin?')">
                <input type="hidden" name="selected_ids" id="selected_ids">
                <button type="submit" class="btn btn-danger mb-3">Seçili Siparişleri Sil</button>
                <table class="table table-dark table-bordered align-middle text-center" style="margin-bottom:0;">
                    <thead>
                        <tr>
                            <th><input type="checkbox" id="select-all-orders" title="Tümünü seç/bırak" /></th>
                            <th>Sipariş No</th>
                            <th>Sağlayıcı No</th>
                            <th>Kullanıcı</th>
                            <th>Adet</th>
                            <th>Fiyat</th>
                            <th>Servis ID</th>
                            <th>Durum</th>
                            <th>Hata</th>
                            <th>İşlem</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for o in orders %}
                        <tr>
                            <td><input type="checkbox" name="order_ids" value="{{ o.id }}"></td>
                            <td>{{ o.id }}</td>
                            <td>{{ o.api_order_id if o.api_order_id else '-' }}</td>
                            <td>{{ o.user.username }}</td>
                            <td>{{ o.amount }}</td>
                            <td>{{ "%.2f"|format(o.total_price) }}</td>
                            <td>{{ o.service_id }}</td>
                            <td>
                                <span class="badge
                                    {% if o.status in ['canceled', 'cancelled'] %}badge-secondary
                                    {% elif o.status == 'completed' %}badge-success
                                    {% elif o.status == 'pending' %}badge-warning
                                    {% elif o.status == 'partial' %}badge-info
                                    {% else %}badge-dark{% endif %}">
                                    {{ durum_turkce(o.status) }}
                                </span>
                                {% if o.error %}
                                    <span class="badge badge-danger">HATA</span>
                                {% endif %}
                            </td>
                            <td>{{ o.error if o.error else "-" }}</td>
                            <td>
                                {% if o.error %}
                                    <form method="post" style="display:inline;" action="{{ url_for('order_resend', order_id=o.id) }}">
                                        <button class="btn btn-warning btn-sm btn-resend" type="submit">Resend</button>
                                    </form>
                                {% endif %}
                                {% if o.status == 'pending' %}
                                  <form method="post" style="display:inline;" action="{{ url_for('order_complete', order_id=o.id) }}">
                                    <button class="btn btn-success btn-sm btn-complete" type="submit">Tamamlandı</button>
                                  </form>
                                {% endif %}
                                {% if o.status not in ['completed', 'canceled', 'cancelled', 'partial'] %}
                                  <form method="post" style="display:inline;" action="{{ url_for('order_cancel', order_id=o.id) }}">
                                    <button class="btn btn-danger btn-sm btn-cancel" type="submit">İptal & Bakiye İade</button>
                                  </form>
                                {% endif %}
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </form>
            {% else %}
            <table class="table table-dark table-bordered align-middle text-center" style="margin-bottom:0;">
                <thead>
                    <tr>
                        <th>Sipariş No</th>
                        <th>Adet</th>
                        <th>Fiyat</th>
                        <th>Servis ID</th>
                        <th>Durum</th>
                    </tr>
                </thead>
                <tbody>
                    {% for o in orders %}
                    <tr>
                        <td>{{ o.id }}</td>
                        <td>{{ o.amount }}</td>
                        <td>{{ "%.2f"|format(o.total_price) }}</td>
                        <td>{{ o.service_id }}</td>
                        <td>
                            <span class="badge
                                {% if o.status in ['canceled', 'cancelled'] %}badge-secondary
                                {% elif o.status == 'completed' %}badge-success
                                {% elif o.status == 'pending' %}badge-warning
                                {% elif o.status == 'partial' %}badge-info
                                {% else %}badge-dark{% endif %}">
                                {{ durum_turkce(o.status) }}
                            </span>
                            {% if o.error %}
                                <span class="badge badge-danger">HATA</span>
                            {% endif %}
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% endif %}

            {# ---- PAGINATION (her iki rol için de) ---- #}
            {% if total_pages > 1 %}
            <nav aria-label="Sayfalar" class="mt-3">
              <ul class="pagination justify-content-center">
                <li class="page-item {% if page <= 1 %}disabled{% endif %}">
                  <a class="page-link" href="?page={{ page-1 }}">Önceki</a>
                </li>

                {% set start = 1 if page-2 < 1 else page-2 %}
                {% set end = total_pages if page+2 > total_pages else page+2 %}

                {% if start > 1 %}
                  <li class="page-item"><a class="page-link" href="?page=1">1</a></li>
                  {% if start > 2 %}
                    <li class="page-item disabled"><span class="page-link">…</span></li>
                  {% endif %}
                {% endif %}

                {% for p in range(start, end + 1) %}
                  {% if p == page %}
                    <li class="page-item active"><span class="page-link">{{ p }}</span></li>
                  {% else %}
                    <li class="page-item"><a class="page-link" href="?page={{ p }}">{{ p }}</a></li>
                  {% endif %}
                {% endfor %}

                {% if end < total_pages %}
                  {% if end < total_pages - 1 %}
                    <li class="page-item disabled"><span class="page-link">…</span></li>
                  {% endif %}
                  <li class="page-item"><a class="page-link" href="?page={{ total_pages }}">{{ total_pages }}</a></li>
                {% endif %}

                <li class="page-item {% if page >= total_pages %}disabled{% endif %}">
                  <a class="page-link" href="?page={{ page+1 }}">Sonraki</a>
                </li>
              </ul>
            </nav>
            {% endif %}

            <a href="{{ url_for('panel') }}" class="btn btn-secondary w-100 mt-3" style="border-radius:12px;">Panele Dön</a>
        </div>
    </div>

    {% if role == 'admin' %}
    <script>
    // Tümünü Seç
    document.getElementById('select-all-orders')?.addEventListener('change', function() {
        let checked = this.checked;
        document.querySelectorAll('input[name="order_ids"]').forEach(function(cb) {
            cb.checked = checked;
        });
    });
    // Form submitte seçili id'leri gizli input'a yaz
    document.getElementById('bulk-delete-form')?.addEventListener('submit', function(e) {
        let selected = [];
        document.querySelectorAll('input[name="order_ids"]:checked').forEach(function(cb) {
            selected.push(cb.value);
        });
        document.getElementById('selected_ids').value = selected.join(',');
        if(selected.length == 0){
          alert("Lütfen en az bir sipariş seç!");
          e.preventDefault();
          return false;
        }
    });
    </script>
    {% endif %}
</body>
</html>
"""

HTML_TICKETS = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <link rel="icon" href="{{ url_for('static', filename='favicon.ico') }}" type="image/x-icon">
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>{{ 'Tüm Destek Talepleri' if is_admin else 'Destek Taleplerim' }}</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"/>
  <style>
    /* Tutarlı mobil metin ölçekleme */
    html { -webkit-text-size-adjust: 100%; }

    body{margin:0;min-height:100vh;background:linear-gradient(-45deg,#121212,#1e1e1e,#212121,#000);background-size:400% 400%;animation:gradientBG 12s ease infinite;color:#fff;overflow:hidden;position:relative}
    @supports (height: 100dvh) { body{ min-height:100dvh; } }
    @keyframes gradientBG{0%{background-position:0% 50%}50%{background-position:100% 50%}100%{background-position:0% 50%}}
    .card{background-color:rgba(0,0,0,.7);border-radius:16px;box-shadow:0 4px 20px rgba(0,0,0,.4);z-index:2;position:relative}
    .form-control,.form-select,textarea{background-color:#1e1e1e;border-color:#444;color:#fff}
    .form-control:focus,.form-select:focus,textarea:focus{background-color:#1e1e1e;border-color:#2186eb;color:#fff;box-shadow:none}
    .form-control::placeholder,textarea::placeholder{color:#bbb}
    .table-dark{background-color:#1f1f1f}.table-dark th,.table-dark td{color:#e6e6e6}
    .badge.bg-warning.text-dark{color:#000!important}
    h3,h5{color:#61dafb;text-shadow:0 2px 12px rgba(0,0,0,.4)}
    a{color:#8db4ff}a:hover{color:#fff;text-decoration:underline}
    .pagination .page-link{background:#1e1e1e;border-color:#444;color:#e6e6e6}
    .pagination .page-link:hover{background:#2a2a2a;color:#fff}
    .pagination .page-item.active .page-link{background:#0ea5e9;border-color:#0ea5e9;color:#fff}
    .pagination .page-item.disabled .page-link{background:#141414;color:#777;border-color:#333}

    /* Arka plan ikonları */
    .animated-social-bg{position:fixed;inset:0;width:100vw;height:100vh;z-index:0;pointer-events:none;overflow:hidden;user-select:none}
    .bg-icon{position:absolute;width:48px;opacity:.13;filter:blur(.2px) drop-shadow(0 4px 24px #0008);animation-duration:18s;animation-iteration-count:infinite;animation-timing-function:ease-in-out;user-select:none}
    .icon1{left:10vw;top:13vh;animation-name:float1}.icon2{left:72vw;top:22vh;animation-name:float2}.icon3{left:23vw;top:67vh;animation-name:float3}.icon4{left:70vw;top:75vh;animation-name:float4}.icon5{left:48vw;top:45vh;animation-name:float5}.icon6{left:81vw;top:15vh;animation-name:float6}.icon7{left:17vw;top:40vh;animation-name:float7}.icon8{left:61vw;top:55vh;animation-name:float8}.icon9{left:33vw;top:24vh;animation-name:float9}.icon10{left:57vw;top:32vh;animation-name:float10}.icon11{left:80vw;top:80vh;animation-name:float11}.icon12{left:8vw;top:76vh;animation-name:float12}.icon13{left:19vw;top:22vh;animation-name:float13}.icon14{left:38vw;top:18vh;animation-name:float14}.icon15{left:27vw;top:80vh;animation-name:float15}.icon16{left:45vw;top:82vh;animation-name:float16}.icon17{left:88vw;top:55vh;animation-name:float17}.icon18{left:89vw;top:28vh;animation-name:float18}
    @keyframes float1{0%{transform:translateY(0)}50%{transform:translateY(-34px) scale(1.09)}100%{transform:translateY(0)}}
    @keyframes float2{0%{transform:translateY(0)}50%{transform:translateY(20px) scale(.97)}100%{transform:translateY(0)}}
    @keyframes float3{0%{transform:translateY(0)}50%{transform:translateY(-27px) scale(1.05)}100%{transform:translateY(0)}}
    @keyframes float4{0%{transform:translateY(0)}50%{transform:translateY(-20px) scale(.95)}100%{transform:translateY(0)}}
    @keyframes float5{0%{transform:translateY(0)}50%{transform:translateY(21px) scale(1.02)}100%{transform:translateY(0)}}
    @keyframes float6{0%{transform:translateY(0)}50%{transform:translateY(-16px) scale(1.05)}100%{transform:translateY(0)}}
    @keyframes float7{0%{transform:translateY(0)}50%{transform:translateY(18px) scale(.98)}100%{transform:translateY(0)}}
    @keyframes float8{0%{transform:translateY(0)}50%{transform:translateY(-14px) scale(1.04)}100%{transform:translateY(0)}}
    @keyframes float9{0%{transform:translateY(0)}50%{transform:translateY(24px) scale(1.06)}100%{transform:translateY(0)}}
    @keyframes float10{0%{transform:translateY(0)}50%{transform:translateY(-22px) scale(1.01)}100%{transform:translateY(0)}}
    @keyframes float11{0%{transform:translateY(0)}50%{transform:translateY(15px) scale(1.06)}100%{transform:translateY(0)}}
    @keyframes float12{0%{transform:translateY(0)}50%{transform:translateY(-18px) scale(1.03)}100%{transform:translateY(0)}}
    @keyframes float13{0%{transform:translateY(0)}50%{transform:translateY(24px) scale(1.04)}100%{transform:translateY(0)}}
    @keyframes float14{0%{transform:translateY(0)}50%{transform:translateY(-20px) scale(1.07)}100%{transform:translateY(0)}}
    @keyframes float15{0%{transform:translateY(0)}50%{transform:translateY(11px) scale(.94)}100%{transform:translateY(0)}}
    @keyframes float16{0%{transform:translateY(0)}50%{transform:translateY(-19px) scale(1.03)}100%{transform:translateY(0)}}
    @keyframes float17{0%{transform:translateY(0)}50%{transform:translateY(16px) scale(1.01)}100%{transform:translateY(0)}}
    @keyframes float18{0%{transform:translateY(0)}50%{transform:translateY(-25px) scale(1.05)}100%{transform:translateY(0)}}

    /* --- Tablo iyileştirmeleri --- */
    .table-fixed{table-layout:fixed;width:100%}
    .table-fixed th,.table-fixed td{white-space:normal;overflow-wrap:anywhere;vertical-align:middle}
    .table-wrap{overflow-x:hidden} /* varsayılan: yatay çubuğu gizle */
    .col-actions{width:320px}
    .action-cell{display:flex;align-items:center;justify-content:center;gap:.5rem;flex-wrap:wrap}
    .action-group{max-width:240px}
    .action-input{max-width:160px}
    .btn-pill{border-radius:9999px}
    .btn-soft-danger{background:rgba(220,53,69,.12);border:1px solid rgba(220,53,69,.35);color:#ff7b86}
    .btn-soft-danger:hover{background:rgba(220,53,69,.2);color:#fff}
    .btn-soft-success{background:rgba(25,135,84,.12);border:1px solid rgba(25,135,84,.35);color:#63e6be}
    .btn-soft-success:hover{background:rgba(25,135,84,.2);color:#fff}

    /* ===== SADECE MOBİL DOKUNUŞLAR ===== */
    @media (max-width: 575.98px) {
      /* Scroll + notch-safe */
      body{
        overflow-x:hidden;
        overflow-y:auto;
        padding:16px 12px calc(16px + env(safe-area-inset-bottom));
      }
      .container{padding-left:0;padding-right:0}
      .card{border-radius:16px}

      /* Başlıklar akışkan boyut */
      h3{font-size:clamp(1.2rem,6vw,1.5rem)}
      h5{font-size:clamp(1rem,5.2vw,1.25rem)}

      /* Tabloyu yatay kaydırılabilir yap */
      .table-wrap{
        overflow-x:auto;
        -webkit-overflow-scrolling:touch;
      }
      .table-fixed{
        display:block;
        width:100%;
        min-width:720px; /* içerik rahat sığsın, x-scroll aktif olsun */
      }

      /* Admin aksiyon hücresi: dikey stack + tam genişlik input */
      .action-cell{flex-direction:column;align-items:stretch;gap:.5rem}
      .action-group{max-width:none;width:100%}
      .action-input{max-width:none;width:100%}

      /* Dokunma hedefleri */
      .btn{min-height:44px}
      .btn-sm{min-height:40px}
      input,select,textarea{min-height:44px}

      /* Arka plan ikonlarını küçült */
      .bg-icon{width:36px;opacity:.12}
    }

    /* Kısa ekran portre */
    @media (max-height:640px) and (orientation:portrait){
      .card{margin-top:12px}
    }
  </style>
</head>
<body class="text-light">
  <!-- Sosyal medya hareketli arka plan -->
  <div class="animated-social-bg">
    <img src="{{ url_for('static', filename='linkedin.png') }}" class="bg-icon icon1">
    <img src="{{ url_for('static', filename='youtube.png') }}" class="bg-icon icon2">
    <img src="{{ url_for('static', filename='twitter.png') }}" class="bg-icon icon3">
    <img src="{{ url_for('static', filename='9gag.png') }}" class="bg-icon icon4">
    <img src="{{ url_for('static', filename='imo.png') }}" class="bg-icon icon5">
    <img src="{{ url_for('static', filename='discord.png') }}" class="bg-icon icon6">
    <img src="{{ url_for('static', filename='goodreads.png') }}" class="bg-icon icon7">
    <img src="{{ url_for('static', filename='twitch.png') }}" class="bg-icon icon8">
    <img src="{{ url_for('static', filename='wechat.png') }}" class="bg-icon icon9">
    <img src="{{ url_for('static', filename='swift.png') }}" class="bg-icon icon10">
    <img src="{{ url_for('static', filename='vkontakte.png') }}" class="bg-icon icon11">
    <img src="{{ url_for('static', filename='envato.png') }}" class="bg-icon icon12">
    <img src="{{ url_for('static', filename='reddit.png') }}" class="bg-icon icon13">
    <img src="{{ url_for('static', filename='facebook.png') }}" class="bg-icon icon14">
    <img src="{{ url_for('static', filename='instagram.png') }}" class="bg-icon icon15">
    <img src="{{ url_for('static', filename='foursquare.png') }}" class="bg-icon icon16">
    <img src="{{ url_for('static', filename='whatsapp.png') }}" class="bg-icon icon17">
    <img src="{{ url_for('static', filename='klout.png') }}" class="bg-icon icon18">
  </div>

  <div class="container py-4">
    <div class="card p-4 mx-auto" style="max-width:1000px;">
      <h3 class="mb-4 text-center">{{ 'Tüm Destek Talepleri' if is_admin else 'Destek & Canlı Yardım' }}</h3>

      {% if not is_admin %}
      <form method="post" class="mb-4">
        <div class="mb-2"><input name="subject" class="form-control" placeholder="Konu" required></div>
        <div class="mb-2"><textarea name="message" class="form-control" placeholder="Mesajınız" rows="3" required></textarea></div>
        <button class="btn btn-primary w-100">Gönder</button>
      </form>
      {% endif %}

      <h5 class="mt-4">{{ 'Tüm Talepler' if is_admin else 'Geçmiş Talepleriniz' }}</h5>
      <div class="table-responsive table-wrap">
        <table class="table table-dark table-bordered table-sm text-center align-middle table-fixed">
          <thead>
            <tr>
              <th>Konu</th>
              {% if is_admin %}<th>Kullanıcı</th>{% endif %}
              <th>Mesaj</th>
              <th>Tarih</th>
              <th>Durum</th>
              <th>Yanıt</th>
              {% if is_admin %}<th class="col-actions">İşlem</th>{% endif %}
            </tr>
          </thead>
          <tbody>
          {% for t in tickets %}
            <tr>
              <td>{{ t.subject }}</td>

              {% if is_admin %}
              <td>
                {% if t.user and t.user.username %}
                  {{ t.user.username }}
                {% elif t.username %}
                  {{ t.username }}
                {% else %}
                  {{ t.user_id }}
                {% endif %}
              </td>
              {% endif %}

              <td>{{ t.message }}</td>
              <td>{{ t.created_at.strftime('%d.%m.%Y %H:%M') }}</td>
              <td>
                {% if t.status == "open" %}
                  <span class="badge bg-warning text-dark">Açık</span>
                {% else %}
                  <span class="badge bg-success">Yanıtlandı</span>
                {% endif %}
              </td>
              <td>{{ t.response or "-" }}</td>

              {% if is_admin %}
              <td class="action-cell">
                <form action="{{ url_for('admin_ticket_reply', ticket_id=t.id) }}" method="post" class="d-inline-flex action-group">
                  <input type="hidden" name="next" value="{{ request.full_path }}">
                  <input name="response" class="form-control form-control-sm action-input" placeholder="Yanıt..." required>
                  <button class="btn btn-soft-success btn-sm btn-pill" title="Yanıtla">✔ Yanıtla</button>
                </form>
                <a href="{{ url_for('admin_ticket_delete', ticket_id=t.id) }}"
                   class="btn btn-soft-danger btn-sm btn-pill"
                   onclick="return confirm('Ticket #{{ t.id }} silinsin mi? Bu işlem geri alınamaz.');">
                  ✖ Sil
                </a>
              </td>
              {% endif %}
            </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>

      {% if total_pages > 1 %}
      <nav aria-label="Sayfalar" class="mt-2">
        <ul class="pagination justify-content-center">
          <li class="page-item {% if page <= 1 %}disabled{% endif %}">
            <a class="page-link" href="?page={{ page-1 }}">Önceki</a>
          </li>

          {% set start = 1 if page-2 < 1 else page-2 %}
          {% set end = total_pages if page+2 > total_pages else page+2 %}

          {% if start > 1 %}
            <li class="page-item"><a class="page-link" href="?page=1">1</a></li>
            {% if start > 2 %}
              <li class="page-item disabled"><span class="page-link">…</span></li>
            {% endif %}
          {% endif %}

          {% for p in range(start, end + 1) %}
            {% if p == page %}
              <li class="page-item active"><span class="page-link">{{ p }}</span></li>
            {% else %}
              <li class="page-item"><a class="page-link" href="?page={{ p }}">{{ p }}</a></li>
            {% endif %}
          {% endfor %}

          {% if end < total_pages %}
            {% if end < total_pages - 1 %}
              <li class="page-item disabled"><span class="page-link">…</span></li>
            {% endif %}
            <li class="page-item"><a class="page-link" href="?page={{ total_pages }}">{{ total_pages }}</a></li>
          {% endif %}

          <li class="page-item {% if page >= total_pages %}disabled{% endif %}">
            <a class="page-link" href="?page={{ page+1 }}">Sonraki</a>
          </li>
        </ul>
      </nav>
      {% endif %}

      <a href="/panel" class="btn btn-secondary btn-sm w-100 mt-3">Panele Dön</a>
    </div>
  </div>
</body>
</html>
"""

HTML_PANEL = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <link rel="icon" href="{{ url_for('static', filename='favicon.ico') }}" type="image/x-icon">
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>Sipariş Paneli</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" />
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
  <style>
    /* Tutarlı mobil metin ölçekleme + iOS zoom fix */
    html { -webkit-text-size-adjust: 100%; }

    body {
      background: #181c20 !important;
      color: #fff;
      font-family: 'Segoe UI', Arial, sans-serif;
    }
    @supports (height: 100dvh) {
      body { min-height: 100dvh; }
    }
    body::before {
      content: "";
      position: fixed;
      top: 0; left: 0;
      width: 100vw; height: 100vh;
      background: linear-gradient(120deg, #212529 0%, #23272b 60%, #181c20 100%);
      z-index: -1;
      opacity: 0.56;
      animation: gradientBG 12s ease-in-out infinite;
      background-size: 200% 200%;
    }
    @keyframes gradientBG {
      0% {background-position: 0% 50%;}
      50% {background-position: 100% 50%;}
      100% {background-position: 0% 50%;}
    }
    /* --- Sosyal medya hareketli arka plan ikonları --- */
    .animated-social-bg {
      position: fixed;
      inset: 0;
      width: 100vw;
      height: 100vh;
      z-index: 0;
      pointer-events: none;
      overflow: hidden;
      user-select: none;
    }
    .bg-icon {
      position: absolute;
      width: 48px;
      opacity: 0.13;
      filter: blur(0.2px) drop-shadow(0 4px 24px #0008);
      animation-duration: 18s;
      animation-iteration-count: infinite;
      animation-timing-function: ease-in-out;
      user-select: none;
    }
    .icon1  { left: 10vw;  top: 13vh; animation-name: float1; }
    .icon2  { left: 72vw;  top: 22vh; animation-name: float2; }
    .icon3  { left: 23vw;  top: 67vh; animation-name: float3; }
    .icon4  { left: 70vw;  top: 75vh; animation-name: float4; }
    .icon5  { left: 48vw;  top: 45vh; animation-name: float5; }
    .icon6  { left: 81vw;  top: 15vh; animation-name: float6; }
    .icon7  { left: 17vw;  top: 40vh; animation-name: float7;}
    .icon8  { left: 61vw;  top: 55vh; animation-name: float8;}
    .icon9  { left: 33vw;  top: 24vh; animation-name: float9;}
    .icon10 { left: 57vw; top: 32vh; animation-name: float10;}
    .icon11 { left: 80vw; top: 80vh; animation-name: float11;}
    .icon12 { left: 8vw;  top: 76vh; animation-name: float12;}
    .icon13 { left: 19vw;  top: 22vh; animation-name: float13;}
    .icon14 { left: 38vw;  top: 18vh; animation-name: float14;}
    .icon15 { left: 27vw;  top: 80vh; animation-name: float15;}
    .icon16 { left: 45vw;  top: 82vh; animation-name: float16;}
    .icon17 { left: 88vw;  top: 55vh; animation-name: float17;}
    .icon18 { left: 89vw;  top: 28vh; animation-name: float18;}
    @keyframes float1  { 0%{transform:translateY(0);} 50%{transform:translateY(-34px) scale(1.09);} 100%{transform:translateY(0);} }
    @keyframes float2  { 0%{transform:translateY(0);} 50%{transform:translateY(20px) scale(0.97);} 100%{transform:translateY(0);} }
    @keyframes float3  { 0%{transform:translateY(0);} 50%{transform:translateY(-27px) scale(1.05);} 100%{transform:translateY(0);} }
    @keyframes float4  { 0%{transform:translateY(0);} 50%{transform:translateY(-20px) scale(0.95);} 100%{transform:translateY(0);} }
    @keyframes float5  { 0%{transform:translateY(0);} 50%{transform:translateY(21px) scale(1.02);} 100%{transform:translateY(0);} }
    @keyframes float6  { 0%{transform:translateY(0);} 50%{transform:translateY(-16px) scale(1.05);} 100%{transform:translateY(0);} }
    @keyframes float7  { 0%{transform:translateY(0);} 50%{transform:translateY(18px) scale(0.98);} 100%{transform:translateY(0);} }
    @keyframes float8  { 0%{transform:translateY(0);} 50%{transform:translateY(-14px) scale(1.04);} 100%{transform:translateY(0);} }
    @keyframes float9  { 0%{transform:translateY(0);} 50%{transform:translateY(24px) scale(1.06);} 100%{transform:translateY(0);} }
    @keyframes float10 { 0%{transform:translateY(0);} 50%{transform:translateY(-22px) scale(1.01);} 100%{transform:translateY(0);} }
    @keyframes float11 { 0%{transform:translateY(0);} 50%{transform:translateY(15px) scale(1.06);} 100%{transform:translateY(0);} }
    @keyframes float12 { 0%{transform:translateY(0);} 50%{transform:translateY(-18px) scale(1.03);} 100%{transform:translateY(0);} }
    @keyframes float13 { 0%{transform:translateY(0);} 50%{transform:translateY(24px) scale(1.04);} 100%{transform:translateY(0);} }
    @keyframes float14 { 0%{transform:translateY(0);} 50%{transform:translateY(-20px) scale(1.07);} 100%{transform:translateY(0);} }
    @keyframes float15 { 0%{transform:translateY(0);} 50%{transform:translateY(11px) scale(0.94);} 100%{transform:translateY(0);} }
    @keyframes float16 { 0%{transform:translateY(0);} 50%{transform:translateY(-19px) scale(1.03);} 100%{transform:translateY(0);} }
    @keyframes float17 { 0%{transform:translateY(0);} 50%{transform:translateY(16px) scale(1.01);} 100%{transform:translateY(0);} }
    @keyframes float18 { 0%{transform:translateY(0);} 50%{transform:translateY(-25px) scale(1.05);} 100%{transform:translateY(0);} }

    .card {
      background: rgba(28,31,34,0.95);
      border-radius: 18px;
      box-shadow: 0 4px 32px 0 rgba(0,0,0,0.25);
      border: none;
    }
    .btn-panel-dark {
      background: #181c20;
      color: #f0f4fa;
      border: 2.2px solid #43464e;
      border-radius: 12px;
      font-weight: 700;
      letter-spacing: .04em;
      box-shadow: 0 1.5px 10px 0 rgba(0,0,0,0.10);
      transition: background .21s, color .15s, border .2s, box-shadow .19s;
      padding: 0.95rem 1.2rem;
      font-size: 1.09rem;
      width: 100%;
      margin-bottom: 0 !important;
    }
    .btn-panel-dark:hover, .btn-panel-dark:focus {
      background: #23272b;
      color: #72e2ff;
      border-color: #43b3fa;
      box-shadow: 0 4px 20px 0 rgba(67,179,250,0.08);
      outline: none;
    }
    .btn-panel-dark b { color: #fff; font-weight: 900; letter-spacing: 0.02em; }
    .btn-panel-outline { background: transparent; color: #8ec9fd; border: 2px solid #31353a; border-radius: 10px; font-weight: 700; padding: 0.72rem 1.2rem; font-size: 1.01rem; margin-bottom: 2px; width: 100%; box-shadow: none; transition: background 0.21s, color .15s, border .2s, box-shadow .17s; opacity: 1; }
    .btn-panel-outline:hover, .btn-panel-outline:focus { background: #222730; color: #41d1ff; border-color: #43b3fa; outline: none; opacity: 1; }
    .btn-custom-outline { background: transparent; border: 1.5px solid #50555c; color: #c2c8d7; border-radius: 8px; transition: all .18s; }
    .btn-custom-outline:hover, .btn-custom-outline:focus { background: #22262c; color: #fff; border-color: #2186eb; }
    .form-control, .form-select { background: #23272b; border: 1.5px solid #323740; color: #e7eaf0; border-radius: 8px; transition: border .17s, box-shadow .17s; }
    .form-control:focus, .form-select:focus { border-color: #2186eb; color: #fff; background: #23272b; box-shadow: 0 0 0 0.10rem #2186eb40; outline: none; }
    .form-label { color: #f0f0f2; font-weight: 600; font-size: 1rem; }
    .alert-secondary { background: #23272b; color: #c3cad8; border: none; border-radius: 8px; }
    .welcome-card { background: linear-gradient(100deg, #242a2f 0%, #181c20 80%); border-radius: 15px; padding: 22px 26px 15px 18px; margin-bottom: 22px; box-shadow: 0 3px 18px 0 rgba(0,0,0,0.23); display: flex; align-items: center; justify-content: space-between; }
    .welcome-icon { font-size: 2.5rem; color: #2186eb; margin-right: 13px; margin-top: 3px; }
    .welcome-title { font-weight: 800; font-size: 1.16rem; margin-bottom: 0.2rem; color: #fff; letter-spacing: 0.015em; }
    .welcome-desc { font-size: 0.95rem; color: #c5c8d4; }
    .welcome-balance { font-size: 1.13rem; color: #fff; font-weight: 700; margin-bottom: 0.18rem; text-align: right; }
    .welcome-balance-label { color: #fff !important; font-weight: 900 !important; letter-spacing: .01em; }
    .welcome-balance-value { color: #41b6ff !important; font-weight: 900 !important; letter-spacing: .01em; font-size: 1.12em; }
    .order-title-center { width: 100%; display: flex; align-items: center; justify-content: center; font-size: 1.6rem; font-weight: 900; color: #22b3ff; letter-spacing: .018em; margin-bottom: 18px; margin-top: 18px; text-shadow: 0 4px 24px #22b3ff1a; gap: 12px; position: relative; min-height: 54px; }
    .order-title-center .bi { color: #22b3ff; font-size: 1.45em; margin-right: 7px; }
    @media (max-width: 767px) { .order-title-center { font-size: 1.2rem; gap: 7px; min-height: 34px; } .order-title-center .bi { font-size: 1.12em; } }
    input[type=number]::-webkit-inner-spin-button, input[type=number]::-webkit-outer-spin-button { -webkit-appearance: none; margin: 0; }
    input[type=number] { -moz-appearance: textfield; appearance: textfield; }
    .form-total-custom { background: #23272b !important; border: 1.5px solid #323740 !important; color: #4fe9ff !important; border-radius: 8px !important; font-size: 1.21em !important; font-weight: 800 !important; letter-spacing: .01em; padding-left: 14px !important; padding-right: 14px !important; transition: border .16s, box-shadow .15s; box-shadow: none; min-height: 44px; text-align: left; }
    .form-total-custom:disabled { background: #23272b !important; color: #4fe9ff !important; opacity: 1; }
    @media (max-width: 575px) { .welcome-card { flex-direction: column; align-items: flex-start; gap: 12px; } .welcome-balance { text-align: left; } .order-title-center { font-size: 1.05rem; gap: 6px; min-height: 27px; } .form-total-custom { font-size: 1.06em !important; } }
    .flash-info-box { margin-bottom: 15px; border-radius: 8px; font-weight: 600; font-size: 1.04em; padding: 10px 20px; border-left: 5px solid #00e1ff; background: #18242d; color: #51f5ff; box-shadow: 0 2px 10px 0 #00e1ff33; animation: fadeinflash .5s; }
    .flash-info-box.error { border-left: 5px solid #ff6363; background: #2a1818; color: #ffc7c7; box-shadow: 0 2px 10px 0 #ff636633; }
    @keyframes fadeinflash { from { opacity: 0; transform: translateY(-18px);} to   { opacity: 1; transform: translateY(0);} }
    /* Modern WhatsApp Butonu */
    #whatsapp-float { position: fixed; right: 32px; bottom: 42px; width: 62px; height: 62px; border-radius: 50%; background: linear-gradient(135deg, #25D366 80%, #075E54 100%); box-shadow: 0 6px 32px 0 #25d36648, 0 1.5px 10px 0 #00000020; color: #fff; display: flex; align-items: center; justify-content: center; z-index: 11000; cursor: pointer; transition: transform .19s cubic-bezier(.27,1.4,.62,.97), box-shadow .22s; border: none; animation: whatsapp-float-pop .7s cubic-bezier(.21,1.4,.72,1) 1; overflow: hidden; }
    #whatsapp-float:hover { transform: scale(1.08) translateY(-3px); box-shadow: 0 12px 48px 0 #25d36684, 0 3px 16px 0 #00000020; color: #fff; background: linear-gradient(135deg, #24ff7d 70%, #128C7E 100%); text-decoration: none; }
    #whatsapp-float .bi-whatsapp { font-size: 2.2em; filter: drop-shadow(0 1px 7px #13f85d66); }
    #whatsapp-float-text { position: absolute; right: 74px; bottom: 0px; font-size: 1.08em; background: #25d366; color: #0b3e1b; border-radius: 14px 0 0 14px; padding: 10px 20px 10px 18px; white-space: nowrap; box-shadow: 0 4px 20px 0 #25d36626; opacity: 0; pointer-events: none; font-weight: 800; letter-spacing: 0.02em; transition: opacity 0.23s; }
    #whatsapp-float:hover #whatsapp-float-text, #whatsapp-float:focus #whatsapp-float-text { opacity: 1; }
    @media (max-width:600px){ #whatsapp-float { right: 14px; bottom: 18px; width: 48px; height: 48px; } #whatsapp-float .bi-whatsapp { font-size: 1.34em; } #whatsapp-float-text { display: none; } }
    @keyframes whatsapp-float-pop { 0% {transform:scale(0.75) translateY(60px);} 70% {transform:scale(1.13) translateY(-12px);} 100% {transform:scale(1) translateY(0);} }

    /* ===== SADECE MOBİL DOKUNUŞLAR ===== */
    @media (max-width: 575.98px) {
      body {
        overflow-x: hidden;
        overflow-y: auto;
        padding: 16px 12px calc(16px + env(safe-area-inset-bottom));
      }
      .container { padding-left: 0; padding-right: 0; }
      .card.p-4 { padding: 16px !important; border-radius: 16px; }

      /* Dokunma hedefleri ve iOS zoom önleme */
      input, select, textarea { min-height: 44px; font-size: 16px; }
      .btn, .btn-lg, .btn-sm { min-height: 44px; touch-action: manipulation; }

      /* Açıklama kutusu küçük ekranda daha kompakt */
      .alert.alert-secondary { min-height: unset; padding: 12px; font-size: .95rem; }

      /* Arka plan ikonlarını küçült/soluklaştır */
      .bg-icon { width: 34px; opacity: 0.10; }

      /* WhatsApp butonu safe-area üstünde */
      #whatsapp-float { right: 14px; bottom: calc(18px + env(safe-area-inset-bottom)); }
    }

    /* Kısa ekran (yüksekliği dar) portrelerde üst boşluğu azalt */
    @media (max-height: 640px) and (orientation: portrait) {
      .container { padding-top: 8px !important; }
      .order-title-center { margin-top: 10px; margin-bottom: 10px; }
    }
  </style>
</head>
<body>
  <!-- Sosyal medya hareketli arka plan -->
  <div class="animated-social-bg">
    <img src="{{ url_for('static', filename='linkedin.png') }}" class="bg-icon icon1">
    <img src="{{ url_for('static', filename='youtube.png') }}" class="bg-icon icon2">
    <img src="{{ url_for('static', filename='twitter.png') }}" class="bg-icon icon3">
    <img src="{{ url_for('static', filename='9gag.png') }}" class="bg-icon icon4">
    <img src="{{ url_for('static', filename='imo.png') }}" class="bg-icon icon5">
    <img src="{{ url_for('static', filename='discord.png') }}" class="bg-icon icon6">
    <img src="{{ url_for('static', filename='goodreads.png') }}" class="bg-icon icon7">
    <img src="{{ url_for('static', filename='twitch.png') }}" class="bg-icon icon8">
    <img src="{{ url_for('static', filename='wechat.png') }}" class="bg-icon icon9">
    <img src="{{ url_for('static', filename='swift.png') }}" class="bg-icon icon10">
    <img src="{{ url_for('static', filename='vkontakte.png') }}" class="bg-icon icon11">
    <img src="{{ url_for('static', filename='envato.png') }}" class="bg-icon icon12">
    <img src="{{ url_for('static', filename='reddit.png') }}" class="bg-icon icon13">
    <img src="{{ url_for('static', filename='facebook.png') }}" class="bg-icon icon14">
    <img src="{{ url_for('static', filename='instagram.png') }}" class="bg-icon icon15">
    <img src="{{ url_for('static', filename='foursquare.png') }}" class="bg-icon icon16">
    <img src="{{ url_for('static', filename='whatsapp.png') }}" class="bg-icon icon17">
    <img src="{{ url_for('static', filename='klout.png') }}" class="bg-icon icon18">
  </div>

  <div class="container py-4">
    <div class="card p-4 mx-auto" style="max-width:800px;">
      <!-- HOŞGELDİN -->
      <div class="welcome-card mb-3">
        <div class="d-flex align-items-center">
          <span class="welcome-icon"><i class="bi bi-person-circle"></i></span>
          <div>
            <div class="welcome-title">Hoşgeldin - {{ current_user }}</div>
            <div class="welcome-desc">Keyifli ve güvenli alışverişler dileriz.</div>
          </div>
        </div>
        <div>
          <div class="welcome-balance">
            <span class="welcome-balance-label">Bakiye:</span>
            <span class="welcome-balance-value" id="balance">{{ balance }} TL</span>
          </div>
          <a href="{{ url_for('orders_page') }}" class="btn btn-panel-outline btn-sm mt-1 w-100" style="min-width:146px;">
            <i class="bi bi-box-seam"></i> Siparişlerim
          </a>
        </div>
      </div>

      <!-- BUTONLAR -->
      <div class="d-grid gap-3 mb-3">
        {% if role == 'admin' %}
          <a href="{{ url_for('manage_users') }}" class="btn btn-panel-dark py-2"><b>Kullanıcı Yönetimi</b></a>
          <a href="{{ url_for('admin_tickets') }}" class="btn btn-panel-dark py-2">Tüm Destek Talepleri</a>
          <a href="{{ url_for('manage_services') }}" class="btn btn-panel-dark py-2">Servisleri Yönet</a>
        {% else %}
          <a href="{{ url_for('bakiye_yukle') }}" class="btn btn-panel-dark py-2">Bakiye Yükle</a>
          <a href="{{ url_for('tickets') }}" class="btn btn-panel-dark py-2">Destek & Canlı Yardım</a>
        {% endif %}
        <a href="{{ url_for('watchads') }}" class="btn btn-panel-dark py-2">Reklam İzle – Bakiye Kazan</a>
      </div>

      <!-- SİPARİŞ FORMU BAŞLIĞI -->
      <div class="order-title-center">
        <i class="bi bi-cart-check"></i> Yeni Sipariş
      </div>

      <!-- SADECE SİPARİŞ BAŞARI MESAJI (YENİ) -->
      <div id="order-messages-area"></div>

      <form id="orderForm" method="post" autocomplete="off">
        <!-- KATEGORİ: artık seçilebilir; yine de backend uyumu için hidden 'category' korunuyor -->
        <div class="mb-3">
          <label class="form-label"><i class="bi bi-star-fill text-warning"></i> Kategori</label>
          <input type="hidden" name="category" id="category_hidden" value="">
          <select class="form-select" id="category_id" name="category_id" required>
            {% for c in categories %}
              <option value="{{ c.id }}">{{ c.icon or "📁" }} {{ c.name }}</option>
            {% endfor %}
          </select>
        </div>

        <div class="mb-3">
          <label class="form-label"><i class="bi bi-box-seam"></i> Servis</label>
          <select class="form-select" name="service_id" id="service_id" required>
            {% for s in services %}
              <option value="{{ s.id }}"
                      data-price="{{ s.price }}"
                      data-min="{{ s.min_amount }}"
                      data-max="{{ s.max_amount }}"
                      data-category-id="{{ s.category_id if s.category_id is not none else '' }}">
                {{ s.name }} – {{ s.price }} TL
              </option>
            {% endfor %}
          </select>
        </div>

        <div class="mb-3">
          <label class="form-label"><i class="bi bi-info-circle"></i> Açıklama</label>
          <div class="alert alert-secondary" style="white-space: pre-line; display: flex; flex-direction: column; justify-content: center; min-height: 160px;">
            <b>LÜTFEN SİPARİŞ VERMEDEN ÖNCE BU KISMI OKU</b>
            ☪️ Bu işaret olan servisler TR gönderimi yapıyor.
            🤖 Bu işaret olan servisler BOT gönderimi yapıyor.

            Sistem, gönderilecek takipçi sayısına göre uygun şekilde çalışır.
            Örnek : Takipçi siparişiniz ortalama 3-6 saat arasında tamamlanır.
            <b>DİKKAT:</b> Takipçi gönderimi organik hesaplardan ve gerçek yapılır. Gizli hesaplara gönderim yapılmaz.
          </div>
        </div>

        <!-- BİLGİ KUTUSU FORMUN TAM ÜSTÜNDE -->
        <div id="ajax-order-result"></div>

        <div class="mb-3">
          <label class="form-label"><i class="bi bi-link-45deg"></i> Sipariş verilecek link</label>
          <input name="username" type="text" class="form-control" placeholder="" required>
        </div>
        <div class="mb-3">
          <label class="form-label"><i class="bi bi-list-ol"></i> Miktar</label>
          <input name="amount" id="amount" type="number" min="1" class="form-control" placeholder="" required>
        </div>
        <div class="mb-3">
          <label class="form-label"><i class="bi bi-currency-dollar"></i> Tutar</label>
          <input type="text" class="form-control form-total-custom" id="total" placeholder="" disabled>
        </div>
        <button type="submit" class="btn btn-panel-dark btn-lg w-100" id="orderSubmitBtn" style="margin-top:4px;margin-bottom:4px;"><b>Siparişi Gönder</b></button>
      </form>

      <script>
        // Otomatik fiyat güncelleme
        const sel = document.getElementById('service_id'),
              amt = document.getElementById('amount'),
              tot = document.getElementById('total');
        function updateTotal(){
          const price = parseFloat(sel.selectedOptions[0]?.dataset.price)||0,
                num   = parseInt(amt.value)||0;
          tot.value = num>0
            ? (num + " × " + price.toFixed(2) + " TL = " + (num*price).toFixed(2) + " TL")
            : "";
        }
        sel.addEventListener('change', updateTotal);
        amt.addEventListener('input', updateTotal);
        document.addEventListener('DOMContentLoaded', updateTotal);
      </script>

      <script>
        // Kategori seçimine göre servisleri filtrele + hidden category'yi güncelle
        const cat = document.getElementById('category_id');
        const hiddenCat = document.getElementById('category_hidden');

        function filterServicesByCategory(){
          const cid = cat.value || "";
          hiddenCat.value = (cat.selectedOptions[0]?.textContent || "").trim(); // backend uyumu

          let firstVisible = null;
          Array.from(sel.options).forEach(o => {
            const match = (cid === "" || o.dataset.categoryId === cid);
            o.hidden = !match;
            if (match && !firstVisible) firstVisible = o;
          });
          if (firstVisible) sel.value = firstVisible.value;
          updateTotal();
        }

        cat.addEventListener('change', filterServicesByCategory);
        document.addEventListener('DOMContentLoaded', filterServicesByCategory);
      </script>

      <script>
        // AJAX sonrası form üstünde kutu göster (SADECE SİPARİŞ BAŞARISI)
        document.getElementById('orderForm').addEventListener('submit', function(e){
          e.preventDefault();
          const btn = document.getElementById('orderSubmitBtn');
          btn.disabled = true;
          fetch('/api/new_order', { method: 'POST', body: new FormData(this) })
            .then(r=>r.json())
            .then(res=>{
              btn.disabled = false;
              const msgArea = document.getElementById('ajax-order-result');
              msgArea.innerHTML = '';
              const msgBox = document.createElement('div');
              msgBox.className = "flash-info-box" + (res.success ? "" : " error");
              msgBox.innerText = res.success ? "Sipariş başarıyla oluşturuldu!" : "Bir hata oluştu";
              msgArea.appendChild(msgBox);
              setTimeout(()=>{ msgBox.remove(); }, 3200);

              if(res.success){
                this.reset(); updateTotal();
                document.getElementById('balance').innerText = res.new_balance + ' TL';
                filterServicesByCategory(); // resetten sonra kategori/servis uyumlu kalsın
              }
            })
            .catch(()=>{
              btn.disabled = false;
              const msgArea = document.getElementById('ajax-order-result');
              msgArea.innerHTML = '';
              const msgBox = document.createElement('div');
              msgBox.className = "flash-info-box error";
              msgBox.innerText = "İstek başarısız!";
              msgArea.appendChild(msgBox);
              setTimeout(()=>{ msgBox.remove(); }, 2800);
            });
        });
      </script>

      <div class="mt-3 text-end">
        <a href="{{ url_for('logout') }}" class="btn btn-custom-outline btn-sm">Çıkış Yap</a>
      </div>
    </div>
  </div>

  <!-- WhatsApp Sohbet Butonu BAŞLANGIÇ -->
  <a href="https://wa.me/447927573543" target="_blank" id="whatsapp-float" title="WhatsApp ile Sohbet Et">
    <span id="whatsapp-float-text">WhatsApp ile Destek!</span>
    <i class="bi bi-whatsapp"></i>
  </a>
  <!-- WhatsApp Sohbet Butonu BİTİŞ -->

  <div class="text-center mt-5" style="font-size: 0.9rem; color: #aaa;">
    <hr style="border-color: #333;">
    <p><strong>İletişim:</strong> 📩 kuzenlertv6996@gmail.com – 📞 +44 7927 573543 - 📸 @baybayimofficial</p>
    <p><strong>📍 Adres:</strong> Mustafa Kemal Paşa Mahallesi, Lale Sokak No:110 D:1</p>
  </div>
</body>
</html>
"""

HTML_ADS_MANAGE = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <link rel="icon" href="{{ url_for('static', filename='favicon.ico') }}" type="image/x-icon">
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Reklam Videosu</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    /* iOS metin ölçek ve zoom davranışı için */
    html { -webkit-text-size-adjust: 100%; }

    body {
      margin: 0;
      min-height: 100vh;
      background: linear-gradient(-45deg, #121212, #1e1e1e, #212121, #000000);
      background-size: 400% 400%;
      animation: gradientBG 12s ease infinite;
      color: #fff;
    }
    @supports (height: 100dvh) {
      body { min-height: 100dvh; }
    }

    @keyframes gradientBG {
      0% {background-position: 0% 50%;}
      50% {background-position: 100% 50%;}
      100% {background-position: 0% 50%;}
    }

    /* --- Sosyal medya hareketli arka plan ikonları --- */
    .animated-social-bg {
      position: fixed;
      inset: 0;
      width: 100vw;
      height: 100vh;
      z-index: 0;
      pointer-events: none;
      overflow: hidden;
      user-select: none;
    }
    .bg-icon {
      position: absolute;
      width: 48px;
      opacity: 0.13;
      filter: blur(0.2px) drop-shadow(0 4px 24px #0008);
      animation-duration: 18s;
      animation-iteration-count: infinite;
      animation-timing-function: ease-in-out;
      user-select: none;
    }
    .icon1  { left: 10vw;  top: 13vh; animation-name: float1; }
    .icon2  { left: 72vw;  top: 22vh; animation-name: float2; }
    .icon3  { left: 23vw;  top: 67vh; animation-name: float3; }
    .icon4  { left: 70vw;  top: 75vh; animation-name: float4; }
    .icon5  { left: 48vw;  top: 45vh; animation-name: float5; }
    .icon6  { left: 81vw;  top: 15vh; animation-name: float6; }
    .icon7  { left: 17vw;  top: 40vh; animation-name: float7;}
    .icon8  { left: 61vw;  top: 55vh; animation-name: float8;}
    .icon9  { left: 33vw;  top: 24vh; animation-name: float9;}
    .icon10 { left: 57vw; top: 32vh; animation-name: float10;}
    .icon11 { left: 80vw; top: 80vh; animation-name: float11;}
    .icon12 { left: 8vw;  top: 76vh; animation-name: float12;}
    .icon13 { left: 19vw;  top: 22vh; animation-name: float13;}
    .icon14 { left: 38vw;  top: 18vh; animation-name: float14;}
    .icon15 { left: 27vw;  top: 80vh; animation-name: float15;}
    .icon16 { left: 45vw;  top: 82vh; animation-name: float16;}
    .icon17 { left: 88vw;  top: 55vh; animation-name: float17;}
    .icon18 { left: 89vw;  top: 28vh; animation-name: float18;}
    @keyframes float1  { 0%{transform:translateY(0);} 50%{transform:translateY(-34px) scale(1.09);} 100%{transform:translateY(0);} }
    @keyframes float2  { 0%{transform:translateY(0);} 50%{transform:translateY(20px) scale(0.97);} 100%{transform:translateY(0);} }
    @keyframes float3  { 0%{transform:translateY(0);} 50%{transform:translateY(-27px) scale(1.05);} 100%{transform:translateY(0);} }
    @keyframes float4  { 0%{transform:translateY(0);} 50%{transform:translateY(-20px) scale(0.95);} 100%{transform:translateY(0);} }
    @keyframes float5  { 0%{transform:translateY(0);} 50%{transform:translateY(21px) scale(1.02);} 100%{transform:translateY(0);} }
    @keyframes float6  { 0%{transform:translateY(0);} 50%{transform:translateY(-16px) scale(1.05);} 100%{transform:translateY(0);} }
    @keyframes float7  { 0%{transform:translateY(0);} 50%{transform:translateY(18px) scale(0.98);} 100%{transform:translateY(0);} }
    @keyframes float8  { 0%{transform:translateY(0);} 50%{transform:translateY(-14px) scale(1.04);} 100%{transform:translateY(0);} }
    @keyframes float9  { 0%{transform:translateY(0);} 50%{transform:translateY(24px) scale(1.06);} 100%{transform:translateY(0);} }
    @keyframes float10 { 0%{transform:translateY(0);} 50%{transform:translateY(-22px) scale(1.01);} 100%{transform:translateY(0);} }
    @keyframes float11 { 0%{transform:translateY(0);} 50%{transform:translateY(15px) scale(1.06);} 100%{transform:translateY(0);} }
    @keyframes float12 { 0%{transform:translateY(0);} 50%{transform:translateY(-18px) scale(1.03);} 100%{transform:translateY(0);} }
    @keyframes float13 { 0%{transform:translateY(0);} 50%{transform:translateY(24px) scale(1.04);} 100%{transform:translateY(0);} }
    @keyframes float14 { 0%{transform:translateY(0);} 50%{transform:translateY(-20px) scale(1.07);} 100%{transform:translateY(0);} }
    @keyframes float15 { 0%{transform:translateY(0);} 50%{transform:translateY(11px) scale(0.94);} 100%{transform:translateY(0);} }
    @keyframes float16 { 0%{transform:translateY(0);} 50%{transform:translateY(-19px) scale(1.03);} 100%{transform:translateY(0);} }
    @keyframes float17 { 0%{transform:translateY(0);} 50%{transform:translateY(16px) scale(1.01);} 100%{transform:translateY(0);} }
    @keyframes float18 { 0%{transform:translateY(0);} 50%{transform:translateY(-25px) scale(1.05);} 100%{transform:translateY(0);} }

    .card {
      background: rgba(0,0,0,0.6);
      border-radius: 14px;
      box-shadow: 0 4px 20px rgba(0,0,0,0.3);
    }
    .form-control {
      background-color: #1e1e1e;
      border-color: #444;
      color: #fff;
    }
    .form-control:focus {
      background-color: #1e1e1e;
      border-color: #2186eb;
      color: #fff;
      box-shadow: none;
    }
    .alert-info {
      background-color: #1c1f23;
      border-color: #3b4c59;
      color: #cde5ff;
    }
    .btn-success, .btn-secondary {
      color: #fff;
    }

    /* ===== SADECE MOBİL DOKUNUŞLAR ===== */
    @media (max-width: 575.98px) {
      body {
        overflow-x: hidden;
        padding: 12px 10px calc(16px + env(safe-area-inset-bottom));
      }
      .container { padding-left: 0; padding-right: 0; }
      .card.p-4 { padding: 16px !important; border-radius: 16px; }

      /* Dokunma hedefleri + iOS zoom fix */
      input, select, textarea { min-height: 44px; font-size: 16px; }
      .btn { min-height: 44px; touch-action: manipulation; }

      /* Arka plan ikonlarını küçült ve soluklaştır */
      .bg-icon { width: 34px; opacity: 0.10; }
    }
  </style>
</head>
<body class="text-light">
  <!-- Sosyal medya hareketli arka plan -->
  <div class="animated-social-bg">
    <img src="{{ url_for('static', filename='linkedin.png') }}" class="bg-icon icon1">
    <img src="{{ url_for('static', filename='youtube.png') }}" class="bg-icon icon2">
    <img src="{{ url_for('static', filename='twitter.png') }}" class="bg-icon icon3">
    <img src="{{ url_for('static', filename='9gag.png') }}" class="bg-icon icon4">
    <img src="{{ url_for('static', filename='imo.png') }}" class="bg-icon icon5">
    <img src="{{ url_for('static', filename='discord.png') }}" class="bg-icon icon6">
    <img src="{{ url_for('static', filename='goodreads.png') }}" class="bg-icon icon7">
    <img src="{{ url_for('static', filename='twitch.png') }}" class="bg-icon icon8">
    <img src="{{ url_for('static', filename='wechat.png') }}" class="bg-icon icon9">
    <img src="{{ url_for('static', filename='swift.png') }}" class="bg-icon icon10">
    <img src="{{ url_for('static', filename='vkontakte.png') }}" class="bg-icon icon11">
    <img src="{{ url_for('static', filename='envato.png') }}" class="bg-icon icon12">
    <img src="{{ url_for('static', filename='reddit.png') }}" class="bg-icon icon13">
    <img src="{{ url_for('static', filename='facebook.png') }}" class="bg-icon icon14">
    <img src="{{ url_for('static', filename='instagram.png') }}" class="bg-icon icon15">
    <img src="{{ url_for('static', filename='foursquare.png') }}" class="bg-icon icon16">
    <img src="{{ url_for('static', filename='whatsapp.png') }}" class="bg-icon icon17">
    <img src="{{ url_for('static', filename='klout.png') }}" class="bg-icon icon18">
  </div>
  <div class="container py-4">
    <div class="card p-4 mx-auto" style="max-width:600px;">
      <h3>Reklam Videosu Ayarları</h3>
      {% with messages = get_flashed_messages() %}
      {% if messages %}
        <div class="alert alert-info mt-3">{{ messages[0] }}</div>
      {% endif %}
      {% endwith %}
      <form method="post" class="mt-3">
        <div class="mb-3">
          <label class="form-label">YouTube Embed URL</label>
          <input name="embed_url" class="form-control" value="{{ embed_url }}">
        </div>
        <button class="btn btn-success w-100">Kaydet</button>
      </form>
      <h5 class="mt-4">Mevcut Video Önizlemesi:</h5>
      <div class="ratio ratio-16x9 mb-3">
        <iframe src="{{ embed_url }}" frameborder="0" allowfullscreen></iframe>
      </div>
      <a href="/panel" class="btn btn-secondary btn-sm w-100">Panele Dön</a>
    </div>
  </div>
</body>
</html>
"""

HTML_WATCH_ADS = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <link rel="icon" href="{{ url_for('static', filename='favicon.ico') }}" type="image/x-icon">
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Reklam İzle – Bakiye Kazan</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
  <style>
    /* iOS metin ölçek & zoom davranışı */
    html { -webkit-text-size-adjust: 100%; }

    body {
      margin: 0;
      min-height: 100vh;
      background: linear-gradient(-45deg, #121212, #1e1e1e, #212121, #000000);
      background-size: 400% 400%;
      animation: gradientBG 12s ease infinite;
      color: #fff;
      overflow-x: hidden;
    }
    @supports (height: 100dvh) {
      body { min-height: 100dvh; }
    }

    @keyframes gradientBG {
      0% {background-position: 0% 50%;}
      50% {background-position: 100% 50%;}
      100% {background-position: 0% 50%;}
    }
    /* --- Sosyal medya hareketli arka plan ikonları --- */
    .animated-social-bg {
      position: fixed;
      inset: 0;
      width: 100vw;
      height: 100vh;
      z-index: 0;
      pointer-events: none;
      overflow: hidden;
      user-select: none;
    }
    .bg-icon {
      position: absolute;
      width: 48px;
      opacity: 0.13;
      filter: blur(0.2px) drop-shadow(0 4px 24px #0008);
      animation-duration: 18s;
      animation-iteration-count: infinite;
      animation-timing-function: ease-in-out;
      user-select: none;
    }
    .icon1  { left: 10vw;  top: 13vh; animation-name: float1; }
    .icon2  { left: 72vw;  top: 22vh; animation-name: float2; }
    .icon3  { left: 23vw;  top: 67vh; animation-name: float3; }
    .icon4  { left: 70vw;  top: 75vh; animation-name: float4; }
    .icon5  { left: 48vw;  top: 45vh; animation-name: float5; }
    .icon6  { left: 81vw;  top: 15vh; animation-name: float6; }
    .icon7  { left: 17vw;  top: 40vh; animation-name: float7;}
    .icon8  { left: 61vw;  top: 55vh; animation-name: float8;}
    .icon9  { left: 33vw;  top: 24vh; animation-name: float9;}
    .icon10 { left: 57vw; top: 32vh; animation-name: float10;}
    .icon11 { left: 80vw; top: 80vh; animation-name: float11;}
    .icon12 { left: 8vw;  top: 76vh; animation-name: float12;}
    .icon13 { left: 19vw;  top: 22vh; animation-name: float13;}
    .icon14 { left: 38vw;  top: 18vh; animation-name: float14;}
    .icon15 { left: 27vw;  top: 80vh; animation-name: float15;}
    .icon16 { left: 45vw;  top: 82vh; animation-name: float16;}
    .icon17 { left: 88vw;  top: 55vh; animation-name: float17;}
    .icon18 { left: 89vw;  top: 28vh; animation-name: float18;}
    @keyframes float1  { 0%{transform:translateY(0);} 50%{transform:translateY(-34px) scale(1.09);} 100%{transform:translateY(0);} }
    @keyframes float2  { 0%{transform:translateY(0);} 50%{transform:translateY(20px) scale(0.97);} 100%{transform:translateY(0);} }
    @keyframes float3  { 0%{transform:translateY(0);} 50%{transform:translateY(-27px) scale(1.05);} 100%{transform:translateY(0);} }
    @keyframes float4  { 0%{transform:translateY(0);} 50%{transform:translateY(-20px) scale(0.95);} 100%{transform:translateY(0);} }
    @keyframes float5  { 0%{transform:translateY(0);} 50%{transform:translateY(21px) scale(1.02);} 100%{transform:translateY(0);} }
    @keyframes float6  { 0%{transform:translateY(0);} 50%{transform:translateY(-16px) scale(1.05);} 100%{transform:translateY(0);} }
    @keyframes float7  { 0%{transform:translateY(0);} 50%{transform:translateY(18px) scale(0.98);} 100%{transform:translateY(0);} }
    @keyframes float8  { 0%{transform:translateY(0);} 50%{transform:translateY(-14px) scale(1.04);} 100%{transform:translateY(0);} }
    @keyframes float9  { 0%{transform:translateY(0);} 50%{transform:translateY(24px) scale(1.06);} 100%{transform:translateY(0);} }
    @keyframes float10 { 0%{transform:translateY(0);} 50%{transform:translateY(-22px) scale(1.01);} 100%{transform:translateY(0);} }
    @keyframes float11 { 0%{transform:translateY(0);} 50%{transform:translateY(15px) scale(1.06);} 100%{transform:translateY(0);} }
    @keyframes float12 { 0%{transform:translateY(0);} 50%{transform:translateY(-18px) scale(1.03);} 100%{transform:translateY(0);} }
    @keyframes float13 { 0%{transform:translateY(0);} 50%{transform:translateY(24px) scale(1.04);} 100%{transform:translateY(0);} }
    @keyframes float14 { 0%{transform:translateY(0);} 50%{transform:translateY(-20px) scale(1.07);} 100%{transform:translateY(0);} }
    @keyframes float15 { 0%{transform:translateY(0);} 50%{transform:translateY(11px) scale(0.94);} 100%{transform:translateY(0);} }
    @keyframes float16 { 0%{transform:translateY(0);} 50%{transform:translateY(-19px) scale(1.03);} 100%{transform:translateY(0);} }
    @keyframes float17 { 0%{transform:translateY(0);} 50%{transform:translateY(16px) scale(1.01);} 100%{transform:translateY(0);} }
    @keyframes float18 { 0%{transform:translateY(0);} 50%{transform:translateY(-25px) scale(1.05);} 100%{transform:translateY(0);} }

    .ad-modern-card {
      max-width: 440px;
      margin: 64px auto;
      border-radius: 18px;
      background: rgba(0, 0, 0, 0.65);
      box-shadow: 0 3px 32px 0 rgba(30, 38, 67, 0.3);
      padding: 40px 34px 34px 34px;
      text-align: center;
      backdrop-filter: blur(10px);
    }
    .ad-modern-title {
      font-weight: 700;
      color: #e6f1ff;
      letter-spacing: 0.01em;
      margin-bottom: 16px;
      font-size: 1.55rem;
      display: flex;
      align-items: center;
      gap: 9px;
      justify-content: center;
    }
    .ad-modern-title i {
      font-size: 1.32em;
      color: #24aaf8;
    }
    .ad-modern-desc {
      color: #d9e8f5;
      background: rgba(36, 170, 248, 0.15);
      border-radius: 11px;
      font-size: 1.1rem;
      padding: 14px 7px 10px 7px;
      margin-bottom: 18px;
      font-weight: 600;
      border: 1.5px solid #7fcaf8;
    }
    .ad-modern-video-frame {
      background: #181c22;
      border-radius: 12px;
      overflow: hidden;
      border: 2.5px solid #7fcaf8;
      margin-bottom: 23px;
      box-shadow: 0 1px 12px 0 rgba(24,34,56,0.1);
    }
    .ad-modern-video-frame video {
      display: block;
      width: 100%;
      height: 245px;
      border: none;
      background: #000;
    }
    .ad-modern-btn {
      background: linear-gradient(90deg, #24aaf8 0%, #3763f4 100%);
      border: none;
      color: #fff;
      padding: 12px 22px;
      border-radius: 10px;
      font-size: 1.09rem;
      font-weight: 600;
      box-shadow: 0 2px 16px 0 rgba(36,170,248,0.11);
      margin-bottom: 13px;
      width: 100%;
      transition: background 0.19s, box-shadow 0.19s;
    }
    .ad-modern-btn:disabled {
      background: #555e6c;
      color: #ccc;
      cursor: not-allowed;
    }
    .ad-modern-timer {
      font-size: 1.07rem;
      color: #eee;
      margin-bottom: 15px;
      font-weight: 500;
    }
    .modern-link-btn {
      background: #727a87;
      border: none;
      color: #fff;
      font-weight: 600;
      padding: 11px 0;
      width: 100%;
      border-radius: 8px;
      margin-top: 5px;
      font-size: 1.04rem;
      transition: background .18s;
      text-decoration: none;
      display: block;
    }
    .modern-link-btn:hover {
      background: #61656d;
    }
    .alert-warning {
      background-color: #4c3e14;
      border-color: #ffe58f;
      color: #ffecb5;
    }

    /* ===== SADECE MOBİL DOKUNUŞLAR ===== */
    @media (max-width: 600px) {
      body {
        padding: 12px 10px calc(16px + env(safe-area-inset-bottom));
      }
      .ad-modern-card {
        max-width: 95vw;
        padding: 20px 4vw;
        margin: 22px auto;
        border-radius: 16px;
      }
      .ad-modern-video-frame video {
        height: 38vw;
        min-height: 145px;
      }
      /* Dokunma hedefleri + iOS zoom fix */
      .ad-modern-btn,
      .modern-link-btn { min-height: 44px; font-size: 16px; }
      /* Arka plan ikonlarını küçült ve soluklaştır */
      .bg-icon { width: 34px; opacity: 0.10; }
    }
  </style>
</head>
<body>
  <!-- Sosyal medya hareketli arka plan -->
  <div class="animated-social-bg">
    <img src="{{ url_for('static', filename='linkedin.png') }}" class="bg-icon icon1">
    <img src="{{ url_for('static', filename='youtube.png') }}" class="bg-icon icon2">
    <img src="{{ url_for('static', filename='twitter.png') }}" class="bg-icon icon3">
    <img src="{{ url_for('static', filename='9gag.png') }}" class="bg-icon icon4">
    <img src="{{ url_for('static', filename='imo.png') }}" class="bg-icon icon5">
    <img src="{{ url_for('static', filename='discord.png') }}" class="bg-icon icon6">
    <img src="{{ url_for('static', filename='goodreads.png') }}" class="bg-icon icon7">
    <img src="{{ url_for('static', filename='twitch.png') }}" class="bg-icon icon8">
    <img src="{{ url_for('static', filename='wechat.png') }}" class="bg-icon icon9">
    <img src="{{ url_for('static', filename='swift.png') }}" class="bg-icon icon10">
    <img src="{{ url_for('static', filename='vkontakte.png') }}" class="bg-icon icon11">
    <img src="{{ url_for('static', filename='envato.png') }}" class="bg-icon icon12">
    <img src="{{ url_for('static', filename='reddit.png') }}" class="bg-icon icon13">
    <img src="{{ url_for('static', filename='facebook.png') }}" class="bg-icon icon14">
    <img src="{{ url_for('static', filename='instagram.png') }}" class="bg-icon icon15">
    <img src="{{ url_for('static', filename='foursquare.png') }}" class="bg-icon icon16">
    <img src="{{ url_for('static', filename='whatsapp.png') }}" class="bg-icon icon17">
    <img src="{{ url_for('static', filename='klout.png') }}" class="bg-icon icon18">
  </div>
  <div class="ad-modern-card">
    <div class="ad-modern-title"><i class="bi bi-play-circle-fill"></i> Reklam İzle – Bakiye Kazan</div>
    <div class="ad-modern-desc">Reklamı izleyerek <b>{{ reward }} TL</b> bakiye kazan!</div>
    {% if already_watched %}
      <div class="alert alert-warning" id="waitDiv">
        <b>Tekrar izleyip bakiye kazanmak için:</b><br>
        <span id="waitTimer"></span>
      </div>
      <script>
        var wait_seconds = {{ wait_seconds }};
        function fmt(sec) {
          var h = Math.floor(sec/3600);
          var m = Math.floor((sec%3600)/60);
          var s = sec%60;
          return (h<10?'0':'')+h+":"+(m<10?'0':'')+m+":"+(s<10?'0':'')+s;
        }
        function countdown() {
          if(wait_seconds>0){
            document.getElementById("waitTimer").innerText = fmt(wait_seconds) + " sonra tekrar izleyebilirsin!";
            wait_seconds--;
            setTimeout(countdown,1000);
          } else {
            location.reload();
          }
        }
        countdown();
      </script>
    {% else %}
      <div class="ad-modern-video-frame mb-3">
        <video id="adVideo" controls>
          <source src="/static/reklam.mp4" type="video/mp4">
          Tarayıcınız video etiketini desteklemiyor.
        </video>
      </div>
      <button class="ad-modern-btn" id="watchBtn" disabled>BAKİYENİ AL</button>
      <div class="ad-modern-timer" id="timer">30 sn kaldı...</div>
      <script>
        let sec = 30;
        let btn = document.getElementById("watchBtn");
        let timer = document.getElementById("timer");
        let video = document.getElementById("adVideo");
        let watched = false;
        video.addEventListener("play", function() { if(!watched) countdown(); });
        function countdown() {
          if (sec > 0) {
            timer.innerText = sec + " sn kaldı...";
            sec--;
            setTimeout(countdown, 1000);
          } else {
            btn.disabled = false;
            timer.innerText = "Bakiyeyi alabilirsin!";
            watched = true;
          }
        }
        btn.onclick = function(){
          btn.disabled = true;
          btn.innerText = "Bakiyen ekleniyor...";
          fetch('/watchads/collect', {method:"POST"}).then(r=>r.json()).then(res=>{
            if(res.success){
              btn.innerText = "Bakiye eklendi!";
            }else{
              btn.innerText = res.msg || "Hata!";
            }
          });
        }
      </script>
    {% endif %}
    <a href="/panel" class="modern-link-btn">Panele Dön</a>
  </div>
</body>
</html>
"""

# --- BOT SETUP ---

def load_bots(path="bots.txt"):
    if not os.path.exists(path): return []
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip().split(":", 1) for line in f if ":" in line]
BOT_CLIENTS = []

for u, p in load_bots():
    sf = f"settings_{u}.json"
    cl = Client()
    cl.private.timeout = 10
    if os.path.exists(sf):
        try:
            cl.load_settings(sf)
            print(f"✅ {u}: cache'dan yüklendi ({sf})")
            # BU KISIMDA BEKLEMENE GEREK YOK!
        except Exception as e:
            print(f"⚠️ {u}: cache yüklenemedi, login denenecek. Hata: {e}")
            try:
                cl.login(u, p)
                cl.dump_settings(sf)
                print(f"✅ {u}: cache sıfırdan oluşturuldu.")
                time.sleep(1)  # Sadece login olunca bekle
            except Exception as e2:
                print(f"⚠️ {u}: login/dump sırasında hata → {e2}")
                continue
    else:
        try:
            print(f"🔑 {u}: cache yok, giriş yapılıyor…")
            cl.login(u, p)
            cl.dump_settings(sf)
            print(f"✅ {u}: ilk oturum tamamlandı ve cache oluşturuldu ({sf})")
            time.sleep(1)  # Sadece login olunca bekle
        except Exception as e:
            print(f"⚠️ {u}: login/dump sırasında hata → {e}")
            continue
    cl._password = p
    BOT_CLIENTS.append(cl)
print("📦 Yüklü bot sayısı:", len(BOT_CLIENTS), "→", [getattr(c, 'username', '?') for c in BOT_CLIENTS])

def follow_user(client, target):
    try:
        uid = client.user_id_from_username(target)
        client.user_follow(uid)
    except LoginRequired:
        client.login(client.username, client._password)
        client.user_follow(client.user_id_from_username(target))

# ------------- DECORATORS -------------

def login_required(f):
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect("/")
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper

def admin_required(f):
    def wrapper(*args, **kwargs):
        user = User.query.get(session.get("user_id"))
        if not user or user.role != "admin":
            abort(403)
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper

# --- SCHEMA SELF-HEAL PATCH (Postgres) ---
from sqlalchemy import text, inspect

def ensure_schema():
    with app.app_context():
        insp = inspect(db.engine)

        # 1) category tablosu yoksa oluştur
        if not insp.has_table("category"):
            db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS category (
              id SERIAL PRIMARY KEY,
              name VARCHAR(120) NOT NULL UNIQUE,
              icon VARCHAR(16),
              "order" INTEGER DEFAULT 0
            );
            """))
            db.session.commit()
            print("✅ category tablosu hazır")

        # 2) service.category_id kolonu yoksa ekle
        service_cols = [c["name"] for c in insp.get_columns("service")]
        if "category_id" not in service_cols:
            db.session.execute(text("""
            ALTER TABLE service
              ADD COLUMN IF NOT EXISTS category_id INTEGER;
            """))
            db.session.commit()
            print("✅ service.category_id eklendi")

        # 3) FK ve index (idempotent)
        db.session.execute(text("""
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'service_category_id_fkey'
          ) THEN
            ALTER TABLE service
              ADD CONSTRAINT service_category_id_fkey
              FOREIGN KEY (category_id)
              REFERENCES category(id)
              ON DELETE SET NULL;
          END IF;
        END $$;
        """))
        db.session.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_service_category_id
        ON service (category_id);
        """))
        db.session.commit()
        print("✅ FK + index hazır")

# ⬇️ modellerden SONRA, app.run'dan ÖNCE çağır
ensure_schema()
# --- /SCHEMA PATCH ---

# --- ROUTELAR ---

@app.route("/admin/ads", methods=["GET", "POST"])
@login_required
@admin_required
def manage_ads():
    ad = AdVideo.query.first()
    if request.method == "POST":
        new_url = request.form.get("embed_url", "").strip()
        if new_url.startswith("https://www.youtube.com/embed/"):
            ad.embed_url = new_url
            db.session.commit()
            flash("Reklam videosu başarıyla güncellendi.")
        else:
            flash("Sadece YouTube embed URL girebilirsiniz.")
    return render_template_string(HTML_ADS_MANAGE, embed_url=ad.embed_url)

# Kullanıcı için reklam izleme ve bakiye kazanma
REWARD = 5.00  # Kullanıcı izlediğinde kazanacağı bakiye

@app.route("/watchads")
@login_required
def watchads():
    user = User.query.get(session.get("user_id"))
    now = datetime.utcnow()
    already = False
    wait_seconds = 0
    if user.last_ad_watch:
        elapsed = (now - user.last_ad_watch).total_seconds()
        if elapsed < 12 * 3600:
            already = True
            wait_seconds = int(12*3600 - elapsed)
    ad = AdVideo.query.first()
    return render_template_string(
        HTML_WATCH_ADS,
        already_watched=already,
        wait_seconds=wait_seconds,
        reward=REWARD
    )

@app.route("/watchads/collect", methods=["POST"])
@login_required
def collect_ads_reward():
    user = User.query.get(session.get("user_id"))
    today = datetime.utcnow().date()
    if user.last_ad_watch and user.last_ad_watch.date() == today:
        return jsonify({"success":False, "msg":"Bugün zaten reklam izledin!"})
    user.balance += REWARD
    user.last_ad_watch = datetime.utcnow()
    db.session.commit()
    return jsonify({"success":True, "new_balance": round(user.balance,2)})

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "")
        usr = User.query.filter_by(username=u).first()
        if not usr or not usr.check_password(p):
            flash("Kullanıcı adı veya şifre yanlış!")
        elif not usr.is_verified:
            flash("Hesabınız e-posta doğrulanmadı, lütfen e-postanızı doğrulayın!")
        else:
            session["user_id"] = usr.id
            return redirect("/panel")
    return render_template_string(HTML_LOGIN)

@app.route("/register", methods=["GET", "POST"])
def register():
    # Kullanıcı eğer doğrulama ekranına takıldı ama session'da veri yoksa temizle:
    if session.get("register_sent") and not session.get("register_temp_user"):
        session.pop("register_sent", None)

    sent = session.get("register_sent", False)
    temp_user = session.get("register_temp_user", {})

    if request.method == "POST":
        if not sent:
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            email = request.form.get("email", "").strip().lower()
            if not username or not password or not email:
                flash("Tüm alanları doldurun!")
            elif User.query.filter_by(username=username).first():
                flash("Bu kullanıcı adı zaten kayıtlı.")
            elif User.query.filter_by(email=email).first():
                flash("Bu e-posta zaten kayıtlı.")
            else:
                verify_code = "%06d" % random.randint(100000, 999999)
                session["register_temp_user"] = {
                    "username": username,
                    "password": generate_password_hash(password),
                    "email": email,
                    "verify_code": verify_code
                }
                send_verification_mail(email, verify_code)
                session["register_sent"] = True
                flash("Doğrulama kodu e-posta adresinize gönderildi.")
                return redirect("/register")
        else:
            code = request.form.get("verify_code", "").strip()
            if not code or not temp_user:
                flash("Bir hata oluştu, tekrar kayıt olun.")
                session.pop("register_sent", None)
                session.pop("register_temp_user", None)
            elif code != temp_user.get("verify_code"):
                flash("Kod yanlış!")
            else:
                user = User(
                    username=temp_user["username"],
                    password_hash=temp_user["password"],
                    email=temp_user["email"],
                    role="viewer",
                    balance=0,
                    is_verified=True
                )
                db.session.add(user)
                db.session.commit()
                flash("Kayıt başarıyla tamamlandı!")
                session.pop("register_sent", None)
                session.pop("register_temp_user", None)
                return redirect("/")
    return render_template_string(HTML_REGISTER, sent=session.get("register_sent", False))

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/users", methods=["GET", "POST"])
@login_required
@admin_required
def manage_users():
    if request.method == "POST":
        u = (request.form.get("u") or "").strip()
        p = request.form.get("pw") or ""
        r = request.form.get("role", "viewer")
        if u and p and not User.query.filter_by(username=u).first():
            db.session.add(User(
                username=u,
                password_hash=generate_password_hash(p),
                email=f"{u}@mail.com",
                role="admin" if r == "admin" else "viewer",
                balance=0,
                is_verified=True
            ))
            db.session.commit()
        # PRG: refresh'te form tekrar gönderilmesin
        return redirect(url_for("manage_users"))

    # Paginasyon
    page = max(1, request.args.get("page", default=1, type=int))
    per_page = 10

    q = User.query.order_by(User.username.asc())
    total = q.count()
    total_pages = max(1, ceil(total / per_page))
    page = min(page, total_pages)  # aralık dışına çıkmasın

    users = q.offset((page - 1) * per_page).limit(per_page).all()
    current_u = User.query.get(session.get("user_id")).username

    return render_template_string(
        HTML_USERS,
        users=users,
        current_user=current_u,
        rolu_turkce=rolu_turkce,
        page=page,
        total_pages=total_pages,
        start_index=(page - 1) * per_page
    )

@app.route("/admin/add-balance", methods=["POST"])
@login_required
@admin_required
def admin_add_balance():
    uname = request.form.get("username")
    amount = float(request.form.get("amount") or 0)
    user = User.query.filter_by(username=uname).first()
    if user and amount > 0:
        user.balance += amount
        db.session.commit()
        flash(f"{uname} kullanıcısına {amount} TL eklendi.")
    else:
        flash("Kullanıcı bulunamadı veya miktar hatalı.")
    return redirect("/users")

@app.route("/cancel/<int:order_id>", methods=["POST"])
@login_required
@admin_required
def cancel_order(order_id):
    order = Order.query.get_or_404(order_id)
    if order.status not in ['complete', 'cancelled']:
        order.status = "cancelled"
        order.error = ""
        user = User.query.get(order.user_id)
        if user:
            user.balance += order.total_price
        db.session.commit()
    # GÖNDEREN SAYFAYA GERİ DÖN
    ref = request.referrer or(url_for("orders_page"))
    return redirect(ref)

@app.route("/panel", methods=["GET", "POST"])
@login_required
def panel():
    # --- Kullanıcıyı çek (flask_login varsa onu kullan; yoksa session fallback)
    user = current_user if getattr(current_user, "is_authenticated", False) else User.query.get(session.get("user_id"))
    if not user:
        return redirect(url_for("login"))

    msg, error = "", ""

    # --- Local aktif servisler
    local = Service.query.filter_by(active=True).order_by(Service.name).all()
    local_ids = {s.id for s in local}

    # --- Seçili external servisler (local’de yoksa ekle)
    try:
        external = fetch_selected_external_services()
    except Exception:
        external = []
    external = [s for s in external if getattr(s, "id", None) not in local_ids]

    # --- Merge
    services = local + external

    # --- Platform tahmini (yoksa)
    for s in services:
        if not getattr(s, "platform", None):
            try:
                setattr(s, "platform", detect_platform(getattr(s, "name", ""), getattr(s, "description", "")))
            except Exception:
                setattr(s, "platform", "instagram")

    # --- Gruplama (panelin başka yerlerinde lazım olabilir)
    grouped_services = {"instagram": [], "tiktok": [], "youtube": []}
    for s in services:
        grouped_services.setdefault(getattr(s, "platform", "instagram"), grouped_services["instagram"]).append(s)

    # --- Kategoriler (HTML_PANEL’in yeni kategori select’i için)
    try:
        categories = Category.query.order_by(Category.order, Category.name).all()
    except Exception:
        categories = []

    # --- POST fallback (AJAX kullanmıyorsan da çalışsın)
    if request.method == "POST":
        target = (request.form.get("username") or "").strip()
        amount = request.form.get("amount", type=int) or 0

        service_id = request.form.get("service_id", type=int)
        service = next((s for s in services if getattr(s, "id", None) == service_id), None)

        if service:
            min_amt = getattr(service, "min_amount", 1) or 1
            max_amt = getattr(service, "max_amount", 1000000) or 1000000
            price   = getattr(service, "price", None)
        else:
            min_amt, max_amt = 1, 1000000
            price = globals().get("SABIT_FIYAT", 1)

        # Tutar hesap (Decimal’a uyumlu)
        try:
            from decimal import Decimal
            price_val = price if isinstance(price, Decimal) else Decimal(str(price))
            total = price_val * Decimal(amount)
        except Exception:
            total = float(price or 1) * float(amount)

        # Bakiye yeter mi?
        balance_val = getattr(user, "balance", 0)
        try:
            enough = (Decimal(str(balance_val)) >= (total if isinstance(total, Decimal) else Decimal(str(total))))
        except Exception:
            enough = float(balance_val) >= float(total)

        if not target or amount <= 0:
            error = "Tüm alanları doğru doldurun!"
        elif amount < min_amt or amount > max_amt:
            error = f"Adet {min_amt}-{max_amt} arası olmalı."
        elif not enough:
            error = "Yetersiz bakiye!"
        else:
            # Siparişi kaydet
            order = Order(
                username=target,
                user_id=user.id,
                amount=amount,
                total_price=total,
                status="pending",
                error=""
            )
            try:
                user.balance = (Decimal(str(balance_val)) - (total if isinstance(total, Decimal) else Decimal(str(total))))
            except Exception:
                user.balance = float(balance_val) - float(total)

            db.session.add(order)
            db.session.commit()

            # Bot gönderimi simülasyon
            status, err = "complete", ""
            try:
                for cl in (BOT_CLIENTS or [])[:amount]:
                    try:
                        follow_user(cl, target)
                    except Exception as e:
                        status, err = "error", str(e)
                        break
            except Exception:
                pass

            order.status = status
            order.error = err
            db.session.commit()

            msg = f"{amount} takipçi başarıyla gönderildi." if status == "complete" else f"Hata: {err}"

    # --- Geçmiş siparişler (admin = hepsi, user = kendi)
    try:
        if getattr(user, "role", "") == "admin":
            orders = Order.query.order_by(Order.created_at.desc()).all()
        else:
            orders = Order.query.filter_by(user_id=user.id).order_by(Order.created_at.desc()).all()
    except Exception:
        orders = []

    # --- Render (HTML_PANEL yeni kategori/servis yapısıyla uyumlu)
    return render_template_string(
        HTML_PANEL,
        orders=orders,
        role=getattr(user, "role", "user"),
        current_user=getattr(user, "username", "Misafir"),
        balance=round(float(getattr(user, "balance", 0)), 2),
        msg=msg,
        error=error,
        rolu_turkce=globals().get("rolu_turkce", {}),
        grouped_services=grouped_services,
        services=services,
        categories=categories
    )

from math import ceil
from flask import request, render_template_string, redirect, url_for

@app.route("/tickets", methods=["GET", "POST"])
@login_required
def tickets():
    user = User.query.get(session.get("user_id"))

    # POST: ticket oluştur + PRG (yenilemede form tekrar gönderilmesin)
    if request.method == "POST":
        subject = (request.form.get("subject") or "").strip()
        message = (request.form.get("message") or "").strip()
        if subject and message:
            ticket = Ticket(user_id=user.id, subject=subject, message=message)
            db.session.add(ticket)
            db.session.commit()
        return redirect(url_for("tickets"))

    # GET: paginasyon
    page = max(1, request.args.get("page", default=1, type=int))
    per_page = 10

    q = (Ticket.query
         .filter_by(user_id=user.id)
         .order_by(Ticket.created_at.desc()))

    total = q.count()
    total_pages = max(1, ceil(total / per_page))
    # istenen sayfa aralık dışındaysa sıkıştır
    page = min(page, total_pages)

    tickets = (q.offset((page - 1) * per_page)
                .limit(per_page)
                .all())

    return render_template_string(
        HTML_TICKETS,
        tickets=tickets,
        page=page,
        total_pages=total_pages
    )

# --- Admin: Ticket listesi ---
@app.route("/admin/tickets")
@admin_required
def admin_tickets():
    page = int(request.args.get("page", 1))
    per_page = 50
    pagination = Ticket.query.order_by(Ticket.id.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    tickets = pagination.items
    total_pages = pagination.pages or 1

    # user_id -> username sözlüğü (kullanıcı adını yazdırmak için)
    users_map = {u.id: u.username for u in User.query.with_entities(User.id, User.username).all()}

    return render_template_string(
        HTML_TICKETS,
        tickets=tickets,
        page=page,
        total_pages=total_pages,
        is_admin=True,              # <-- BUNU GÖNDER!
        users_map=users_map,
    )

# --- Admin: Ticket sil ---
@app.route("/admin/tickets/delete/<int:ticket_id>", methods=["GET"], endpoint="admin_ticket_delete")
@admin_required
def admin_ticket_delete(ticket_id):
    t = db.session.get(Ticket, ticket_id)
    if not t:
        flash("Ticket bulunamadı.", "warning")
        return redirect(request.referrer or url_for("admin_tickets"))
    try:
        db.session.delete(t)
        db.session.commit()
        flash("Ticket silindi.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Silme hatası: {e}", "danger")
    return redirect(request.referrer or url_for("admin_tickets"))

@app.route("/services", methods=["GET", "POST"])
@login_required
def services():
    user = User.query.get(session.get("user_id"))

    # — Admin ise fiyatları kaydetme işlemi —
    if request.method == "POST" and user.role == "admin":
        for svc in Service.query.filter_by(active=True).all():
            form_key = f"price_{svc.id}"
            if form_key in request.form:
                try:
                    new_price = float(request.form[form_key])
                    if new_price > 0:
                        svc.price = new_price
                except ValueError:
                    pass
        db.session.commit()
        flash("Servis fiyatları güncellendi.", "success")

    # 1) Yerel servisler
    local = Service.query.filter_by(active=True).all()
    # 2) Seçili external servisler (EXT_SELECTED_IDS içinde tanımlı olanlar)
    external = fetch_selected_external_services()
    # 3) İkisini birleştir
    servisler = local + external

    return render_template_string(
        HTML_SERVICES,
        servisler=servisler,
        user=user
    )

from math import ceil
from flask import request, render_template_string

@app.route("/orders")
@login_required
def orders_page():
    user = User.query.get(session.get("user_id"))

    # Rol belirle
    role = "admin" if user.role == "admin" else "user"

    # Paginasyon parametreleri
    page = max(1, request.args.get("page", default=1, type=int))
    per_page = 10

    # Sorgu
    q = Order.query.order_by(Order.id.desc())
    if role != "admin":
        q = q.filter_by(user_id=user.id)

    # Sayfa hesapları
    total = q.count()
    total_pages = max(1, ceil(total / per_page))
    page = min(page, total_pages)  # aralık dışına çıkmasın

    # Kayıtları çek
    orders = q.offset((page - 1) * per_page).limit(per_page).all()

    return render_template_string(
        HTML_ORDERS_SIMPLE,
        orders=orders,
        role=role,
        page=page,
        total_pages=total_pages,
        durum_turkce=durum_turkce
    )

@app.route("/save_announcement", methods=["POST"])
@login_required
def save_announcement():
    user_id = session.get("user_id")
    if not user_id:
        abort(401)
    user = User.query.get(user_id)
    if not user or user.role != "admin":
        abort(403)
    
    announcement = request.form.get("announcement", "")
    global announcement_text
    announcement_text = announcement
    
    flash("Duyuru kaydedildi.", "success")
    return redirect(url_for("panel"))

from flask import jsonify, request

@app.route("/api/new_order", methods=["POST"])
@login_required
def api_new_order():
    # --- Kullanıcıyı al (flask_login tercih) ---
    try:
        if getattr(current_user, "is_authenticated", False):
            user = db.session.get(User, current_user.id)
        else:
            user = db.session.get(User, session.get("user_id"))
    except Exception:
        user = db.session.get(User, session.get("user_id"))

    if not user:
        return jsonify({"success": False, "error": "Oturum bulunamadı."}), 401

    # --- Form verileri ---
    username = (request.form.get("username") or "").strip()

    try:
        amount = int(request.form.get("amount") or 0)
    except ValueError:
        amount = 0

    try:
        service_id = int(request.form.get("service_id") or 0)
    except ValueError:
        service_id = 0

    service = db.session.get(Service, service_id)
    if not service:
        return jsonify({"success": False, "error": "Servis bulunamadı."}), 404

    # --- Min/Max kontrolü ---
    min_amt = int(getattr(service, "min_amount", 10) or 10)
    max_amt = int(getattr(service, "max_amount", 1000) or 1000)
    if not username:
        return jsonify({"success": False, "error": "Kullanıcı/link gerekli."}), 400
    if amount < min_amt or amount > max_amt:
        return jsonify({"success": False, "error": f"Adet {min_amt}-{max_amt} arası olmalı."}), 400

    # --- Fiyat/Total hesapları (Decimal güvenli) ---
    unit_price = D(getattr(service, "price", 0))
    total = D(unit_price * amount)

    # --- Bakiye kontrolü ---
    if D(getattr(user, "balance", 0)) < total:
        return jsonify({"success": False, "error": "Yetersiz bakiye!"}), 400

    # --- Varsayılanlar ---
    status = "pending"
    error = None
    api_order_id = None

    # --- ResellersMM entegrasyonu (harici servis id >= 100000) ---
    if service.id >= 100000:
        try:
            real_service_id = service.id - 100000
            resp = requests.post(
                EXTERNAL_API_URL,
                data={
                    "key": EXTERNAL_API_KEY,
                    "action": "add",
                    "service": real_service_id,
                    "link": username,
                    "quantity": amount,
                },
                timeout=15
            )
            # 200 olsa da gövde error dönebilir → direkt JSON'u incele
            result = resp.json()
            if "order" in result:
                api_order_id = str(result["order"])
                # status'ü istersen "processing" yapabilirsin, ama "pending" de ok.
            else:
                status = "error"
                error = result.get("error", "ResellersMM sipariş hatası!")
        except Exception as e:
            status = "error"
            error = f"ResellersMM API hatası: {e}"

    # --- Siparişi oluştur ---
    order = Order(
        username=username,
        user_id=user.id,
        amount=amount,
        status=status,
        total_price=total,        # Kolon Float ise SQLAlchemy float'a çevirir; Numeric ise Decimal saklar.
        service_id=service_id,
        error=error,
        api_order_id=api_order_id
    )

    # --- Bakiye düşme (sadece hata yoksa) ---
    if status != "error":
        balance_sub(user, total)

    db.session.add(order)
    db.session.commit()

    # --- Yanıt ---
    new_balance = D(getattr(user, "balance", 0))
    if status == "error":
        # frontend "success" alanına çok bağlıysa True bırakmak istersen değiştir, ama doğrusu False.
        return jsonify({"success": False, "new_balance": float(new_balance), "info": error}), 502

    return jsonify({"success": True, "new_balance": float(new_balance)})

@app.route("/admin/order_resend/<int:order_id>", methods=["POST"])
@login_required
@admin_required
def admin_order_resend(order_id):
    order = Order.query.get_or_404(order_id)
    service = Service.query.get(order.service_id)
    if service and service.id >= 100000:
        try:
            real_service_id = service.id - 100000
            resp = requests.post(EXTERNAL_API_URL, data={
                "key": EXTERNAL_API_KEY,
                "action": "add",
                "service": real_service_id,
                "link": order.username,
                "quantity": order.amount
            }, timeout=10)
            resp.raise_for_status()
            result = resp.json()
            if "order" in result:
                order.status = "pending"
                order.error = ""
            else:
                order.status = "waiting"
                order.error = result.get("error", "ResellersMM sipariş hatası!")
        except Exception as e:
            order.status = "waiting"
            order.error = "ResellersMM API bağlantı/yanıt hatası: "+str(e)
        db.session.commit()
    return redirect(rl_for("orders_page"))

@app.route("/api/orders/list")
@login_required
def api_orders_list():
    user = User.query.get(session.get("user_id"))
    orders = Order.query.filter_by(user_id=user.id).order_by(Order.created_at.desc()).all()
    return jsonify({
      "orders":[
        {
          "id": o.id,
          "username": o.username,
          "amount": o.amount,
          "status": o.status,
          "created_at": o.created_at.strftime('%d.%m.%Y %H:%M')
        } for o in orders
      ]
    })

@app.route("/orders/resend/<int:order_id>", methods=["POST"])
@login_required
@admin_required
def resend_order(order_id):
    order = Order.query.get_or_404(order_id)
    user = User.query.get(order.user_id)
    service = Service.query.get(order.service_id)

    if user.balance < order.total_price:
        flash("Kullanıcının bakiyesi hala yetersiz!", "danger")
        return redirect("/orders")

    order.status = "pending"
    order.error = ""

    # Eğer dış servis ise ResellersMM'ye tekrar gönder
    if service and service.id >= 100000:
        try:
            real_service_id = service.id - 100000
            resp = requests.post(EXTERNAL_API_URL, data={
                "key": EXTERNAL_API_KEY,
                "action": "add",
                "service": real_service_id,
                "link": order.username,
                "quantity": order.amount
            }, timeout=10)
            resp.raise_for_status()
            result = resp.json()
            if "order" not in result:
                order.status = "error"
                order.error = result.get("error", "ResellersMM sipariş hatası!")
                db.session.commit()
                flash(order.error, "danger")
                return redirect("/orders")
        except Exception as e:
            order.status = "error"
            order.error = "ResellersMM API bağlantı/yanıt hatası: "+str(e)
            db.session.commit()
            flash(order.error, "danger")
            return redirect("/orders")

    user.balance -= order.total_price
    db.session.commit()
    flash("Sipariş tekrar başlatıldı!", "success")
    return redirect("/orders")

@app.route('/orders/resend/<int:order_id>', methods=['POST'])
@login_required
def order_resend(order_id):
    user = User.query.get(session.get("user_id"))
    if not user or user.role != "admin":
        return jsonify({"success": False, "error": "Yetkisiz erişim!"}), 403
    order = Order.query.get(order_id)
    if not order:
        return jsonify({"success": False, "error": "Sipariş bulunamadı."})
    service = Service.query.get(order.service_id)
    # Sadece Resellersmm (dış servis) siparişleri için:
    if not (service and service.id >= 100000):
        return jsonify({"success": False, "error": "Bu sipariş Resellersmm servisi değil."})
    try:
        real_service_id = service.id - 100000
        resp = requests.post(EXTERNAL_API_URL, data={
            "key": EXTERNAL_API_KEY,
            "action": "add",
            "service": real_service_id,
            "link": order.username,
            "quantity": order.amount
        }, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        if "order" in result:
            order.status = "pending"
            order.error = None
            db.session.commit()
            return jsonify({"success": True, "message": "Sipariş Resellersmm'e tekrar sıraya alındı!"})
        else:
            order.error = result.get("error", "ResellersMM sipariş hatası!")
            db.session.commit()
            return jsonify({"success": False, "error": order.error})
    except Exception as e:
        order.error = str(e)
        db.session.commit()
        return jsonify({"success": False, "error": "API bağlantı/yanıt hatası: " + str(e)})

@app.route('/orders/complete/<int:order_id>', methods=['POST'])
@login_required
def order_complete(order_id):
    user = User.query.get(session.get("user_id"))
    if not user or getattr(user, "role", None) != "admin":
        abort(403)
    order = Order.query.get(order_id)
    if not order:
        flash("Sipariş bulunamadı.", "danger")
        return redirect(url_for("orders_page"))
    order.status = "completed"
    order.error = None
    db.session.commit()
    flash("Sipariş manuel tamamlandı.", "success")
    return redirect(url_for("orders_page"))

from sqlalchemy.exc import IntegrityError

# panel.py
@app.route('/admin/users/force-delete/<int:user_id>', methods=['GET'], endpoint='admin_force_delete_user')
@admin_required
def admin_force_delete_user(user_id):
    # İstersen kendi kendini silmeyi engelle
    if session.get("user_id") == user_id:
        flash("Kendi hesabını silemezsin.", "warning")
        return redirect(url_for('manage_users'))

    # Varsa yoksa diye hafif kontrol
    u = db.session.get(User, user_id)
    if not u:
        flash("Kullanıcı bulunamadı ya da zaten silinmiş.", "info")
        return redirect(url_for('manage_users'))

    try:
        deleted = force_delete_user_by_fk(user_id)
        if deleted > 0:
            flash("Kullanıcı ve tüm bağlı verileri KÖKÜNDEN silindi.", "success")
        else:
            flash("Hiçbir şey silinmedi (kullanıcı bulunamadı).", "warning")
    except Exception as e:
        db.session.rollback()
        print("[FORCE-DEL][HATA]", e)
        flash(f"Silme hatası: {e}", "danger")
    return redirect(url_for('manage_users'))

@app.route('/order/cancel/<int:order_id>', methods=['POST'])
@login_required
@admin_required
def order_cancel(order_id):
    order = Order.query.get(order_id)
    if not order:
        flash("Sipariş bulunamadı.", "danger")
        return redirect(url_for("orders_page"))

    # Daha önce iade yazılmış mı? (idempotent koruması)
    already_refunded = WalletTransaction.query.filter_by(order_id=order.id, type='refund').first() is not None

    if order.status in ("canceled", "cancelled") and already_refunded:
        flash("Sipariş zaten iptal/iade edilmiş.", "warning")
        return redirect(url_for("orders_page"))

    try:
        # Durumu iptal’e çek
        order.status = "canceled"
        db.session.flush()

        # İade daha önce yazılmadıysa şimdi yaz
        did_refund = False
        if not already_refunded:
            did_refund = apply_refund(order_id=order.id, amount=order.total_price)

        db.session.commit()

        if did_refund:
            flash("Sipariş iptal edildi ve bakiye iade edildi.", "success")
        else:
            flash("Sipariş iptal edildi (iade daha önce işlenmiş).", "info")

    except Exception as e:
        db.session.rollback()
        flash(f"İşlem başarısız: {e}", "danger")

    return redirect(url_for("orders_page"))

@app.route('/api/order_status', methods=['POST'])
@login_required
@admin_required
def api_order_status():
    data = request.get_json(force=True, silent=True) or {}

    order_id = data.get("order_id")
    if not order_id:
        return {"success": False, "error": "order_id zorunlu"}

    order = Order.query.get(order_id)
    if not order:
        return {"success": False, "error": "Sipariş bulunamadı"}

    status_in = (data.get("status") or "").lower().strip()

    # Gelen durumları normalize et
    normalize = {
        "complete":   "Tamamlandı",
        "completed":  "Tamamlandı",
        "cancel":     "İade edildi",
        "canceled":   "İade edildi",
        "cancelled":  "İade edildi",
        "in progress": "İşlemde",
        "pending":    "İşlemde",
        "started":    "Sırada",
        "processing": "Sırada",
        "partial":    "Kısmi Tamamlandı",
        "refunded":   "İade edildi",
        "fail": "Sırada",  
    }
    status = normalize.get(status_in)
    if not status:
        return {"success": False, "error": "Geçersiz durum"}

    try:
        order.status = status
        db.session.flush()

        # İade gereken durumlar: canceled/cancelled/refunded/partial
        if status in ("canceled", "partial"):
            # Sağlayıcıdan gelen opsiyonel iade tutarı:
            refund_amount = (
                data.get("refunded_amount") or
                data.get("refund_amount") or
                data.get("refund")
            )
            if refund_amount is not None:
                try:
                    refund_amount = float(refund_amount)
                except Exception:
                    refund_amount = None

            # CANCELED ise sağlayıcı tutar vermediyse full iade yap
            if refund_amount is None and status == "canceled":
                refund_amount = float(order.total_price or 0)

            # PARTIAL’da tutar gelmediyse iade yazma (sağlayıcı ne kadar iade ettiğini söylemeli)
            if refund_amount and refund_amount > 0:
                apply_refund(order_id=order.id, amount=refund_amount)

        db.session.commit()
        return {"success": True, "order_id": order.id, "status": order.status}

    except Exception as e:
        db.session.rollback()
        return {"success": False, "error": str(e)}

@app.route("/reset-registration", methods=["POST"])
def reset_registration():
    session.pop("register_sent", None)
    session.pop("register_temp_user", None)
    return redirect("/register")

@app.route('/admin/service/<int:service_id>/update', methods=['POST'])
@admin_required
def update_service(service_id):
    max_amount = int(request.form.get('max_amount', 1000))
    service = Service.query.get_or_404(service_id)
    service.max_amount = max_amount
    db.session.commit()
    flash('Servis ayarları güncellendi!', 'success')
    return redirect(url_for('manage_services'))

def fetch_resellersmm_status(api_order_id):
    try:
        resp = requests.post(EXTERNAL_API_URL, data={
            "key": EXTERNAL_API_KEY,
            "action": "status",
            "order": api_order_id
        }, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Status API Hatası: {e}")
        return {}

@app.route("/admin/tickets/reply/<int:ticket_id>", methods=["POST"], endpoint="admin_ticket_reply")
@admin_required
def admin_ticket_reply(ticket_id):
    t = db.session.get(Ticket, ticket_id)
    if not t:
        flash("Ticket bulunamadı.", "warning")
        return redirect(url_for("admin_tickets"))
    t.response = request.form.get("response", "").strip()
    t.status = request.form.get("status", "answered")
    db.session.commit()
    flash("Yanıt kaydedildi.", "success")
    return redirect(request.referrer or url_for("admin_tickets"))

@app.route("/orders/bulk_delete", methods=["POST"])
@login_required
@admin_required
def delete_orders_bulk():
    selected = request.form.get("selected_ids", "")
    id_list = [int(i) for i in selected.split(",") if i.strip().isdigit()]
    if id_list:
        Order.query.filter(Order.id.in_(id_list)).delete(synchronize_session=False)
        db.session.commit()
        flash(f"{len(id_list)} sipariş silindi!", "success")
    else:
        flash("Hiçbir sipariş seçilmedi.", "warning")
    return redirect(url_for("orders_page"))

def sync_external_order_status():
    with app.app_context():
        try:
            external_orders = Order.query.filter(
                Order.status.in_(["pending", "started"]),
                Order.api_order_id != None,
                Order.service_id >= 100000
            ).all()
            for order in external_orders:
                try:
                    result = fetch_resellersmm_status(order.api_order_id)
                    print(f"Order ID: {order.id}, API Order ID: {order.api_order_id}, API Sonuç: {result}", flush=True)
                    new_status = result.get("status", "").lower()
                    print(f"Order ID: {order.id}, new_status: {new_status}", flush=True)
                    status_map = {
                        "completed": "completed",
                        "canceled": "canceled",
                        "pending": "pending",
                        "in progress": "pending",
                        "partial": "partial"
                    }
                    mapped_status = status_map.get(new_status, order.status)
                    print(f"Order ID: {order.id}, mapped_status: {mapped_status}, mevcut status: {order.status}", flush=True)
                    if order.status != mapped_status:
                        order.status = mapped_status
                        db.session.commit()
                        print(f"[Senkron] Order {order.id}: Durum güncellendi: {mapped_status}", flush=True)
                except Exception as order_err:
                    print(f"[SYNC][ORDER][ERROR] Order ID {order.id}: {order_err}", flush=True)
        except Exception as e:
            print(f"[SYNC][ERROR] Genel hata: {e}", flush=True)
    # 180 saniye sonra tekrar çalıştır
    threading.Timer(60, sync_external_order_status).start()

import base64
import json
from flask import request, abort

import base64
import json
import hmac
import hashlib
from flask import request, jsonify

@app.route('/paytr_callback', methods=['POST'])
def paytr_callback():
    data = request.form.to_dict()

    merchant_oid = data.get("merchant_oid","")
    status = data.get("status","")
    total_amount = data.get("total_amount","")  # string, kuruş
    remote_hash = data.get("hash","") or data.get("hash_str","")

    # 1) İmza doğrulama
    cb_str = f"{merchant_oid}{PAYTR_MERCHANT_SALT}{status}{total_amount}"
    my_hash = base64.b64encode(
        hmac.new(PAYTR_MERCHANT_KEY.encode(), cb_str.encode(), hashlib.sha256).digest()
    ).decode()

    if my_hash != remote_hash:
        return "INVALID HASH", 400

    # 2) Ödeme kaydını bul
    payment = Payment.query.filter_by(merchant_oid=merchant_oid).first()
    if not payment:
        # yoksa yine 200 dön; PayTR retry döngüsüne sokma
        return "OK"

    # 3) Duruma göre güncelle
    if status == "success":
        try:
            # İsteğe göre iki kontrol:
            # a) DB'deki beklenen tutar = PayTR total_amount?
            if payment.amount_kurus != int(total_amount):
                # Tutarsızlık varsa logla, gene de istersen DB tutarını baz al
                pass

            user = User.query.get(payment.user_id)
            if user:
                user.balance += payment.amount_kurus / 100.0  # TL
            payment.status = "success"
            db.session.commit()
        except Exception:
            db.session.rollback()
            # logla (hata olsa da PayTR’a OK dön; yoksa tekrarlar)
            return "OK"
    else:
        payment.status = "failed"
        db.session.commit()

    return "OK"

import uuid

@app.route("/bakiye-yukle", methods=["GET", "POST"])
@login_required
def bakiye_yukle():
    user = User.query.get(session.get("user_id"))
    if request.method == "POST":
        amount = float(request.form.get("amount", 0))
        if amount < 1:
            return render_template_string(HTML_BAKIYE_YUKLE, msg="En az 1 TL yükleyebilirsin.")

        # --- IP çözümleme (senin kodun) ---
        def _get_client_ip():
            xff = request.headers.get("X-Forwarded-For", "")
            if xff:
                return xff.split(",")[0].strip()
            return request.remote_addr or "0.0.0.0"
        def _resolve_user_ip():
            force_ip = os.getenv("PAYTR_FORCE_IP")
            if force_ip:
                return force_ip.strip()
            ip = _get_client_ip()
            if ip.startswith(("127.", "10.", "192.168.", "172.16.")):
                return os.getenv("PAYTR_FORCE_IP", "1.1.1.1")
            return ip
        user_ip = _resolve_user_ip()

        # --- Ödeme parametreleri (senin kodun) ---
        merchant_oid = f"ORDER{user.id}{int(time.time())}"
        email = user.email
        payment_amount = int(amount * 100)  # kuruş
        user_name = user.username
        user_address = "Online"
        user_phone = "5555555555"  # opsiyonel

        # >>>>>>>>>>>> BURASI EKLEME YERİ <<<<<<<<<<<<
        # Token almadan ÖNCE, isteği DB'ye 'pending' olarak kaydet
        payment = Payment(
            merchant_oid=merchant_oid,
            user_id=user.id,
            amount_kurus=payment_amount,
            status='pending'
        )
        db.session.add(payment)
        db.session.commit()
        # >>>>>>>>>>>> EKLEME SONU <<<<<<<<<<<<

        # --- Sepet & imza (senin kodun) ---
        basket_list = [["Bakiye Yükleme", "1", f"{payment_amount/100:.2f}"]]
        user_basket = base64.b64encode(json.dumps(basket_list, ensure_ascii=False).encode("utf-8")).decode("utf-8")

        no_installment = "0"
        max_installment = "0"
        currency = "TL"
        test_mode = "0"  # CANLI'da 0; istersen hiç gönderme + hash'ten çıkar

        token_str = (
            PAYTR_MERCHANT_ID +
            user_ip +
            merchant_oid +
            email +
            str(payment_amount) +
            user_basket +
            no_installment +
            max_installment +
            currency +
            test_mode +
            PAYTR_MERCHANT_SALT
        )
        paytr_token = base64.b64encode(
            hmac.new(PAYTR_MERCHANT_KEY.encode("utf-8"), token_str.encode("utf-8"), hashlib.sha256).digest()
        ).decode("utf-8")

        paytr_args = {
            'merchant_id': PAYTR_MERCHANT_ID,
            'user_ip': user_ip,
            'merchant_oid': merchant_oid,
            'email': email,
            'payment_amount': payment_amount,
            'paytr_token': paytr_token,
            'user_basket': user_basket,
            'no_installment': no_installment,
            'max_installment': max_installment,
            'merchant_ok_url': url_for('payment_success', _external=True),
            'merchant_fail_url': url_for('payment_fail', _external=True),
            'user_name': user_name,
            'user_address': user_address,
            'user_phone': user_phone,
            'debug_on': 0,     # CANLI
            'timeout_limit': 30,
            'currency': "TL",
            'test_mode': 0,    # CANLI
            'lang': "tr"
        }

        r = requests.post("https://www.paytr.com/odeme/api/get-token", data=paytr_args)
        rj = r.json()
        if rj.get("status") == "success":
            iframe_token = rj["token"]
            iframe_html = f"""
            <div style='max-width:600px;margin:40px auto;box-shadow:0 2px 24px #0080ff22;padding:30px 8px;border-radius:20px;'>
              <iframe src="https://www.paytr.com/odeme/guvenli/{iframe_token}" frameborder="0" width="100%" height="700px" style="border-radius:16px;"></iframe>
              <p style="margin-top:18px;text-align:center;color:#fff">Ödeme işlemin bitince <a href='{url_for('bakiye_yukle')}' style="color:#8ecfff">tekrar yükleme ekranına dön</a></p>
            </div>
            """
            return render_template_string(iframe_html)
        else:
            return render_template_string(HTML_BAKIYE_YUKLE, msg=f"PayTR Hatası: {rj.get('reason', 'Bilinmeyen hata')}")

    return render_template_string(HTML_BAKIYE_YUKLE, msg=None)

@app.route('/payment_success')
def payment_success():
    # iFrame içinden gelirse, üst pencereyi yönlendir:
    return """
    <html><body style="background:#101214;color:#fff;font-family:sans-serif">
      <script>
        try {
          if (window.top && window.top !== window) {
            window.top.location.href = '/panel';
          } else {
            window.location.href = '/panel';
          }
        } catch (e) {
          window.location.href = '/panel';
        }
      </script>
      <p style="text-align:center;margin-top:40px">Ödeme işlemi tamamlandı, panele yönlendiriliyorsunuz...</p>
    </body></html>
    """

@app.route("/manage_services", methods=["GET", "POST"])
@app.route("/services/manage", methods=["GET", "POST"])  # eski yol da çalışsın
@login_required
@admin_required
def manage_services():
    from decimal import Decimal

    # --- Kullanıcı (opsiyonel, ihtiyacın varsa)
    user = current_user if getattr(current_user, "is_authenticated", False) else User.query.get(session.get("user_id"))

    # --- Kategoriler (template için)
    try:
        categories = Category.query.order_by(Category.order, Category.name).all()
    except Exception:
        categories = []

    # --- Local servisler (DB)
    local_services = Service.query.order_by(Service.id).all()
    local_ids = {s.id for s in local_services}  # template “Local/External” rozeti için referans

    # --- API'den servisleri çek (external_services)
    try:
        external_services = fetch_selected_external_services()
    except Exception:
        external_services = []

    if not external_services:
        # API yoksa / hata varsa sadece DB'yi göster, kategorileri de geçir
        flash("API'den servis çekilemedi. Lütfen bağlantını ve API'yı kontrol et!", "danger")
        return render_template_string(
            HTML_SERVICES_MANAGE,
            services=local_services,
            local_ids=local_ids,
            categories=categories
        )

    # --- API ile DB senkronizasyonu (senin 1. kodundaki mantık)
    api_ids = {getattr(s, "id", None) for s in external_services if getattr(s, "id", None) is not None}

    # 1) API'de olmayan servisleri DB'den sil
    to_delete = local_ids - api_ids
    if to_delete:
        Service.query.filter(Service.id.in_(to_delete)).delete(synchronize_session=False)
        db.session.commit()
        # local listesini güncelle
        local_services = Service.query.order_by(Service.id).all()
        local_ids = {s.id for s in local_services}

    # 2) API'den gelen ama DB'de olmayan servisleri DB'ye ekle
    to_add = api_ids - local_ids
    if to_add:
        for s in external_services:
            sid = getattr(s, "id", None)
            if sid in to_add:
                try:
                    price_val = getattr(s, "price", 0)
                    price_dec = price_val if isinstance(price_val, Decimal) else Decimal(str(price_val))
                except Exception:
                    price_dec = Decimal("0")

                db.session.add(Service(
                    id         = sid,
                    name       = getattr(s, "name", f"Ext-{sid}"),
                    description= getattr(s, "description", "") or "",
                    price      = price_dec,
                    min_amount = getattr(s, "min_amount", 1) or 1,
                    max_amount = getattr(s, "max_amount", 1000) or 1000,
                    active     = True,
                    # kategori_id = None  # istersen default kategori ata
                ))
        db.session.commit()

    # 3) POST işlemleri
    if request.method == "POST":
        # a) Kategori oluştur
        if "create_category" in request.form:
            name = (request.form.get("new_cat_name") or "").strip()
            icon = (request.form.get("new_cat_icon") or "").strip()[:8] or "📁"
            if name:
                db.session.add(Category(name=name, icon=icon))
                db.session.commit()
                flash("Kategori oluşturuldu.", "success")
            else:
                flash("Kategori adı boş olamaz.", "warning")
            return redirect(url_for("manage_services"))

        # b) Kategori sil (boşsa)
        if "delete_category" in request.form:
            try:
                cid = int(request.form.get("delete_category"))
            except Exception:
                cid = None
            if cid is not None:
                in_use = Service.query.filter_by(category_id=cid).count()
                if in_use == 0:
                    Category.query.filter_by(id=cid).delete()
                    db.session.commit()
                    flash("Kategori silindi.", "success")
                else:
                    flash("Bu kategoride bağlı servis var, önce onları taşı.", "error")
            return redirect(url_for("manage_services"))

        # c) External → Local manuel ekleme (template’inde varsa)
        if "add_external" in request.form:
            try:
                ext_id = int(request.form["add_external"])
            except Exception:
                ext_id = None

            if ext_id is not None:
                # Zaten senkron yaptığımız için büyük ihtimalle DB'de vardır; yine de kontrol et
                exists = Service.query.get(ext_id)
                if exists:
                    flash("Servis zaten veritabanında.", "warning")
                else:
                    ext = next((e for e in external_services if getattr(e, "id", None) == ext_id), None)
                    if not ext:
                        flash("External servis bulunamadı.", "error")
                    else:
                        try:
                            price_val = getattr(ext, "price", 0)
                            price_dec = price_val if isinstance(price_val, Decimal) else Decimal(str(price_val))
                        except Exception:
                            price_dec = Decimal("0")

                        db.session.add(Service(
                            id         = ext_id,
                            name       = getattr(ext, "name", f"Ext-{ext_id}"),
                            description= getattr(ext, "description", "") or "",
                            price      = price_dec,
                            min_amount = getattr(ext, "min_amount", 1) or 1,
                            max_amount = getattr(ext, "max_amount", 1000) or 1000,
                            active     = True
                        ))
                        db.session.commit()
                        flash("External servis veritabanına eklendi.", "success")
            return redirect(url_for("manage_services"))

        # d) Servis düzenlemelerini kaydet (senin 2. kodundaki + kategori)
        if "save_changes" in request.form:
            # Local servisleri yeniden çek (güncel)
            locals_now = Service.query.order_by(Service.id).all()
            local_ids_now = {s.id for s in locals_now}
            for s in locals_now:
                # sadece ‘local’ olarak değerlendirdiğin id’ler düzenlenebilir olsun istiyorsan:
                if s.id in local_ids:  # başlangıçtaki local_ids referansı
                    # İSİM
                    new_name = request.form.get(f"name_{s.id}")
                    if new_name is not None and new_name.strip():
                        s.name = new_name.strip()

                    # AÇIKLAMA
                    new_desc = request.form.get(f"desc_{s.id}")
                    if new_desc is not None:
                        s.description = new_desc

                    # FİYAT
                    price_val = request.form.get(f"price_{s.id}")
                    if price_val not in (None, ""):
                        try:
                            s.price = Decimal(str(price_val))
                        except Exception:
                            pass

                    # MAX
                    max_val = request.form.get(f"max_{s.id}")
                    if max_val not in (None, ""):
                        try:
                            s.max_amount = int(max_val)
                        except Exception:
                            pass

                    # KATEGORİ
                    cat_val = request.form.get(f"category_{s.id}")
                    s.category_id = int(cat_val) if (cat_val and cat_val.isdigit()) else None

            db.session.commit()
            flash("Düzenlemeler kaydedildi.", "success")
            return redirect(url_for("manage_services"))

        # e) Eski POST formatın (name_X/desc_X/price_X) – save_changes yoksa
        #    (1. kodundaki güncelleme döngüsü)
        updated_any = False
        for svc in Service.query.order_by(Service.id).all():
            nk = f"name_{svc.id}"
            dk = f"desc_{svc.id}"
            pk = f"price_{svc.id}"
            if nk in request.form:
                svc.name        = request.form[nk].strip() or svc.name
                svc.description = request.form.get(dk, "").strip() or svc.description
                try:
                    np = request.form.get(pk)
                    if np not in (None, ""):
                        np_f = float(np)
                        if np_f > 0:
                            try:
                                svc.price = Decimal(str(np_f))
                            except Exception:
                                svc.price = np_f
                except Exception:
                    pass
                updated_any = True

        if updated_any:
            db.session.commit()
            flash("Servisler başarıyla güncellendi.", "success")
            return redirect(url_for("manage_services"))

    # --- Son durumda güncel DB’yi göster
    services = Service.query.order_by(Service.id).all()
    return render_template_string(
        HTML_SERVICES_MANAGE,
        services=services,
        local_ids=local_ids,     # başlangıçta DB’de olanlar; template bunları “Local” diye rozetler
        categories=categories
    )

@app.route('/payment_fail')
def payment_fail():
    return """
    <html><body style="background:#101214;color:#fff;font-family:sans-serif">
      <script>
        try {
          if (window.top && window.top !== window) {
            window.top.location.href = '/bakiye-yukle';
          } else {
            window.location.href = '/bakiye-yukle';
          }
        } catch (e) {
          window.location.href = '/bakiye-yukle';
        }
      </script>
      <p style="text-align:center;margin-top:40px">Ödeme başarısız/iptal. Tekrar deneyin...</p>
    </body></html>
    """

@app.route('/google6aef354bd638dfc4.html')
def google_verify():
    return "google-site-verification: google6aef354bdd638dfc4.html", 200, {'Content-Type': 'text/html; charset=utf-8'}

# DOSYANIN EN SONUNA KOY!
sync_external_order_status()

@app.route('/robots.txt')
def robots_txt():
    return app.send_static_file('robots.txt')

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))