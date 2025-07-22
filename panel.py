import os
import json
from flask import Flask, session, request, redirect, render_template_string
from instagrapi import Client
from instagrapi.exceptions import LoginRequired

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "çok-gizli-bir-anahtar")
PASSWORD = "admin"

# —————————————— HTML ŞABLONLARI ——————————————

HTML_FORM = """
<!DOCTYPE html>
<html><head><title>Giriş</title></head>
<body>
  <h2>Giriş Yap</h2>
  <form method="post">
    <label>Kullanıcı Adı:</label><br>
    <input type="text" name="username"><br><br>
    <label>Şifre:</label><br>
    <input type="password" name="password"><br><br>
    <input type="submit" value="Giriş">
  </form>
</body>
</html>
"""

HTML_ORDER = """
<!DOCTYPE html>
<html><head><title>Sipariş Paneli</title></head>
<body>
  <h2>Yeni Sipariş</h2>
  <form method="post">
    <input type="text" name="username" placeholder="Takip edilecek hesap">
    <input type="submit" value="Sipariş Ver">
  </form>
  <p><a href="/logout">Çıkış Yap</a></p>
  <hr>
  <h3>Geçmiş Siparişler</h3>
  {% if orders %}
    <table border="1" cellpadding="4" cellspacing="0">
      <tr>
        <th>#</th><th>Kullanıcı Adı</th><th>Durum</th><th>Hata</th>
      </tr>
      {% for o in orders %}
      <tr>
        <td>{{ loop.index }}</td>
        <td>{{ o.username }}</td>
        <td>{{ o.status }}</td>
        <td>{{ o.error or "" }}</td>
      </tr>
      {% endfor %}
    </table>
  {% else %}
    <p>Henüz sipariş yok.</p>
  {% endif %}
</body>
</html>
"""

# —————————————— BOT HAZIRLIK ——————————————

def load_bots(path="bots.txt"):
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip().split(":", 1) for line in f if ":" in line]

BOT_CLIENTS = []
for u, p in load_bots():
    cl = Client()
    cl.private.timeout = 10
    try:
        cl.login(u, p)
        cl.dump_settings(f"settings_{u}.json")
        cl._password = p
        BOT_CLIENTS.append(cl)
        print(f"{u}: login ve cache OK")
    except Exception as e:
        print(f"{u}: login başarısız → {e}")

def follow_user(client, target):
    try:
        uid = client.user_id_from_username(target)
        client.user_follow(uid)
    except LoginRequired:
        # eğer session eskidiyse yeniden login ol ve tekrar dene
        client.login(client.username, client._password)
        client.user_follow(client.user_id_from_username(target))

# —————————————— ROUTE’LAR ——————————————

@app.route("/", methods=["GET", "POST"])
def index():
    if session.get("logged_in"):
        return redirect("/panel")
    if request.method == "POST":
        u = request.form.get("username","")
        p = request.form.get("password","")
        if u == "admin" and p == PASSWORD:
            session["logged_in"] = True
            return redirect("/panel")
    return render_template_string(HTML_FORM)

@app.route("/panel", methods=["GET", "POST"])
def panel():
    if not session.get("logged_in"):
        return redirect("/")

    if request.method == "POST":
        target = request.form.get("username","").strip()
        if target:
            # 1) orders.json'dan objeleri oku (liste <- dict objeleri)
            try:
                orders = json.load(open("orders.json", encoding="utf-8"))
            except:
                orders = []

            # 2) takip et ve durum/hata belirle
            status = "complete"
            error_msg = ""
            for cl in BOT_CLIENTS:
                try:
                    follow_user(cl, target)
                except Exception as e:
                    status = "error"
                    error_msg = str(e)
                    break

            # 3) yeni bir dict ekle
            orders.append({
                "username": target,
                "status": status,
                "error": error_msg
            })
            with open("orders.json", "w", encoding="utf-8") as f:
                json.dump(orders, f, ensure_ascii=False, indent=2)

        return redirect("/panel")

    # GET: geçmiş siparişleri oku ve şablona ver
    try:
        orders_raw = json.load(open("orders.json", encoding="utf-8"))
    except:
        orders_raw = []
    # wrap into simple objects for template
    class O: pass
    orders = []
    for o in orders_raw:
        obj = O()
        obj.username = o.get("username")
        obj.status   = o.get("status")
        obj.error    = o.get("error")
        orders.append(obj)

    return render_template_string(HTML_ORDER, orders=orders)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
