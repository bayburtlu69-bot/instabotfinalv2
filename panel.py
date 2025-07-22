import os
import json
import time
from flask import Flask, session, request, redirect, render_template_string
from instagrapi import Client
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)
app.secret_key = "çok-gizli-bir-anahtar"   # Bunu kendin rastgele üretip gizli tut
PASSWORD = "admin"

# Şifre girişi için HTML
HTML_FORM = """ 
<!DOCTYPE html>
<html><head><title>Insta Bot Panel</title></head>
<body>
  <h2>Şifreyi Girin</h2>
  <form method="post">
    <input type="password" name="password" placeholder="Şifre">
    <input type="submit" value="Giriş">
  </form>
</body>
</html>
"""

# Sipariş formu ve geçmiş sipariş tablosu için HTML (Jinja2 şablonu)
HTML_ORDER = """
<!DOCTYPE html>
<html><head><title>Sipariş Paneli</title></head>
<body>
  <h2>Sipariş Oluştur</h2>
  <form method="post">
    <input type="text" name="username" placeholder="Takip edilecek hesap">
    <input type="submit" value="Sipariş Ver">
  </form>
  <p><a href="/logout">Çıkış Yap</a></p>

  <hr>
  <h3>Geçmiş Siparişler</h3>
  {% if orders %}
    <table border="1" cellpadding="4" cellspacing="0">
      <tr><th>#</th><th>Kullanıcı Adı</th></tr>
      {% for o in orders %}
        <tr>
          <td>{{ loop.index }}</td>
          <td>{{ o }}</td>
        </tr>
      {% endfor %}
    </table>
  {% else %}
    <p>Henüz sipariş yok.</p>
  {% endif %}
</body>
</html>
"""

# --- İnstabot fonksiyonları ---
def load_bots(path="bots.txt"):
    """bots.txt'ten 'username:password' satırlarını oku."""
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip().split(":", 1) for line in f if ":" in line]

def get_clients():
    """Her bot için Client oluştur ve cache (settings) yükle veya login ol."""
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
    """Tek bir Client ile target kullanıcıyı takip et."""
    uid = client.user_id_from_username(target)
    client.user_follow(uid)

# --- Bot client'larını ve executor'u hazırla ---
BOT_CLIENTS = get_clients()
FOLLOW_EXECUTOR = ThreadPoolExecutor(max_workers=len(BOT_CLIENTS))

# --- Flask route'ları ---
@app.route("/", methods=["GET","POST"])
def index():
    # Eğer zaten giriş yaptıysa doğrudan /panel'e yönlendir
    if session.get("logged_in"):
        return redirect("/panel")
    # POST ile şifre gönderildiyse kontrol et
    if request.method == "POST" and request.form.get("password") == PASSWORD:
        session["logged_in"] = True
        return redirect("/panel")
    return render_template_string(HTML_FORM)

@app.route("/panel", methods=["GET","POST"])
def panel():
    if not session.get("logged_in"):
        return redirect("/")

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        if username:
            # 1) orders.json'a kaydet (isteğe bağlı arşiv)
            try:
                orders = json.load(open("orders.json", encoding="utf-8"))
            except:
                orders = []
            orders.append(username)
            json.dump(orders, open("orders.json", "w", encoding="utf-8"))

            # 2) Paralel takip isteği
            def task(client):
                try:
                    follow_user(client, username)
                    print(f"{client.username} → {username} takibe başladı")
                except Exception as e:
                    print(f"⚠️ {client.username} ile hata: {e}")

            FOLLOW_EXECUTOR.map(task, BOT_CLIENTS)

        return redirect("/panel")

    # GET isteğinde geçmiş siparişleri yükle ve şablona ver
    try:
        orders = json.load(open("orders.json", encoding="utf-8"))
    except:
        orders = []
    return render_template_string(HTML_ORDER, orders=orders)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
