import os, json, time
from flask import Flask, session, request, redirect, render_template_string
from instagrapi import Client

app = Flask(__name__)
app.secret_key = "çok-gizli-bir-anahtar"  # Bunu kendi gizli anahtarınızla değiştirin

PASSWORD = "admin"

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
</body>
</html>
"""

# --- İnstabot fonksiyonları ---
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
            print(f"{u}: cache'den yüklendi")
        else:
            cl.login(u, p)
            cl.dump_settings(sf)
            print(f"{u}: ilk giriş yapıldı ve cache kaydedildi")
        clients.append(cl)
    return clients

def follow_user(client, target):
    uid = client.user_id_from_username(target)
    client.user_follow(uid)

# Bot client'larını hazırla
BOT_CLIENTS = get_clients()

# --- Flask route'ları ---
@app.route("/", methods=["GET","POST"])
def index():
    if session.get("logged_in"):
        return redirect("/panel")
    if request.method == "POST" and request.form.get("password") == PASSWORD:
        session["logged_in"] = True
        return redirect("/panel")
    return render_template_string(HTML_FORM)

@app.route("/panel", methods=["GET","POST"])
def panel():
    if not session.get("logged_in"):
        return redirect("/")

    if request.method == "POST":
        username = request.form.get("username")
        if username:
            # orders.json'a kaydet
            try:
                orders = json.load(open("orders.json", encoding="utf-8"))
            except:
                orders = []
            orders.append(username)
            json.dump(orders, open("orders.json","w", encoding="utf-8"))
            print(f"DEBUG: orders.json’a yazıldı → {orders}")  # Debug satırı

            # hemen botlarla takip et
            for client in BOT_CLIENTS:
                try:
                    follow_user(client, username)
                    print(f"{client.username} → {username} takibe başladı")
                except Exception as e:
                    print(f"⚠️ {client.username} ile hata: {e}")

        return redirect("/panel")

    return render_template_string(HTML_ORDER)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"Sunucu {port} portunda başlatıldı")
    app.run(host="0.0.0.0", port=port)
