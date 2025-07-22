import os
import json
import time
from flask import Flask, session, request, redirect, render_template_string
from instagrapi import Client
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)
app.secret_key = "çok-gizli-bir-anahtar"  # Bunu kendin güvenli bir değerle değiştir
PASSWORD = "admin"

# --- HTML Şablonları ---
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

# --- İnstabot yardımcı fonksiyonları ---
def load_bots(path="bots.txt"):
    """Bots.txt'ten 'username:password' çiftlerini oku."""
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip().split(":", 1) for line in f if ":" in line]

def get_clients():
    """Her bot için Client oluştur, settings cache yükle veya login ol."""
    clients = []
    for username, password in load_bots():
        cl = Client()
        cl.private.timeout = 10
        settings_file = f"settings_{username}.json"
        if os.path.exists(settings_file):
            cl.load_settings(settings_file)
        else:
            cl.login(username, password)
            cl.dump_settings(settings_file)
        clients.append(cl)
    return clients

def follow_user(client, target_username):
    """Belirtilen client ile hedef kullanıcıyı takip et."""
    uid = client.user_id_from_username(target_username)
    client.user_follow(uid)

# --- Botları ve executor'ı başlat ---
BOT_CLIENTS = get_clients()
print("Yüklü bot hesapları:", [c.username for c in BOT_CLIENTS])
FOLLOW_EXECUTOR = ThreadPoolExecutor(max_workers=len(BOT_CLIENTS))

# --- Flask rotaları ---
@app.route("/", methods=["GET", "POST"])
def index():
    if session.get("logged_in"):
        return redirect("/panel")
    if request.method == "POST" and request.form.get("password") == PASSWORD:
        session["logged_in"] = True
        return redirect("/panel")
    return render_template_string(HTML_FORM)

@app.route("/panel", methods=["GET", "POST"])
def panel():
    if not session.get("logged_in"):
        return redirect("/")

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        if username:
            # 1) orders.json'a kaydet
            try:
                with open("orders.json", "r", encoding="utf-8") as f:
                    orders = json.load(f)
            except:
                orders = []
            orders.append(username)
            with open("orders.json", "w", encoding="utf-8") as f:
                json.dump(orders, f)

            # 2) Paralel takip denemesi
            def task(client):
                print(f"[PARALLEL] Deniyor → {client.username}")
                follow_user(client, username)
                print(f"[PARALLEL] Başarılı → {client.username}")

            futures = [FOLLOW_EXECUTOR.submit(task, client) for client in BOT_CLIENTS]
            for future in futures:
                try:
                    future.result(timeout=30)
                except Exception as e:
                    print(f">>> Parallel task hatası: {e}")

            # 3) Sıralı retry denemesi
            for client in BOT_CLIENTS:
                print(f"[RETRY] Sıralı deneme → {client.username}")
                try:
                    follow_user(client, username)
                    print(f"[RETRY] Başarılı → {client.username}")
                except Exception as e:
                    print(f"[RETRY] Hala hata → {client.username}: {e}")

        return redirect("/panel")

    # GET: Geçmiş siparişleri yükle ve şablona aktar
    try:
        with open("orders.json", "r", encoding="utf-8") as f:
            orders = json.load(f)
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
