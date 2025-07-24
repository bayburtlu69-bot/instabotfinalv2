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

# --- BOT AYARI ---
def load_bots(path="bots.txt"):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip().split(":", 1) for line in f if ":" in line]

BOT_CLIENTS = []
for username, password in load_bots():
    sf = f"settings_{username}.json"
    cl = Client()
    cl.private.timeout = 10
    if os.path.exists(sf):
        try:
            cl.load_settings(sf)
            print(f"✅ {username}: Cache'dan yüklendi ({sf})")
        except Exception as e:
            print(f"⚠️ {username}: Cache yüklenemedi → {e}")
    try:
        cl.login(username, password)
        cl.dump_settings(sf)
        print(f"🔑 {username}: Login tamamlandı, cache oluşturuldu")
    except Exception as e:
        print(f"⚠️ {username}: Login hatası → {e}")
        continue
    cl._password = password
    BOT_CLIENTS.append(cl)
    time.sleep(1)

print("📦 Yüklü bot sayısı:", len(BOT_CLIENTS), "→", [c.username for c in BOT_CLIENTS])

app = Flask(__name__)
app.url_map.strict_slashes = False
app.secret_key = os.getenv("SECRET_KEY", "çok-gizli-bir-anahtar")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///app.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

SABIT_FIYAT = 0.2

# --- MODELS ---
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

class Announcement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

with app.app_context():
    db.create_all()
    # ensure admin user exists
    if not User.query.filter_by(username="admin").first():
        db.session.add(User(
            username="admin",
            password_hash=generate_password_hash("6906149Miko"),
            email="kuzenlertv6996@gmail.com",
            role="admin",
            balance=1000,
            is_verified=True
        ))
    # ensure service
    svc = Service.query.filter_by(name="Instagram Takipçi").first()
    if svc:
        svc.price = SABIT_FIYAT
    else:
        db.session.add(Service(
            name="Instagram Takipçi",
            description="Gerçek ve Türk takipçi gönderimi.",
            price=SABIT_FIYAT,
            min_amount=1,
            max_amount=1000,
            active=True
        ))
    db.session.commit()

# --- SMTP & UTIL ---
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

# --- THEME & TEMPLATES ---
THEME_HEAD = """
<style>
:root {--main-bg-light:#f8f9fa;--main-bg-dark:#23272b;--main-txt-light:#212529;--main-txt-dark:#f8f9fa;}
body {background-color:var(--main-bg-dark);color:var(--main-txt-dark);transition:background 0.2s,color 0.2s;}
.card,.table,.form-control,.form-select,.btn,.alert {transition:background 0.2s,color 0.2s;}
.theme-light body {background-color:var(--main-bg-light)!important;color:var(--main-txt-light)!important;}
.theme-light .card,.theme-light .table,.theme-light .form-control,.theme-light .form-select {background-color:#fff!important;color:#212529!important;}
.theme-light .btn-secondary,.theme-light .btn-info {background-color:#dee2e6!important;color:#212529!important;}
.theme-light .btn-danger {background-color:#f8d7da!important;color:#842029!important;}
.theme-dark body {background-color:var(--main-bg-dark);}
.theme-dark .card,.theme-dark .table,.theme-dark .form-control,.theme-dark .form-select {background-color:#23272b!important;color:#fff!important;}
.theme-dark .btn-secondary,.theme-dark .btn-info {background-color:#343a40!important;color:#fff!important;}
.theme-dark .btn-danger {background-color:#dc3545!important;color:#fff!important;}
.theme-dark .form-control {background-color:#2b2f33!important;border-color:#444!important;color:#eee!important;}
.theme-dark .form-control::placeholder {color:#999!important;opacity:1!important;}
.theme-light .form-control {background-color:#fff!important;border-color:#ced4da!important;color:#212529!important;}
.theme-light .form-control::placeholder {color:#666!important;}
.theme-toggle-btn {position:fixed;top:10px;right:10px;z-index:9999;}
</style>
<script>
function setTheme(theme){
  document.documentElement.className=theme==="light"?"theme-light":"theme-dark";
  localStorage.setItem("panelTheme",theme);
  document.getElementById("themeBtn").innerHTML=theme==="light"? '🌙 Karanlık Mod' : '☀️ Aydınlık Mod';
}
function toggleTheme(){
  var th = document.documentElement.className.includes("theme-light") ? "dark" : "light";
  setTheme(th);
}
window.onload=function(){
  setTheme(localStorage.getItem("panelTheme")||"dark");
}
</script>
"""
THEME_TOGGLE_BUTTON = """
<button id="themeBtn" class="btn btn-secondary theme-toggle-btn" onclick="toggleTheme()">🌙 Karanlık Mod</button>
"""
def wrap_theme(html):
    return html.replace("<head>", "<head>"+THEME_HEAD).replace("</body>", THEME_TOGGLE_BUTTON+"</body>")

# EDIT ANNOUNCEMENT TEMPLATE
HTML_ANNOUNCEMENT = wrap_theme("""
<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Duyuru Ayarları</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body>
<div class="container py-4">
  <div class="card p-4 mx-auto" style="max-width:600px;">
    <h3>Duyuru Ayarları</h3>
    <form method="post">
      <div class="mb-3">
        <label class="form-label">Duyuru İçeriği:</label>
        <textarea name="content" class="form-control" rows="4">{{ announcement.content if announcement else "" }}</textarea>
      </div>
      <button type="submit" name="action" value="save" class="btn btn-success w-100 mb-2">Kaydet</button>
      {% if announcement %}
      <button type="submit" name="action" value="delete" class="btn btn-danger w-100">Sil</button>
      {% endif %}
    </form>
    <a href="{{ url_for('panel') }}" class="btn btn-secondary btn-sm mt-3">Panele Dön</a>
  </div>
</div>
</body>
</html>
""")

HTML_LOGIN = wrap_theme("""
<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>insprov.uk</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body class="d-flex justify-content-center align-items-center" style="height:100vh;">
  <div class="card shadow p-4" style="min-width:340px;">
    <h3 class="mb-3 text-center">insprov.uk</h3>
    {% with messages = get_flashed_messages() %}
      {% if messages %}
        <div class="alert alert-danger p-2 py-1 small mb-3" role="alert">
          {% for message in messages %}{{ message }}<br>{% endfor %}
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
""")

HTML_REGISTER = wrap_theme("""
<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Kayıt Ol</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body class="d-flex justify-content-center align-items-center" style="height:100vh;">
  <div class="card shadow p-4" style="min-width:370px;">
    <h3 class="mb-3 text-center">insprov.uk <span class="text-primary">Kayıt</span></h3>
    {% with messages = get_flashed_messages() %}
      {% if messages %}
        <div class="alert alert-danger p-2 py-1 small mb-3" role="alert">
          {% for message in messages %}{{ message }}<br>{% endfor %}
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
""")

HTML_USERS = wrap_theme("""
<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Kullanıcı Yönetimi</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body>
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
          <thead><tr><th>#</th><th>Kullanıcı</th><th>Rol</th><th>Bakiye</th><th>İşlem</th></tr></thead>
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
      <div class="mt-3"><a href="{{ url_for('panel') }}" class="btn btn-secondary btn-sm">Panele Dön</a></div>
    </div>
  </div>
</body>
</html>
""")

HTML_BALANCE = wrap_theme("""
<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Bakiye Yükle</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body>
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
        <thead><tr><th>Tarih</th><th>Tutar</th><th>Durum</th><th>Açıklama</th><th>Ret Sebebi</th></tr></thead>
        <tbody>
        {% for req in requests %}
          <tr>
            <td>{{ req.created_at.strftime('%d.%m.%Y %H:%M') }}</td>
            <td>{{ req.amount }}</td>
            <td>
              {% if req.status == "pending" %}<span class="badge bg-warning text-dark">Bekliyor</span>{% elif req.status=="approved" %}<span class="badge bg-success">Onaylandı</span>{% else %}<span class="badge bg-danger">Reddedildi</span>{% endif %}
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
""")

HTML_BALANCE_REQUESTS = wrap_theme("""
<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Bakiye Talepleri</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body>
  <div class="container py-4">
    <div class="card p-4 mx-auto" style="max-width:800px;">
      <h3>Bakiye Talepleri</h3>
      {% with messages = get_flashed_messages() %}
        {% if messages %}
          <div class="alert alert-info p-2 small mb-2" role="alert">{% for m in messages %}{{ m }}<br>{% endfor %}</div>
        {% endif %}
      {% endwith %}
      <table class="table table-dark table-bordered">
        <thead><tr><th>#</th><th>Kullanıcı</th><th>Tutar</th><th>Tarih</th><th>Durum</th><th>Açıklama</th><th>Ret Sebebi</th><th>İşlem</th></tr></thead>
        <tbody>
        {% for req in reqs %}
          <tr>
            <td>{{ req.id }}</td><td>{{ req.user.username }}</td><td>{{ req.amount }}</td><td>{{ req.created_at.strftime('%d.%m.%Y %H:%M') }}</td><td>
            {% if req.status=="pending" %}<span class="badge bg-warning text-dark">Bekliyor</span>{% elif req.status=="approved" %}<span class="badge bg-success">Onaylandı</span>{% else %}<span class="badge bg-danger">Reddedildi</span>{% endif %}
            </td><td>{{ req.explanation or "" }}</td><td>{{ req.reject_reason or "" }}</td><td>
            {% if req.status=="pending" %}
              <form method="post" style="display:inline-block"><input type="hidden" name="req_id" value="{{ req.id }}"><input name="explanation" class="form-control form-control-sm mb-1" placeholder="Onay açıklama"><button name="action" value="approve" class="btn btn-success btn-sm">Onayla</button></form>
              <form method="post" style="display:inline-block"><input type="hidden" name="req_id" value="{{ req.id }}"><input name="explanation" class="form-control form-control-sm mb-1" placeholder="Ret açıklama"><select name="reject_reason" class="form-select form-select-sm mb-1"><option value="">Ret sebebi seç</option><option>BANKA HESABINA PARA AKTARILMAMIŞ</option><option>HATALI İSİM SOYİSİM</option><option>BAŞKA BİR KULLANICIDAN GELEN BİLDİRİM</option><option>MANUEL RED (Açıklamada belirt)</option></select><button name="action" value="reject" class="btn btn-danger btn-sm">Reddet</button></form>
            {% else %}<span class="text-muted">—</span>{% endif %}
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
""")

HTML_SERVICES = wrap_theme("""
<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Servisler & Fiyat Listesi</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body>
  <div class="container py-4">
    <div class="card p-4 mx-auto" style="max-width:700px;">
      <h3>Aktif Servisler & Fiyat Listesi</h3>
      <table class="table table-dark table-bordered mt-3">
        <thead><tr><th>Servis</th><th>Açıklama</th><th>Fiyat</th><th>Min/Max</th></tr></thead>
        <tbody>
        {% for s in servisler %}
          <tr><td>{{ s.name }}</td><td>{{ s.description }}</td><td>{{ s.price }} TL</td><td>{{ s.min_amount }}/{{ s.max_amount }}</td></tr>
        {% endfor %}
        </tbody>
      </table>
      <a href="/panel" class="btn btn-secondary btn-sm mt-3">Panele Dön</a>
    </div>
  </div>
</body>
</html>
""")

HTML_TICKETS = wrap_theme("""
<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Destek & Ticket</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body>
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
      <h5 class="mt-4 mb-2">Geçmiş Talepler</h5>
      <table class="table table-dark table-bordered text-center">
        <thead><tr><th>Tarih</th><th>Konu</th><th>Mesaj</th><th>Durum</th><th>Yanıt</th></tr></thead>
        <tbody>
        {% for t in tickets %}
          <tr><td>{{ t.created_at.strftime('%d.%m.%Y %H:%M') }}</td><td>{{ t.subject }}</td><td>{{ t.message }}</td><td>{% if t.status=="open" %}<span class="badge bg-warning text-dark">Açık</span>{% else %}<span class="badge bg-success">Yanıtlandı</span>{% endif %}</td><td>{{ t.response or "" }}</td></tr>
        {% endfor %}
        </tbody>
      </table>
      <a href="/panel" class="btn btn-secondary btn-sm w-100">Panele Dön</a>
    </div>
  </div>
</body>
</html>
""")

HTML_ADMIN_TICKETS = wrap_theme("""
<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Admin Ticket</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body>
  <div class="container py-4">
    <div class="card p-4 mx-auto" style="max-width:900px;">
      <h2 class="mb-4">Tüm Destek Talepleri</h2>
      <table class="table table-dark table-bordered text-center align-middle">
        <thead><tr><th>ID</th><th>Kullanıcı</th><th>Tarih</th><th>Konu</th><th>Mesaj</th><th>Durum</th><th>Yanıt</th><th>İşlem</th></tr></thead>
        <tbody>
        {% for t in tickets %}
          <tr><td>{{ t.id }}</td><td>{{ t.user.username }}</td><td>{{ t.created_at.strftime('%d.%m.%Y %H:%M') }}</td><td>{{ t.subject }}</td><td>{{ t.message }}</td><td>{% if t.status=="open" %}<span class="badge bg-warning text-dark">Açık</span>{% else %}<span class="badge bg-success">Yanıtlandı</span>{% endif %}</td><td>{{ t.response or "" }}</td><td>{% if t.status=="open" %}<form method="post"><input type="hidden" name="ticket_id" value="{{ t.id }}"><input name="response" class="form-control mb-1" placeholder="Yanıt"><button class="btn btn-success btn-sm w-100">Yanıt & Kapat</button></form>{% else %}<span class="text-muted">—</span>{% endif %}</td></tr>
        {% endfor %}
        </tbody>
      </table>
      <a href="/panel" class="btn btn-secondary btn-sm w-100">Panele Dön</a>
    </div>
  </div>
</body>
</html>
""")

HTML_PANEL = wrap_theme("""
<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Sipariş Paneli</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body>
  <div class="container py-4">
    <div class="card p-4 mx-auto" style="max-width:800px;">
      <div class="d-flex justify-content-between align-items-center mb-3">
        <div><b>{{ current_user }}</b> <span class="badge bg-info text-dark">{{ rolu_turkce(role) }}</span></div>
        <div>Bakiye: <b>{{ balance }} TL</b></div>
      </div>
      <div class="d-grid gap-3 mb-3">
        {% if role=='admin' %}
          <a href="{{ url_for('manage_users') }}" class="btn btn-secondary py-2">Kullanıcı Yönetimi</a>
          <a href="/balance/requests" class="btn btn-warning py-2">Bakiye Talepleri</a>
          <a href="/admin/tickets" class="btn btn-danger py-2">Tüm Destek Talepleri</a>
        {% else %}
          <a href="/balance" class="btn btn-warning py-2">Bakiye Yükle</a>
          <a href="/tickets" class="btn btn-danger py-2">Destek & Yardım</a>
        {% endif %}
        <a href="/services" class="btn btn-info py-2">Servisler & Fiyat Listesi</a>
      </div>
      <div class="mb-3">
        <div class="card"><div class="card-header">Duyurular</div><div class="card-body">
          {% if announcement and announcement.content %}
            {{ announcement.content }}
          {% else %}
            Henüz duyuru yok.
          {% endif %}
          {% if role=='admin' %}
            <a href="{{ url_for('edit_announcement') }}" class="btn btn-warning w-100 mt-2">Duyuru Ekle/Sil</a>
          {% endif %}
        </div></div>
      </div>
      <h4 class="mb-3 mt-4">Yeni Sipariş</h4>
      <form method="post" class="row g-2 align-items-end mb-2">
        <div class="col"><input name="username" class="form-control" placeholder="Takip edilecek hesap" required></div>
        <div class="col"><input name="amount" type="number" min="1" class="form-control" placeholder="Takipçi adedi" required></div>
        <div class="col"><button class="btn btn-success w-100">Sipariş Ver</button></div>
      </form>
      <div class="mb-2"><b>Her takipçi adedi için fiyat : 0.2 TL’dir.</b></div>
      {% if error %}<div class="alert alert-danger py-2 small mb-2">{{ error }}</div>{% endif %}
      {% if msg %}<div class="alert alert-success py-2 small mb-2">{{ msg }}</div>{% endif %}
      <hr>
      <h5>Geçmiş Siparişler</h5>
      {% if orders %}
        <div class="table-responsive"><table class="table table-dark table-striped table-bordered align-middle">
          <thead><tr><th>#</th><th>Hedef</th><th>Adet</th><th>Fiyat</th><th>Durum</th><th>Hata</th>{% if role=='admin' %}<th>İptal</th>{% endif %}</tr></thead>
          <tbody>
          {% for o in orders %}
            <tr>
              <td>{{ loop.index }}</td><td>{{ o.username }}</td><td>{{ o.amount }}</td><td>{{ o.total_price }}</td>
              <td><span class="badge {% if o.status=='complete' %}bg-success{% elif o.status=='error' %}bg-danger{% else %}bg-warning text-dark{% endif %}">{{ o.status }}</span></td>
              <td>{{ o.error }}</td>
              {% if role=='admin' %}
                <td>
                  {% if o.status not in ['complete','cancelled'] %}
                    <form method="post" action="{{ url_for('cancel_order', order_id=o.id) }}"><button class="btn btn-outline-danger btn-sm">İptal</button></form>
                  {% else %}
                    <span class="text-muted">–</span>
                  {% endif %}
                </td>
              {% endif %}
            </tr>
          {% endfor %}
          </tbody>
        </table></div>
      {% else %}
        <div class="alert alert-secondary">Henüz sipariş yok.</div>
      {% endif %}
      <div class="mt-3 text-end"><a href="{{ url_for('logout') }}" class="btn btn-outline-danger btn-sm">Çıkış</a></div>
    </div>
  </div>
</body>
</html>
""")

# --- HELPERS ---
def follow_user(client, target):
    try:
        uid = client.user_id_from_username(target)
        client.user_follow(uid)
    except LoginRequired:
        client.login(client.username, client._password)
        client.user_follow(client.user_id_from_username(target))

def login_required(f):
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect("/")
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper

def admin_required(f):
    def wrapper(*args, **kwargs):
        u = User.query.get(session.get("user_id"))
        if not u or u.role != "admin":
            abort(403)
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper

# --- ROUTES ---
@app.route("/", methods=["GET","POST"])
def login():
    if request.method=="POST":
        u = request.form.get("username","").strip()
        p = request.form.get("password","")
        usr = User.query.filter_by(username=u).first()
        if not usr or not usr.check_password(p):
            flash("Kullanıcı adı veya şifre yanlış!")
        elif not usr.is_verified:
            flash("E-posta doğrulanmadı!")
        else:
            session["user_id"] = usr.id
            return redirect("/panel")
    return render_template_string(HTML_LOGIN)

@app.route("/register", methods=["GET","POST"])
def register():
    sent = session.get("register_sent", False)
    temp = session.get("register_temp", {})
    if request.method=="POST":
        if not sent:
            username = request.form.get("username","").strip()
            password = request.form.get("password","")
            email = request.form.get("email","").strip().lower()
            if not username or not password or not email:
                flash("Tüm alanları doldurun!")
            elif User.query.filter_by(username=username).first():
                flash("Bu kullanıcı adı kayıtlı.")
            else:
                code = "%06d" % random.randint(0,999999)
                session["register_temp"] = {
                    "username": username,
                    "password": generate_password_hash(password),
                    "email": email,
                    "code": code
                }
                session["register_sent"] = True
                send_verification_mail(email, code)
                flash("Doğrulama kodu gönderildi.")
                return redirect("/register")
        else:
            code = request.form.get("verify_code","").strip()
            if temp and code == temp.get("code"):
                db.session.add(User(
                    username=temp["username"],
                    password_hash=temp["password"],
                    email=temp["email"],
                    role="viewer",
                    balance=10.0,
                    is_verified=True
                ))
                db.session.commit()
                session.pop("register_sent")
                session.pop("register_temp")
                flash("Kayıt tamamlandı.")
                return redirect("/")
            else:
                flash("Kod yanlış veya süresi doldu.")
    return render_template_string(HTML_REGISTER, sent=sent)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/users", methods=["GET","POST"])
@login_required
@admin_required
def manage_users():
    if request.method=="POST":
        u = request.form.get("u","").strip()
        pw = request.form.get("pw","")
        r = request.form.get("role","viewer")
        if u and pw and not User.query.filter_by(username=u).first():
            db.session.add(User(
                username=u,
                password_hash=generate_password_hash(pw),
                email=f"{u}@mail.com",
                role=r,
                balance=10.0,
                is_verified=True
            ))
            db.session.commit()
    users = User.query.order_by(User.username).all()
    return render_template_string(HTML_USERS,
        users=users,
        current_user=User.query.get(session["user_id"]).username,
        rolu_turkce=rolu_turkce
    )

@app.route("/users/delete/<int:user_id>")
@login_required
@admin_required
def delete_user(user_id):
    admin = User.query.get(session["user_id"])
    usr = User.query.get_or_404(user_id)
    if usr.username != admin.username:
        db.session.delete(usr)
        db.session.commit()
    return redirect("/users")

@app.route("/admin/add-balance", methods=["POST"])
@login_required
@admin_required
def admin_add_balance():
    uname = request.form.get("username","")
    amt = float(request.form.get("amount") or 0)
    user = User.query.filter_by(username=uname).first()
    if user and amt > 0:
        user.balance += amt
        db.session.commit()
        flash(f"{uname} kullanıcısına {amt} TL eklendi.")
    else:
        flash("Kullanıcı bulunamadı veya miktar hatalı.")
    return redirect("/users")

@app.route("/cancel/<int:order_id>", methods=["POST"])
@login_required
@admin_required
def cancel_order(order_id):
    o = Order.query.get_or_404(order_id)
    if o.status not in ["complete","cancelled"]:
        o.status = "cancelled"
        user = User.query.get(o.user_id)
        if user:
            user.balance += o.total_price
        db.session.commit()
    return redirect("/panel")

@app.route("/panel", methods=["GET","POST"])
@login_required
def panel():
    user = User.query.get(session["user_id"])
    msg = error = ""
    if request.method=="POST":
        target = request.form.get("username","").strip()
        try:
            amount = int(request.form.get("amount",""))
        except:
            amount = 0
        total = amount * SABIT_FIYAT

        if not target or amount <= 0:
            error = "Tüm alanları doldurun!"
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
            for cl in BOT_CLIENTS[:amount]:
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

    orders = (
        Order.query.order_by(Order.created_at.desc()).all()
        if user.role == "admin"
        else Order.query.filter_by(user_id=user.id).order_by(Order.created_at.desc()).all()
    )
    announcement = Announcement.query.first()
    return render_template_string(HTML_PANEL,
        current_user=user.username,
        role=user.role,
        balance=round(user.balance,2),
        msg=msg,
        error=error,
        orders=orders,
        rolu_turkce=rolu_turkce,
        announcement=announcement
    )

@app.route("/balance", methods=["GET","POST"])
@login_required
def user_balance():
    user = User.query.get(session["user_id"])
    msg = err = ""
    if request.method == "POST":
        try:
            amt = float(request.form.get("amount","0"))
        except:
            amt = 0
        if amt <= 0:
            err = "Geçersiz tutar."
        else:
            db.session.add(BalanceRequest(user_id=user.id, amount=amt))
            db.session.commit()
            msg = "Başvurunuz alındı. Admin onayını bekleyin."
    reqs = BalanceRequest.query.filter_by(user_id=user.id).order_by(BalanceRequest.created_at.desc()).all()
    return render_template_string(HTML_BALANCE, msg=msg, err=err, requests=reqs)

@app.route("/balance/requests", methods=["GET","POST"])
@login_required
@admin_required
def balance_requests():
    if request.method == "POST":
        rid = int(request.form.get("req_id"))
        action = request.form.get("action")
        expl = request.form.get("explanation","")
        rej = request.form.get("reject_reason","")
        r = BalanceRequest.query.get(rid)
        if r and r.status == "pending":
            if action == "approve":
                r.status = "approved"
                r.user.balance += r.amount
                r.explanation = expl
            else:
                r.status = "rejected"
                r.explanation = expl
                r.reject_reason = rej
            db.session.commit()
            flash("İşlem tamamlandı.")
    reqs = BalanceRequest.query.order_by(BalanceRequest.created_at.desc()).all()
    return render_template_string(HTML_BALANCE_REQUESTS, reqs=reqs)

@app.route("/services")
@login_required
def services():
    servisler = Service.query.filter_by(active=True).all()
    return render_template_string(HTML_SERVICES, servisler=servisler)

@app.route("/tickets", methods=["GET","POST"])
@login_required
def tickets():
    user = User.query.get(session["user_id"])
    if request.method == "POST":
        subj = request.form.get("subject","").strip()
        msg_ = request.form.get("message","").strip()
        if subj and msg_:
            db.session.add(Ticket(user_id=user.id, subject=subj, message=msg_))
            db.session.commit()
    tix = Ticket.query.filter_by(user_id=user.id).order_by(Ticket.created_at.desc()).all()
    return render_template_string(HTML_TICKETS, tickets=tix)

@app.route("/admin/tickets", methods=["GET","POST"])
@login_required
@admin_required
def admin_tickets():
    if request.method == "POST":
        tid = int(request.form.get("ticket_id"))
        resp = request.form.get("response","").strip()
        t = Ticket.query.get(tid)
        if t and t.status == "open" and resp:
            t.response = resp
            t.status = "closed"
            db.session.commit()
    tix = Ticket.query.order_by(Ticket.created_at.desc()).all()
    return render_template_string(HTML_ADMIN_TICKETS, tickets=tix)

@app.route("/announcement", methods=["GET","POST"])
@login_required
@admin_required
def edit_announcement():
    ann = Announcement.query.first()
    if request.method == "POST":
        if request.form.get("action") == "delete":
            if ann:
                db.session.delete(ann)
                db.session.commit()
        else:
            content = request.form.get("content","").strip()
            if ann:
                ann.content = content
            else:
                db.session.add(Announcement(content=content))
            db.session.commit()
        return redirect("/panel")
    return render_template_string(HTML_ANNOUNCEMENT, announcement=ann)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))