# Opsiyonel: Gunicorn ayarları
workers = 2
bind = "0.0.0.0:10000"  # Render iç ortam portu otomatik atanır; startCommand'da port belirtmiyoruz.
timeout = 120
