# BAYBAYİM — Render.com + Neon.com Hazır Paket

Bu paket **Render (Web Service)** üzerinde Python/Flask uygulamanı (panel.py içindeki `app`) çalıştırmak ve **Neon PostgreSQL** ile bağlamak için düzenlendi.

## İçerik
- `render.yaml` → Render Blueprint dosyası (otomatik servis oluşturma)
- `.env.example` → Neon `DATABASE_URL` örneği
- (Opsiyonel) `gunicorn.conf.py` → İleri ayarlar (gerekmez)
- Uygulama dosyaların → olduğu gibi

---

## 1) GitHub'a yükle
Render, Git depo ile çalışır. En kolay yol:
1. GitHub'da yeni bir repo aç: **baybayim**
2. Bu paket içindeki TÜM dosyaları GitHub'a yükle (web arayüzünden sürükle-bırak da olur).

> Alternatif: Render "Blueprint" ile de deploy edebilirsin (New → Blueprint → Git repo'nu seç).

## 2) Render'da servis oluştur
1. Render → **New → Web Service** (ya da **Blueprint**).
2. Repo'nu bağla.
3. Ortam: **Python**
4. **Build Command**: `pip install -r requirements.txt`
5. **Start Command**: `gunicorn panel:app`
6. Region: yakın olanı seç.

> `render.yaml` kullanıyorsan bu ayarlar otomatik gelir.

## 3) Neon PostgreSQL bağla
1. Neon → projen → **Connection String**’i kopyala.
2. Render → Servis → **Environment** sekmesi → **Add Environment Variable**:
   - **Key**: `DATABASE_URL`
   - **Value**: (Neon bağlantı adresin)  
   - Kaydet ve **Deploy** et.

## 4) Test
- Render sana bir URL verir: `https://baybayim.onrender.com` gibi.
- Aç: **çalışıyorsa OK**. Hata varsa **Logs** sekmesinden bak.

## 5) Cloudflare (domain bağlama)
- Cloudflare DNS → `www.baybayim.com` için **CNAME** oluştur.
  - **Name**: `www`
  - **Target**: Render URL'in (`baybayim.onrender.com`)
  - Turuncu bulut **Açık**.
- SSL/TLS → **Full** (origin Render, zaten HTTPS).

## 6) Lokal geliştirme (opsiyonel)
Lokalden denemek istersen:
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# DATABASE_URL gerekiyorsa bir .env dosyasına ekle
gunicorn -b 127.0.0.1:8000 panel:app
# Tarayıcı: http://127.0.0.1:8000
```

## Notlar
- Uygulama giriş noktası: **panel.py** ve içinde `app` (Flask) olmalı.
- `requirements.txt` gerekli paketleri içermeli (Flask, SQLAlchemy, gunicorn, vs.).
- Hata alırsan Render **Logs** sayfasındaki çıktıyı kopyala; birlikte çözeriz.
