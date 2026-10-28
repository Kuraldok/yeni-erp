# Yeni ERP

Türkçe arayüzlü, telefon ve bilgisayarlarda kullanılabilen ilk ERP sürümü. Python 3.12+ ve Django 5.2 LTS kullanır. Kullanıcılar aynı veri tabanına bağlanır; herkesin erişimi hesabına atanan izinlerle belirlenir.

## İlk kurulum

Terminal, komut yazdığınız ekrandır. Bulut ortamında aşağıdaki komutları sırayla çalıştırın:

```bash
cd /workspace/yeni-erp
bash scripts/setup.sh
.venv/bin/python manage.py createsuperuser
bash scripts/start.sh
```

1. `setup.sh`, paketleri kurar, eksik veri tabanı tablolarını oluşturur ve hazır rolleri ekler. Tekrar çalıştırılabilir; verileri silmez.
2. `createsuperuser`, ilk yönetici hesabınızı oluşturur. Kullanıcı adınızı ve güçlü parolanızı terminale girin. Parola yazılırken görünmez. Hazır veya ortak bir yönetici parolası yoktur.
3. `start.sh`, geliştirme sunucusunu 8000 numaralı yerel portta açar. Durdurmak için Ctrl+C kullanın. Bu işlem uygulamayı internete yayımlamaz. Bulut arayüzünden erişim için ileride ayrıca güvenli yönlendirme yapılandırılmalıdır.

`.venv`, bu projeye özel Python paketlerinin bulunduğu klasördür. `db.sqlite3`, geliştirme verilerini kalıcı tutar; sunucuyu kapatmak verileri silmez. Bu dosya GitHub'a gönderilmez. `.env.example` yalnızca örnektir; otomatik okunmaz. Değişkenleri terminalde `export` ile veya bulut ortamı ayarlarından tanımlayın.

## İlk kullanım

- Yönetici hesabınızla giriş yapın. Ana panelde ürün, cari, sipariş ve stok hareketi sayıları görünür.
- **Ürün kartları:** Benzersiz ürün kodu, ad, birim ve asgari stok belirleyin.
- **Depolar:** En az bir depo oluşturun.
- **Stok hareketleri:** Ürün, depo ve miktar seçerek açılış stoğunu `Giriş` olarak kaydedin. `Çıkış` mevcut stoktan büyük olamaz. Açıklamaya belge veya düzeltme nedenini yazın. Hareketler silinmez ve değiştirilmez; düzeltme için ters yönde yeni hareket girilir.
- **Müşteri ve tedarikçiler:** Satış için müşteri; satın alma için tedarikçi tanımlayın. Aynı cari iki türü de destekleyebilir.
- **Siparişler:** Önce `Taslak` sipariş açın. Sonra **Sipariş kalemleri** ekranında ürün, miktar ve fiyat ekleyin. Ardından siparişi onaylayabilirsiniz. Onaylı siparişin kalemlerini değiştirmek için önce siparişi taslağa alın. Toplamlar KDV hariçtir ve siparişin para birimiyle hesaplanır. Sipariş durumu stokları otomatik değiştirmez.
- **Üretim rotaları:** Ürünü ve rota sürümünü belirleyin. **Rota adımları** ekranında sıra, operasyon, iş istasyonu, süre ve talimat girin. Aynı rotada sıra numarası tekrarlanamaz.
- **Kullanıcı ve yetki yönetimi:** Yönetim ekranında kullanıcı oluşturup bir gruba ekleyin. Yeni kullanıcı, rol atanmadıkça işletme kayıtlarını göremez. `Etkin` seçeneğini kaldırmak hesabı kapatır. Sıradan kullanıcılara `Personel durumu` veya `Süper kullanıcı` yetkisi vermeyin.

## Roller

| Rol | Yetki |
| --- | --- |
| Gözlemci | Tüm işletme kayıtlarını okur; değiştiremez. |
| Depo sorumlusu | Ürün ve depoları okur; stok hareketlerini okur ve ekler. |
| Sipariş sorumlusu | Ürünleri okur; cari, sipariş ve kalem ekler/düzenler. |
| Üretim sorumlusu | Ürünleri okur; rota ve adımları ekler/düzenler. |
| ERP yöneticisi | Tüm işletme modüllerini yönetir; kayıt silme ve kullanıcı yönetimi içermez. |
| Süper kullanıcı | Kullanıcı/grup yönetimi dahil tüm yetkiler. Yalnızca güvenilir sistem yöneticisine verilir. |

Bir kullanıcı birden fazla gruba katılabilir; izinler birleşir. Roller `setup_roles` komutuyla tekrar eklenebilir. Komut mevcut özel izinleri kaldırmaz. Gruba yalnızca `Görüntüleyebilir`, `Ekleyebilir`, `Değiştirebilir` izinlerini gerektiği kadar verin. Giriş yapan kullanıcıların parolaları Django tarafından özetlenerek saklanır.

## Kontroller

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test --verbosity 2
```

Testler geçici, ayrı bir veri tabanı kullanır; geliştirme kayıtlarını silmez. GitHub Actions aynı kontrolleri push veya pull request geldiğinde çalıştıracak şekilde yapılandırılmıştır. GitHub Actions sonuçlarını deponun Actions sekmesinden kontrol edebilirsiniz; yerel test sonuçları bulut ortamındaki test çalıştırmasına aittir.

## Bulutta çalışma ve sınırlar

Bu sürüm geliştirme içindir; henüz yayımlanmamıştır. SQLite küçük denemeler için uygundur. Birden fazla kişinin yoğun ve eşzamanlı yazacağı canlı sistem için PostgreSQL kullanın. PostgreSQL bağlantısı `DATABASE_URL` üzerinden desteklenir; bu oturumda PostgreSQL üzerinde test edilmedi.

Canlıya geçmeden önce HTTPS, PostgreSQL, yedekleme/geri yükleme, oturum güvenliği, giriş denemesi sınırlaması ve izleme ayrıca kurulup doğrulanmalıdır. `DJANGO_DEBUG=false`, güvenli `DJANGO_SECRET_KEY`, doğru `DJANGO_ALLOWED_HOSTS` ve `DJANGO_CSRF_TRUSTED_ORIGINS` tanımlanmalıdır. Gerçek anahtar ve bağlantı parolalarını depoya veya sohbete yazmayın. Yerel `.local-secret` yalnızca geliştirmede kullanılır; uygulama canlı modda anahtar verilmezse başlamaz.

İleride canlı çalışma için `collectstatic` sonrasında `gunicorn config.wsgi:application` kullanılabilir. Statik dosyaları WhiteNoise sunar. HTTPS yönlendirme ve ters vekil güven ayarları seçilen bulut sağlayıcısına göre ayrıca yapılandırılmalıdır; şu anda dağıtım yapılmaz.

İlk sürüm kapsamı: kayıt ekleme/düzenleme/listeleme, arama, sayfalama ve yetkiler. Muhasebe, fatura/e-belge, KDV hesabı, stok rezervasyonu, otomatik stok tüketimi ve otomatik sevkiyat henüz yoktur. Excel reçeteleri ve üretim iş emirleri aşağıdaki bölümde açıklanmıştır. Kullanıcı işlemleri için kapsamlı denetim günlüğü yoktur; stok hareketlerinde kaydeden kullanıcı ve tarih tutulur. Telefon düzeni CSS ile hazırlanmıştır; gerçek cihaz/tarayıcı testi yapılmamıştır.

## Verileri koruma ve GitHub

`db.sqlite3`, `.local-secret`, `.venv` ve parolalar `.gitignore` ile depodan hariç tutulur. Veri tabanını dosyaları listeleyen bir herkese açık sunucuyla sunmayın. Verileri silen `flush`, sıfırlama veya veri tabanı dosyasını silme komutlarını kullanmayın. Güncelleme öncesi yedek alın. SQLite yedeğini uygulama kapalıyken güvenli bir konuma kopyalayabilirsiniz; çalışan uygulama için SQLite'ın çevrimiçi yedekleme yöntemini kullanın. PostgreSQL için sağlayıcının otomatik yedeğini ve `pg_dump` yöntemini kullanın; geri yüklemeyi ayrıca deneyin.

Kodun GitHub deposu: [Kuraldok/yeni-erp](https://github.com/Kuraldok/yeni-erp). Kaynak kodu ve şema değişiklikleri Git ile sürümlenir; gerçek veri tabanı, gizli anahtarlar ve yerel paketler GitHub'a gönderilmez. GitHub'a kod kaydetmek uygulamayı internete yayımlamaz. Veri modeli ve teknik kararlar: [docs/teknik-yapi.md](docs/teknik-yapi.md).


## Ahşap / döküm üretimi ve Excel reçeteleri

Ürün ve hammadde listeleri Excel'den boş alanları doldurmadan aktarılabilir. Menüye **Excel aktarımları**, **Ürün reçeteleri**, **Reçete satırları ve eksikler**, **Standart üretim akışları** ve **Üretim iş emirleri** eklendi. Standart akışlar ahşap ve döküm üretimini, gerektiğinde montajı, paketlemeyi ve teslimata hazırlığı kapsar.

İhtiyaç hesabı, iş emri açma ve eksik verilerin incelenmesi için [Üretim ve Excel kullanım rehberini](docs/uretim-ve-excel.md) okuyun. Üretim miktarları stokları otomatik değiştirmez. Yeni veri tabanı tabloları mevcut kayıtlar silinmeden `scripts/setup.sh` ile kurulur. İlk yönetici hesabınız yoksa yukarıdaki `createsuperuser` adımını kullanın.


## Ürün kartları ve ürün grupları

Arayüz başlığı **Kural Döküm** olarak kullanılır. **Ürün kartları** listesinde ana ürünler görünür. Ürün grubu seçip **Filtrele** düğmesine basabilirsiniz; metin araması aynı anda kullanılabilir ve sayfa değiştirirken filtreler korunur. Grubu boş olan kayıtlar için **Grubu belirtilmeyenler** seçeneği vardır.

Ana ürünün koduna, adına veya **Ayrıntılar** bağlantısına tıkladığınızda yeni tarayıcı sekmesi açılır. Burada **Ürün bilgileri** ve **Yarı mamuller ve hammaddeler** sekmeleri bulunur. Bileşenlerin kodu, adı, birim ihtiyacı, ölçü birimi, biliniyorsa üretim alanı, kaynak satırı ve inceleme notları gösterilir. Kaynakta Mamul/Parça gibi diğer türde verilen bileşenler ayrı bölümde korunur. Birden fazla reçete sürümü varsa kaynak seçilir; farklı sürümlerin miktarları birleştirilmez.

Ana ürün görünümü mevcut üst ürün ilişkilerinden belirlenir; yarı mamul, hammadde ve parça kartları ana listede gösterilmez. Henüz reçetesi olmayan elle açılmış mamul/sınıflandırılmamış bağımsız kartlar görünmeye devam eder. Liste değişikliği stok, kart veya reçete kayıtlarını silmez; bileşen ayrıntıları için mevcut reçete görüntüleme izinleri geçerlidir.

Ürün ayrıntısındaki birim ihtiyaçlar adet için ondalıksız, diğer ölçü birimleri için üç ondalık basamakla gösterilir (örneğin `2 Adet`, `21,000 KG`). Aynı gösterim reçetenin ihtiyaç hesabında da kullanılır. Bu yalnızca ekrandaki biçimdir; hesaplamalarda ve veri tabanında özgün hassasiyet korunur. Boş miktarlar boş kalır. Ana kontrol panelindeki ürün toplamı, Ürün Kartları listesindeki ana ürünleri sayar; bileşenleri veya aynı ürüne ait farklı reçete sürümlerini ayrıca saymaz.


## Yarı mamul üretim ve temin rotaları

Çeşmelerin tüm yarı mamulleri döküm üretimine; bankların Taşıyıcı Ayak/Gövde yarı mamulleri döküme, Ahşap Bölüm yarı mamulleri ahşap üretimine bağlanır. Diğer bank yarı mamulleri dışarıdan hazır temin edilir. Rotalar ana ürünün yarı mamul ayrıntılarından ve **Üretim ve temin rotaları** menüsünden açılır. Hazır temin kartları için iç üretim işi açılamaz. Ayrıntılar ve tekrar çalıştırılabilir kurulum komutu [üretim rehberinde](docs/uretim-ve-excel.md) yer alır.
