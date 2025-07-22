from flask import Flask, session, request, redirect, render_template_string
import json, os

app = Flask(__name__)
app.secret_key = "çok-gizli-bir-anahtar"  # Bunu rastgele bir değere çevirin

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

@app.route("/", methods=["GET","POST"])
def index():
    if session.get("logged_in"):
        return redirect("/panel")
    if request.method=="POST" and request.form.get("password")==PASSWORD:
        session["logged_in"] = True
        return redirect("/panel")
    return render_template_string(HTML_FORM)

@app.route("/panel", methods=["GET","POST"])
def panel():
    if not session.get("logged_in"):
        return redirect("/")
    if request.method=="POST":
        username = request.form.get("username")
        if username:
            try:
                with open("orders.json","r") as f:
                    orders = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                orders = []
            orders.append(username)
            with open("orders.json","w") as f:
                json.dump(orders, f)
        return redirect("/panel")
    return render_template_string(HTML_ORDER)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

if __name__=="__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
