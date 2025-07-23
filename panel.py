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

SABIT_FIYAT = 0.5  # Takipçi başı sabit fiyat

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

# --- BAKİYE YÜKLEME BAŞVURU TABLOSU ---
class BalanceRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    amount = db.Column(db.Float, nullable=False)
    description = db.Column(db.String(256))
    status = db.Column(db.String(16), default="pending")  # pending, approved, rejected
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

# --- SMTP GMAIL AYARLARI ---
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_ADDR = "kuzenlertv6996@gmail.com"
SMTP_PASS = "nurkqldoqcaefqwk"  # Gmail uygulama şifresi

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

# --- HTML ŞABLONLARI ---
HTML_LOGIN = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <title>insprov.uk</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
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
      <div class="mb-2">
        <label class="form-label">Kullanıcı Adı:</label>
        <input name="username" class="form-control" placeholder="Kullanıcı Adı">
      </div>
      <div class="mb-3">
        <label class="form-label">Şifre:</label>
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
<html lang="tr">
<head>
  <meta charset="utf-8">
  <title>Kayıt Ol</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
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
        <div class="mb-2">
          <label class="form-label">Kullanıcı Adı:</label>
          <input name="username" class="form-control" placeholder="Kullanıcı Adı" required>
        </div>
        <div class="mb-2">
          <label class="form-label">Şifre:</label>
          <input name="password" type="password" class="form-control" placeholder="Şifre" required>
        </div>
        <div class="mb-3">
          <label class="form-label">E-Posta:</label>
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
<html lang="tr">
<head>
  <meta charset="utf-8">
  <title>Kullanıcı Yönetimi</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-dark text-light">
  <div class="container py-4">
    <div class="card p-4 mx-auto" style="max-width:700px;">
      <h3>Kullanıcı Yönetimi</h3>
      <form method="post" class="row g-2 align-items-end mb-4">
        <div class="col">
          <input name="u" class="form-control" placeholder="Yeni kullanıcı">
        </div>
        <div class="col">
          <input name="pw" type="password" class="form-control" placeholder="Parola">
        </div>
        <div class="col">
          <select name="role" class="form-select">
            <option value="admin">Yönetici</option>
            <option value="viewer">Kullanıcı</option>
          </select>
        </div>
        <div class="col">
          <button class="btn btn-success">Ekle</button>
        </div>
      </form>
      <hr>
      <h5>Mevcut Kullanıcılar</h5>
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
        <div class="col">
          <input name="username" class="form-control" placeholder="Kullanıcı adı">
        </div>
        <div class="col">
          <input name="amount" type="number" step="0.01" class="form-control" placeholder="Tutar">
        </div>
        <div class="col">
          <button class="btn btn-primary">Bakiye Ekle</button>
        </div>
      </form>
      <div class="mt-3">
        <a href="{{ url_for('panel') }}" class="btn btn-secondary btn-sm">Panel’e Dön</a>
      </div>
    </div>
  </div>
</body>
</html>
"""

HTML_PANEL = """
<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <title>Sipariş Paneli</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-dark text-light">
  <div class="container py-4">
    <div class="card p-4 mx-auto" style="max-width:800px;">
      <div class="d-flex justify-content-between align-items-center mb-3">
        <div>
          <b>{{ current_user }}</b> <span class="badge bg-info text-dark">{{ rolu_turkce(role) }}</span>
        </div>
        <div>Bakiye: <b>{{ balance }} TL</b></div>
      </div>
      {% if role=='admin' %}
        <a href="{{ url_for('manage_users') }}" class="btn btn-secondary btn-sm mb-3">Kullanıcı Yönetimi</a>
      {% endif %}
      <h4 class="mb-3">Yeni Sipariş</h4>
      <form method="post" class="row g-2 align-items-end mb-2">
        <div class="col">
          <input name="username" class="form-control" placeholder="Takip edilecek hesap" required>
        </div>
        <div class="col">
          <input name="amount" type="number" min="1" class="form-control" placeholder="Takipçi adedi" required>
        </div>
        <div class="col">
          <button class="btn btn-success w-100">Sipariş Ver</button>
        </div>
      </form>
      <div class="mb-2"><b>Her takipçi adedi için fiyat: 0.50 TL’dir.</b></div>
      {% if error %}
        <div class="alert alert-danger py-2 small mb-2">{{ error }}</div>
      {% endif %}
      {% if msg %}
        <div class="alert alert-success py-2 small mb-2">{{ msg }}</div>
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

HTML_BALANCE = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="utf-8">
    <title>Bakiye Yükle</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-dark text-light">
<div class='container my-4'>
    <div class="card p-4 mx-auto" style="max-width:500px;">
        <h4 class="mb-3">Bakiye Yükle (Havale/EFT Bildir)</h4>
        {% if msg %}
          <div class='alert alert-info'>{{ msg }}</div>
        {% endif %}
        <form method='post'>
            <div class='mb-2'><input name='amount' class='form-control' type='number' step='0.01' placeholder='Yüklenecek tutar (TL)' required></div>
            <div class='mb-3'><input name='desc' class='form-control' placeholder='Açıklama (örn: Havale dekont no)'></div>
            <button class='btn btn-success w-100'>Bildirim Gönder</button>
        </form>
        <div class='mt-3 alert alert-secondary'>
          <b>Banka:</b> QNB Finansbank<br>
          <b>IBAN:</b> TR70 0004 6008 7088 8000 1117 44<br>
          <b>Hesap Sahibi:</b> Mükail Aktaş<br>
          <b>Açıklama:</b> Lütfen kullanıcı adınızı açıklamaya yazın!<br>
          <small>Havale/EFT sonrası bu formu doldurun, admin onaylayınca bakiyeniz yüklenecek.</small>
        </div>
        <a href='/panel' class='btn btn-link mt-2'>Panele Dön</a>
    </div>
</div>
</body>
</html>
"""

HTML_BALANCE_REQS = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="utf-8">
    <title>Bakiye Talepleri</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-dark text-light">
<div class='container my-4'>
    <div class="card p-4 mx-auto" style="max-width:800px;">
        <h4 class="mb-3">Bakiye Yükleme Başvuruları</h4>
        {% if msg %}
          <div class='alert alert-success'>{{ msg }}</div>
        {% endif %}
        <table class="table table-dark table-striped table-bordered align-middle">
          <thead>
            <tr>
              <th>#</th>
              <th>Kullanıcı</th>
              <th>Tutar (TL)</th>
              <th>Açıklama</th>
              <th>Tarih</th>
              <th>Durum</th>
              <th>İşlem</th>
            </tr>
          </thead>
          <tbody>
          {% for r in requests %}
            <tr>
              <td>{{ loop.index }}</td>
              <td>{{ r.user.username }}</td>
              <td>{{ r.amount }}</td>
              <td>{{ r.description }}</td>
              <td>{{ r.created_at.strftime('%d.%m.%Y %H:%M') }}</td>
              <td>
                {% if r.status=='approved' %}
                  <span class='badge bg-success'>Onaylandı</span>
                {% elif r.status=='rejected' %}
                  <span class='badge bg-danger'>Reddedildi</span>
                {% else %}
                  <span class='badge bg-warning text-dark'>Bekliyor</span>
                {% endif %}
              </td>
              <td>
                {% if r.status=='pending' %}
                  <form method='post' style='display:inline-block'>
                    <input type='hidden' name='req_id' value='{{ r.id }}'>
                    <button name='action' value='approve' class='btn btn-success btn-sm'>Onayla</button>
                    <button name='action' value='reject' class='btn btn-danger btn-sm ms-2'>Reddet</button>
                  </form>
                {% else %}
                  <span class="text-muted">–</span>
                {% endif %}
              </td>
            </tr>
          {% endfor %}
          </tbody>
        </table>
        <a href='/panel' class='btn btn-link mt-2'>Panele Dön</a>
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
    # Panelde link gösterimi:
    panel_html = HTML_PANEL.replace(
        "<div class=\"mb-2\"><b>Her takipçi adedi için fiyat: 0.50 TL’dir.</b></div>",
        "<div class=\"mb-2\"><b>Her takipçi adedi için fiyat: 0.50 TL’dir.</b></div>"
        "<a href='/balance' class='btn btn-warning btn-sm my-2'>Bakiye Yükle (Havale/EFT)</a>"
        "{% if role=='admin' %}<a href='/balance/requests' class='btn btn-info btn-sm my-2 ms-2'>Bakiye Talepleri</a>{% endif %}"
    )
    return render_template_string(
        panel_html,
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
def balance():
    user = User.query.get(session.get("user_id"))
    msg = ""
    if request.method == "POST":
        try:
            amount = float(request.form.get("amount", "0"))
        except:
            amount = 0
        desc = request.form.get("desc", "")
        if amount <= 0:
            msg = "Lütfen geçerli bir tutar girin."
        else:
            req = BalanceRequest(user_id=user.id, amount=amount, description=desc, status="pending")
            db.session.add(req)
            db.session.commit()
            msg = "Başvurunuz alındı, admin onaylayınca bakiye hesabınıza geçecek!"
    return render_template_string(HTML_BALANCE, msg=msg)

@app.route("/balance/requests", methods=["GET", "POST"])
@login_required
@admin_required
def balance_requests():
    msg = ""
    if request.method == "POST":
        req_id = int(request.form.get("req_id", 0))
        action = request.form.get("action")
        req = BalanceRequest.query.get(req_id)
        if req and req.status == "pending":
            if action == "approve":
                req.status = "approved"
                req.user.balance += req.amount
                msg = f"{req.user.username} kullanıcısına {req.amount} TL yüklendi!"
            elif action == "reject":
                req.status = "rejected"
                msg = "Başvuru reddedildi."
            db.session.commit()
    requests = BalanceRequest.query.order_by(BalanceRequest.created_at.desc()).all()
    return render_template_string(HTML_BALANCE_REQS, requests=requests, msg=msg)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
