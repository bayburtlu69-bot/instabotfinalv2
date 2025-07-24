import os
import time
import random
import smtplib
from email.mime.text import MIMEText
from flask import (
    Flask, session, request, redirect,
    render_template_string, abort, url_for, flash
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from instagrapi import Client
from instagrapi.exceptions import LoginRequired

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

with app.app_context():
    db.create_all()
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

# --- SMTP AYARLARI ---
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
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Servisler ve Fiyat Listesi</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body class="bg-dark text-light">
  <div class="container py-4">
    <div class="card p-4 mx-auto" style="max-width:700px;">
      <h3>Aktif Servisler & Fiyat Listesi</h3>
      <table class="table table-dark table-bordered mt-3">
        <thead>
          <tr>
            <th>Servis</th>
            <th>Açıklama</th>
            <th>Fiyat (1 Adet)</th>
            <th>Min/Max</th>
          </tr>
        </thead>
        <tbody>
        {% for s in servisler %}
          <tr>
            <td>{{ s.name }}</td>
            <td>{{ s.description }}</td>
            <td>{{ s.price }} TL</td>
            <td>{{ s.min_amount }} / {{ s.max_amount }}</td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
      <a href="/panel" class="btn btn-secondary btn-sm mt-3">Panele Dön</a>
    </div>
  </div>
</body>
</html>
"""

HTML_TICKETS = """
<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Destek & Ticket Sistemi</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body class="bg-dark text-light">
  <div class="container py-4">
    <div class="card p-4 mx-auto" style="max-width:650px;">
      <h2 class="mb-3">Destek & Ticket Sistemi</h2>
      <form method="post">
        <label class="form-label">Konu</label>
        <input name="subject" class="form-control mb-2" placeholder="Konu başlığı">
        <label class="form-label">Mesaj</label>
        <textarea name="message" class="form-control mb-3" placeholder="Destek talebiniz..." rows="3"></textarea>
        <button class="btn btn-danger w-100 mb-3">Gönder</button>
      </form>
      <h5 class="mt-4 mb-2">Geçmiş Destek Talepleriniz</h5>
      <table class="table table-dark table-bordered text-center">
        <thead>
          <tr>
            <th>Tarih</th><th>Konu</th><th>Mesaj</th><th>Durum</th><th>Yanıt</th>
          </tr>
        </thead>
        <tbody>
        {% for t in tickets %}
          <tr>
            <td>{{ t.created_at.strftime('%d.%m.%Y %H:%M') }}</td>
            <td>{{ t.subject }}</td>
            <td>{{ t.message }}</td>
            <td>
              {% if t.status == "open" %}<span class="badge bg-warning text-dark">Açık</span>
              {% else %}<span class="badge bg-success">Yanıtlandı</span>{% endif %}
            </td>
            <td>{{ t.response or "" }}</td>
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

HTML_ORDERS_SIMPLE = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Geçmiş Siparişler</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" />
</head>
<body class="bg-dark text-light">
  <div class="container py-4">
    <div class="card p-4 mx-auto" style="max-width:650px;">
      <h2 class="mb-3">Geçmiş Siparişleriniz</h2>

      <table class="table table-dark table-bordered text-center mb-3">
        <thead>
          <tr>
            <th>#</th>
            <th>Hedef Kullanıcı</th>
            <th>Adet</th>
            <th>Fiyat</th>
            <th>Durum</th>
            <th>Hata</th>
            <th>İptal</th>
          </tr>
        </thead>
        <tbody>
          {% for s in orders %}
          <tr>
            <td>{{ loop.index }}</td>
            <td>{{ s.target }}</td>
            <td>{{ s.amount }}</td>
            <td>{{ "%.2f"|format(s.price) }}</td>
            <td>
              {% if s.status == "cancelled" %}
                <span class="badge bg-danger">İptal Edildi</span>
              {% elif s.status == "completed" %}
                <span class="badge bg-success">Tamamlandı</span>
              {% else %}
                <span class="badge bg-secondary">{{ s.status }}</span>
              {% endif %}
            </td>
            <td>{{ s.error or "-" }}</td>
            <td>
              {% if s.can_cancel %}
                <form method="POST" action="/order/cancel/{{ s.id }}" style="display:inline;">
                  <button type="submit" class="btn btn-sm btn-danger">İptal</button>
                </form>
              {% else %}
                -
              {% endif %}
            </td>
          </tr>
          {% else %}
          <tr>
            <td colspan="7" class="text-center text-muted">Henüz sipariş yok.</td>
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

HTML_PANEL = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Sipariş Paneli</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" />
  <style>
    /* Duyuru kutusu için modern, soft bir tasarım */
    .announcement-box {
      background-color: #d1ecf1;
      color: #0c5460;
      border: 1px solid #bee5eb;
      border-radius: 8px;
      padding: 15px 20px;
      font-weight: 500;
      white-space: pre-wrap;
      box-shadow: 0 2px 6px rgba(13, 110, 253, 0.15);
    }
    .announcement-label {
      font-weight: 700;
      color: #0b7285;
      margin-bottom: 6px;
      display: block;
    }
    textarea.form-control {
      border-radius: 8px;
      box-shadow: 0 2px 6px rgba(0, 123, 255, 0.25);
    }
  </style>
</head>
<body class="bg-dark text-light">
  <div class="container py-4">
    <div class="card p-4 mx-auto" style="max-width:800px;">
      <div class="d-flex justify-content-between align-items-center mb-3">
        <div>
          <b>{{ current_user }}</b> <span class="badge bg-info text-dark">{{ rolu_turkce(role) }}</span>
        </div>
        <div class="d-flex align-items-center gap-3">
          <div>Bakiye: <b>{{ balance }} TL</b></div>
          <a href="{{ url_for('orders') }}" class="btn btn-sm btn-primary">📦 Geçmiş Siparişler</a>
        </div>
      </div>
      <div class="d-grid gap-3 mb-3">
        {% if role == 'admin' %}
          <a href="{{ url_for('manage_users') }}" class="btn btn-secondary btn-block py-2">Kullanıcı Yönetimi</a>
          <a href="{{ url_for('balance_requests') }}" class="btn btn-warning btn-block py-2">Bakiye Talepleri</a>
          <a href="{{ url_for('admin_tickets') }}" class="btn btn-danger btn-block py-2">Tüm Destek Talepleri</a>
        {% else %}
          <a href="{{ url_for('user_balance') }}" class="btn btn-warning btn-block py-2">Bakiye Yükle</a>
          <a href="{{ url_for('tickets') }}" class="btn btn-danger btn-block py-2">Destek & Canlı Yardım</a>
        {% endif %}
        <a href="{{ url_for('services') }}" class="btn btn-info btn-block py-2">Servisler & Fiyat Listesi</a>
      </div>
      <h4 class="mb-3 mt-4">Yeni Sipariş</h4>
      <form method="post" class="row g-2 align-items-end mb-2">
        <div class="col"><input name="username" class="form-control" placeholder="Takip edilecek hesap" required></div>
        <div class="col"><input name="amount" type="number" min="1" class="form-control" placeholder="Takipçi adedi" required></div>
        <div class="col"><button class="btn btn-success w-100">Sipariş Ver</button></div>
      </form>
      <div class="mb-2"><b>Her takipçi adedi için fiyat: 0.50 TL’dir.</b></div>
      {% if error %}
        <div class="alert alert-danger py-2 small mb-2">{{ error }}</div>
      {% endif %}
      {% if msg %}
        <div class="alert alert-success py-2 small mb-2">{{ msg }}</div>
      {% endif %}
      <hr>
      <h5>Duyurular</h5>
      {% if role == 'admin' %}
        <form method="post" action="{{ url_for('save_announcement') }}">
          <label for="announcement" class="announcement-label">Duyuru Metni</label>
          <textarea id="announcement" name="announcement" rows="4" class="form-control mb-2" placeholder="Duyuru metnini buraya yazın...">{{ announcement_text or "" }}</textarea>
          <button class="btn btn-primary btn-sm">Duyuruyu Kaydet</button>
        </form>
      {% else %}
        <div class="announcement-box">
          {{ announcement_text or "Henüz duyuru yok." }}
        </div>
      {% endif %}
      <hr>
      <h5>Geçmiş Siparişler</h5>
      {% if orders %}
        <div class="table-responsive">
          <table class="table table-dark table-striped table-bordered align-middle">
            <thead>
              <tr>
                <th>#</th><th>Hedef Kullanıcı</th><th>Adet</th><th>Fiyat</th><th>Durum</th><th>Hata</th>
                {% if role == 'admin' %}<th>İptal</th>{% endif %}
              </tr>
            </thead>
            <tbody>
              {% for o in orders %}
              <tr>
                <td>{{ loop.index }}</td>
                <td>{{ o.username }}</td>
                <td>{{ o.amount }}</td>
                <td>{{ o.total_price }}</td>
                <td>
                  {% if o.status == 'complete' %}
                    <span class="badge bg-success">{{ o.status }}</span>
                  {% elif o.status == 'cancelled' %}
                    <span class="badge bg-secondary">{{ o.status }}</span>
                  {% elif o.status == 'error' %}
                    <span class="badge bg-danger">{{ o.status }}</span>
                  {% else %}
                    <span class="badge bg-warning text-dark">{{ o.status }}</span>
                  {% endif %}
                </td>
                <td>{{ o.error }}</td>
                {% if role == 'admin' %}
                <td>
                  {% if o.status not in ['complete','cancelled'] %}
                    <form method="post" action="{{ url_for('cancel_order', order_id=o.id) }}" style="display:inline">
                      <button class="btn btn-sm btn-outline-danger">İptal Et</button>
                    </form>
                  {% else %}
                    <span class="text-muted">–</span>
                  {% endif %}
                </td>
                {% endif %}
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      {% else %}
        <div class="alert alert-secondary mt-2">Henüz sipariş yok.</div>
      {% endif %}
      <div class="mt-3 text-end">
        <a href="{{ url_for('logout') }}" class="btn btn-outline-danger btn-sm">Çıkış Yap</a>
      </div>
    </div>
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
        except Exception as e:
            print(f"⚠️ {u}: cache yüklenemedi, login denenecek. Hata: {e}")
            try:
                cl.login(u, p)
                cl.dump_settings(sf)
                print(f"✅ {u}: cache sıfırdan oluşturuldu.")
            except Exception as e2:
                print(f"⚠️ {u}: login/dump sırasında hata → {e2}")
                continue
    else:
        try:
            print(f"🔑 {u}: cache yok, giriş yapılıyor…")
            cl.login(u, p)
            cl.dump_settings(sf)
            print(f"✅ {u}: ilk oturum tamamlandı ve cache oluşturuldu ({sf})")
        except Exception as e:
            print(f"⚠️ {u}: login/dump sırasında hata → {e}")
            continue
    cl._password = p
    BOT_CLIENTS.append(cl)
    time.sleep(10)
print("📦 Yüklü bot sayısı:", len(BOT_CLIENTS), "→", [getattr(c, 'username', '?') for c in BOT_CLIENTS])

def follow_user(client, target):
    try:
        uid = client.user_id_from_username(target)
        client.user_follow(uid)
    except LoginRequired:
        client.login(client.username, client._password)
        client.user_follow(client.user_id_from_username(target))

# --- DECORATORS ---
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

# --- DECORATORS ---
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
                    balance=10.0,
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
                balance=10.0,
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
    return redirect("/panel")

@app.route("/panel", methods=["GET", "POST"])
@login_required
def panel():
    user = User.query.get(session.get("user_id"))
    msg, error = "", ""
    if request.method == "POST":
        target = request.form.get("username", "").strip()
        try:
            amount = int(request.form.get("amount", "").strip())
        except:
            amount = 0
        price = SABIT_FIYAT
        total = amount * price
        if not target or amount <= 0:
            error = "Tüm alanları doğru doldurun!"
        elif user.balance < total:
            error = "Yetersiz bakiye!"
        elif len(BOT_CLIENTS) == 0:
            error = "Sistemde çalışan bot yok!"
        else:
            order = Order(
                username=target,
                user_id=user.id,
                amount=amount,
                status="pending",
                error="",
                total_price=total
            )
            user.balance -= total
            db.session.add(order)
            db.session.commit()
            status, err = "complete", ""
            for idx, cl in enumerate(BOT_CLIENTS[:amount], start=1):
                try:
                    follow_user(cl, target)
                except Exception as e:
                    status, err = "error", str(e)
                    break
            order.status = status
            order.error = err
            db.session.commit()
            if status == "complete":
                msg = f"{amount} takipçi başarıyla gönderildi."
            else:
                error = f"Bir hata oluştu: {err}"
    if user.role == "admin":
        orders = Order.query.order_by(Order.created_at.desc()).all()
    else:
        orders = Order.query.filter_by(user_id=user.id).order_by(Order.created_at.desc()).all()
    return render_template_string(
        HTML_PANEL,
        orders=orders,
        role=user.role,
        current_user=user.username,
        balance=round(user.balance, 2),
        msg=msg,
        error=error,
        rolu_turkce=rolu_turkce
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

@app.route("/services")
@login_required
def services():
    servisler = Service.query.filter_by(active=True).all()
    return render_template_string(HTML_SERVICES, servisler=servisler)

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
def orders():
    # Test amaçlı örnek veri
    orders = [
        {
            "id": 1,
            "target": "mikailaaktas",
            "amount": 2,
            "price": 0.4,
            "status": "cancelled",
            "error": "login_required",
            "can_cancel": False,
        },
        {
            "id": 2,
            "target": "awdwad",
            "amount": 1000,
            "price": 200.0,
            "status": "completed",
            "error": None,
            "can_cancel": False,
        },
    ]
    return render_template_string(HTML_ORDERS_SIMPLE, orders=orders)

from flask import session

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))