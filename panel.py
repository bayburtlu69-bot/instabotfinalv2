import os
import json
import time
from datetime import datetime
from flask import Flask, session, request, redirect, render_template_string, abort
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from instagrapi import Client
from concurrent.futures import ThreadPoolExecutor

# --- Uygulama ve veri tabanı ayarları ---
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "çok-gizli-bir-anahtar")  # production’da ENV VAR olarak sakla
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///app.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# --- Kullanıcı ve roller (sabit) ---
# Parolaları istediğin şekilde değiştir, production’da ENV VAR veya migration ile yönetin
USERS = {
    "admin":   {"password": generate_password_hash("adminpass"),   "role": "admin"},
    "viewer":  {"password": generate_password_hash("viewerpass"),  "role": "viewer"},
}

# --- Model tanımı (siparişler) ---
class Order(db.Model):
    id        = db.Column(db.Integer,   primary_key=True)
    username  = db.Column(db.String(128), nullable=False)
    status    = db.Column(db.String(32),  default="pending")     # pending, processing, complete, error
    error     = db.Column(db.String(256), nullable=True)
    created   = db.Column(db.DateTime,    default=datetime.utcnow)

# Database’i oluştur
with app.app_context():
    db.create_all()

# --- İnstabot yardımcı fonksiyonları ---
def load_bots(path="bots.txt"):
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip().split(":", 1) for line in f if ":" in line]

def get_clients():
    clients = []
    for u, p in load_bots():
        cl = Client()
        cl.private.timeout = 10
        sf = f"settings_{u}.json"
        if os.path.exists(sf):
            cl.load_settings(sf)
        else:
            cl.login(u, p)
            cl.dump_settings(sf)
        clients.append(cl)
    return clients

def follow_user(client, target):
    uid = client.user_id_from_username(target)
    client.user_follow(uid)

# Bot client’larını ve executor’u hazırla
BOT_CLIENTS      = get_clients()
FOLLOW_EXECUTOR  = ThreadPoolExecutor(max_workers=len(BOT_CLIENTS))

# --- HTML Şablonları ---
HTML_FORM = """
<!DOCTYPE html>
<html><head><title>Giriş</title></head>
<body>
  <h2>Giriş Yap</h2>
  <form method="post">
    <input type="text" name="username" placeholder="Kullanıcı Adı"><br>
    <input type="password" name="password" placeholder="Şifre"><br>
    <input type="submit" value="Giriş">
  </form>
</body>
</html>
"""

HTML_ORDER = """
<!DOCTYPE html>
<html><head><title>Panel</title></head>
<body>
  <h2>Yeni Sipariş</h2>
  {% if role == "admin" %}
  <form method="post">
    <input type="text" name="username" placeholder="Takip edilecek hesap">
    <input type="submit" value="Sipariş Ver">
  </form>
  {% else %}
    <p>Sipariş vermeye yetkiniz yok.</p>
  {% endif %}

  <p><a href="/logout">Çıkış Yap</a></p>
  <hr>

  <h3>Geçmiş Siparişler</h3>
  <table border="1" cellpadding="4" cellspacing="0">
    <tr>
      <th>#</th><th>Kullanıcı</th><th>Durum</th><th>Hata</th><th>Tarih</th>
    </tr>
    {% for o in orders %}
    <tr>
      <td>{{ loop.index }}</td>
      <td>{{ o.username }}</td>
      <td>{{ o.status }}</td>
      <td>{{ o.error or "" }}</td>
      <td>{{ o.created.strftime("%Y-%m-%d %H:%M") }}</td>
    </tr>
    {% endfor %}
  </table>
</body>
</html>
"""

# --- Yardımcı decorator ---
def login_required(f):
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect("/")
        return f(*args, **kwargs)
    wrapped.__name__ = f.__name__
    return wrapped

# --- Routes ---
@app.route("/", methods=["GET","POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "")
        user = USERS.get(u)
        if user and check_password_hash(user["password"], p):
            session["logged_in"] = True
            session["user"] = u
            session["role"] = user["role"]
            return redirect("/panel")
        # basitçe yeniden göster
    return render_template_string(HTML_FORM)

@app.route("/panel", methods=["GET","POST"])
@login_required
def panel():
    role = session.get("role")
    # Sipariş ekleme yalnızca admin için
    if request.method == "POST":
        if role != "admin":
            abort(403)
        target = request.form.get("username", "").strip()
        if target:
            # 1) DB'ye ekle pending olarak
            order = Order(username=target, status="pending")
            db.session.add(order)
            db.session.commit()

            # 2) Processing
            order.status = "processing"
            db.session.commit()

            # 3) Botlarla takip et
            errors = []
            for client in BOT_CLIENTS:
                try:
                    follow_user(client, target)
                except Exception as e:
                    errors.append(str(e))
            # 4) Son durumu kaydet
            if errors:
                order.status = "error"
                order.error = "; ".join(errors)
            else:
                order.status = "complete"
            db.session.commit()

        return redirect("/panel")

    # GET: tüm siparişleri sırala
    orders = Order.query.order_by(Order.created.desc()).all()
    return render_template_string(HTML_ORDER, orders=orders, role=role)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
