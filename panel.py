import os
from dotenv import load_dotenv
load_dotenv()
# PayTR credentials
PAYTR_MERCHANT_ID = os.getenv("PAYTR_MERCHANT_ID", "")
PAYTR_MERCHANT_KEY = os.getenv("PAYTR_MERCHANT_KEY", "")
PAYTR_MERCHANT_SALT = os.getenv("PAYTR_MERCHANT_SALT", "")

import time
import random
import smtplib
import threading
from datetime import datetime, timedelta
from email.mime.text import MIMEText

from flask import (
    Flask, session, request, redirect,
    render_template_string, abort, url_for, flash, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

from flask import Flask, session, redirect, request, render_template_string

import requests

TELEGRAM_BOT_TOKEN = "8340662506:AAHwcqKMsGlQ08mlOVTXT2xAUC6vjH3_r20"  # Başında 'bot' yok!
TELEGRAM_CHAT_ID = "6744917275"

def telegram_mesaj_gonder(mesaj):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mesaj,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, data=payload)
        print("Telegram response:", response.text)
        return response.ok
    except Exception as e:
        print("Telegram Hatası:", e)
        return False

import requests  # ← Harici servis için
import json
from functools import wraps

# --- Harici servis entegrasyonu (ResellersMM) ---
EXTERNAL_API_URL = "https://resellersmm.com/api/v2/"
EXTERNAL_API_KEY = "6b0e961c4a42155ba44bfd4384915c27"

# --- Platform algılama & manuel override ---
PLATFORM_OVERRIDES = {
    # Ör: 100000 + SAĞLAYICI_SERVIS_ID : "tiktok" / "youtube"
    # 100000 + 2273: "tiktok",
    # 100000 + 2111: "tiktok",
    # 100000 + 922: "youtube",
    # 100000 + 942: "youtube",
}

def detect_platform(*parts: str) -> str:
    t = (" ".join([p or "" for p in parts])).lower()
    if any(k in t for k in ["tiktok", "tik tok", "tt ", "douyin"]):
        return "tiktok"
    if any(k in t for k in ["youtube", "yt ", " y.t", "shorts", "abon", "subscriber"]):
        return "youtube"
    return "instagram"

# --- Çekmek istediğimiz ResellersMM servis ID’leri ---

EXT_SELECTED_IDS = [1192, 1231, 1593, 1594, 831, 2273, 2111, 922, 942, 2037, 913,]  # Seçili servisleri çek
  # Örneğin sadece 1 ve 2 no’lu servisleri çek

def fetch_selected_external_services():
    """Sadece EXT_SELECTED_IDS’deki ResellersMM servislerini çeker, hem dict hem list olanağı var."""
    try:
        resp = requests.get(
            EXTERNAL_API_URL,
            params={"key": EXTERNAL_API_KEY, "action": "services"},
            timeout=10
        )
        resp.raise_for_status()
        payload = resp.json()

        # payload dict ise içindeki 'data'yı, değilse (zaten list ise) kendisini al
        if isinstance(payload, dict):
            data = payload.get("data", [])
        else:
            data = payload

        # Burada artık data kesinlikle bir list
        # Filtreleme:
        filtered = [
            item for item in data
            if int(item.get("service", 0)) in EXT_SELECTED_IDS
        ]

        services = []
        for item in filtered:
            svc = Service(
                id=100000 + int(item["service"]),
                name=item.get("name","İsim yok"),
                description=item.get("description", item.get("name","")),
                price=float(item.get("rate", 0)),
                min_amount=int(item.get("min",1)),
                max_amount=int(item.get("max",1)),
                active=True
            )
            # platform belirle (override > otomatik tespit)
            plat = PLATFORM_OVERRIDES.get(svc.id) or detect_platform(item.get("category",""), item.get("name",""))
            setattr(svc, "platform", plat)
            services.append(svc)

        return services

    except Exception as e:
        print("❌ fetch_selected_external_services hata:", e)
        return []

# --- /Harici servis entegrasyonu ---

# --- External servis seçim mekanizması ---
EXT_SELECTION_FILE = "ext_selection.json"

def load_selected_ext_ids():
    try:
        with open(EXT_SELECTION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def save_selected_ext_ids(ids):
    with open(EXT_SELECTION_FILE, "w", encoding="utf-8") as f:
        json.dump(ids, f)

def durum_turkce(status):
    mapping = {
        "completed": "Tamamlandı",
        "pending": "Sırada",
        "started": "Sırada",
        "canceled": "İptal Edildi",
        "cancelled": "İptal Edildi",
        "partial": "Kısmen Tamamlandı"
    }
    return mapping.get(status, status)

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = User.query.get(session.get("user_id"))
        if not user or user.role != "admin":
            abort(403)
        return f(*args, **kwargs)
    return wrapper

def sync_services_with_api(api_services):
    """
    api_services: API'den gelen tüm servis objeleri listesi (veya dict listesi)
    Buradan servis ID'leri alınacak ve veri tabanında olmayanlar silinecek.
    """
    # Eğer api_services bir obje/dict ise id'leri çıkar:
    if hasattr(api_services[0], "id"):
        api_service_ids = set(s.id for s in api_services)
    elif isinstance(api_services[0], dict):
        api_service_ids = set(s["id"] for s in api_services)
    else:
        return  # Liste boşsa

    db_services = Service.query.all()
    db_service_ids = set(s.id for s in db_services)

    # Sadece API'de olmayan (eski) servisleri bul
    to_delete = db_service_ids - api_service_ids
    if to_delete:
        Service.query.filter(Service.id.in_(to_delete)).delete(synchronize_session=False)
        db.session.commit()

# --- /External servis seçim mekanizması ---

app = Flask(__name__)
app.url_map.strict_slashes = False
app.secret_key = os.getenv("SECRET_KEY", "çok-gizli-bir-anahtar")
app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://neondb_owner:npg_r0Vg1Gospfmt@ep-old-firefly-a23lm21m-pooler.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 280
}

db = SQLAlchemy(app)

SABIT_FIYAT = 0.5

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
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), unique=True, nullable=False)
    description = db.Column(db.String(512))
    price = db.Column(db.Float, nullable=False)
    min_amount = db.Column(db.Integer, default=1)
    max_amount = db.Column(db.Integer, default=1000)
    active = db.Column(db.Boolean, default=True)

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

with app.app_context():
    db.create_all()
    # Admin, Service ve AdVideo başlangıç kayıtları
    if not User.query.filter_by(username="admin").first():
        db.session.add(User(
            username="admin",
            password_hash=generate_password_hash("6906149Miko"),
            email="kuzenlertv6996@gmail.com",
            role="admin",
            balance=1000,
            is_verified=True
        ))
        db.session.commit()
    if not Service.query.first():
        db.session.add(Service(
            name="Instagram Takipçi",
            description="Gerçek ve Türk takipçi gönderimi.",
            price=SABIT_FIYAT,
            min_amount=1,
            max_amount=1000,
            active=True
        ))
        db.session.commit()
    if not AdVideo.query.first():
        db.session.add(AdVideo(embed_url="https://www.youtube.com/embed/KzJk7e7XF3g"))
        db.session.commit()

# --- SMTP AYARLARI (mail ile ilgili) ---
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_ADDR = "kuzenlertv6996@gmail.com"
SMTP_PASS = "nurkqldoqcaefqwk"
def send_verification_mail(email, code):
    subject = "Kayıt Doğrulama Kodunuz"
    body = f"Merhaba,\n\nKayıt işlemini tamamlamak için doğrulama kodunuz: {code}\n\nİnsprov.uk Ekibi"
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

HTML_LOGIN = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Baybayim - Sosyal Medya Hizmetleri</title>
  <meta name="description" content="Baybayim ile Instagram, TikTok, YouTube gibi tüm platformlara hızlı ve güvenilir sosyal medya hizmetleri. Hemen kaydol, avantajları kaçırma!">
  <link rel="icon" href="{{ url_for('static', filename='favicon.ico') }}" type="image/x-icon">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">

  <!-- Open Graph Meta -->
  <meta property="og:title" content="Baybayim - Sosyal Medya Hizmetleri">
  <meta property="og:description" content="Baybayim ile Instagram, TikTok, YouTube gibi tüm platformlara hızlı ve güvenilir sosyal medya hizmetleri. Hemen kaydol, avantajları kaçırma!">
  <meta property="og:type" content="website">
  <meta property="og:url" content="https://baybayim.com/">
  <meta property="og:image" content="https://baybayim.com/static/logo.png"> <!-- LOGO YOLUNU KENDİNE GÖRE AYARLA -->
  <meta property="og:locale" content="tr_TR">

  <!-- Twitter Card Meta -->
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="Baybayim - Sosyal Medya Hizmetleri">
  <meta name="twitter:description" content="Baybayim ile Instagram, TikTok, YouTube gibi tüm platformlara hızlı ve güvenilir sosyal medya hizmetleri. Hemen kaydol, avantajları kaçırma!">
  <meta name="twitter:image" content="https://baybayim.com/static/logo.png"> <!-- LOGO YOLUNU KENDİNE GÖRE AYARLA -->
  <!-- <meta name="twitter:site" content="@baybayim"> -->
  <style>
    body {
      margin: 0;
      height: 100vh;
      background: linear-gradient(-45deg, #121212, #1e1e1e, #212121, #000000);
      background-size: 400% 400%;
      animation: gradientBG 12s ease infinite;
      color: #fff;
      overflow: hidden;
      position: relative;
    }
    @keyframes gradientBG {
      0% {background-position: 0% 50%;}
      50% {background-position: 100% 50%;}
      100% {background-position: 0% 50%;}
    }
    .form-control,
    .form-control:focus {
      background-color: #2c2c2c;
      color: #f1f1f1;
      border: 1px solid #555;
    }
    ::placeholder {
      color: #aaa;
      opacity: 1;
    }
    .card {
      background-color: #1b1b1b;
      color: #fff;
      border-radius: 16px;
      z-index: 2;
      position: relative;
    }
    .logo-img {
      width: 62px;
      height: 62px;
      display: block;
      margin: 0 auto 12px auto;
      border-radius: 20%;
      box-shadow: 0 4px 16px #0005;
      object-fit: contain;
      background: #232323;
    }
    .modern-title {
      font-family: 'Montserrat', 'Segoe UI', Arial, sans-serif;
      font-size: 2.6rem;
      font-weight: 900;
      text-align: center;
      letter-spacing: 0.01em;
      margin-bottom: 20px;
      background: linear-gradient(92deg, #58a7ff 10%, #b95cff 65%, #2feea3 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
      text-shadow: 0 4px 24px #000c, 0 2px 2px #1e254430;
      filter: none;
      line-height: 1.1;
      transition: all .25s;
      padding-bottom: 2px;
      text-transform: uppercase;
    }
    .alert-custom {
      background-color: #1f1f1f;
      color: #fff;
      border-left: 4px solid #0d6efd;
      padding: 10px 12px;
      border-radius: 6px;
      font-size: 0.95rem;
      margin-bottom: 1rem;
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
  </style>
</head>
<body class="d-flex justify-content-center align-items-center">
  <!-- Hareketli Sosyal Medya İkonları Arka Planı -->
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

  <div class="card shadow p-4" style="min-width:340px; z-index:2; position:relative;">
    <img src="{{ url_for('static', filename='logo.png') }}" alt="Logo" class="logo-img">
    <div class="modern-title">BAYBAYİM</div>
    {% with messages = get_flashed_messages() %}
      {% if messages %}
        <div class="alert-custom text-center">
          {% for message in messages %}
            {{ message }}<br>
          {% endfor %}
        </div>
      {% endif %}
    {% endwith %}
    <form method="post">
      <div class="mb-2">
        <label class="form-label">Kullanıcı Adı:</label>
        <input name="username" class="form-control" placeholder="">
      </div>
      <div class="mb-3">
        <label class="form-label">Şifre:</label>
        <input name="password" type="password" class="form-control" placeholder="">
      </div>
      <button class="btn btn-primary w-100">Giriş</button>
    </form>
    <div class="text-center mt-2">
      <a href="/register" class="btn btn-link btn-sm">Kayıt Ol</a>
    </div>
  </div>
</body>
</html>
"""

HTML_REGISTER = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <link rel="icon" href="{{ url_for('static', filename='favicon.ico') }}" type="image/x-icon">
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kayıt Ol</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body {
      margin: 0;
      height: 100vh;
      background: linear-gradient(-45deg, #121212, #1e1e1e, #212121, #000000);
      background-size: 400% 400%;
      animation: gradientBG 12s ease infinite;
      color: #fff;
      overflow: hidden;
      position: relative;
    }
    @keyframes gradientBG {
      0% {background-position: 0% 50%;}
      50% {background-position: 100% 50%;}
      100% {background-position: 0% 50%;}
    }
    .form-control,
    .form-control:focus {
      background-color: #2c2c2c;
      color: #f1f1f1;
      border: 1px solid #555;
    }
    ::placeholder {
      color: #aaa;
      opacity: 1;
    }
    .card {
      background-color: #1b1b1b;
      color: #fff;
      border-radius: 16px;
      z-index: 2;
      position: relative;
    }
    .logo-img {
      width: 62px;
      height: 62px;
      display: block;
      margin: 0 auto 12px auto;
      border-radius: 20%;
      box-shadow: 0 4px 16px #0005;
      object-fit: contain;
      background: #232323;
    }
    .modern-title-row {
      display: flex;
      justify-content: center;
      align-items: center;
      gap: 14px;
      margin-bottom: 8px;
      margin-top: 3px;
    }
    .modern-title,
    .modern-title-register {
      font-family: 'Montserrat', 'Segoe UI', Arial, sans-serif;
      font-size: 2.6rem;
      font-weight: 900;
      letter-spacing: 0.01em;
      text-transform: uppercase;
      line-height: 1.13;
      background: linear-gradient(92deg, #58a7ff 10%, #b95cff 65%, #2feea3 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
      text-shadow: 0 4px 24px #000c, 0 2px 2px #1e254430;
      filter: none;
      transition: all .25s;
      display: inline-block;
      padding: 0 2px;
    }
    .modern-title-register {
      background: linear-gradient(90deg, #14fff1 0%, #4294ff 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
      text-shadow: 0 2px 10px #00485a55;
      margin-left: 2px;
      padding: 0 2px;
      font-size: 2.6rem;
    }
    .spaced-link {
      display: block;
      margin-top: 10px;
    }
    .custom-alert {
      background-color: #292929;
      border-left: 5px solid #4da3ff;
      padding: 12px 15px;
      border-radius: 6px;
      color: #fff;
      font-size: 0.92rem;
      margin-bottom: 18px;
      text-align: center;
    }
    .text-danger.btn-link {
      margin-top: 8px;
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
  </style>
</head>
<body class="d-flex justify-content-center align-items-center">
  <!-- Hareketli Sosyal Medya İkonları Arka Planı -->
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
  <div class="card shadow p-4" style="min-width:370px;">
    <img src="{{ url_for('static', filename='logo.png') }}" alt="Logo" class="logo-img">
    <div class="modern-title-row">
      <span class="modern-title">BAYBAYİM</span>
      <span class="modern-title-register">KAYIT</span>
    </div>
    {% with messages = get_flashed_messages() %}
      {% if messages %}
        {% for message in messages %}
          <div class="custom-alert">
            {{ message }}
          </div>
        {% endfor %}
      {% endif %}
    {% endwith %}
    {% if not sent %}
      <form method="post">
        <div class="mb-2">
          <label class="form-label">Kullanıcı Adı:</label>
          <input name="username" class="form-control" placeholder="" required>
        </div>
        <div class="mb-2">
          <label class="form-label">Şifre:</label>
          <input name="password" type="password" class="form-control" placeholder="" required>
        </div>
        <div class="mb-3">
          <label class="form-label">E-Posta:</label>
          <input name="email" type="email" class="form-control" placeholder="" required>
        </div>
        <button class="btn btn-success w-100 mb-2">Kayıt Ol</button>
      </form>
    {% else %}
      <form method="post">
        <div class="mb-3">
          <label class="form-label">E-Posta Adresinize Gönderilen Kod:</label>
          <input name="verify_code" class="form-control" placeholder="" required>
        </div>
        <button class="btn btn-primary w-100 mb-2">Kodu Doğrula</button>
      </form>
      <form method="post" action="/reset-registration" class="text-center">
        <button type="submit" class="btn btn-link btn-sm text-decoration-none text-danger">Kayıt İşleminden Vazgeç</button>
      </form>
    {% endif %}
    <div class="text-center mt-2">
      <a href="/" class="btn btn-link btn-sm text-decoration-none spaced-link">Giriş Yap</a>
    </div>
  </div>
</body>
</html>
"""

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
    .form-control::placeholder {
      color: #aaa;
    }
    input[type=number]::-webkit-inner-spin-button,
    input[type=number]::-webkit-outer-spin-button {
      -webkit-appearance: none;
      margin: 0;
    }
    input[type=number] {
      -moz-appearance: textfield;
      appearance: textfield;
    }
    .table-dark {
      background-color: #2c2c2c;
    }
    .table-dark th, .table-dark td {
      color: #eee;
    }
    a {
      color: #8db4ff;
    }
    a:hover {
      color: #fff;
      text-decoration: underline;
    }
    .btn {
      font-weight: 500;
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
        <table class="table table-dark table-striped table-bordered align-middle mb-4">
          <thead>
            <tr>
              <th>#</th><th>Kullanıcı</th><th>Rol</th><th>Bakiye</th><th>İşlem</th>
            </tr>
          </thead>
          <tbody>
            {% for usr in users %}
              <tr>
                <td>{{ loop.index }}</td>
                <td>{{ usr.username }}</td>
                <td>{{ rolu_turkce(usr.role) }}</td>
                <td>{{ usr.balance }}</td>
                <td>
                  {% if usr.username != current_user %}
                    <a href="{{ url_for('delete_user', user_id=usr.id) }}" class="btn btn-danger btn-sm">Sil</a>
                  {% else %}
                    <span class="text-muted">–</span>
                  {% endif %}
                </td>
              </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
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

HTML_SERVICES_MANAGE = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <link rel="icon" href="{{ url_for('static', filename='favicon.ico') }}" type="image/x-icon">
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Servisleri Yönet</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"/>
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
    .form-control, .form-control-sm, .form-select {
      background-color: #2e2e2e !important;
      color: #f1f1f1 !important;
      border: 1px solid #444;
      box-shadow: none;
    }
    .form-control:focus, .form-control-sm:focus, .form-select:focus {
      background-color: #2e2e2e !important;
      color: #fff !important;
      border-color: #666;
      box-shadow: none;
    }
    .form-control::placeholder,
    .form-control-sm::placeholder {
      color: #aaa;
    }
    .table-dark th, .table-dark td {
      color: #eee;
    }
    .btn {
      font-weight: 500;
    }
    a {
      color: #8db4ff;
    }
    a:hover {
      color: #fff;
      text-decoration: underline;
    }
    input[type=number]::-webkit-inner-spin-button,
    input[type=number]::-webkit-outer-spin-button {
      -webkit-appearance: none;
      margin: 0;
    }
    input[type=number] {
      -moz-appearance: textfield;
      appearance: textfield;
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
  <div class="card mx-auto" style="max-width:800px;">
    <div class="card-body">
      <h3>Servisleri Yönet</h3>
      <form method="post" action="{{ url_for('manage_services') }}">
        <table class="table table-dark table-striped align-middle">
          <thead>
            <tr>
              <th>ID</th>
              <th>Servis</th>
              <th>Açıklama</th>
              <th>Fiyat (TL)</th>
              <th>Min</th>
              <th>Max</th>
              <th>Kaynak</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
          {% for s in services %}
            <tr>
              <td>{{ s.id }}</td>
              <td>
                <input name="name_{{s.id}}" class="form-control form-control-sm"
                       value="{{ s.name }}" {% if s.id not in local_ids %}readonly{% endif %}>
              </td>
              <td>
                <input name="desc_{{s.id}}" class="form-control form-control-sm"
                       value="{{ s.description }}" {% if s.id not in local_ids %}readonly{% endif %}>
              </td>
              <td style="width:100px">
                <input type="number" step="any" min="0" name="price_{{ s.id }}"
                       class="form-control form-control-sm"
                       value="{{ '{:.5f}'.format(s.price) if s.price is not none else '' }}"
              </td>
              <td>
                {{ s.min_amount }}
              </td>
              <td>
                <input name="max_{{s.id}}" type="number" min="{{ s.min_amount }}" class="form-control form-control-sm"
                       value="{{ s.max_amount }}" {% if s.id not in local_ids %}readonly{% endif %}>
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
                <button type="submit" name="add_external" value="{{ s.id }}" class="btn btn-sm btn-primary">
                  Veritabanına Ekle
                </button>
                {% endif %}
              </td>
            </tr>
          {% endfor %}
          </tbody>
        </table>
        <div class="d-grid">
          <button class="btn btn-success" type="submit">Düzenlemeleri Kaydet</button>
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
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Bakiye Yükle</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"/>
  <style>
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
            <input type="number" min="1" step="1" class="form-control" id="amount" name="amount" placeholder="100" required>
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
  <p><strong>İletişim:</strong> kuzenlertv6996@gmail.com – 0530 190 09 69</p>
  <p><strong>Adres:</strong> Mustafa Kemal Paşa Mahallesi, Lale Sokak No:110 D:1</p>
</div>
</body>
</html>
"""

HTML_SERVICES = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <link rel="icon" href="{{ url_for('static', filename='favicon.ico') }}" type="image/x-icon">
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Servisler & Fiyat Listesi</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"/>
  <style>
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
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Ticket Yönetimi (Admin)</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
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

    .form-control::placeholder {
      color: #aaa;
    }

    .table-dark {
      background-color: #1f1f1f;
    }

    .table-dark td, .table-dark th {
      color: #e6e6e6;
    }

    .btn-success,
    .btn-secondary {
      font-weight: 500;
    }

    .text-muted {
      color: #bbb !important;
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
  <title>Dış Servis Seçimi</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"/>
  <style>
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
    .form-control::placeholder {
      color: #aaa;
    }
    .table-dark {
      background-color: #1f1f1f;
    }
    .table-dark td, .table-dark th {
      color: #e6e6e6;
    }
    .btn {
      font-weight: 500;
    }
    h3 {
      color: #f8f9fa;
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
    <title>Geçmiş Siparişler</title>
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
        ::-webkit-scrollbar {
          width: 0px;
          height: 0px;
          background: transparent;
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
                            <th>
                                <input type="checkbox" id="select-all-orders" title="Tümünü seç/bırak" />
                            </th>
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
                            <td>
                                <input type="checkbox" name="order_ids" value="{{ o.id }}">
                            </td>
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
            <a href="{{ url_for('panel') }}" class="btn btn-secondary w-100 mt-4" style="border-radius:12px;">Panele Dön</a>
        </div>
    </div>
    {% if role == 'admin' %}
    <script>
    // Tümünü Seç
    document.getElementById('select-all-orders').addEventListener('change', function(e) {
        let checked = this.checked;
        document.querySelectorAll('input[name="order_ids"]').forEach(function(cb) {
            cb.checked = checked;
        });
    });

    // Form submitte seçili id'leri gizli input'a yaz
    document.getElementById('bulk-delete-form').addEventListener('submit', function(e) {
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
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Destek Taleplerim</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"/>
  <style>
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
    @keyframes gradientBG {
      0% {background-position: 0% 50%;}
      50% {background-position: 100% 50%;}
      100% {background-position: 0% 50%;}
    }
    .card {
      background-color: rgba(0, 0, 0, 0.7);
      border-radius: 16px;
      box-shadow: 0 4px 20px rgba(0,0,0,0.4);
      z-index: 2;
      position: relative;
    }
    .form-control, .form-select, textarea {
      background-color: #1e1e1e;
      border-color: #444;
      color: #fff;
    }
    .form-control:focus, .form-select:focus, textarea:focus {
      background-color: #1e1e1e;
      border-color: #2186eb;
      color: #fff;
      box-shadow: none;
    }
    .form-control::placeholder,
    textarea::placeholder {
      color: #bbb;
    }
    .table-dark {
      background-color: #1f1f1f;
    }
    .table-dark th, .table-dark td {
      color: #e6e6e6;
    }
    .badge.bg-warning.text-dark {
      color: #000 !important;
    }
    h3, h5 {
      color: #61dafb;
      text-shadow: 0 2px 12px rgba(0,0,0,0.4);
    }
    a {
      color: #8db4ff;
    }
    a:hover {
      color: #fff;
      text-decoration: underline;
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
    <div class="card p-4 mx-auto" style="max-width:800px;">
      <h3 class="mb-4 text-center">Destek & Canlı Yardım</h3>
      <form method="post" class="mb-4">
        <div class="mb-2">
          <input name="subject" class="form-control" placeholder="Konu" required>
        </div>
        <div class="mb-2">
          <textarea name="message" class="form-control" placeholder="Mesajınız" rows="3" required></textarea>
        </div>
        <button class="btn btn-primary w-100">Gönder</button>
      </form>

      <h5 class="mt-4">Geçmiş Talepleriniz</h5>
      <div class="table-responsive">
        <table class="table table-dark table-bordered table-sm text-center align-middle">
          <thead>
            <tr>
              <th>Konu</th>
              <th>Mesaj</th>
              <th>Tarih</th>
              <th>Durum</th>
              <th>Yanıt</th>
            </tr>
          </thead>
          <tbody>
          {% for t in tickets %}
            <tr>
              <td>{{ t.subject }}</td>
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
            </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>

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
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Sipariş Paneli</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" />
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
  <style>
    body {
      background: #181c20 !important;
      color: #fff;
      font-family: 'Segoe UI', Arial, sans-serif;
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
    /* --- Senin diğer CSS kodların burada aynı şekilde devam ediyor --- */
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
    .btn-panel-dark b {
      color: #fff;
      font-weight: 900;
      letter-spacing: 0.02em;
    }
    .btn-panel-outline {
      background: transparent;
      color: #8ec9fd;
      border: 2px solid #31353a;
      border-radius: 10px;
      font-weight: 700;
      padding: 0.72rem 1.2rem;
      font-size: 1.01rem;
      margin-bottom: 2px;
      width: 100%;
      box-shadow: none;
      transition: background 0.21s, color .15s, border .2s, box-shadow .17s;
      opacity: 1;
    }
    .btn-panel-outline:hover, .btn-panel-outline:focus {
      background: #222730;
      color: #41d1ff;
      border-color: #43b3fa;
      outline: none;
      opacity: 1;
    }
    .btn-custom-outline {
      background: transparent;
      border: 1.5px solid #50555c;
      color: #c2c8d7;
      border-radius: 8px;
      transition: all .18s;
    }
    .btn-custom-outline:hover, .btn-custom-outline:focus {
      background: #22262c;
      color: #fff;
      border-color: #2186eb;
    }
    .form-control, .form-select {
      background: #23272b;
      border: 1.5px solid #323740;
      color: #e7eaf0;
      border-radius: 8px;
      transition: border .17s, box-shadow .17s;
    }
    .form-control:focus, .form-select:focus {
      border-color: #2186eb;
      color: #fff;
      background: #23272b;
      box-shadow: 0 0 0 0.10rem #2186eb40;
      outline: none;
    }
    .form-label {
      color: #f0f0f2;
      font-weight: 600;
      font-size: 1rem;
    }
    .alert-secondary {
      background: #23272b;
      color: #c3cad8;
      border: none;
      border-radius: 8px;
    }
    .welcome-card {
      background: linear-gradient(100deg, #242a2f 0%, #181c20 80%);
      border-radius: 15px;
      padding: 22px 26px 15px 18px;
      margin-bottom: 22px;
      box-shadow: 0 3px 18px 0 rgba(0,0,0,0.23);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .welcome-icon {
      font-size: 2.5rem;
      color: #2186eb;
      margin-right: 13px;
      margin-top: 3px;
    }
    .welcome-title {
      font-weight: 800;
      font-size: 1.16rem;
      margin-bottom: 0.2rem;
      color: #fff;
      letter-spacing: 0.015em;
    }
    .welcome-desc {
      font-size: 0.95rem;
      color: #c5c8d4;
    }
    .welcome-balance {
      font-size: 1.13rem;
      color: #fff;
      font-weight: 700;
      margin-bottom: 0.18rem;
      text-align: right;
    }
    .welcome-balance-label {
      color: #fff !important;
      font-weight: 900 !important;
      letter-spacing: .01em;
    }
    .welcome-balance-value {
      color: #41b6ff !important;
      font-weight: 900 !important;
      letter-spacing: .01em;
      font-size: 1.12em;
    }
    .order-title-center {
      width: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 1.6rem;
      font-weight: 900;
      color: #22b3ff;
      letter-spacing: .018em;
      margin-bottom: 18px;
      margin-top: 18px;
      text-shadow: 0 4px 24px #22b3ff1a;
      gap: 12px;
      position: relative;
      min-height: 54px;
    }
    .order-title-center .bi {
      color: #22b3ff;
      font-size: 1.45em;
      margin-right: 7px;
    }
    @media (max-width: 767px) {
      .order-title-center { font-size: 1.2rem; gap: 7px; min-height: 34px; }
      .order-title-center .bi { font-size: 1.12em; }
    }
    input[type=number]::-webkit-inner-spin-button, 
    input[type=number]::-webkit-outer-spin-button { 
      -webkit-appearance: none; 
      margin: 0; 
    }
    input[type=number] { 
      -moz-appearance: textfield;
      appearance: textfield;
    }
    .form-total-custom {
      background: #23272b !important;
      border: 1.5px solid #323740 !important;
      color: #4fe9ff !important;
      border-radius: 8px !important;
      font-size: 1.21em !important;
      font-weight: 800 !important;
      letter-spacing: .01em;
      padding-left: 14px !important;
      padding-right: 14px !important;
      transition: border .16s, box-shadow .15s;
      box-shadow: none;
      min-height: 44px;
      text-align: left;
    }
    .form-total-custom:disabled {
      background: #23272b !important;
      color: #4fe9ff !important;
      opacity: 1;
    }
    @media (max-width: 575px) {
      .welcome-card { flex-direction: column; align-items: flex-start; gap: 12px; }
      .welcome-balance { text-align: left; }
      .order-title-center { font-size: 1.05rem; gap: 6px; min-height: 27px; }
      .form-total-custom { font-size: 1.06em !important; }
    }
    .flash-info-box {
      margin-bottom: 15px;
      border-radius: 8px;
      font-weight: 600;
      font-size: 1.04em;
      padding: 10px 20px;
      border-left: 5px solid #00e1ff;
      background: #18242d;
      color: #51f5ff;
      box-shadow: 0 2px 10px 0 #00e1ff33;
      animation: fadeinflash .5s;
    }
    .flash-info-box.error {
      border-left: 5px solid #ff6363;
      background: #2a1818;
      color: #ffc7c7;
      box-shadow: 0 2px 10px 0 #ff636633;
    }
    @keyframes fadeinflash {
      from { opacity: 0; transform: translateY(-18px);}
      to   { opacity: 1; transform: translateY(0);}
        /* Modern WhatsApp Butonu */
    }
    #whatsapp-float {
      position: fixed;
      right: 32px;
      bottom: 42px;
      width: 62px;
      height: 62px;
      border-radius: 50%;
      background: linear-gradient(135deg, #25D366 80%, #075E54 100%);
      box-shadow: 0 6px 32px 0 #25d36648, 0 1.5px 10px 0 #00000020;
      color: #fff;
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 11000;
      cursor: pointer;
      transition: transform .19s cubic-bezier(.27,1.4,.62,.97), box-shadow .22s;
      border: none;
      animation: whatsapp-float-pop .7s cubic-bezier(.21,1.4,.72,1) 1;
      overflow: hidden;
    }
    #whatsapp-float:hover {
      transform: scale(1.08) translateY(-3px);
      box-shadow: 0 12px 48px 0 #25d36684, 0 3px 16px 0 #00000020;
      color: #fff;
      background: linear-gradient(135deg, #24ff7d 70%, #128C7E 100%);
      text-decoration: none;
    }
    #whatsapp-float .bi-whatsapp {
      font-size: 2.2em;
      filter: drop-shadow(0 1px 7px #13f85d66);
    }
    #whatsapp-float-text {
      position: absolute;
      right: 74px;
      bottom: 0px;
      font-size: 1.08em;
      background: #25d366;
      color: #0b3e1b;
      border-radius: 14px 0 0 14px;
      padding: 10px 20px 10px 18px;
      white-space: nowrap;
      box-shadow: 0 4px 20px 0 #25d36626;
      opacity: 0;
      pointer-events: none;
      font-weight: 800;
      letter-spacing: 0.02em;
      transition: opacity 0.23s;
    }
    #whatsapp-float:hover #whatsapp-float-text,
    #whatsapp-float:focus #whatsapp-float-text {
      opacity: 1;
    }
    @media (max-width:600px){
      #whatsapp-float { right: 14px; bottom: 18px; width: 48px; height: 48px; }
      #whatsapp-float .bi-whatsapp { font-size: 1.34em; }
      #whatsapp-float-text { display: none; }
    }
    @keyframes whatsapp-float-pop {
      0% {transform:scale(0.75) translateY(60px);}
      70% {transform:scale(1.13) translateY(-12px);}
      100% {transform:scale(1) translateY(0);}
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
<!-- BUTONLAR -->
<div class="d-grid gap-3 mb-3">
  {% if role == 'admin' %}
    <a href="{{ url_for('manage_users') }}" class="btn btn-panel-dark py-2"><b>Kullanıcı Yönetimi</b></a>
    <a href="{{ url_for('admin_tickets') }}" class="btn btn-panel-dark py-2">Tüm Destek Talepleri</a>
    <a href="{{ url_for('manage_services') }}" class="btn btn-panel-dark py-2">Servisleri Yönet</a>
  {% else %}
    <a href="{{ url_for('bakiye_yukle') }}" class="btn btn-panel-dark py-2">Bakiye Yükle</a>
    <a href="{{ url_for('tickets') }}" class="btn btn-panel-dark py-2">Destek & Canlı Yardım</a>
    {% if role in ['user', 'viewer'] %}
        </button>
      </form>
    {% endif %}
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
  <div class="mb-3">
    <label class="form-label"><i class="bi bi-star-fill text-warning"></i> Kategori</label>
    <select class="form-select" name="category" required>
      <option value="🌐 Tüm Sosyal Medya Servisleri 📱" selected>🌐 Tüm Sosyal Medya Servisleri 📱</option>
    </select>
  </div>
  <div class="mb-3">
    <label class="form-label"><i class="bi bi-box-seam"></i> Servis</label>
    <select class="form-select" name="service_id" id="service_id" required>
      {% for s in services %}
        <option value="{{ s.id }}" data-price="{{ s.price }}" data-min="{{ s.min_amount }}" data-max="{{ s.max_amount }}">
          {{ s.name }} – {{ s.price }} TL
        </option>
      {% endfor %}
    </select>
  </div>
  <div class="mb-3">
    <label class="form-label"><i class="bi bi-info-circle"></i> Açıklama</label>
    <div class="alert alert-secondary" style="white-space: pre-line; display: flex; flex-direction: column; justify-content: center; min-height: 160px;">
      <b>LÜTFEN SİPARİŞ VERMEDEN ÖNCE BU KISMI OKU</b>
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
    const price = parseFloat(sel.selectedOptions[0].dataset.price)||0,
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
  // AJAX sonrası form üstünde kutu göster (SADECE SİPARİŞ BAŞARISI)
  document.getElementById('orderForm').addEventListener('submit', function(e){
    e.preventDefault();
    const btn = document.getElementById('orderSubmitBtn');
    btn.disabled = true;
    fetch('/api/new_order', {
      method: 'POST',
      body: new FormData(this)
    })
    .then(r=>r.json())
    .then(res=>{
      btn.disabled = false;
      const msgArea = document.getElementById('ajax-order-result');
      msgArea.innerHTML = '';
      const msgBox = document.createElement('div');
      msgBox.className = "flash-info-box" + (res.success ? "" : " error");
      msgBox.innerText = res.success
        ? "Sipariş başarıyla oluşturuldu!"
        : "Bir hata oluştu";
      msgArea.appendChild(msgBox);
      setTimeout(()=>{ msgBox.remove(); }, 3200);

      if(res.success){
        this.reset(); updateTotal();
        document.getElementById('balance').innerText = res.new_balance + ' TL';
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
  <a href="https://wa.me/905301900969" target="_blank" id="whatsapp-float" title="WhatsApp ile Sohbet Et">
    <span id="whatsapp-float-text">WhatsApp ile Destek!</span>
    <i class="bi bi-whatsapp"></i>
  </a>
  <!-- WhatsApp Sohbet Butonu BİTİŞ -->
<div class="text-center mt-5" style="font-size: 0.9rem; color: #aaa;">
  <hr style="border-color: #333;">
  <p><strong>İletişim:</strong> kuzenlertv6996@gmail.com – 0530 190 09 69</p>
  <p><strong>Adres:</strong> Mustafa Kemal Paşa Mahallesi, Lale Sokak No:110 D:1</p>
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
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Reklam Videosu</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body {
      margin: 0;
      height: 100vh;
      background: linear-gradient(-45deg, #121212, #1e1e1e, #212121, #000000);
      background-size: 400% 400%;
      animation: gradientBG 12s ease infinite;
      color: #fff;
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
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Reklam İzle – Bakiye Kazan</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
  <style>
    body {
      margin: 0;
      min-height: 100vh;
      background: linear-gradient(-45deg, #121212, #1e1e1e, #212121, #000000);
      background-size: 400% 400%;
      animation: gradientBG 12s ease infinite;
      color: #fff;
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
    @media (max-width: 600px) {
      .ad-modern-card {
        max-width: 95vw;
        padding: 20px 4vw;
        margin: 22px auto;
      }
      .ad-modern-video-frame video {
        height: 38vw;
        min-height: 145px;
      }
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
        u = request.form.get("u", "").strip()
        p = request.form.get("pw", "")
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
    users = User.query.order_by(User.username).all()
    return render_template_string(
        HTML_USERS,
        users=users,
        current_user=User.query.get(session.get("user_id")).username,
        rolu_turkce=rolu_turkce
    )

@app.route("/users/delete/<int:user_id>")
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    
    # Admin kendi kendini silmeye çalışmasın
    if user.id == session.get("user_id"):
        flash("Kendi hesabınızı silemezsiniz!")
        return redirect(url_for("manage_users"))

    try:
        db.session.delete(user)
        db.session.commit()
        flash("Kullanıcı başarıyla silindi.")
    except Exception as e:
        db.session.rollback()
        flash(f"Hata oluştu: {str(e)}")

    return redirect(url_for("manage_users"))

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
    user = User.query.get(session.get("user_id"))
    msg, error = "", ""

    # 1) Yerel servisler (DB’deki Service tablosu)
    local = Service.query.filter_by(active=True).all()
    local_ids = {s.id for s in local}
    # 2) Seçili external servisler (bunlarda min/max da olmalı!)
    external = fetch_selected_external_services()
    external = [s for s in external if s.id not in local_ids]
    # 3) Merge: yerel + sadece local’de olmayan external
    services = local + external
    # Platform alanı yoksa (local servisler) otomatik tahmin et
    for s in services:
        if not hasattr(s, "platform") or not getattr(s, "platform"):
            setattr(s, "platform", detect_platform(getattr(s, "name", ""), getattr(s, "description", "")))
    # Platforma göre grupla
    grouped_services = {"instagram": [], "tiktok": [], "youtube": []}
    for s in services:
        grouped_services.get(getattr(s, "platform", "instagram"), grouped_services["instagram"]).append(s)


    # *** SERVISLERIN min/max DEĞERLERİ HER SERVIS İÇİN VAR OLMALI ***
    # Fiyat referansı sadece ilk servis için:
    price = services[0].price if services else SABIT_FIYAT

    if request.method == "POST":
        target = request.form.get("username","").strip()
        try:
            amount = int(request.form.get("amount",""))
        except:
            amount = 0

        # *** SERVIS ID'YI DOĞRU ÇEK ***
        service_id = request.form.get("service_id", type=int)
        service = next((s for s in services if s.id == service_id), None)

        # Min/max değerini seçili servise göre kontrol et!
        if service:
            min_amt = getattr(service, "min_amount", 1)
            max_amt = getattr(service, "max_amount", 1000000)
            price = service.price
        else:
            min_amt, max_amt, price = 1, 1000000, SABIT_FIYAT

        total = amount * price

        if not target or amount <= 0:
            error = "Tüm alanları doğru doldurun!"
        elif amount < min_amt or amount > max_amt:
            error = f"Adet {min_amt}-{max_amt} arası olmalı."
        elif user.balance < total:
            error = "Yetersiz bakiye!"
        else:
            # siparişi kaydet…
            order = Order(username=target, user_id=user.id,
                          amount=amount, total_price=total,
                          status="pending", error="")
            user.balance -= total
            db.session.add(order); db.session.commit()

            # BOT’larla gönderimi simüle et
            status, err = "complete", ""
            for cl in BOT_CLIENTS[:amount]:
                try:
                    follow_user(cl, target)
                except Exception as e:
                    status, err = "error", str(e)
                    break

            order.status = status
            order.error = err
            db.session.commit()

            msg = f"{amount} takipçi başarıyla gönderildi." if status=="complete" else f"Hata: {err}"

    # Geçmiş siparişler
    if user.role=="admin":
        orders = Order.query.order_by(Order.created_at.desc()).all()
    else:
        orders = Order.query.filter_by(user_id=user.id).order_by(Order.created_at.desc()).all()

    return render_template_string(HTML_PANEL,
        orders=orders,
        role=user.role,
        current_user=user.username,
        balance=round(user.balance,2),
        msg=msg,
        error=error,
        rolu_turkce=rolu_turkce,
        grouped_services=grouped_services,
        services=services
    )

@app.route("/services/manage", methods=["GET", "POST"])
@login_required
@admin_required
def manage_services():
    user = User.query.get(session["user_id"])

    # 1) API'den servisleri çek (external_services)
    external_services = fetch_selected_external_services()
    if not external_services or len(external_services) == 0:
        flash("API'den servis çekilemedi. Lütfen bağlantını ve API'yı kontrol et!", "danger")
        # Yine de DB'dekileri göster
        tüm_servisler = Service.query.order_by(Service.id).all()
        return render_template_string(
            HTML_SERVICES_MANAGE,
            services=tüm_servisler,
            local_ids={s.id for s in tüm_servisler}
        )

    # 2) Veritabanındaki servisleri çek
    local_services = Service.query.order_by(Service.id).all()
    local_ids = {s.id for s in local_services}
    api_ids = {s.id for s in external_services}

    # 3) API'de olmayan servisleri DB'den sil
    to_delete = local_ids - api_ids
    if to_delete:
        Service.query.filter(Service.id.in_(to_delete)).delete(synchronize_session=False)
        db.session.commit()

    # 4) API'den gelen ama DB'de olmayan servisleri DB'ye ekle
    to_add = api_ids - local_ids
    for s in external_services:
        if s.id in to_add:
            db.session.add(Service(
                id = s.id,
                name = s.name,
                description = s.description,
                price = s.price,
                min_amount = s.min_amount,
                max_amount = s.max_amount
            ))
    db.session.commit()

    # 5) POST işlemleri (servis güncelleme)
    if request.method == "POST":
        for svc in Service.query.order_by(Service.id).all():
            nk = f"name_{svc.id}"
            dk = f"desc_{svc.id}"
            pk = f"price_{svc.id}"
            if nk in request.form:
                svc.name        = request.form[nk].strip() or svc.name
                svc.description = request.form[dk].strip() or svc.description
                try:
                    np = float(request.form[pk])
                    if np > 0:
                        svc.price = np
                except:
                    pass
        db.session.commit()
        flash("Servisler başarıyla güncellendi.", "success")
        return redirect(url_for("manage_services"))

    # 6) Güncel DB'deki servisleri tekrar çek ve ekrana gönder
    tüm_servisler = Service.query.order_by(Service.id).all()
    return render_template_string(
        HTML_SERVICES_MANAGE,
        services=tüm_servisler,
        local_ids={s.id for s in tüm_servisler}
    )

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

@app.route("/tickets", methods=["GET", "POST"])
@login_required
def tickets():
    user = User.query.get(session.get("user_id"))
    if request.method == "POST":
        subject = request.form.get("subject", "").strip()
        message = request.form.get("message", "").strip()
        if subject and message:
            ticket = Ticket(user_id=user.id, subject=subject, message=message)
            db.session.add(ticket)
            db.session.commit()
    tickets = Ticket.query.filter_by(user_id=user.id).order_by(Ticket.created_at.desc()).all()
    return render_template_string(HTML_TICKETS, tickets=tickets)

@app.route("/admin/tickets", methods=["GET", "POST"])
@login_required
@admin_required
def admin_tickets():
    if request.method == "POST":
        ticket_id = int(request.form.get("ticket_id"))
        response = request.form.get("response", "").strip()
        ticket = Ticket.query.get(ticket_id)
        if ticket and ticket.status == "open" and response:
            ticket.response = response
            ticket.status = "closed"
            db.session.commit()
    tickets = Ticket.query.order_by(Ticket.created_at.desc()).all()
    return render_template_string(HTML_ADMIN_TICKETS, tickets=tickets)

@app.route("/orders")
@login_required
def orders_page():
    user = User.query.get(session.get("user_id"))
    if user.role == "admin":
        orders = Order.query.order_by(Order.id.desc()).all()
        role = "admin"
    else:
        orders = Order.query.filter_by(user_id=user.id).order_by(Order.id.desc()).all()
        role = "user"
    return render_template_string(HTML_ORDERS_SIMPLE, orders=orders, role=role, durum_turkce=durum_turkce)

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
    user = User.query.get(session.get("user_id"))
    username = request.form.get("username")
    amount = int(request.form.get("amount") or 0)
    service_id = int(request.form.get("service_id") or 0)
    service = Service.query.filter_by(id=service_id).first()
    if not service:
        return jsonify({"success": False, "error": "Servis bulunamadı."})

    total = service.price * amount

    # DİNAMİK MİN-MAX KONTROLÜ
    min_amt = getattr(service, "min_amount", 10)
    max_amt = getattr(service, "max_amount", 1000)
    if not username or amount < min_amt or amount > max_amt:
        return jsonify({"success": False, "error": f"Adet {min_amt}-{max_amt} arası olmalı."})

    if user.balance < total:
        return jsonify({"success": False, "error": "Yetersiz bakiye!"})

    # Varsayılanlar
    status = "pending"
    error = None
    api_order_id = None

    # Eğer Resellersmm API kullanılacaksa
    if service.id >= 100000:
        try:
            real_service_id = service.id - 100000
            resp = requests.post(EXTERNAL_API_URL, data={
                "key": EXTERNAL_API_KEY,
                "action": "add",
                "service": real_service_id,
                "link": username,
                "quantity": amount
            }, timeout=10)
            resp.raise_for_status()
            result = resp.json()
            if "order" in result:
                api_order_id = str(result["order"])
            else:
                status = "error"
                error = result.get("error", "Resellersmm sipariş hatası!")
        except Exception as e:
            status = "error"
            error = "ResellersMM API bağlantı/yanıt hatası: " + str(e)

    # Siparişi oluştur
    order = Order(
        username=username,
        user_id=user.id,
        amount=amount,
        status=status,
        total_price=total,
        service_id=service_id,
        error=error,
        api_order_id=api_order_id
    )
    user.balance -= total
    db.session.add(order)
    db.session.commit()

    if status == "error":
        return jsonify({"success": True, "new_balance": round(user.balance, 2), "info": error})
    else:
        return jsonify({"success": True, "new_balance": round(user.balance, 2)})

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

@app.route('/order/cancel/<int:order_id>', methods=['POST'])
@login_required
def order_cancel(order_id):
    user = User.query.get(session.get("user_id"))
    # Sadece admin iptal edebilir:
    if not user or user.role != "admin":
        abort(403)

    order = Order.query.get(order_id)
    if not order:
        flash("Sipariş bulunamadı.", "danger")
        return redirect(url_for("orders_page"))

    # Eğer sipariş zaten iptal edilmişse, tekrar iade etme!
    if order.status == "cancelled":
        flash("Sipariş zaten iptal edilmiş.", "warning")
        return redirect(url_for("orders_page"))

    target_user = User.query.get(order.user_id)
    if not target_user:
        flash("Müşteri bulunamadı.", "danger")
        return redirect(url_for("orders_page"))

    # Siparişi iptal et ve bakiyeyi iade et
    order.status = "cancelled"
    order.error = None
    target_user.balance += order.total_price   # --- BAKİYE İADE!

    db.session.commit()
    flash("Sipariş iptal edildi ve bakiye iade edildi.", "success")
    return redirect(url_for("orders_page"))

@app.route('/api/order_status', methods=['POST'])
def api_order_status():
    if not (session.get("user_id") and User.query.get(session["user_id"]).role == "admin"):
        return {"success": False, "error": "Yetkisiz işlem"}
    data = request.get_json()
    order = Order.query.get(data.get("order_id"))
    if not order:
        return {"success": False, "error": "Sipariş bulunamadı"}
    if data.get("status") not in ["pending", "started", "complete", "cancelled"]:
        return {"success": False, "error": "Geçersiz durum"}
    order.status = data.get("status")
    db.session.commit()
    return {"success": True}

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