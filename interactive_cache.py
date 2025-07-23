# interactive_cache.py
from instagrapi import Client
import os

def create_cache(username, password):
    cl = Client()
    try:
        cl.login(username, password)
    except Exception as e:
        # challenge istendiği anda yakalıyoruz
        if hasattr(cl, 'last_json') and 'challenge' in cl.last_json.get('message', '').lower():
            # basit çözüm: e‑posta tercih et
            challenge_url = cl.last_json['challenge']['api_path']
            cl.challenge_resolve_simple(challenge_url)
            code = input("E‑postana gelen 6 haneli kodu gir: ")
            cl.challenge_code(code)
        else:
            raise
    # oturum tamamlandıysa ayarları kaydet
    sf = f"settings_{username}.json"
    cl.dump_settings(sf)
    print(f"[OK] Cache oluşturuldu: {sf}")

if __name__ == "__main__":
    # testaktas4 için çalıştır:
    create_cache("testaktas4", "6906149Miko")
