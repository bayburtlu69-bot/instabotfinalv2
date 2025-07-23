
# Flask, threading ve takip sistemi dahil
import os
import json
import threading
from flask import Flask, session, request, redirect, render_template_string
from instagrapi import Client
from instagrapi.exceptions import LoginRequired

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "çok-gizli-bir-anahtar")

ORDERS_FILE = "orders.json"
BOTS_FILE = "bots.txt"

HTML_PANEL = """
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Takipçi Paneli</title></head>
<body>
  <h2>Yeni Sipariş</h2>
  <form method="post">
    <input name="username" placeholder="Takip edilecek kullanıcı" required>
    <button type="submit">Başlat</button>
  </form>
  <hr>
  <h3>Siparişler</h3>
  <ul>
    {% for o in orders %}
      <li><b>{{ o.username }}</b> – {{ o.status }} – {{ o.error }}</li>
    {% endfor %}
  </ul>
</body></html>
"""

def load_bots(path=BOTS_FILE):
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip().split(":", 1) for line in f if ":" in line]

def load_clients():
    clients = []
    for u, p in load_bots():
        sf = f"settings_{u}.json"
        cl = Client()
        cl.private.timeout = 10
        if os.path.exists(sf):
            cl.load_settings(sf)
            cl._password = p
            clients.append(cl)
            print(f"[+] {u} yüklendi")
    return clients

BOT_CLIENTS = load_clients()

def follow_user(client, target):
    try:
        uid = client.user_id_from_username(target)
        client.user_follow(uid)
    except LoginRequired:
        client.login(client.username, client._password)
        client.user_follow(client.user_id_from_username(target))

@app.route("/", methods=["GET", "POST"])
def panel():
    if request.method == "POST":
        target = request.form.get("username", "").strip()
        if target:
            status, error = "complete", ""
            threads = []

            def task(cl):
                nonlocal status, error
                try:
                    follow_user(cl, target)
                    print(f"[✓] {cl.username} → {target}")
                except Exception as e:
                    print(f"[!] Hata ({cl.username}):", e)
                    status, error = "error", str(e)

            for cl in BOT_CLIENTS:
                t = threading.Thread(target=task, args=(cl,))
                threads.append(t)
                t.start()

            for t in threads:
                t.join()

            try:
                raw = json.load(open(ORDERS_FILE, encoding="utf-8"))
            except:
                raw = []
            raw.append({"username": target, "status": status, "error": error})
            with open(ORDERS_FILE, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
        return redirect("/")

    try:
        raw = json.load(open(ORDERS_FILE, encoding="utf-8"))
    except:
        raw = []
    return render_template_string(HTML_PANEL, orders=raw)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
