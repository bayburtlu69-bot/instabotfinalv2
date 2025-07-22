import os, json, time
from instagrapi import Client

# 1) bots.txt'tan bot bilgilerini oku
def load_bots(path="bots.txt"):
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip().split(":", 1) for line in f if ":" in line]

# 2) Client nesnesi oluşturup, ayarları yükle veya login ol ve kaydet
def get_clients():
    clients = []
    for username, password in load_bots():
        cl = Client()
        # istersen timeout ekle (örn. 10sn)
        cl.private.timeout = 10
        settings_file = f"settings_{username}.json"
        if os.path.exists(settings_file):
            cl.load_settings(settings_file)
            print(f"{username}: ayarlar yüklendi (cache’den)")
        else:
            try:
                cl.login(username, password)
                cl.dump_settings(settings_file)
                print(f"{username}: ilk giriş ve cache kaydedildi")
            except Exception as e:
                print(f"{username}: giriş hatası → {e}")
                continue
        clients.append(cl)
    return clients

# 3) orders.json'u oku, botlarla sırayla takip et
def process_orders():
    try:
        orders = json.load(open("orders.json", encoding="utf-8"))
    except Exception:
        print("orders.json okunamadı veya boş.")
        return

    if not orders:
        print("Sipariş (orders.json) listesi boş.")
        return

    clients = get_clients()
    if not clients:
        print("Hiçbot yok veya hepsi login olurken hata verdi.")
        return

    for i, target in enumerate(orders):
        bot = clients[i % len(clients)]
        try:
            print(f"{bot.username} → {target} takibe başlıyor…")
            bot.user_follow(bot.user_id_from_username(target))
            print(f"✅ {bot.username} → {target} tamamlandı")
        except Exception as e:
            print(f"❌ {bot.username} işlem hatası: {e}")
        time.sleep(5)  # isteğe bağlı: botlar arası bekleme

if __name__ == "__main__":
    print("🔸 instabot çalıştırıldı")
    process_orders()
    print("🔸 Tüm işlemler bitti.")
