import os, json
from flask import Flask, session, request, redirect, render_template_string
from instagrapi import Client
from instagrapi.exceptions import LoginRequired

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "çok-gizli-bir-anahtar")
PASSWORD = "admin"

HTML_FORM = """… (değişmedi) …"""
HTML_ORDER = """… (değişmedi) …"""

# Bot yükleme & takip fonksiyonları aynen önceki…

# … load_bots(), BOT_CLIENTS tanımı, follow_user vb. …

@app.route("/", methods=["GET","POST"])
def index():
    # … login kodu …
    return render_template_string(HTML_FORM)

@app.route("/panel", methods=["GET","POST"])
def panel():
    if not session.get("logged_in"):
        return redirect("/")

    if request.method == "POST":
        target = request.form.get("username","").strip()
        if target:
            # orders.json'dan oku (hem eski string hem dict kabul et)
            try:
                orders_raw = json.load(open("orders.json", encoding="utf-8"))
            except:
                orders_raw = []

            # takip ve durum belirle
            status = "complete"
            error_msg = ""
            for cl in BOT_CLIENTS:
                try:
                    follow_user(cl, target)
                except Exception as e:
                    status = "error"
                    error_msg = str(e)
                    break

            # yeni girdiyi dict olarak ekle
            orders_raw.append({
                "username": target,
                "status": status,
                "error": error_msg
            })

            # kaydet
            with open("orders.json","w", encoding="utf-8") as f:
                json.dump(orders_raw, f, ensure_ascii=False, indent=2)

        return redirect("/panel")

    # GET: tüm girdileri normalize ederek objeye çevir
    try:
        orders_raw = json.load(open("orders.json", encoding="utf-8"))
    except:
        orders_raw = []

    class O: pass
    orders = []
    for o in orders_raw:
        obj = O()
        if isinstance(o, str):
            # eski format: sadece username
            obj.username = o
            obj.status   = "complete"
            obj.error    = ""
        else:
            # yeni format: dict
            obj.username = o.get("username")
            obj.status   = o.get("status", "")
            obj.error    = o.get("error", "")
        orders.append(obj)

    return render_template_string(HTML_ORDER, orders=orders)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
