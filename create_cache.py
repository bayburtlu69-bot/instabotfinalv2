from instagrapi import Client

# bots.txt’teki her satırı buraya da liste olarak koy:
bots = [
    ("testter1233211", "6906149Miko"),
    ("testaktas1",     "6906149Miko"),
    ("testaktas2",     "6906149Miko"),  
    ("testaktas3",     "6906149Miko"),  # yeni bot
]

for username, password in bots:
    print(f"[+] {username} için oturum açılıyor…")
    cl = Client()
    cl.login(username, password)
    fn = f"settings_{username}.json"
    cl.dump_settings(fn)
    print(f"[+] Oluşturuldu: {fn}")
