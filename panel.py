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
app.secret_key = os.getenv("SECRET_KEY", "çok-gizli-bir-anahtar")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///app.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# ————— User modeli —————
class User(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    role          = db.Column(db.String(16), nullable=False)  # "admin" veya "viewer"

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

# ————— DB & seed admin —————
with app.app_context():
    db.create_all()
    if not User.query.filter_by(username="admin").first():
        db.session.add(User(
            username="admin",
            password_hash=generate_password_hash("admin"),
            role="admin"
        ))
        db.session.commit()

# ————— HTML Şablonları (aynı kalacak) —————
HTML_LOGIN = """ … """
HTML_USERS = """ … """
HTML_PANEL = """ … """

# ————— Sipariş kaydı için JSON yolu —————
ORDERS_FILE = "orders.json"

# ————— Bot hazırlığı & cache yükleme —————
def load_bots(path="bots.txt"):
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip().split(":",1) for line in f if ":" in line]

BOT_CLIENTS = []
for u, p in load_bots():
    cl = Client()
    cl.private.timeout = 10
    sf = f"settings_{u}.json"
    if os.path.exists(sf):
        # Localde önceden dump_settings ile oluşturduğun cache dosyasını yükle
        cl.load_settings(sf)
        cl._password = p        # gerekirse retry için parola tut
        BOT_CLIENTS.append(cl)
        print(f"✅ {u}: cache'dan yüklendi ({sf})")
    else:
        # Eğer cache yoksa, interaktif login mümkün değil; atla
        print(f"⚠️ {u}: '{sf}' bulunamadı; lütfen önce localde oturum açıp dump_settings() ile oluşturun")

# **Kaç bot yüklendiğini konsola bas**
print("📦 Yüklü bot sayısı:", len(BOT_CLIENTS), "→", [c.username for c in BOT_CLIENTS])

def follow_user(client, target):
    try:
        uid = client.user_id_from_username(target)
        client.user_follow(uid)
    except LoginRequired:
        # cache eskidiyse retry
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

# ————— Auth & diğer rotalar (aynı kalacak) —————
@app.route("/", methods=["GET","POST"])
def login():
    # … login kodu …
    return render_template_string(HTML_LOGIN)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/users", methods=["GET","POST"])
@login_required
def manage_users():
    # … kullanıcı yönetimi kodu …
    return render_template_string(HTML_USERS, users=users, current_user=session.get("user"))

@app.route("/users/delete/<int:user_id>")
@login_required
def delete_user(user_id):
    # … delete kodu …
    return redirect("/users")

@app.route("/cancel/<int:order_idx>", methods=["POST"])
@login_required
def cancel_order(order_idx):
    # … iptal kodu …
    return redirect("/panel")

@app.route("/panel", methods=["GET","POST"])
@login_required
def panel():
    role = session.get("role")
    if request.method == "POST":
        if role != "admin":
            abort(403)
        target = request.form.get("username", "").strip()
        if target:
            try:
                raw = json.load(open(ORDERS_FILE, encoding="utf-8"))
            except:
                raw = []
            status, error = "complete", ""
            # **Her bot için debug log**
            for idx, cl in enumerate(BOT_CLIENTS, start=1):
                print(f"[{idx}/{len(BOT_CLIENTS)}] Deneme → {cl.username}")
                try:
                    follow_user(cl, target)
                    print(f"[{idx}/{len(BOT_CLIENTS)}] ✅ {cl.username} takibe başladı")
                except Exception as e:
                    print(f"[{idx}/{len(BOT_CLIENTS)}] ⚠️ {cl.username} ile hata: {e}")
                    status, error = "error", str(e)
                    break
            raw.append({"username": target, "status": status, "error": error})
            with open(ORDERS_FILE, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
        return redirect("/panel")

    # … GET işlemleri ve orders objesi hazırlanması …
    return render_template_string(
        HTML_PANEL,
        orders=orders,
        role=role,
        current_user=session.get("user")
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))
