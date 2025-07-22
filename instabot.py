def load_bots(path="bots.txt"):
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip().split(":",1) for line in f if ":" in line]

if __name__ == "__main__":
    from instagrapi import Client
    import json

    # 1) Botları yükle ve login ol
    bots = load_bots()
    clients = []
    for u,p in bots:
        cl = Client(); cl.login(u,p)
        clients.append(cl)

    # 2) Siparişleri oku
    with open("orders.json","r",encoding="utf-8") as f:
        orders = json.load(f)

    # 3) Sırayla takip et
    for i, target in enumerate(orders):
        bot = clients[i % len(clients)]
        bot.user_follow(bot.user_id_from_username(target))
        print(f"{bot.username} → {target}")
