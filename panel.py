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

# ————— DB dosyasını ve seed admin’i oluştur —————
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
HTML_LOGIN = """
<!DOCTYPE html>
<html><head><title>Giriş</title></head>
<body>
  <h2>Giriş Yap</h2>
  <form method="post">
    <input name="username" placeholder="Kullanıcı Adı"><br><br>
    <input name="password" type="password" placeholder="Şifre"><br><br>
    <input type="submit" value="Giriş">
  </form>
</body>
</html>
"""

HTML_USERS = """
<!DOCTYPE html>
<html><head><title>Kullanıcı Yönetimi</title></head>
<body>
  <h2>Kullanıcılar</h2>
  <form method="post">
    <input name="u" placeholder="Yeni kullanıcı adı">
    <input name="pw" placeholder="Şifre">
    <select name="role">
      <option value="admin">admin</option>
      <option value="viewer">viewer</option>
    </select>
    <button type="submit">Ekle</button>
  </form>
  <table border=1 cellpadding=4>
    <tr><th>#</th><th>Kullanıcı</th><th>Rol</th><th>İşlem</th></tr>
    {% for usr in users %}
    <tr>
      <td>{{ loop.index }}</td>
      <td>{{ usr.username }}</td>
      <td>{{ usr.role }}</td>
      <td>
        {% if usr.username != current_user %}
          <a href="{{ url_for('delete_user', user_id=usr.id) }}">Sil</a>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </table>
  <p><a href="{{ url_for('panel') }}">Panel’e Dön</a></p>
</body>
</html>
"""

HTML_PANEL = """
<!DOCTYPE html>
<html><head><title>Sipariş Paneli</title></head>
<body>
  <p>Hoşgeldin <b>{{ current_user }}</b> ({{ role }})</p>
  {% if role=='admin' %}
    <p><a href="{{ url_for('manage_users') }}">Kullanıcı Yönetimi</a></p>
  {% endif %}
  <h2>Yeni Sipariş</h2>
  {% if role=='admin' %}
    <form method="post">
      <input name="username" placeholder="Takip edilecek hesap">
      <button type="submit">Sipariş Ver</button>
    </form>
  {% else %}
    <p>Bu işlemi yapmaya yetkiniz yok.</p>
  {% endif %}
  <hr>
  <h3>Geçmiş Siparişler</h3>
  {% if orders %}
    <table border=1 cellpadding=4>
      <tr>
        <th>#</th><th>Kullanıcı</th><th>Durum</th><th>Hata</th><th>İşlem</th>
      </tr>
      {% for o in orders %}
      <tr>
        <td>{{ loop.index }}</td>
        <td>{{ o.username }}</td>
        <td>{{ o.status }}</td>
        <td>{{ o.error }}</td>
        <td>
          {% if o.status not in ['complete','cancelled'] and role=='admin' %}
            <form method="post" action="{{ url_for('cancel_order', order_idx=loop.index0) }}" style="display:inline">
              <button type="submit">İptal Et</button>
            </form>
          {% else %}
            —
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </table>
  {% else %}
    <p>Henüz sipariş yok.</p>
  {% endif %}
  <p><a href="{{ url_for('logout') }}">Çıkış Yap</a></p>
</body>
</html>
"""

# ————— Sipariş kaydı için JSON yolu —————
ORDERS_FILE = "orders.json"

# ————— Bot hazırlığı —————
def load_bots(path="bots.txt"):
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip().split(":",1) for line in f if ":" in line]

BOT_CLIENTS = []
for u,p in load_bots():
    cl = Client(); cl.private.timeout = 10
    try:
        cl.login(u,p)
        cl.dump_settings(f"settings_{u}.json")
        cl._password = p
        BOT_CLIENTS.append(cl)
        print(f"{u}: login OK")
    except Exception as e:
        print(f"{u}: login başarısız → {e}")

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

# ————— Auth Routes —————
@app.route("/", methods=["GET","POST"])
def login():
    if request.method=="POST":
        u = request.form.get("username","")
        p = request.form.get("password","")
        usr = User.query.filter_by(username=u).first()
        if usr and usr.check_password(p):
            session["user"] = usr.username
            session["role"] = usr.role
            return redirect("/panel")
    return render_template_string(HTML_LOGIN)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# ————— Kullanıcı Yönetimi —————
@app.route("/users", methods=["GET","POST"])
@login_required
def manage_users():
    if session.get("role")!="admin":
        abort(403)
    if request.method=="POST":
        u = request.form.get("u","").strip()
        p = request.form.get("pw","")
        r = request.form.get("role","viewer")
        if u and p and not User.query.filter_by(username=u).first():
            new = User(
                username=u,
                password_hash=generate_password_hash(p),
                role=r
            )
            db.session.add(new)
            db.session.commit()
    users = User.query.order_by(User.username).all()
    return render_template_string(
        HTML_USERS,
        users=users,
        current_user=session.get("user")
    )

@app.route("/users/delete/<int:user_id>")
@login_required
def delete_user(user_id):
    if session.get("role")!="admin":
        abort(403)
    usr = User.query.get_or_404(user_id)
    if usr.username!= session.get("user"):
        db.session.delete(usr)
        db.session.commit()
    return redirect("/users")

# ————— Sipariş iptal rotası —————
@app.route("/cancel/<int:order_idx>", methods=["POST"])
@login_required
def cancel_order(order_idx):
    if session.get("role")!="admin":
        abort(403)
    try:
        orders = json.load(open(ORDERS_FILE, encoding="utf-8"))
    except:
        orders = []
    if 0 <= order_idx < len(orders):
        orders[order_idx]["status"] = "cancelled"
        orders[order_idx]["error"] = ""
        with open(ORDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(orders, f, ensure_ascii=False, indent=2)
    return redirect("/panel")

# ————— Sipariş Paneli —————
@app.route("/panel", methods=["GET","POST"])
@login_required
def panel():
    role = session.get("role")
    if request.method=="POST":
        if role!="admin":
            abort(403)
        target = request.form.get("username","").strip()
        if target:
            try:
                raw = json.load(open(ORDERS_FILE, encoding="utf-8"))
            except:
                raw = []
            status, error = "complete", ""
            for cl in BOT_CLIENTS:
                try:
                    follow_user(cl, target)
                except Exception as e:
                    status, error = "error", str(e)
                    break
            raw.append({"username":target,"status":status,"error":error})
            with open(ORDERS_FILE,"w",encoding="utf-8") as f:
                json.dump(raw,f,ensure_ascii=False,indent=2)
        return redirect("/panel")

    try:
        raw = json.load(open(ORDERS_FILE, encoding="utf-8"))
    except:
        raw = []
    class O: pass
    orders=[]
    for o in raw:
        obj=O()
        obj.username=o.get("username")
        obj.status  =o.get("status")
        obj.error   =o.get("error")
        orders.append(obj)

    return render_template_string(
        HTML_PANEL,
        orders=orders,
        role=role,
        current_user=session.get("user")
    )

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))
