
import json
from instabot import login_bot, follow_user

BOT_USERNAME = "botkullaniciadi"
BOT_PASSWORD = "botsifre123"

def process_orders():
    try:
        with open("orders.json", "r") as f:
            orders = json.load(f)
    except:
        orders = []

    cl = login_bot(BOT_USERNAME, BOT_PASSWORD)

    for username in orders:
        try:
            follow_user(cl, username)
            print(f"✔ {username} başarıyla takip edildi.")
        except Exception as e:
            print(f"✘ {username} takip edilemedi: {e}")

    with open("orders.json", "w") as f:
        json.dump([], f)

if __name__ == "__main__":
    process_orders()
