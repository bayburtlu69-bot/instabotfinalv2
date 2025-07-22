import os
import json
from flask import (
    Flask, session, request, redirect,
    render_template_string, abort, url_for
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from instagrapi import Client
from instagrapi.exceptions import LoginRequired

# ————— Uygulama & DB ayarları —————
app = Flask(__name__)
# Aşağıdaki satır ile hem "/panel" hem de "/panel/" gibi URL’ler çalışır:
app.url_map.strict_slashes = False

app.secret_key = os.getenv("SECRET_KEY", "çok-gizli-bir-anahtar")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///app.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# ————— User modeli —————
class User(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    role          = db.Column(db.String(16), nullable=False)  # admin veya viewer

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

# ————— DB oluştur & seed admin —————
with app.app_context():
    db.create_all()
    if not User.query.filter_by(username="admin").first():
        db.session.add(User(
            username="admin",
            password_hash=generate_password_hash("admin"),
            role="admin"
        ))
        db.session.commit()

# ————— HTML Şablonları —————
HTML_LOGIN = """ ... """   # Mevcut tam bloklarınızı buraya koyun
HTML_USERS = """ ... """
HTML_PANEL = """ ... """

# ————— Sipariş verilerini saklayacağımız dosya —————
ORDERS_FILE = "orders.json"

# ————— Bot hazırlığı & cache yükleme —————
def load_bots(path="bots.txt"):
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip().split(":", 1) for line in f if ":" in line]

BOT_CLIENTS = []
for u, p in load_bots():
    sf = f"settings_{u}.json"
    cl = Client()
    cl.private.timeout = 10
    if os.path.exists(sf):
        cl.load_settings(sf)
        cl._password = p
        BOT_CLIENTS.append(cl)
        print(f"✅ {u}: cache'dan yüklendi ({sf})")
    else:
        print(f"⚠️ {u}: '{sf}' bulunamadı; önce localde oturum açıp `dump_settings()` ile oluşturun.")
        print(f"   → Bunun için proje kökünde create_cache.py betiğini kullanabilirsiniz.")

print("📦 Yüklü bot sayısı:", len(BOT_CLIENTS), "→", [c.username for c in BOT_CLIENTS])

def follow_user(client, target):
    try:
        uid = client.user_id_from_username(target)
        client.user_follow(uid)
    except LoginRequired:
        client.login(client.username, client._password)
        client.user_follow(client.user_id_from_username(target))

# ————— Yardımcı decorator —————
def login_required(f):
    def wrapper(*args, **kwargs):
        if not session.get("user"):
            return redirect("/")
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper

# ————— Auth, kullanıcı yönetimi, iptal, panel vs. —————
# ... (diğer route’larınız aynen kalsın) ...

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
