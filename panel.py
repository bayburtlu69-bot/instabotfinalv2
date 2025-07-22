import os
import json
from flask import Flask, session, request, redirect, render_template_string
from instagrapi import Client
from instagrapi.exceptions import LoginRequired     # ← bunu ekliyoruz

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "çok-gizli-bir-anahtar")
PASSWORD = "admin"

HTML_FORM = """  … aynen önceki … """
HTML_ORDER = """ … aynen önceki … """

def load_bots(path="bots.txt"):
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip().split(":", 1) for line in f if ":" in line]

def get_clients():
    clients = []
    for u, p in load_bots():
        cl = Client()
        cl.private.timeout = 10
        settings_file = f"settings_{u}.json"
        try:
            # Her zaman login ol
            cl.login(u, p)
            cl.dump_settings(settings_file)
            print(f"{u}: login başarılı ve cache kaydedildi")
        except Exception as e:
            print(f"{u}: login hatası → {e}")
            continue
        clients.append(cl)
    return clients

def follow_user(client, target):
    try:
        uid = client.user_id_from_username(target)
        client.user_follow(uid)
    except LoginRequired:
        # Eğer session eskidiyse yeniden login ol
        print(f"{client.username}: yeniden login gerektirdi, login deneniyor…")
        # elimizde parola yok, o yüzden yüklediğimiz ayarı kullanarak:
        # settings dosyasında password yok; bu nedenle
        # en basit yol, load_bots’tan parola alınan bir dict tutmak
        # ya da client objesine parola eklemek gerekebilir.
        # Aşağıda, client._password diye bir öznitelik atandığını varsayarak:
        client.login(client.username, client._password)
        client.user_follow(client.user_id_from_username(target))

# --- Botları hazırla ---
# get_clients içinde login olduktan sonra, Client objesine _password ekleyelim:
BOT_CLIENTS = []
for u,p in load_bots():
    cl = Client()
    cl.private.timeout = 10
    try:
        cl.login(u, p)
        cl.dump_settings(f"settings_{u}.json")
        cl._password = p      # parola saklıyoruz, gerekirse retry için
        BOT_CLIENTS.append(cl)
        print(f"{u}: login ve cache OK")
    except Exception as e:
        print(f"{u}: login başarısız → {e}")

@app.route("/", methods=["GET","POST"])
def index():
    # … aynen önceki …
    return render_template_string(HTML_FORM)

@app.route("/panel", methods=["GET","POST"])
def panel():
    if not session.get("logged_in"):
        return redirect("/")

    if request.method=="POST":
        target = request.form.get("username","").strip()
        if target:
            # orders.json’a ekle (aynı önceki kod)
            try:
                orders = json.load(open("orders.json", encoding="utf-8"))
            except:
                orders = []
            orders.append(target)
            json.dump(orders, open("orders.json","w", encoding="utf-8"))

            # sadece tek turda takip et
            for cl in BOT_CLIENTS:
                try:
                    follow_user(cl, target)
                    print(f"{cl.username} → {target} takibe başladı")
                except Exception as e:
                    print(f"⚠️ {cl.username} ile hata: {e}")

        return redirect("/panel")

    # GET: geçmiş siparişleri oku
    try:
        orders = json.load(open("orders.json", encoding="utf-8"))
    except:
        orders = []
    return render_template_string(HTML_ORDER, orders=orders)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
