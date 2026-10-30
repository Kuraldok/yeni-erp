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
- **Siparişler:** **Yeni Sipariş** ekranında sipariş bilgilerini ve birden fazla ürün/miktar/fiyat satırını birlikte girin. Satışta yalnızca ana ürünler, satın almada yalnızca hammaddeler seçilir. Sipariş kalemleri aynı anda taslak siparişe kaydedilir; **Sipariş kalemleri** ekranından da eklenip düzenlenebilir. Ardından siparişi onaylayabilirsiniz. Onaylı siparişin kalemlerini değiştirmek için önce siparişi taslağa alıp kaydedin. Toplamlar KDV hariçtir ve siparişin para birimiyle hesaplanır. Sipariş durumu stokları otomatik değiştirmez.
- **Üretim rotaları:** Ürünü ve rota sürümünü belirleyin. **Rota adımları** ekranında sıra, operasyon, iş istasyonu, süre ve talimat girin. Aynı rotada sıra numarası tekrarlanamaz.
- **Kullanıcı ve yetki yönetimi:** Yönetim ekranında kullanıcı oluşturup bir gruba ekleyin. Yeni kullanıcı, rol atanmadıkça işletme kayıtlarını göremez. `Etkin` seçeneğini kaldırmak hesabı kapatır. Sıradan kullanıcılara `Personel durumu` veya `Süper kullanıcı` yetkisi vermeyin.

## Roller

| Rol | Yetki |
| --- | --- |
| Gözlemci | İşletme kayıtlarını okur; değiştiremez. Ayrıntılı işlem geçmişi yetkisi içermez. |
| Depo sorumlusu | Ürün ve depoları okur; stok hareketlerini okur ve ekler. |
| Sipariş sorumlusu | Ürünleri okur; cari, sipariş ve kalem ekler/düzenler; taslak siparişin kalemini gerekçeyle iptal eder. |
| Üretim sorumlusu | Rota, iş emri ve tüketim reçetesini yönetir; üretimi ilerletip üretim stok fişi oluşturur. Elle stok hareketi ekleme yetkisi içermez. |
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

İlk sürüm kapsamı: kayıt ekleme/düzenleme/listeleme, arama, sayfalama ve yetkiler. Muhasebe, fatura/e-belge ve KDV hesabı henüz yoktur. Stok rezervasyonu ile onaylı teslimat / mal kabul fişleri desteklenir; fiziksel sevkiyat kendiliğinden yapılmaz. Excel reçeteleri ve üretim iş emirleri aşağıdaki bölümde açıklanmıştır. ERP iş kayıtlarının ekleme, düzenleme, silme ve kalem iptali **İşlem geçmişi** ekranında kullanıcı ve zaman bilgisiyle tutulur. Chromium üzerinde masaüstü ve telefon ekran boyutları kontrol edildi; fiziksel cihaz testi yapılmadı.

## Verileri koruma ve GitHub

`db.sqlite3`, `.local-secret`, `.venv` ve parolalar `.gitignore` ile depodan hariç tutulur. Veri tabanını dosyaları listeleyen bir herkese açık sunucuyla sunmayın. Verileri silen `flush`, sıfırlama veya veri tabanı dosyasını silme komutlarını kullanmayın. Güncelleme öncesi yedek alın. SQLite yedeğini uygulama kapalıyken güvenli bir konuma kopyalayabilirsiniz; çalışan uygulama için SQLite'ın çevrimiçi yedekleme yöntemini kullanın. PostgreSQL için sağlayıcının otomatik yedeğini ve `pg_dump` yöntemini kullanın; geri yüklemeyi ayrıca deneyin.

Kodun GitHub deposu: [Kuraldok/yeni-erp](https://github.com/Kuraldok/yeni-erp). Kaynak kodu ve şema değişiklikleri Git ile sürümlenir; gerçek veri tabanı, gizli anahtarlar ve yerel paketler GitHub'a gönderilmez. GitHub'a kod kaydetmek uygulamayı internete yayımlamaz. Veri modeli ve teknik kararlar: [docs/teknik-yapi.md](docs/teknik-yapi.md).


## Ahşap / döküm üretimi ve Excel reçeteleri

Ürün ve hammadde listeleri Excel'den boş alanları doldurmadan aktarılabilir. Menüye **Excel aktarımları**, **Ürün reçeteleri**, **Reçete satırları ve eksikler**, **Standart üretim akışları** ve **Üretim iş emirleri** eklendi. Standart akışlar ahşap ve döküm üretimini, gerektiğinde montajı, paketlemeyi ve teslimata hazırlığı kapsar.

İhtiyaç hesabı, iş emri açma ve eksik verilerin incelenmesi için [Üretim ve Excel kullanım rehberini](docs/uretim-ve-excel.md) okuyun. Yeni iş emirleri **Üretim ve stok** modundadır: doğrulanmış tüketim reçetesi ve depolar seçildikten sonra son aşamada gerçek miktarla stok tüketimi ve ürün girişi birlikte oluşur. Eski işler yalnızca aşama takibi olarak korunur. Yeni veri tabanı tabloları mevcut kayıtlar silinmeden `scripts/setup.sh` ile kurulur. İlk yönetici hesabınız yoksa yukarıdaki `createsuperuser` adımını kullanın.


## Ürün kartları ve ürün grupları

Arayüz başlığı **Kural Döküm** olarak kullanılır. **Ürün kartları** listesinde ana ürünler önce ürün grubuna, sonra ürün koduna göre sıralanır. Kartın grubu boşsa reçete kaynak grubu kullanılır; birden fazla grup varsa alfabetik ilk grup sıralamayı belirler. Ürün grubu seçip **Filtrele** düğmesine basabilirsiniz; metin araması aynı anda kullanılabilir ve sayfa değiştirirken filtreler korunur. Grubu boş olan kayıtlar için **Grubu belirtilmeyenler** seçeneği vardır.

Ürün grubu filtresi ana panelde, bütün modül listelerinde, Excel aktarım raporunda ve reçete ihtiyaç hesabında bulunur. Siparişler kalemlerindeki, depolar stok hareketlerindeki, cariler siparişlerindeki ürünlere göre süzülür. Birden fazla eşleşme kaydı çoğaltmaz; sipariş toplamı tüm kalemleri kapsar. Dolaylı filtre için ilgili kayıtları görüntüleme izni gerekir. Siparişler listesindeki oluşturma düğmesi **Yeni Sipariş** olarak adlandırılır.

Ana ürünün koduna, adına veya **Ayrıntılar** bağlantısına tıkladığınızda yeni tarayıcı sekmesi açılır. Burada **Ürün bilgileri** ve **Yarı mamuller ve hammaddeler** sekmeleri bulunur. Bileşenlerin kodu, adı, birim ihtiyacı, ölçü birimi, biliniyorsa üretim alanı, kaynak satırı ve inceleme notları gösterilir. Kaynakta Mamul/Parça gibi diğer türde verilen bileşenler ayrı bölümde korunur. Birden fazla reçete sürümü varsa kaynak seçilir; farklı sürümlerin miktarları birleştirilmez.

Ana ürün görünümü mevcut üst ürün ilişkilerinden belirlenir; yarı mamul, hammadde ve parça kartları ana listede gösterilmez. Henüz reçetesi olmayan elle açılmış mamul/sınıflandırılmamış bağımsız kartlar görünmeye devam eder. Liste değişikliği stok, kart veya reçete kayıtlarını silmez; bileşen ayrıntıları için mevcut reçete görüntüleme izinleri geçerlidir.

Ürün ayrıntısındaki birim ihtiyaçlar adet için ondalıksız, diğer ölçü birimleri için üç ondalık basamakla gösterilir (örneğin `2 Adet`, `21,000 KG`). Aynı gösterim reçetenin ihtiyaç hesabında da kullanılır. Yeni Adet miktarları tam sayı olmalıdır. Geçmişteki kesirli Adet kaynakları değiştirilmeden incelemeye ayrılır; ekranda özgün değer ve inceleme uyarısı gösterilir. Boş miktarlar boş kalır. Ana kontrol panelindeki ürün toplamı, Ürün Kartları listesindeki ana ürünleri sayar; bileşenleri veya aynı ürüne ait farklı reçete sürümlerini ayrıca saymaz.

**Reçete satırları ve eksikler** listesindeki **Bir üst ürün için ihtiyaç** sütunu adet için ondalıksız, diğer birimler için iki ondalık basamakla gösterilir (`2`, `8,45`, `7,00`). Bu biçimlendirme miktarları yalnızca ekranda yuvarlar; kaynak ve kayıtlı değerler korunur.


## Yarı mamul üretim ve temin rotaları

Çeşmelerin tüm yarı mamulleri döküm üretimine; bankların Taşıyıcı Ayak/Gövde yarı mamulleri döküme, Ahşap Bölüm yarı mamulleri ahşap üretimine bağlanır. Diğer bank yarı mamulleri dışarıdan hazır temin edilir. Rotalar ana ürünün yarı mamul ayrıntılarından ve **Üretim ve temin rotaları** menüsünden açılır. Hazır temin kartları için iç üretim işi açılamaz. Ayrıntılar ve tekrar çalıştırılabilir kurulum komutu [üretim rehberinde](docs/uretim-ve-excel.md) yer alır.

Şömineler, Posta kutusu, Soba, Sandalye, Kapak, Bariyer, Ferforje korkuluk, Masa ve masa ayakları ile Döküm saksı bileşenleri döküme; Ahşap saksı bileşenleri ahşaba bağlanır. Mangalda **Ahşap saplı kulp** ve **Tel ızgara sapı** ahşap, diğer adı belli yarı mamuller döküm üretimidir. Bu yeni kategorilerde reçete bileşeni olarak **Mamul** yazılan satırlar da yarı mamul gibi rota alır; özgün Excel değerleri ve mevcut kart türleri korunur. Hammaddeler ve parçalar bu kuralla sınıflandırılmaz. Ayrıntılar ve eksik/çelişkili kayıtların davranışı [kategori kuralları bölümünde](docs/uretim-ve-excel.md#diğer-kategorilerin-üretim-rotaları) açıklanır.

**Izgara / Izgaralar** ürünleri tek mamuldür; döküm rotası doğrudan ana ürün kartına bağlanır. Yarı mamul veya bileşen kodu oluşturulmaz. Bu gruptaki kodsuz hammadde satırlarının ihtiyaçları kaynak miktar ve birim üzerinden hesaplanır; mevcut miktar/birim ve döküm hammaddesi kontrolleri geçerlidir. Ürün ayrıntısında doğrudan üretim rotası ve ayrı hammadde bölümü gösterilir. Stok üretiminde kodsuz hammadde satırını ayrı tüketim kopyasında mevcut bir hammadde stok kartıyla eşleştirin; kaynak satıra kod eklenmez.

## Sipariş ve kalemleri birlikte girme

1. **Siparişler → Yeni Sipariş** yolunu açın. Numarayı, türü, uygun müşteri/tedarikçiyi, tarihi ve para birimini girin.
2. **Satış** seçildiğinde **Ürün Kartları** listesindeki ana ürünler görünür; hammadde, yarı mamul, parça veya yalnızca başka ürünün bileşeni olan Mamul kartları seçilemez. **Satın alma** seçildiğinde yalnızca Hammadde türündeki kartlar görünür.
3. **Sipariş kalemleri** bölümünde ürün, pozitif adet/miktar ve negatif olmayan KDV hariç birim fiyat girin. Miktar kartın gösterilen birimine göredir; birimi boş kartta değer tahmin edilmez. **+ Ürün ekle** ile birden fazla satır açabilirsiniz. **Satırı kaldır** yalnızca henüz kaydedilmemiş satırı formdan çıkarır.
4. **Ürün grubu** seçimi yeni ürün seçeneklerini daraltır. Önceden seçilmiş uygun ürünler korunur; aynı siparişe farklı gruplardan ürün eklenebilir. Tür değiştiğinde uyumsuz ürünler yeniden seçilmelidir. Tarayıcı betiği çalışmıyorsa **Seçimi güncelle** düğmesi bilgileri kaydetmeden seçenekleri yeniler.
5. **Kaydet**, sipariş ve geçerli kalemleri tek işlemde kaydeder. Hatalı satır veya kayıt hatası varsa o gönderimin başlığı/kalemleri kısmen kaydedilmez. Önceden kullanılan, önce boş taslak açıp sonra kalem ekleme yöntemi de desteklenir.

Siparişi düzenlerken mevcut kalemler aynı ekranda görünür. **Sipariş kalemleri →** bağlantısı yalnızca o siparişin satırlarını açar; buradaki yeni kayıt düğmesi siparişi önceden seçer. Bir ekrandaki değişiklik diğerinde aynı kayda yansır; ikinci bir kalem kopyası oluşturulmaz. Kalem listesinde sipariş seçimi, ürün grubu ve sayfalama birlikte çalışır.

Başka türe uygun olmayan kalemleri bulunan siparişin türü değiştirilemez. Onaylı siparişin kalemleri salt okunur gösterilir; önce durumu **Taslak** yapıp kaydedin, ardından kalemleri değiştirin. Mevcut kayıtlar otomatik silinmez veya dönüştürülmez. Satır ekleme/değiştirme için ilgili sipariş kalemi izinleri, seçenekler için ürün ve ilişkili sipariş/cari görüntüleme izinleri gerekir.


## Tam sayı miktarlar, kalem iptali ve üretim stoğu

Adet miktarları sipariş, stok, üretim, tüketim reçetesi ve asgari stokta tam sayı olarak girilir ve gösterilir. Kg, metre, litre ve fiyat alanları kesirli olabilir. Geçmişteki kesirli Adet değerleri sessizce yuvarlanmaz; **Miktar incelemeleri** listesinde korunur. Excel kaynağı değişmez.

Kayıtlı sipariş kaleminin **İptal et** bağlantısına tıklayıp gerekçe girin. Onaylı siparişi önce taslağa alın. Kalem silinmez, toplamdan çıkarılır; iptal eden, tarih ve gerekçe siparişte görünür. Yeni eklenip henüz kaydedilmemiş satırlar önceki gibi kaldırılabilir.

Üretimden önce **Stok tüketim reçeteleri** ekranında ürünün bir adedi için doğrudan girdileri tanımlayın. Kaynak Excel reçetesinden ayrı bir tüketim kopyası da oluşturabilirsiniz. Girdileri stok kartı, birim ve miktarla eşleştirip doğrudan tüketim olduğunu doğrulayın. İş emrinde bu sürümü, girdi çıkış deposunu ve ürün giriş deposunu seçin. Son aşamada gerçekleşen üretim miktarını kaydedin; **Üretim stok fişleri** ekranı girdi çıkışlarını ve ürün girişini birlikte gösterir. Ayrıntılı örnekler [üretim rehberindedir](docs/uretim-ve-excel.md#üretim-ve-stok-bağlantısı).

Yarı mamul üretilirken hammaddesi düşer; mamul üretilirken yarı mamul stoğu ve yalnızca o aşamada eklenen doğrudan hammaddeler düşer. Yarı mamulün hammaddesini mamulde tekrar tanımlamayın. Yetersiz stok, eksik eşleştirme veya birim uyumsuzluğunda son aşama tamamlanmaz; kısmi fiş oluşmaz. Aynı parti isteği ikinci kez stoğa işlenmez; kalan miktar için yeni üretim partileri kaydedilebilir. Eski işlere geriye dönük stok uygulanmaz. Yalnız paketleme/takip için stoksuz iş açma ayrıca yetki gerektirir.

**İşlem geçmişi**, ERP yöneticisi veya ayrıca yetki verilen hesaplarca okunur; değiştirme ve silme ekranı yoktur. Sistem komutları kullanıcı bilinmiyorsa “Sistem / komut” olarak kaydedilir; geçmiş veriler için eski kullanıcı/tarih tahmin edilmez. Geçiş `0004` ve `0005` migration'larıyla mevcut kayıtlar korunarak yapılır; güncellemeden önce yedek alın ve `migrate` ardından `setup_roles` çalıştırın.


## Sipariş, üretim, depo ve maliyet akışı

- **Stok kartları**, ana ürünlerin yanında hammadde, yarı mamul ve parçaları da yönetir. **Ürün kartları** ana ürün görünümünü korur. Kart ayrıntısında depo bazında fiziksel, ayrılmış ve serbest miktar ile biliniyorsa stok değeri görünür. Sipariş veya stok geçmişi bulunan kartın birimi değiştirilemez. Sipariş kaleminde birim, ürün kodu ve adı ayrıca saklanır. Pasif kartlar yeni sipariş, stok ve üretimde kullanılamaz.
- Dökümde **Pik / Alüminyum** seçimi, reçetede seçilmeyen alternatif etkinse üretimi durdurur. İki malzemeyi tek seçimle birlikte tüketmez; ayrı tüketim sürümü hazırlayın veya kullanılmayan girdiyi tüketimden çıkarın.
- **Siparişler → Ayrıntılar** sipariş takibini açar. Satış kaleminden **Üretim aç** ile aynı ürünün iş emrini bağlayın; yarı mamul işleri önceki iş bağlantılarıyla izlenir. **Stok ayır** ile mevcut mamulü satışa rezerve edin. Bağlı üretim çıktısı da kalan satış ihtiyacı kadar otomatik ayrılır.
- İş emri başlatılırken **kalan üretimin stok ihtiyacı** hesaplanır ve girdiler ayrılır. Diğer sipariş ve işlere ayrılmış malzeme serbest stok sayılmaz. Yetersiz stokta iş başlamaz; aşama, tüketim kopyası ve rezervasyon kısmen bırakılmaz. Üretim içi eksik veya fazla tüketim sonrası kalan ihtiyaç yeniden ayrılır; eksik miktar işte görünür.
- Son aşamada **bu partide sağlam üretim**, **fire / hurda** ve gerekçesini girin. Varsayılan tüketim `(sağlam + fire) × doğrudan reçete`dir; gerçek tüketim farklıysa ilgili girdi miktarını yazın. Fire sağlam ürün stoğuna girmez. Tüm partinin reddi de kaydedilebilir. Plan tamamlanana kadar son aşama açık kalır. Eksik üretimle kapatma ayrıca gerekçe ister. Tekrarlanan form aynı partiyi yeniden oluşturmaz.
- **Stok belgeleri → Yeni stok belgesi** ile teslimat, mal kabul, transfer veya sayım fişi hazırlayın. Önce taslağı inceleyin, sonra **Fişi işle** düğmesine basın. Teslimat / mal kabul onaylı siparişin etkin kalemlerine bağlıdır; kalan miktar aşılamaz. Sipariş ancak tüm etkin kalemler fişlerle tamamlanınca kapanır. Kısmi teslimat / kabul açık miktarı azaltır.
- Transfer iki depodaki çıkış/girişi birlikte kaydeder. Sayımda **sayılan toplam** girilir; fark hesaplanır. Sayım taslağından sonra stok değişirse yeni sayım gerekir. Başka işlere ayrılmış miktar sayım farkıyla veya elle stok çıkışıyla tüketilemez.
- **Kalan üretimi iptal et**, kaydedilmiş partileri korur ve girdi rezervasyonunu bırakır. İşlenmiş depo veya üretim fişini **ters fiş** ile düzeltin; gerekçe, kullanıcı ve zaman saklanır. Ürün ayrılmış veya tüketilmişse ters fiş durur. Bağlı teslimatı önce ters çevirin, gerekirse satış rezervasyonunu bırakın. Üretim ters fişi ilgili partiyi rapor toplamından çıkarır ve tamamlanmış işi tekrar son aşamaya açar.
- Stok girişinde biliniyorsa **TL birim maliyetini** girin. TRY mal kabulünde sipariş fiyatı kullanılır; dövizli mal kabulde TL maliyeti açıkça yazılır. Çıkışlar depo bazında hareketli ağırlıklı ortalamayla değerlenir. Üretim maliyeti gerçek girdiler + girilen işçilik + diğer giderlerdir. Maliyeti eksik malzeme sıfır sayılmaz; çıktı maliyeti de bilinmiyor görünür. Hurda/fire kaydı bir üretim kaybıdır; kendiliğinden geri kazanılmış hammadde veya tahmini hurda satış değeri oluşturulmaz.
- Menüde **Stok ve depo raporu**, **Üretim ve maliyet raporu**, **Sipariş ve teslimat raporu** bulunur. Tarih ve ürün grubu, stok raporunda ayrıca depo seçilir; CSV indirilebilir. Üretim raporu sağlam/fire, kalan miktar, reçete-gerçek tüketim farkı ve maliyetleri gösterir. Geçmiş tarihli stok raporu geçmiş rezervasyon tahmin etmez.

Depo sorumlusu fiş hazırlayıp işleyebilir; sipariş sorumlusu mamul ayırıp rezervasyonu bırakabilir. Üretim sorumlusu kısmi üretim ve iş iptalini yönetir; açık satış kalemlerini fiyatlarını görmeden üretime bağlayabilir. Ters fiş yetkisi varsayılan olarak ERP yöneticisindedir. Roller `setup_roles` ile mevcut özel izinler kaldırılmadan güncellenir.

Geçiş `0006`–`0011` migration'larıyla yapılır. Önceki iş durumları, miktarlar ve kaynak Excel satırları korunur. Eski sipariş kalemlerine geçiş anındaki kart bilgileri kopyalanır; bilinmeyen eski fiyatlar, teslimatlar, rezervasyonlar veya kullanıcılar üretilmez. Eski tamamlanmış siparişlerin durumu korunur; teslim/kabul raporu yalnız kaydedilmiş fişleri gösterir. Yedek alın, `migrate` ve `setup_roles` çalıştırın. Bu çalışma geliştirme ortamında yapılmıştır.

Ters çıkışta özgün fişin maliyeti güncel stok ortalamasından farklıysa **Ters fiş değer farkı** ayrıca saklanır; stok değerinden sessizce düşmez veya negatif değer bırakmaz. Stok ve üretim raporları bu düzeltmeyi ayrı TL sütununda gösterir.
