import os
import time
import random
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText

from flask import (
    Flask, session, request, redirect,
    render_template_string, abort, url_for, flash, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

import requests  # ← Harici servis için
import json
from functools import wraps

# --- Harici servis entegrasyonu (ResellersMM) ---
EXTERNAL_API_URL = "https://resellersmm.com/api/v2/"
EXTERNAL_API_KEY = "6b0e961c4a42155ba44bfd4384915c27"

# --- Çekmek istediğimiz ResellersMM servis ID’leri ---

EXT_SELECTED_IDS = [1583, 827]  # Örneğin sadece 1 ve 2 no’lu servisleri çek

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

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = User.query.get(session.get("user_id"))
        if not user or user.role != "admin":
            abort(403)
        return f(*args, **kwargs)
    return wrapper
# --- /External servis seçim mekanizması ---

app = Flask(__name__)
app.url_map.strict_slashes = False
app.secret_key = os.getenv("SECRET_KEY", "çok-gizli-bir-anahtar")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///app.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

SABIT_FIYAT = 0.5

# --- MODELLER ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    email = db.Column(db.String(120), unique=True)
    role = db.Column(db.String(16), nullable=False)
    balance = db.Column(db.Float, default=10.0)
    is_verified = db.Column(db.Boolean, default=False)
    last_ad_watch = db.Column(db.DateTime, default=None)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    amount = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(32), default="pending")
    error = db.Column(db.String(256), default="")
    total_price = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    user = db.relationship("User")

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
    name = db.Column(db.String(64), unique=True, nullable=False)
    description = db.Column(db.String(256))
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
    subject = "insprov.uk Kayıt Doğrulama Kodunuz"
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
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>insprov.uk</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body class="bg-dark d-flex justify-content-center align-items-center" style="height:100vh;">
  <div class="card shadow p-4" style="min-width:340px;">
    <h3 class="mb-3 text-center">insprov.uk</h3>
    {% with messages = get_flashed_messages() %}
      {% if messages %}
        <div class="alert alert-danger p-2 py-1 small mb-3" role="alert">
          {% for message in messages %}
            {{ message }}<br>
          {% endfor %}
        </div>
      {% endif %}
    {% endwith %}
    <form method="post">
      <div class="mb-2"><label class="form-label">Kullanıcı Adı:</label>
        <input name="username" class="form-control" placeholder="Kullanıcı Adı">
      </div>
      <div class="mb-3"><label class="form-label">Şifre:</label>
        <input name="password" type="password" class="form-control" placeholder="Şifre">
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
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Kayıt Ol</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body class="bg-dark d-flex justify-content-center align-items-center" style="height:100vh;">
  <div class="card shadow p-4" style="min-width:370px;">
    <h3 class="mb-3 text-center">insprov.uk <span class="text-primary">Kayıt</span></h3>
    {% with messages = get_flashed_messages() %}
      {% if messages %}
        <div class="alert alert-danger p-2 py-1 small mb-3" role="alert">
          {% for message in messages %}
            {{ message }}<br>
          {% endfor %}
        </div>
      {% endif %}
    {% endwith %}
    {% if not sent %}
      <form method="post">
        <div class="mb-2"><label class="form-label">Kullanıcı Adı:</label>
          <input name="username" class="form-control" placeholder="Kullanıcı Adı" required>
        </div>
        <div class="mb-2"><label class="form-label">Şifre:</label>
          <input name="password" type="password" class="form-control" placeholder="Şifre" required>
        </div>
        <div class="mb-3"><label class="form-label">E-Posta:</label>
          <input name="email" type="email" class="form-control" placeholder="E-Posta" required>
        </div>
        <button class="btn btn-success w-100">Kayıt Ol</button>
      </form>
    {% else %}
      <form method="post">
        <div class="mb-3">
          <label class="form-label">E-Posta Adresinize Gönderilen Kod:</label>
          <input name="verify_code" class="form-control" placeholder="Doğrulama Kodu" required>
        </div>
        <button class="btn btn-primary w-100">Kodu Doğrula</button>
      </form>
    {% endif %}
    <div class="text-center mt-2">
      <a href="/" class="btn btn-link btn-sm">Girişe Dön</a>
    </div>
  </div>
</body>
</html>
"""

HTML_USERS = """
<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Kullanıcı Yönetimi</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body class="bg-dark text-light">
  <div class="container py-4">
    <div class="card p-4 mx-auto" style="max-width:700px;">
      <h3>Kullanıcı Yönetimi</h3>
      <form method="post" class="row g-2 align-items-end mb-4">
        <div class="col"><input name="u" class="form-control" placeholder="Yeni kullanıcı"></div>
        <div class="col"><input name="pw" type="password" class="form-control" placeholder="Parola"></div>
        <div class="col"><select name="role" class="form-select">
            <option value="admin">Yönetici</option>
            <option value="viewer">Kullanıcı</option>
          </select></div>
        <div class="col"><button class="btn btn-success">Ekle</button></div>
      </form>
      <hr><h5>Mevcut Kullanıcılar</h5>
      <div class="table-responsive"><table class="table table-dark table-striped table-bordered align-middle mb-4">
          <thead><tr>
            <th>#</th><th>Kullanıcı</th><th>Rol</th><th>Bakiye</th><th>İşlem</th>
          </tr></thead>
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
      <div class="mt-3"><a href="{{ url_for('panel') }}" class="btn btn-secondary btn-sm">Panel’e Dön</a></div>
    </div>
  </div>
</body>
</html>
"""

HTML_SERVICES_MANAGE = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Servisleri Yönet</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"/>
</head>
<body class="bg-dark text-light">
  <div class="container py-4">
    <div class="card mx-auto" style="max-width:800px">
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
                  <input name="price_{{s.id}}" type="number" step="0.01" min="0.01"
                         class="form-control form-control-sm"
                         value="{{ "%.2f"|format(s.price) }}" {% if s.id not in local_ids %}readonly{% endif %}>
                </td>
                <td>{{ s.min_amount }}</td>
                <td>{{ s.max_amount }}</td>
                <td>
                  {% if s.id in local_ids %}
                    <span class="badge bg-success">Local</span>
                  {% else %}
                    <span class="badge bg-warning text-dark">External</span>
                  {% endif %}
                </td>
                <td>
                  {% if s.id not in local_ids %}
                  <!-- External ise ekle butonu -->
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
            <button class="btn btn-success" type="submit">Fiyatları Kaydet</button>
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

HTML_BALANCE = """
<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Bakiye Yükle</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body class="bg-dark text-light">
  <div class="container py-4">
    <div class="card p-4 mx-auto" style="max-width:500px;">
      <h3>Bakiye Yükle</h3>
      <div class="alert alert-info">
        <b>Bakiye yükleme için banka bilgileri:</b><br>
        IBAN: <b>TR70 0004 6008 7088 8000 1117 44</b><br>
        Ad Soyad: <b>Mükail Aktaş</b><br>
        <small>Açıklamaya <b>kullanıcı adınızı</b> yazmayı unutmayın!</small>
      </div>
      {% if msg %}<div class="alert alert-success">{{ msg }}</div>{% endif %}
      {% if err %}<div class="alert alert-danger">{{ err }}</div>{% endif %}
      <form method="post" class="mb-4">
        <label class="form-label">Tutar (TL):</label>
        <input name="amount" type="number" step="0.01" min="1" class="form-control mb-2" required>
        <button class="btn btn-primary w-100">Başvuru Yap</button>
      </form>
      <h5>Geçmiş Bakiye Talepleriniz</h5>
      <table class="table table-dark table-bordered table-sm">
        <thead>
          <tr>
            <th>Tarih</th>
            <th>Tutar</th>
            <th>Durum</th>
            <th>Açıklama</th>
            <th>Ret Sebebi</th>
          </tr>
        </thead>
        <tbody>
        {% for req in requests %}
          <tr>
            <td>{{ req.created_at.strftime('%d.%m.%Y %H:%M') }}</td>
            <td>{{ req.amount }}</td>
            <td>
              {% if req.status == "pending" %}<span class="badge bg-warning text-dark">Bekliyor</span>
              {% elif req.status == "approved" %}<span class="badge bg-success">Onaylandı</span>
              {% elif req.status == "rejected" %}<span class="badge bg-danger">Reddedildi</span>
              {% endif %}
            </td>
            <td>{{ req.explanation or "" }}</td>
            <td>{{ req.reject_reason or "" }}</td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
      <a href="/panel" class="btn btn-secondary btn-sm">Panele Dön</a>
    </div>
  </div>
</body>
</html>
"""

HTML_BALANCE_REQUESTS = """
<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Bakiye Talepleri</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body class="bg-dark text-light">
  <div class="container py-4">
    <div class="card p-4 mx-auto" style="max-width:800px;">
      <h3>Bakiye Talepleri</h3>
      {% with messages = get_flashed_messages() %}
        {% if messages %}
          <div class="alert alert-info p-2 small mb-2" role="alert">
            {% for message in messages %}
              {{ message }}<br>
            {% endfor %}
          </div>
        {% endif %}
      {% endwith %}
      <table class="table table-dark table-bordered">
        <thead>
          <tr>
            <th>#</th><th>Kullanıcı</th><th>Tutar</th><th>Tarih</th><th>Durum</th><th>Açıklama</th><th>Ret Sebebi</th><th>İşlem</th>
          </tr>
        </thead>
        <tbody>
        {% for req in reqs %}
          <tr>
            <td>{{ req.id }}</td>
            <td>{{ req.user.username }}</td>
            <td>{{ req.amount }}</td>
            <td>{{ req.created_at.strftime('%d.%m.%Y %H:%M') }}</td>
            <td>
              {% if req.status == "pending" %}<span class="badge bg-warning text-dark">Bekliyor</span>
              {% elif req.status == "approved" %}<span class="badge bg-success">Onaylandı</span>
              {% elif req.status == "rejected" %}<span class="badge bg-danger">Reddedildi</span>
              {% endif %}
            </td>
            <td>{{ req.explanation or "" }}</td>
            <td>{{ req.reject_reason or "" }}</td>
            <td>
              {% if req.status == "pending" %}
              <form method="post" style="display:inline-block">
                <input type="hidden" name="req_id" value="{{ req.id }}">
                <input type="text" name="explanation" class="form-control form-control-sm mb-1" placeholder="Onay açıklama">
                <button class="btn btn-success btn-sm" name="action" value="approve">Onayla</button>
              </form>
              <form method="post" style="display:inline-block">
                <input type="hidden" name="req_id" value="{{ req.id }}">
                <input type="text" name="explanation" class="form-control form-control-sm mb-1" placeholder="Ret açıklama (isteğe bağlı)">
                <select name="reject_reason" class="form-select form-select-sm mb-1">
                  <option value="">Ret sebebi seç</option>
                  <option>BANKA HESABINA PARA AKTARILMAMIŞ</option>
                  <option>HATALI İSİM SOYİSİM</option>
                  <option>BAŞKA BİR KULLANICIDAN GELEN BİLDİRİM</option>
                  <option>MANUEL RED (Açıklamada belirt)</option>
                </select>
                <button class="btn btn-danger btn-sm" name="action" value="reject">Reddet</button>
              </form>
              {% else %}
                <span class="text-muted">—</span>
              {% endif %}
            </td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
      <a href="/panel" class="btn btn-secondary btn-sm">Panele Dön</a>
    </div>
  </div>
</body>
</html>
"""

HTML_SERVICES = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Servisler & Fiyat Listesi</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"/>
  <style>
    .editing .text { display:none; }
    .editing .inp  { display:inline-block !important; width:100%; }
  </style>
</head>
<body class="bg-dark text-light">
  <div class="container py-4">
    <div class="card mx-auto" style="max-width:900px">
      <div class="card-body">
        <h3>Servisler & Fiyat Listesi</h3>
        <div class="d-flex justify-content-between align-items-center mb-3">
          <div><strong>Toplam:</strong> {{ servisler|length }} servis</div>
          <div class="d-flex">
            <input id="search" class="form-control form-control-sm me-2" placeholder="Search…">
            {% if user.role=='admin' %}
              <button id="editBtn" class="btn btn-outline-info btn-sm">Edit</button>
            {% endif %}
          </div>
        </div>

        {# — Form, sadece admin görsün — #}
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
            <button type="submit" class="btn btn-success btn-sm me-2">Save</button>
            <button type="button" id="cancel" class="btn btn-secondary btn-sm">Cancel</button>
          </div>
        </form>
        {% endif %}

      </div>
    </div>
  </div>

  <script>
    // Search
    document.getElementById('search').addEventListener('input', function(){
      const q = this.value.toLowerCase();
      document.querySelectorAll('#tbl tbody tr').forEach(tr=>{
        tr.style.display = tr.innerText.toLowerCase().includes(q) ? '' : 'none';
      });
    });

    {% if user.role=='admin' %}
    // Edit mode
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
</body>
</html>
"""

HTML_ADMIN_TICKETS = """
<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Ticket Yönetimi (Admin)</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body class="bg-dark text-light">
  <div class="container py-4">
    <div class="card p-4 mx-auto" style="max-width:900px;">
      <h2 class="mb-4">Tüm Destek Talepleri</h2>
      <table class="table table-dark table-bordered text-center align-middle">
        <thead>
          <tr>
            <th>ID</th><th>Kullanıcı</th><th>Tarih</th><th>Konu</th><th>Mesaj</th><th>Durum</th><th>Yanıt</th><th>İşlem</th>
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
              {% if t.status == "open" %}<span class="badge bg-warning text-dark">Açık</span>
              {% else %}<span class="badge bg-success">Yanıtlandı</span>{% endif %}
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
      <a href="/panel" class="btn btn-secondary btn-sm w-100">Panele Dön</a>
    </div>
  </div>
</body>
</html>
"""

HTML_EXTERNAL_MANAGE = """
<!DOCTYPE html>
<html lang="tr"><head>
  <meta charset="utf-8"><title>Dış Servis Seçimi</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"/>
</head>
<body class="bg-dark text-light">
  <div class="container py-4">
    <h3>Dış Servis Seçimi (ResellersMM)</h3>
    <form method="post">
      <table class="table table-dark table-striped">
        <thead>
          <tr><th>Seç</th><th>Servis Adı</th><th>Min / Max</th></tr>
        </thead>
        <tbody>
        {% for s in all_ext %}
          <tr>
            <td>
              <input type="checkbox" name="ext_{{s.id}}"
                {% if s.id in selected %}checked{% endif %}>
            </td>
            <td>{{ s.name }}</td>
            <td>{{ s.min_amount }} / {{ s.max_amount }}</td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
      <button class="btn btn-success">Kaydet</button>
      <a href="{{ url_for('panel') }}" class="btn btn-secondary ms-2">Panele Dön</a>
    </form>
  </div>
</body>
</html>
"""

HTML_ORDERS_SIMPLE = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Geçmiş Siparişler</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" />
  <style>
    .badge-sirada { background: #ffd500; color: #222; font-weight: 600; }
    .badge-basladi { background: #42c3e8; color: #222; font-weight: 600; }
    .badge-tamamlandi { background: #29c46a; font-weight: 600; }
    .badge-iptal { background: #949ba5; font-weight: 600; }
    .badge-hata { background: #e45858; font-weight: 600; }
  </style>
</head>
<body class="bg-dark text-light">
  <div class="container py-4">
    <div class="card p-4 mx-auto" style="max-width:750px;">
      <h2 class="mb-3">Geçmiş Siparişler</h2>
      <table class="table table-dark table-bordered text-center mb-3">
        <thead>
          <tr>
            <th>#</th>
            {% if role == 'admin' %}<th>Kullanıcı</th>{% endif %}
            <th>Hedef Kullanıcı</th>
            <th>Adet</th>
            <th>Fiyat</th>
            <th>Durum</th>
            <th>Hata</th>
            {% if role == 'admin' %}<th>Durumu Değiştir</th>{% endif %}
          </tr>
        </thead>
        <tbody>
          {% for o in orders %}
          <tr>
            <td>{{ loop.index }}</td>
            {% if role == 'admin' %}<td>{{ o.user.username }}</td>{% endif %}
            <td>{{ o.username }}</td>
            <td>{{ o.amount }}</td>
            <td>{{ "%.2f"|format(o.total_price) }}</td>
            <td>
              {% if o.status == 'pending' %}
                <span class="badge badge-sirada">Sırada</span>
              {% elif o.status == 'started' %}
                <span class="badge badge-basladi">Başladı</span>
              {% elif o.status == 'complete' %}
                <span class="badge badge-tamamlandi">Tamamlandı</span>
              {% elif o.status == 'cancelled' %}
                <span class="badge badge-iptal">İptal Edildi</span>
              {% elif o.status == 'error' %}
                <span class="badge badge-hata">Hata</span>
              {% else %}
                <span class="badge bg-secondary">{{ o.status }}</span>
              {% endif %}
            </td>
            <td>{{ o.error or "-" }}</td>
            {% if role == 'admin' %}
            <td>
              {% if o.status not in ['complete','cancelled'] %}
              <select class="form-select form-select-sm" style="min-width:120px"
                onchange="changeStatus('{{ o.id }}', this.value)">
                <option disabled selected>Durumu seç</option>
                <option value="pending">Sırada</option>
                <option value="started">Başladı</option>
                <option value="complete">Tamamlandı</option>
                <option value="cancelled">İptal Edildi</option>
              </select>
              {% else %}
                <span class="text-muted">–</span>
              {% endif %}
            </td>
            {% endif %}
          </tr>
          {% else %}
          <tr>
            <td colspan="{% if role == 'admin' %}9{% else %}7{% endif %}" class="text-center text-muted">Henüz sipariş yok.</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      <a href="/panel" class="btn btn-secondary btn-sm w-100">Panele Dön</a>
    </div>
  </div>
  {% if role == 'admin' %}
  <script>
    function changeStatus(orderId, newStatus) {
      fetch('/api/order_status', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({order_id: orderId, status: newStatus})
      }).then(res=>res.json()).then(res=>{
        if(res.success){
          location.reload();
        }else{
          alert("Hata: " + (res.error || "Durum güncellenemedi!"));
        }
      });
    }
  </script>
  {% endif %}
</body>
</html>
"""

HTML_PANEL = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Sipariş Paneli</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" />
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
  <style>
    .welcome-card {
      background: linear-gradient(90deg, #f8fafc 0%, #e0e7ef 100%);
      border-radius: 18px;
      padding: 20px 28px 16px 24px;
      margin-bottom: 20px;
      box-shadow: 0 2px 18px 0 rgba(0,0,0,0.04);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .welcome-left {
      display: flex;
      align-items: flex-start;
      gap: 16px;
    }
    .welcome-icon {
      font-size: 2.3rem;
      color: #2186eb;
      margin-top: 2px;
    }
    .welcome-title {
      font-weight: 700;
      font-size: 1.2rem;
      margin-bottom: 0.1rem;
      color: #222a38;
      letter-spacing: 0.02em;
    }
    .welcome-desc {
      font-size: 0.97rem;
      color: #5c6474;
    }
    .welcome-balance {
      font-size: 1.08rem;
      color: #2b2f41;
      font-weight: 600;
      margin-bottom: 0.3rem;
      text-align: right;
    }
    @media (max-width: 575px) {
      .welcome-card { flex-direction: column; align-items: flex-start; gap: 14px; }
      .welcome-balance { text-align: left; }
    }
  </style>
</head>
<body class="bg-dark text-light">
  <div class="container py-4">
    <div class="card p-4 mx-auto" style="max-width:800px;">

      <!-- HOŞGELDİN ALANI -->
      <div class="welcome-card">
        <div class="welcome-left">
          <span class="welcome-icon"><i class="bi bi-person-circle"></i></span>
          <div>
            <div class="welcome-title">Hoşgeldin, {{ current_user }}</div>
            <div class="welcome-desc">Keyifli ve güvenli alışverişler dileriz.</div>
          </div>
        </div>
        <div>
          <div class="welcome-balance">Bakiye: <span style="color:#2186eb" id="balance">{{ balance }} TL</span></div>
          <a href="{{ url_for('orders') }}" class="btn btn-sm btn-primary mt-1 w-100" style="min-width:148px;">
            <i class="bi bi-box-seam"></i> Geçmiş Siparişler
          </a>
        </div>
      </div>

      <!-- ANA BUTONLAR -->
      <div class="d-grid gap-3 mb-3">
        {% if role == 'admin' %}
          <a href="{{ url_for('manage_users') }}" class="btn btn-secondary py-2">Kullanıcı Yönetimi</a>
          <a href="{{ url_for('balance_requests') }}" class="btn btn-warning py-2">Bakiye Talepleri</a>
          <a href="{{ url_for('admin_tickets') }}" class="btn btn-danger py-2">Tüm Destek Talepleri</a>
          <a href="{{ url_for('manage_services') }}" class="btn btn-info py-2">Servisleri Yönet</a>
        {% else %}
          <a href="{{ url_for('user_balance') }}" class="btn btn-warning py-2">Bakiye Yükle</a>
          <a href="{{ url_for('tickets') }}" class="btn btn-danger py-2">Destek & Canlı Yardım</a>
        {% endif %}
        <a href="{{ url_for('watchads') }}" class="btn btn-success py-2">Reklam İzle – Bakiye Kazan</a>
      </div>

      <!-- SİPARİŞ FORMU -->
      <h4 class="mb-3 mt-4">Yeni Sipariş</h4>
      <form id="orderForm" method="post" autocomplete="off">
        <div class="mb-3">
          <label class="form-label"><i class="bi bi-star-fill text-warning"></i> Kategori</label>
          <select class="form-select" name="category" required>
            <option value="instagram" selected>Instagram</option>
          </select>
        </div>
        <div class="mb-3">
          <label class="form-label"><i class="bi bi-box-seam"></i> Servis</label>
          <select class="form-select" name="service_id" id="service_id" required>
            {% for s in services %}
              <option value="{{ s.id }}" data-price="{{ s.price }}">
                {{ s.name }} – {{ "%.2f"|format(s.price) }} TL
              </option>
            {% endfor %}
          </select>
        </div>
        <div class="mb-3">
          <label class="form-label"><i class="bi bi-info-circle"></i> Açıklama</label>
          <div class="alert alert-secondary" style="white-space: pre-line;">
            Sistem, gönderilecek takipçi sayısına göre en uygun şekilde çalışır.

            Örnek: 1000 Türk gerçek takipçi siparişiniz ortalama 3-6 saat arasında tamamlanır.

            DİKKAT: Takipçi gönderimi, organik hesaplardan ve gerçek Türk profillerden yapılır. 
            Gizli (kapalı) hesaplara gönderim yapılmaz. Lütfen gönderimden önce hesabınızın herkese açık olduğundan emin olun.
          </div>
        </div>
        <div class="mb-3">
          <label class="form-label"><i class="bi bi-link-45deg"></i> Takip Edilecek Hesap</label>
          <input name="username" type="text" class="form-control" placeholder="Instagram kullanıcı adını girin" required>
        </div>
        <div class="mb-3">
          <label class="form-label"><i class="bi bi-list-ol"></i> Adet</label>
          <input name="amount" id="amount" type="number" min="1" class="form-control" placeholder="Adet" required>
        </div>
        <div class="mb-3">
          <label class="form-label"><i class="bi bi-currency-dollar"></i> Tutar</label>
          <input type="text" class="form-control" id="total" placeholder="Tutar otomatik hesaplanır" disabled>
        </div>
        <button type="submit" class="btn btn-primary w-100" id="orderSubmitBtn">Siparişi Gönder</button>
      </form>

      <!-- Fiyat hesaplama script -->
      <script>
        const sel = document.getElementById('service_id'),
              amt = document.getElementById('amount'),
              tot = document.getElementById('total');

        function updateTotal(){
          const price = parseFloat(sel.selectedOptions[0].dataset.price)||0,
                num   = parseInt(amt.value)||0;
          tot.value = num>0
            ? `${num}×${price.toFixed(2)} TL = ${(num*price).toFixed(2)} TL`
            : "";
        }
        sel.addEventListener('change', updateTotal);
        amt.addEventListener('input', updateTotal);
      </script>

      <!-- AJAX sipariş gönderme (opsiyonel) -->
      <script>
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
            if(res.success){
              this.reset(); updateTotal();
              document.getElementById('balance').innerText = res.new_balance + ' TL';
              alert('Sipariş başarıyla alındı!');
            } else {
              alert(res.error || 'Bir hata oluştu');
            }
          })
          .catch(()=>{ btn.disabled = false; alert('İstek başarısız'); });
        });
      </script>

      <div class="mt-3 text-end">
        <a href="{{ url_for('logout') }}" class="btn btn-outline-danger btn-sm">Çıkış Yap</a>
      </div>

    </div>
  </div>
</body>
</html>
"""

HTML_ADS_MANAGE = """
<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Reklam Videosu</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body class="bg-dark text-light">
  <div class="container py-4">
    <div class="card p-4 mx-auto" style="max-width:600px;">
      <h3>Reklam Videosu Ayarları</h3>
      {% with messages = get_flashed_messages() %}
      {% if messages %}
        <div class="alert alert-info">{{ messages[0] }}</div>
      {% endif %}
      {% endwith %}
      <form method="post">
        <div class="mb-3">
          <label class="form-label">YouTube Embed URL</label>
          <input name="embed_url" class="form-control" value="{{ embed_url }}">
        </div>
        <button class="btn btn-success w-100">Kaydet</button>
      </form>
      <h5 class="mt-4">Mevcut Video Önizlemesi:</h5>
      <iframe width="100%" height="315" src="{{ embed_url }}" frameborder="0" allowfullscreen></iframe>
      <a href="/panel" class="btn btn-secondary btn-sm mt-3">Panele Dön</a>
    </div>
  </div>
</body>
</html>
"""

HTML_ADS_MANAGE = """
<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Reklam Videosu</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body class="bg-dark text-light">
  <div class="container py-4">
    <div class="card p-4 mx-auto" style="max-width:600px;">
      <h3>Reklam Videosu Ayarları</h3>
      {% with messages = get_flashed_messages() %}
      {% if messages %}
        <div class="alert alert-info">{{ messages[0] }}</div>
      {% endif %}
      {% endwith %}
      <form method="post">
        <div class="mb-3">
          <label class="form-label">YouTube Embed URL</label>
          <input name="embed_url" class="form-control" value="{{ embed_url }}">
        </div>
        <button class="btn btn-success w-100">Kaydet</button>
      </form>
      <h5 class="mt-4">Mevcut Video Önizlemesi:</h5>
      <iframe width="100%" height="315" src="{{ embed_url }}" frameborder="0" allowfullscreen></iframe>
      <a href="/panel" class="btn btn-secondary btn-sm mt-3">Panele Dön</a>
    </div>
  </div>
</body>
</html>
"""

HTML_WATCH_ADS = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Reklam İzle – Bakiye Kazan</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body { background: #22252b; min-height: 100vh; }
    .ad-modern-card { max-width: 440px; margin: 64px auto; border-radius: 18px; background: #fff; box-shadow: 0 3px 32px 0 rgba(30,38,67,0.14); padding: 40px 34px 34px 34px; text-align: center; }
    .ad-modern-title { font-weight: 700; color: #23273e; letter-spacing: 0.01em; margin-bottom: 16px; font-size: 1.55rem; display: flex; align-items: center; gap: 9px; justify-content: center; }
    .ad-modern-title i { font-size: 1.32em; color: #24aaf8; }
    .ad-modern-desc { color: #274164; background: #e7f3ff; border-radius: 11px; font-size: 1.14rem; padding: 14px 7px 10px 7px; margin-bottom: 18px; font-weight: 600; border: 1.5px solid #c7e6fe; }
    .ad-modern-video-frame { background: #181c22; border-radius: 12px; overflow: hidden; border: 2.5px solid #e8f1fa; margin-bottom: 23px; box-shadow: 0 1px 12px 0 rgba(24,34,56,0.07);}
    .ad-modern-video-frame video { display: block; width: 100%; height: 245px; border: none; background: #000;}
    .ad-modern-btn { background: linear-gradient(90deg, #24aaf8 0%, #3763f4 100%); border: none; color: #fff; padding: 12px 22px; border-radius: 10px; font-size: 1.09rem; font-weight: 600; box-shadow: 0 2px 16px 0 rgba(36,170,248,0.11); margin-bottom: 13px; width: 100%; transition: background 0.19s, box-shadow 0.19s; }
    .ad-modern-btn:disabled, .ad-modern-btn[disabled] { background: linear-gradient(90deg, #7cc6b7 0%, #95cfc0 100%); color: #edf4f3; opacity: 1; cursor: not-allowed; }
    .ad-modern-timer { font-size: 1.07rem; color: #1e3c6c; margin-bottom: 15px; font-weight: 500; letter-spacing: 0.01em; }
    .modern-link-btn { background: #727a87; border: none; color: #fff; font-weight: 600; padding: 11px 0; width: 100%; border-radius: 8px; margin-top: 5px; font-size: 1.04rem; transition: background .18s; text-decoration: none; display: block; }
    .modern-link-btn:hover { background: #61656d; color: #fff; }
    @media (max-width: 600px) { .ad-modern-card { max-width: 99vw; padding: 18px 4vw 17px 4vw; margin: 22px auto;} .ad-modern-video-frame video { height: 39vw; min-height: 145px;} }
  </style>
</head>
<body>
  <div class="ad-modern-card">
    <div class="ad-modern-title"><i class="bi bi-play-circle-fill"></i> Reklam İzle – Bakiye Kazan</div>
    <div class="ad-modern-desc">Reklamı izleyerek <b>{{ reward }} TL</b> bakiye kazan!</div>
    {% if already_watched %}
      <div class="alert alert-warning" id="waitDiv">
        <b>Tekrar izleyip bakiye kazanmak için:</b><br>
        <span id="waitTimer"></span>
      </div>
      <script>
        // Geriye kalan süreyi gösteren sayaç:
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
        <video id="adVideo" width="100%" height="245" controls>
          <source src="/static/reklam.mp4" type="video/mp4">
          Tarayıcınız video etiketini desteklemiyor.
        </video>
      </div>
      <button class="ad-modern-btn" id="watchBtn" disabled>30 sn sonra Bakiyeyi Al</button>
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
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
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
REWARD = 0.15  # Kullanıcı izlediğinde kazanacağı bakiye

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
            temp_user = session.get("register_temp_user", {})
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
                flash("Kayıt başarıyla tamamlandı! Giriş yapabilirsiniz.")
                session.pop("register_sent", None)
                session.pop("register_temp_user", None)
                return redirect("/")
    sent = session.get("register_sent", False)
    return render_template_string(HTML_REGISTER, sent=sent)

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
@login_required
@admin_required
def delete_user(user_id):
    admin = User.query.get(session.get("user_id"))
    usr = User.query.get_or_404(user_id)
    if usr.username != admin.username:
        db.session.delete(usr)
        db.session.commit()
    return redirect("/users")

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
    ref = request.referrer or url_for("orders")
    return redirect(ref)

@app.route("/panel", methods=["GET", "POST"])
@login_required
def panel():
    user = User.query.get(session.get("user_id"))
    msg, error = "", ""

    # 1) Yerel servisler (DB’daki Service tablosu)
    local = Service.query.filter_by(active=True).all()
    local_ids = {s.id for s in local}
    # 2) Seçili external servisler
    external = fetch_selected_external_services()
    external = [s for s in external if s.id not in local_ids]
    # 3) Merge: yerel + sadece local’de olmayan external
    services = local + external

    # Fiyat referansı
    price = services[0].price if services else SABIT_FIYAT

    if request.method == "POST":
        target = request.form.get("username","").strip()
        try:
            amount = int(request.form.get("amount",""))
        except:
            amount = 0
        total = amount * price

        if not target or amount <= 0:
            error = "Tüm alanları doğru doldurun!"
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
        services=services
    )

@app.route("/services/manage", methods=["GET", "POST"])
@login_required
@admin_required
def manage_services():
    user = User.query.get(session["user_id"])

    # 1) Veritabanındaki (yerel) servisler
    local_services = Service.query.order_by(Service.id).all()
    local_ids = {s.id for s in local_services}

    # 2) Dış API’den seçili servisler
    external_services = fetch_selected_external_services()
    external_services = [s for s in external_services if s.id not in local_ids]

    # 3) External servisi veritabanına ekleme
    if request.method == "POST" and "add_external" in request.form:
        ext_id = int(request.form.get("add_external"))
        ext_service = next((s for s in external_services if s.id == ext_id), None)
        if ext_service:
            # External servisten local'a kopyala ve kaydet
            new_service = Service(
                id = ext_service.id,
                name = ext_service.name,
                description = ext_service.description,
                price = ext_service.price,
                min_amount = ext_service.min_amount,
                max_amount = ext_service.max_amount
            )
            db.session.add(new_service)
            db.session.commit()
            flash("Servis veritabanına eklendi ve artık düzenlenebilir!", "success")
        return redirect(url_for("manage_services"))

    # 4) POST ile güncelleme:
    if request.method == "POST" and "add_external" not in request.form:
        for svc in local_services:
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
        flash("Yerel servisler başarıyla güncellendi.", "success")
        return redirect(url_for("manage_services"))

    tüm_servisler = local_services + external_services
    return render_template_string(
        HTML_SERVICES_MANAGE,
        services=tüm_servisler,
        local_ids=local_ids
    )

@app.route("/balance", methods=["GET", "POST"])
@login_required
def user_balance():
    user = User.query.get(session.get("user_id"))
    msg, err = "", ""
    if request.method == "POST":
        try:
            amount = float(request.form.get("amount", 0))
        except:
            amount = 0
        if amount <= 0:
            err = "Tutar geçersiz."
        else:
            r = BalanceRequest(user_id=user.id, amount=amount)
            db.session.add(r)
            db.session.commit()
            msg = "Başvuru başarıyla iletildi. Admin onayını bekleyiniz."
    requests = BalanceRequest.query.filter_by(user_id=user.id).order_by(BalanceRequest.created_at.desc()).all()
    return render_template_string(HTML_BALANCE, msg=msg, err=err, requests=requests)

@app.route("/balance/requests", methods=["GET", "POST"])
@login_required
@admin_required
def balance_requests():
    if request.method == "POST":
        req_id = int(request.form.get("req_id"))
        action = request.form.get("action")
        explanation = request.form.get("explanation", "")
        reject_reason = request.form.get("reject_reason", "")
        req = BalanceRequest.query.get(req_id)
        if not req or req.status != "pending":
            flash("İşlem yapılamadı.")
            return redirect("/balance/requests")
        if action == "approve":
            req.status = "approved"
            req.user.balance += req.amount
            req.explanation = explanation
            db.session.commit()
            flash("Bakiye talebi onaylandı.")
        elif action == "reject":
            req.status = "rejected"
            req.explanation = explanation
            req.reject_reason = reject_reason
            db.session.commit()
            flash("Bakiye talebi reddedildi.")
    reqs = BalanceRequest.query.order_by(BalanceRequest.created_at.desc()).all()
    return render_template_string(HTML_BALANCE_REQUESTS, reqs=reqs)

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
def orders():
    user = User.query.get(session.get("user_id"))
    if user.role == "admin":
        orders = Order.query.order_by(Order.created_at.desc()).all()
    else:
        orders = Order.query.filter_by(user_id=user.id).order_by(Order.created_at.desc()).all()
    return render_template_string(HTML_ORDERS_SIMPLE, orders=orders, role=user.role)

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
        return jsonify({"success":False, "error":"Servis bulunamadı."})
    total = service.price * amount
    if not username or amount<10 or amount>1000:
        return jsonify({"success":False, "error":"Adet 10-1000 arası olmalı."})
    if user.balance < total:
        return jsonify({"success":False, "error":"Yetersiz bakiye!"})
    order = Order(
        username=username,
        user_id=user.id,
        amount=amount,
        status="pending",  # Sırada
        total_price=total
    )
    user.balance -= total
    db.session.add(order)
    db.session.commit()
    # Yeni bakiye de dön!
    return jsonify({"success":True, "new_balance": round(user.balance,2)})

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))