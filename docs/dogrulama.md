# İlk sürüm doğrulaması — 28 Ekim 2026

- Python 3.12, Django 5.2.18, SQLite.
- `python manage.py test --verbosity 2`: **20 test geçti**, başarısız veya atlanan test yok. Geçici test veri tabanı kullanıldı.
- `python manage.py check`: sorun yok.
- `python manage.py makemigrations --check --dry-run`: eksik şema değişikliği yok.
- `scripts/setup.sh`: iki kez çalıştırıldı; ikinci çalıştırmada yeni migration yok, mevcut roller korunuyor.
- `collectstatic`: uygulama ve Django yönetim dosyaları hazır.
- Yerel geliştirme sunucusu: giriş sayfası 200, CSS 200, anonim ana panel 302. Giriş formu ve CSRF alanı yanıt içeriğinde doğrulandı.
- Test edilen akışlar: giriş/çıkış, rol kısıtları, yönetim izinleri, CSRF, ürün ekleme/arama, tüm modül formları, stok giriş/çıkışı ve depo bazında bakiye, yetersiz stok, stok geçmişinin korunması, sipariş ve kalem ekleme/onaylama, ondalık toplam, tarih ve cari uyumu, onaylı kalem kilidi, rota/adım oluşturma/sıralama, sayfalama.
- PostgreSQL, gerçek mobil tarayıcı, yük/eşzamanlılık ve canlı dağıtım doğrulanmadı. GitHub Actions yapılandırıldı ancak uzakta çalıştırılmadı.
- İlk yönetici hesabı otomatik oluşturulmadı; kullanıcı `createsuperuser` ile kendi hesabını oluşturmalı.
- Mevcut iş verileri silinmedi. GitHub'a push veya yayın yapılmadı.
- Bulut taslağında `install_script` ve `start_skill` kaydedildi. Taslak kaydı yayımlama değildir; gelecekte etkinleştirmek için ortam ayarlarında inceleyip kaydetmek ve ortamı yayımlamak gerekir. Kullanıcının isteği gereği yayımlama yapılmadı.
