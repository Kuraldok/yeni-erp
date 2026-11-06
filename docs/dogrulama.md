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

## Yedi inceleme bulgusunun düzeltilmesi — 2 Kasım 2026

- Geçici veritabanında 258 test geçti: önceki 226 teste ek 32 senaryo. Django kontrolü, migration tutarlılığı ve JavaScript sözdizimi kontrolü başarılı.
- Açık çıktı/girdi işlerinde kartın birim/tür/etkinlik/üretim tanımı değişiklikleri ve kontrollü toplu yollar sınandı. İşlem geçmişi korundu; kart açıklaması gibi güvenli alanlar düzenlenebiliyor. Elle/ORM/toplu açılan hammaddeler dış temin rotasını otomatik alıyor; yeni hammadde iç üretim rotası ve üretim çıktısı reddediliyor.
- 1 Kasım üretimi ve 2 Kasım ters fişi ayrı dönemlerde +2 / -2 olarak doğrulandı. Önceki günün stok ve üretim raporu tutarlı kaldı; aynı dönem iptalinde net sıfır, bilinmeyen maliyet ve negatif sayısal CSV kontrol edildi.
- Aktarımda alan eksiği sıfır olsa da rota uyarısı görünür oldu. Kaynak grubu filtresi, yetkili POST ile yeniden değerlendirme, kullanıcı/zaman izi ve özgün kaynak hücrelerinin korunması doğrulandı.
- İlk 50 kart dışındaki kritik stok ana panelde önceliklendirildi. 61 kritik kartın tamamı iki sayfada gösterildi; raporların 50 kayıtla sayfalanması ve CSV'nin bütün filtrelenmiş kayıtları içermesi sınandı.
- Taslak fiş düzenleme, kalemi geçmişte saklayarak çıkarma, iptal nedeni/kullanıcı/zaman, eksik/yinelenmiş/başka belgeye ait kalem kimlikleri, eski sürümden düzenleme/iptal/işleme, yetkiler, CSRF ve sayımı açıkça yenileme kontrolleri geçti. İşlenmiş ve iptal edilmiş fişler korunuyor.
- Stok kartı depo bakiyesi okumaları 2 ve 22 depoda aynı üç sorguyla çalıştı; önceki incelemede 4 depoda 29 sorgu vardı. Üretim raporunda iş sayısı artınca sorgu sayısı büyümedi; sipariş teslimatları toplu hesaplandı.
- Chromium'da ayrı sentetik veritabanıyla kart koruması → elle hammadde → kritik stok → dönem raporu/CSV → rota uyarısı/yeniden değerlendirme → taslak düzenle/kalem çıkar/işle → gerekçeli iptal akışı tamamlandı. Masaüstü/mobil sayfa taşması veya JavaScript hatası görülmedi.
- Geliştirme ortamına `0012` uygulandı ve roller ek izinlerle güncellendi. 35 mevcut tablonun bütün özgün satırları ve eski sütun değerleri bire bir korundu; SQLite bütünlük ve yabancı anahtar denetimleri başarılı. Test hesapları ve işlem örnekleri yalnız geçici veritabanında kaldı. Canlı dağıtım yapılmadı.

## Kamelyalar ve piknik masaları rota kuralı — 4 Kasım 2026

- Geçici veritabanında 270 test geçti; önceki 258 teste 12 senaryo eklendi. Django kontrolü ve migration tutarlılığı başarılı; şema değişikliği gerekmedi.
- Birleşik/ayrı ve tekil/çoğul kategori adları, Türkçe harfler ve fazla boşluklar doğrulandı. Ana ürünler, Yarı Mamul ve Mamul etiketli bileşenler dört standart ahşap adımını aldı; hammaddeler dış teminde, parçalar üretim kuralının dışında kaldı.
- Tekrarlı kurulum, özel rota/adım/sürelerin korunması, kaynak kapsamı, açık ana ürün/yarı mamul işleri, kategoriler arası çelişki, reçetesiz kartlar ve eksik/kendine bağlı bileşenler sınandı. Özgün kaynak hücreleri, boş miktar ve birimler değiştirilmedi; yeni stok hareketi oluşmadı.
- Ahşap işlerinin rota/adımlarını kullanması, ürün ayrıntısında doğrudan ana ürün rotası ve Mamul etiketli yarı mamullerin gösterimi, manuel stok seçimi ve grup filtreli çelişki uyarısı Django test istemcisiyle doğrulandı.
- Geliştirme kataloğunda ürün/reçete kaydı olmadığından `setup_component_routes` 0 yeni kayıt oluşturdu. Geliştirme veritabanının SHA-256 özeti değişmedi. Kural yeni Excel aktarımlarında otomatik uygulanır; örnek kayıtlar yalnızca geçici test veritabanında kullanıldı.

## Teslimat, rezervasyon ve üretim bağımlılıkları — 4 Kasım 2026

- Geçici veritabanında 291 test geçti; önceki 270 teste 21 senaryo eklendi. Django kontrolü ve migration tutarlılığı başarılı; şema değişikliği gerekmedi.
- Normal taslak düzenleme ekranında 3 adetlik kalem çıkarılıp 2 adetlik kalem işlendi. 5 adet siparişte teslim/kabul 2, kalan 3 ve sipariş açık kaldı. Satış, satın alma, takip ekranı, rapor/CSV, sonraki gerçek teslimat ve ters fiş doğrulandı; çıkarılan satır geçmişte korundu.
- Başka depodan tam/kısmi teslimatta rezervasyon toplamının kalan talebe inmesi, aynı belgedeki bölünmüş satırlar, eski fazla rezervasyonun son teslimatta bırakılması, diğer sipariş rezervasyonlarının korunması, kullanıcı/zaman/gerekçe ve hatada stok/belge/rezervasyon/audit'in birlikte geri alınması sınandı.
- Kendine bağlı, doğrudan ve dolaylı döngüler; toplu ekleme, başarısız `set()`, ters ilişki, tekrarlı ekleme, geçerli ortak bağımlılıklar, başladıktan sonra bağlantı değişiklikleri ve form hatasında diğer alanların da korunması doğrulandı.
- Bağlı çalışan/tamamlanmış iş varken ters üretim engellendi. Taslak ve iptal edilmiş bağlı işlerle izinli ters işlem, aynı ters fişin tekrarı, özgün parti ve stok geçmişi, eski tutarsız ön koşulda ilerleme/iptal koruması test edildi.
- Geliştirme veritabanının SHA-256 özeti değişmedi; SQLite bütünlük ve yabancı anahtar kontrolleri başarılı. Örnek işlemler yalnız bellekteki test veritabanında kaldı. GitHub'a aktarım veya sunucu dağıtımı yapılmadı.

## Ana ürün kodunun yanında resim — 6 Kasım 2026

- Geçici veritabanında 315 test geçti; önceki 291 teste 24 resim senaryosu eklendi. Django kontrolü ve migration tutarlılığı başarılı. Pillow 13.0.0 eklendi.
- Ana ürün oluşturma/düzenleme, tek resim ekleme/değiştirme/silme, ürün ve stok listesinde kodla aynı hücre, normal/sayfa içi ayrıntılar, yetkiler, CSRF, değişmiş resme eski formdan müdahale ve yalnız ana ürün kapsamı sınandı.
- JPG/PNG/WebP, dosya boyutu ve piksel sınırı, bozuk veya sahte dosya, hareketli resim, EXIF yönü/metadata temizliği, şeffaflık, 1600/160 piksel boyutları ve yetkili önbellek/HEAD/304 yanıtları doğrulandı. Liste sorgusu resim byte'larını almadan tek ek sorguyla metadata getiriyor.
- Resim kaydı veya silme/audit sırasında hata olduğunda kart, resim ve işlem geçmişinin birlikte geri alınması doğrulandı. Mevcut stok/reçete/iş verileri korundu; açık üretim işi bulunan ana ürünün resmi değiştirilebiliyor.
- Chromium'da ayrı sentetik veritabanıyla resim ekleme → kod yanındaki önizleme → sayfa içi ayrıntı → stok kartından değiştirme → hatalı dosyada eski resmi koruma → silme akışı tamamlandı. Masaüstü ve mobilde sayfa taşması veya JavaScript hatası görülmedi. Gözlemci resmi görebildi, düzenleyemedi; anonim resim isteği girişe yönlendi.
- Geliştirme ortamına `0013` uygulandı. Önceden bulunan 35 tablonun bütün özgün satırları/sütun değerleri korundu; SQLite bütünlük ve yabancı anahtar kontrolleri başarılı. Yeni resim tablosu boş kaldı; örnekler yalnız geçici veritabanlarında kullanıldı. GitHub'a aktarım veya sunucu dağıtımı yapılmadı.
