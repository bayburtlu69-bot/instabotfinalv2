
from flask import Flask, request, redirect, render_template_string
import json

app = Flask(__name__)

PASSWORD = "admin"

HTML_FORM = """
<!DOCTYPE html>
<html>
<head><title>Insta Bot Panel</title></head>
<body>
  <h2>Şifreyi Girin</h2>
  <form method="post">
    <input type="password" name="password">
    <input type="submit" value="Giriş">
  </form>
</body>
</html>
"""

HTML_ORDER = """
<!DOCTYPE html>
<html>
<head><title>Sipariş Paneli</title></head>
<body>
  <h2>Sipariş Oluştur</h2>
  <form method="post">
    <input type="text" name="username" placeholder="Takip edilecek hesap">
    <input type="submit" value="Sipariş Ver">
  </form>
</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST" and request.form.get("password") == PASSWORD:
        return redirect("/panel")
    return HTML_FORM

@app.route("/panel", methods=["GET", "POST"])
def panel():
    if request.method == "POST":
        username = request.form.get("username")
        if username:
            try:
                with open("orders.json", "r") as f:
                    orders = json.load(f)
            except:
                orders = []
            orders.append(username)
            with open("orders.json", "w") as f:
                json.dump(orders, f)
        return redirect("/panel")
    return HTML_ORDER

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
