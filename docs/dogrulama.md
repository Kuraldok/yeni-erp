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


## Operasyon geliştirmeleri — 30 Ekim 2026

- Geliştirme ortamında 196 test geçti; Django kontrolü ve migration tutarlılığı başarılı.
- Ek kapsam: döküm alternatifi, pasif kart, sipariş birim kopyası, bileşen kart yönetimi, net ihtiyaç, rezervasyon, kısmi partiler, fire ve gerçek tüketim, iş iptali, üretim ters fişi, kısmi teslimat ve mal kabul, otomatik sipariş kapatma, transfer, sayım ve eski sayım taslağı, değer koruma, bilinmeyen maliyet, rapor/CSV, yetkiler ve hatada tam geri alma.
- Eski `0005` şemasından tamamlanmış üretim, stok ve sipariş içeren örneğin geçişi doğrulandı; özgün değerler, tarihler ve kullanıcılar korundu, fiyat ve tarihçe üretilmedi.
- Chromium'da ayrı sentetik veritabanıyla sipariş → bağlı üretim → iki parti ve fire → kısmi teslimatlar → teslimat ters fişi → rezervasyon/bırakma → üretim ters fişi → iş iptali → transfer → sayım → rapor/CSV akışı tamamlandı.
- Masaüstü ve mobil ekran boyutlarında taşma ve JavaScript hatası görülmedi. Fiziksel cihaz ve PostgreSQL yük/eşzamanlılık testi bu çalışmada yapılmadı.
- Güncel operasyon geliştirmeleri yerelde doğrulandı; GitHub'a aktarım ve sunucuya dağıtım bu adımın kapsamında değildir.


## Sehpalar rota kuralı — 30 Ekim 2026

- Ana projede 204 test geçti; Django kontrolü ve migration tutarlılığı başarılı. Yeni migration gerekmedi.
- Masa tablası ahşap, diğer adı belli yarı mamuller döküm olarak doğrulandı. Mamul etiketli bileşenler, ad/harf/boşluk farkları, kapsam dışı kartlar, eksik ad, ortak kart çelişkisi ve mevcut özel rota adımlarının korunması test edildi.
- Geliştirme veritabanında henüz ürün/reçete kaydı bulunmadığından rota komutu 0 yeni kayıt oluşturdu. Yeni aktarımlarda kural otomatik uygulanır. Geliştirme veritabanının dosya özeti değişmedi; önceki kayıtlar ve kaynaklar korundu.

## Ürün kartlarında sayfa içi sekmeler — 2 Kasım 2026

- Geçici veritabanıyla 206 test geçti; Django kontrolü, migration tutarlılığı ve JavaScript sözdizimi kontrolü başarılı. Şema değişikliği gerekmedi.
- Ürün ayrıntılarında kaynak satırı sütununun gizlendiği, kaynak kayıtlarının korunduğu ve sayfa içi ayrıntı isteklerinde mevcut ürün/reçete izinlerinin uygulandığı doğrulandı.
- Chromium'da ayrı sentetik veritabanıyla kod/ad/Ayrıntılar bağlantıları, aynı ürünün tekrar seçilmesi, sekme kapatma, klavye kullanımı, ürün bilgileri ve bileşen geçişleri, reçete sürümü seçimi, başarısız isteği yeniden deneme doğrulandı. Listeye dönüldüğünde arama, grup filtresi, sayfa, dikey ve tablo içi yatay kaydırma konumları korundu.
- Masaüstü ve mobil ekran boyutlarında sayfa taşması veya JavaScript hatası görülmedi. JavaScript kapalıyken ayrıntıların aynı tarayıcı sekmesinde açıldığı kontrol edildi.
- Geliştirme veritabanının dosya özeti değişmedi. Çalışma geliştirme ortamında tamamlandı; bu adımda GitHub'a aktarım veya canlı dağıtım yapılmadı.

## Stok kartlarında ana ürün görünümü — 2 Kasım 2026

- Geçici veritabanıyla 213 test geçti; Django kontrolü ve migration tutarlılığı başarılı. Şema değişikliği gerekmedi.
- Stok kartları listesi ürün kartlarıyla aynı ana ürünleri, grup/kod sıralamasını, filtreleri ve sayfalamayı kullanıyor. Yarı mamul, hammadde, parça ve yalnız bileşen olan Mamul kartları ana listede gösterilmiyor; kaynak kayıtları korunuyor.
- Ortak ayrıntı görünümünde ürün bilgileri, reçete sürümleri ve bileşenler doğrulandı. Stok ekranındaki ek depo bakiyeleri bölümünde fiziksel/ayrılmış/serbest miktar ve bilinen maliyetler korundu; ürün, reçete, stok ve rezervasyon izinleri ayrıca sınandı.
- Chromium'da ayrı sentetik veritabanıyla liste → ürün sekmesi → depo bakiyeleri → bileşen kartı → listeye dönüş akışı tamamlandı. Kod/ad/Ayrıntılar bağlantıları, sekme kapatma, tekrar seçim, klavye kullanımı, reçete değiştirme, hata sonrası yeniden deneme ve JavaScript kapalı kullanım kontrol edildi.
- Listeye dönüşte arama, grup, sayfa ve kaydırma konumları korundu. Masaüstü/mobil ekran boyutlarında sayfa taşması veya JavaScript hatası görülmedi. Bileşen düzenleme adresleri ve stok/üretim seçimleri korunuyor.
- Geliştirme veritabanının dosya özeti değişmedi; test kayıtları yalnızca geçici veritabanında oluşturuldu. Çalışma geliştirme ortamında tamamlandı.

## Hammadde temini ve manuel stok seçimi — 2 Kasım 2026

- Geçici veritabanıyla 226 test geçti; Django kontrolü ve migration tutarlılığı başarılı. Şema değişikliği gerekmedi.
- Bütün gruplarda hammadde dış temin rotası, Excel aktarımı, kaynak boşluklarının korunması, tekrar çalıştırma, özel tedarik adımları ve eski rota sürümlerinin korunması doğrulandı. Açık üretim işiyle çelişen kartta değişiklik yapılmadığı ve uyarı verildiği sınandı.
- Manuel stok formunda yarı mamul seçiminin, birim verisinin ve doğrudan gönderilen ürün kimliklerinin kısıtlandığı doğrulandı. Kaynakta Mamul yazan yarı mamul bileşenleri de özgün etiket korunarak dışarıda bırakılıyor. Hammadde, ana ürün ve parça için manuel giriş ve kaydeden kullanıcı bilgisi çalışıyor.
- Hammadde → yarı mamul üretimi → mamul üretiminde yarı mamul tüketimi akışı doğrulandı; otomatik giriş/çıkışlar doğru miktarlarda oluştu. Eski stok hareketleri ve kaynak satırları korundu.
- Chromium'da ayrı sentetik veritabanıyla ürün seçenekleri, hammadde girişi, hatalı yarı mamul gönderiminde stok/audit kaydı oluşmaması, dış temin rotası ve üretim açma bağlantısının bulunmaması kontrol edildi. Masaüstü/mobil ekran boyutlarında sayfa taşması veya JavaScript hatası görülmedi.
- Geliştirme kataloğu boş olduğundan rota komutu 0 yeni kayıt oluşturdu. Geliştirme veritabanının dosya özeti değişmedi; kural yeni Excel aktarımlarında otomatik uygulanacak.
