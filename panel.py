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

EXT_SELECTED_IDS = [854, 827, 1588,]  # Örneğin sadece 1 ve 2 no’lu servisleri çek

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

from datetime import datetime

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
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>insprov.uk</title>
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
    a {
      color: #89b4f8;
    }
    a:hover {
      color: #ffffff;
      text-decoration: underline;
    }
  </style>
</head>
<body class="d-flex justify-content-center align-items-center">
  <div class="card shadow p-4" style="min-width:340px;">
    <h3 class="mb-3 text-center">insprov.uk</h3>

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
  </style>
</head>
<body class="d-flex justify-content-center align-items-center">
  <div class="card shadow p-4" style="min-width:370px;">
    <h3 class="mb-3 text-center">insprov.uk <span class="text-primary">Kayıt</span></h3>

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
    /* number input: okları kaldır */
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
  </style>
</head>
<body class="text-light">
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
    /* Number input oklarını kaldır */
    input[type=number]::-webkit-inner-spin-button,
    input[type=number]::-webkit-outer-spin-button {
      -webkit-appearance: none;
      margin: 0;
    }
    input[type=number] {
      -moz-appearance: textfield;
      appearance: textfield;
    }
  </style>
</head>
<body class="text-light">
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

HTML_BALANCE = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Bakiye Yükle</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body {
      margin: 0;
      min-height: 100vh;
      background: linear-gradient(-45deg, #1e1e1e, #2c2f34, #1e1e1e, #000000);
      background-size: 400% 400%;
      animation: gradientBG 15s ease infinite;
      color: #fff;
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
    }
    .form-control, .form-select {
      background-color: #2e2e2e !important;
      color: #f1f1f1 !important;
      border: 1px solid #444;
    }
    .form-control::placeholder,
    .form-select::placeholder {
      color: #aaa;
    }
    .form-control:focus, .form-select:focus {
      background-color: #2e2e2e !important;
      color: #fff !important;
      border-color: #2186eb;
      box-shadow: none !important;
    }
    input[type=number]::-webkit-inner-spin-button,
    input[type=number]::-webkit-outer-spin-button {
      -webkit-appearance: none;
      margin: 0;
    }
    input[type=number] {
      -moz-appearance: textfield;
    }
    .table-dark th, .table-dark td {
      color: #eee;
    }
    .alert-info {
      background-color: #1a2a3a;
      color: #cce4ff;
      border-color: #2a4d6b;
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
  </style>
</head>
<body class="text-light">
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
        <input name="amount" type="number" step="0.01" min="1" class="form-control mb-2" placeholder="" required>
        <button class="btn btn-primary w-100">Ödemeyi Yaptım</button>
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
<html lang="tr"><head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Bakiye Talepleri</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body {
      margin: 0;
      min-height: 100vh;
      background: linear-gradient(-45deg, #1e1e1e, #2c2f34, #1e1e1e, #000000);
      background-size: 400% 400%;
      animation: gradientBG 15s ease infinite;
      color: #fff;
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
    }
    .form-control,
    .form-select {
      background-color: #2e2e2e;
      color: #fff;
      border: 1px solid #444;
    }
    .form-control::placeholder,
    .form-select option {
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
    .alert-info {
      background-color: #1a2a3a;
      color: #cce4ff;
      border-color: #2a4d6b;
    }
  </style>
</head>
<body class="text-light">
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

    .editing .text { display: none; }
    .editing .inp  { display: inline-block !important; width: 100%; }

    .card {
      background: rgba(20, 20, 20, 0.9);
      border-radius: 14px;
      box-shadow: 0 4px 20px rgba(0,0,0,0.3);
      color: #f1f1f1;
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
  </style>
</head>
<body>
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
</body>
</html>
"""

HTML_ADMIN_TICKETS = """
<!DOCTYPE html>
<html lang="tr">
<head>
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

    .btn-success {
      font-weight: 500;
    }

    .btn-secondary {
      font-weight: 500;
    }

    .text-muted {
      color: #bbb !important;
    }
  </style>
</head>
<body>
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
  </style>
</head>
<body>
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
        }
        @keyframes gradientBG {
          0% {background-position: 0% 50%;}
          50% {background-position: 100% 50%;}
          100% {background-position: 0% 50%;}
        }

        .container { margin-top: 60px; }
        .table { background: #1f1f1f; color: #eaeaea; border-radius: 16px; }
        .table th, .table td { vertical-align: middle; color: #fff; }
        .badge-warning { background: #ffc107; color: #000; }
        .badge-success { background: #28a745; }
        .badge-secondary { background: #6c757d; }
        .badge-danger { background: #dc3545; }
        .orders-card {
          border-radius: 25px;
          box-shadow: 0 4px 24px rgba(0,0,0,0.5);
          padding: 40px;
          background: rgba(33, 37, 41, 0.95);
        }
        .flash-msg { margin-bottom: 24px; }
        .btn-resend, .btn-complete, .btn-cancel {
          margin: 2px 0;
        }
        h1 {
          color: #61dafb;
          text-shadow: 0 2px 16px #000a;
        }
    </style>
</head>
<body>
    <div class="container d-flex justify-content-center">
        <div class="orders-card w-100" style="max-width: 1000px;">
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

            <div class="table-responsive">
                <table class="table table-dark table-bordered align-middle text-center">
                    <thead>
                        <tr>
                            <th>#</th>
                            {% if role == 'admin' %}
                              <th>Kullanıcı</th>
                            {% endif %}
                            <th>Hedef Kullanıcı</th>
                            <th>Adet</th>
                            <th>Fiyat</th>
                            <th>Durum</th>
                            {% if role == 'admin' %}
                              <th>Hata</th>
                              <th>İşlem</th>
                            {% endif %}
                        </tr>
                    </thead>
                    <tbody>
                        {% for o in orders %}
                        <tr>
                            <td>{{ loop.index }}</td>
                            {% if role == 'admin' %}
                              <td>{{ o.user.username }}</td>
                            {% endif %}
                            <td>{{ o.username }}</td>
                            <td>{{ o.amount }}</td>
                            <td>{{ "%.2f"|format(o.total_price) }}</td>
                            <td>
                                {% if role != 'admin' %}
                                    {% if o.status == 'pending' %}
                                        <span class="badge badge-warning">Sırada</span>
                                    {% elif o.status == 'completed' %}
                                        <span class="badge badge-success">Tamamlandı</span>
                                    {% elif o.status == 'cancelled' %}
                                        <span class="badge badge-secondary">İptal Edildi</span>
                                    {% else %}
                                        <span class="badge badge-warning">Sırada</span>
                                    {% endif %}
                                {% else %}
                                    {% if o.error %}
                                        <span class="badge badge-danger">HATA</span>
                                    {% elif o.status == 'pending' %}
                                        <span class="badge badge-warning">Sırada</span>
                                    {% elif o.status == 'completed' %}
                                        <span class="badge badge-success">Tamamlandı</span>
                                    {% elif o.status == 'cancelled' %}
                                        <span class="badge badge-secondary">İptal Edildi</span>
                                    {% else %}
                                        <span class="badge badge-secondary">{{ o.status }}</span>
                                    {% endif %}
                                {% endif %}
                            </td>
                            {% if role == 'admin' %}
                            <td>{{ o.error if o.error else "-" }}</td>
                            <td>
                                {% if o.error %}
                                    <form method="post" style="display:inline;" action="/orders/resend/{{ o.id }}">
                                        <button class="btn btn-warning btn-sm btn-resend" type="submit">Resend</button>
                                    </form>
                                {% endif %}
                                {% if o.status == 'pending' %}
                                  <form method="post" style="display:inline;" action="/orders/complete/{{ o.id }}">
                                    <button class="btn btn-success btn-sm btn-complete" type="submit">Tamamlandı</button>
                                  </form>
                                {% endif %}
                                {% if o.status != 'completed' and o.status != 'cancelled' %}
                                  <form method="post" style="display:inline;" action="/orders/cancel/{{ o.id }}">
                                    <button class="btn btn-danger btn-sm btn-cancel" type="submit">İptal & Bakiye İade</button>
                                  </form>
                                {% endif %}
                            </td>
                            {% endif %}
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            <a href="/panel" class="btn btn-secondary w-100 mt-4" style="border-radius:12px;">Panele Dön</a>
        </div>
    </div>
</body>
</html>
"""

HTML_TICKETS = """
<!DOCTYPE html>
<html lang="tr">
<head>
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
  </style>
</head>
<body class="text-light">
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
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Sipariş Paneli</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" />
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
  <style>
    body {
      margin: 0;
      font-family: Arial, sans-serif;
      background: #000;
      overflow-x: hidden;
      color: #fff;
    }

    body::before {
      content: "";
      position: fixed;
      top: 0; left: 0;
      width: 100%; height: 100%;
      background: linear-gradient(-45deg, #1e1e1e, #2c2f34, #1e1e1e, #000000);
      background-size: 400% 400%;
      animation: gradientBG 15s ease infinite;
      z-index: -1;
      opacity: 0.4;
    }

    @keyframes gradientBG {
      0% { background-position: 0% 50%; }
      50% { background-position: 100% 50%; }
      100% { background-position: 0% 50%; }
    }

    h4 {
      color: #ffffff !important;
    }

    .card {
      background-color: rgba(33, 37, 41, 0.92);
      border-radius: 14px;
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

    .form-label {
      color: #fff !important;
      font-weight: 500;
    }

    .alert-secondary {
      background-color: #2c2f34;
      border-color: #3c3f46;
      color: #ddd;
    }

    .btn-primary, .btn-secondary, .btn-warning, .btn-danger, .btn-success, .btn-info {
      color: #fff;
    }

    .btn-outline-danger {
      color: #dc3545;
      border-color: #dc3545;
    }

    .btn-outline-danger:hover {
      background-color: #dc3545;
      color: #fff;
    }

    .welcome-card {
      background: linear-gradient(90deg, #2b2f41 0%, #1e1e1e 100%);
      border-radius: 18px;
      padding: 20px 28px 16px 24px;
      margin-bottom: 20px;
      box-shadow: 0 2px 18px 0 rgba(0,0,0,0.3);
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
      color: #fff;
      letter-spacing: 0.02em;
    }

    .welcome-desc {
      font-size: 0.97rem;
      color: #ccc;
    }

    .welcome-balance {
      font-size: 1.08rem;
      color: #fff;
      font-weight: 600;
      margin-bottom: 0.3rem;
      text-align: right;
    }

    input[disabled] {
      background-color: #2c2f34 !important;
      color: #d6dce5 !important;
      opacity: 1 !important;
    }

    input[type=number]::-webkit-outer-spin-button,
    input[type=number]::-webkit-inner-spin-button {
      -webkit-appearance: none;
      margin: 0;
    }

    input[type=number] {
      -moz-appearance: textfield;
    }

    @media (max-width: 575px) {
      .welcome-card { flex-direction: column; align-items: flex-start; gap: 14px; }
      .welcome-balance { text-align: left; }
    }
  </style>
</head>
<body class="text-light">
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
          <div class="welcome-balance">Bakiye: <span style="color:#4da3ff" id="balance">{{ balance }} TL</span></div>
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
          <div class="alert alert-secondary" style="white-space: pre-line; display: flex; flex-direction: column; justify-content: center; min-height: 200px;">
            <b>LÜTFEN SİPARİŞ VERMEDEN ÖNCE BU KISMI OKU</b>

            Sistem, gönderilecek takipçi sayısına göre en uygun şekilde çalışır.

            Örnek: 1000 Türk gerçek takipçi siparişiniz ortalama 3-6 saat arasında tamamlanır.

            <b>DİKKAT:</b> Takipçi gönderimi, organik hesaplardan ve gerçek Türk profillerden yapılır. 
            Gizli (kapalı) hesaplara gönderim yapılmaz.
          </div>
        </div>
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
          <input type="text" class="form-control" id="total" placeholder="" disabled>
        </div>
        <button type="submit" class="btn btn-primary w-100" id="orderSubmitBtn">Siparişi Gönder</button>
      </form>

      <script>
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
      </script>

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
<html lang="tr">
<head>
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
    if not username or amount < 10 or amount > 1000:
        return jsonify({"success":False, "error":"Adet 10-1000 arası olmalı."})
    if user.balance < total:
        return jsonify({"success":False, "error":"Yetersiz bakiye!"})

    # Varsayılanlar
    status = "pending"
    error = None

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
            if "order" not in result:
                # HATA GELDİ, siparişi error statüsünde kaydet!
                status = "error"
                error = result.get("error", "Resellersmm sipariş hatası!")
        except Exception as e:
            status = "error"
            error = "ResellersMM API bağlantı/yanıt hatası: "+str(e)

    # Siparişi oluştur
    order = Order(
        username=username,
        user_id=user.id,
        amount=amount,
        status=status,
        total_price=total,
        service_id=service_id,
        error=error
    )
    user.balance -= total
    db.session.add(order)
    db.session.commit()

    if status == "error":
        return jsonify({"success":True, "new_balance": round(user.balance,2), "info":error})
    else:
        return jsonify({"success":True, "new_balance": round(user.balance,2)})

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
    return redirect(url_for("orders"))

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
        return redirect(url_for("orders"))
    order.status = "completed"
    order.error = None
    db.session.commit()
    flash("Sipariş manuel tamamlandı.", "success")
    return redirect(url_for("orders"))

@app.route('/orders/cancel/<int:order_id>', methods=['POST'])
@login_required
def order_cancel(order_id):
    user = User.query.get(session.get("user_id"))
    # Sadece admin iptal edebilir:
    if not user or user.role != "admin":
        abort(403)

    order = Order.query.get(order_id)
    if not order:
        flash("Sipariş bulunamadı.", "danger")
        return redirect(url_for("orders"))

    # Eğer sipariş zaten iptal edilmişse, tekrar iade etme!
    if order.status == "cancelled":
        flash("Sipariş zaten iptal edilmiş.", "warning")
        return redirect(url_for("orders"))

    target_user = User.query.get(order.user_id)
    if not target_user:
        flash("Müşteri bulunamadı.", "danger")
        return redirect(url_for("orders"))

    # Siparişi iptal et ve bakiyeyi iade et
    order.status = "cancelled"
    order.error = None
    target_user.balance += order.total_price   # --- BAKİYE İADE!

    db.session.commit()
    flash("Sipariş iptal edildi ve bakiye iade edildi.", "success")
    return redirect(url_for("orders"))

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))