<p><img src="app/static/logo-wordmark.svg" alt="MargeRadar" width="480"></p>

# MargeRadar — Hollanda pazaryeri kârlılık ve fırsat bulucu (MVP)

bol.com ve Amazon.nl'deki ürünleri EAN/GTIN ile eşleştirir, platform bazında **gerçek net kârı** (komisyon, fulfilment, KDV, iade, reklam, ithalat dahil) hesaplar, fiyat geçmişini tutar, fırsatları gerekçeli olarak puanlar ve eşik aşıldığında uyarı verir. Kaufland, MediaMarkt, eBay ve tedarikçi katalogları CSV ile hemen eklenebilir; mimari API bağlayıcısı eklemeye hazırdır.

> Tek kullanıcılı, yerel çalışan bir uygulamadır. Otomatik satın alma ya da otomatik ilan açma **yoktur**. Scraping, CAPTCHA aşma veya platform kurallarını ihlal eden yöntemler **kullanılmaz**.

---

## 1. Teknik plan (kısa)

| Katman | Seçim | Neden |
|---|---|---|
| Dil | Python 3.12+ (3.14 ile test edildi) | Veri/CSV işleme, hesaplama ve test için en az sürtünme |
| Web | FastAPI + Jinja2 (sunucu tarafı sayfalar) | Tek komutla çalışır; derleme adımı, Node veya SPA gerekmez |
| Veritabanı | SQLite (tek dosya: `data/nlfirsat.db`) | Kurulum gerektirmez, tek kullanıcı için yeterli; şema PostgreSQL'e taşınabilir |
| Grafik | Chart.js (CDN) | Hafif; internet yoksa tablo görünümü yine çalışır |
| HTTP | Python stdlib `urllib` | Bağlayıcılar için ek bağımlılık yok |
| Test | pytest | 50 test: kâr, komisyon, EAN, içe aktarma, uyarı, bağlayıcı ayrıştırma, sayfalar |

**Akış:** Tedarikçi CSV'si / API / pazaryeri CSV'si → `importers` (EAN normalize + eşleştirme) → `products`, `listings`, `offer_snapshots` (fiyat geçmişi) → `services.analyze_product` (komisyon kuralı + maliyet profili → `profit.calculate`) → puan (`scoring`), uyum (`compliance`) → pano / ürün sayfası / uyarılar (`alerts`).

```
app/
  schema.sql         veritabanı şeması (her veri noktasında kaynak + zaman)
  matching.py        EAN/GTIN doğrulama (kontrol hanesi, UPC/GTIN-14), nitelik eşleştirme
  fees.py            komisyon kural motoru (tek oran / fiyat eşiği / dilim, min. ücret, sabit ücret bantları, KDV)
  profit.py          net kâr, marj, ROI, başabaş fiyatı, kalem kalem döküm, "kesin/tahmini" kararı
  history.py         ani fiyat değişimi tespiti
  scoring.py         fırsat puanı + gerekçeler
  compliance.py      marka / yetkili satıcı / Amazon kısıtı / bol koşulları / CE-GPSR kontrol listesi
  alerts.py          eşikler, tekrar bildirimi önleme, e-posta (SMTP ya da .eml dosyası)
  importers.py       CSV içe aktarma (tedarikçi, teklifler, arama sonuçları, komisyon kuralları)
  connectors/        base (arayüz, hız sınırı, 429 geri çekilme), bol.py, amazon_sp.py, platform kaydı
  services.py        ürün analizi (tüm modülleri birleştirir)
  sync.py            API senkronizasyonu
  demo.py            örnek veri üretimi (normal CSV içe aktarma yolundan yüklenir)
  main.py, templates/, static/   web arayüzü
templates_csv/       boş CSV şablonları
sample_data/         demo komutunun ürettiği örnek CSV'ler
tests/               pytest
```

---

## 2. Kurulum ve çalıştırma (Windows / macOS / Linux)

```bash
cd nl-firsat
python -m venv .venv
# Windows:  .venv\Scripts\activate      macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt

python -m app.cli demo                 # örnek veriyle veritabanını oluşturur (var olan DB'yi SİLER)
python -m uvicorn app.main:app --port 8000
```

Tarayıcıda **http://127.0.0.1:8000** adresini açın.

Diğer komutlar:

```bash
python -m pytest -q                              # testler
python -m app.cli init                           # boş veritabanı
python -m app.cli import-supplier fiyatlar.csv   # tedarikçi listesi
python -m app.cli import-offers teklifler.csv    # pazaryeri fiyatları
python -m app.cli import-rules komisyon.csv      # komisyon kuralları
python -m app.cli sync                           # API'lerden güncelle (.env doluysa) + uyarılar
```

API anahtarları için `.env.example` dosyasını `.env` olarak kopyalayıp doldurun. Anahtarlar yalnızca ortam değişkenlerinden okunur; veritabanına veya koda yazılmaz.

### Demo'da neler görülür

| Ürün (kurgusal marka) | Gösterdiği durum |
|---|---|
| Kookfix spatelset | Amazon'da **kesin net kâr**; tüm uyum kontrolleri tamam; EAN'sız ikinci tedarikçi kaydı **onay bekliyor** |
| Lumo bureaulamp | Amazon'da **ani fiyat düşüşü (−15%)**; "fiyat eşiği" komisyon kademesi (≤ €20 %8 / üstü %15) |
| Bouwmax schroevendraaierset | FBA ücreti girilmemiş → **tahmini**; veri 5 günlük → **eski veri**; EAN'sız Kaufland satırı **eşleşme onayı** bekliyor |
| Glowskin serum | bol.com'da kural yok → **komisyon oranı gerekli** (kâr gösterilmez, sayfada oran giriş formu) |
| Snoerloos USB-C kabel | "Dilim bazında" komisyon; CE/RoHS **risk** işaretli → puan düşer |
| Vitaro citruspers | Alış fiyatı yok → **kâr gösterilmez**, yalnızca başabaş alış fiyatı |
| Nordhout snijplankenset | İki platformda da **"arama sonucunda listelenmedi"** → yeni ürün adayı |
| Kookfix pannenlappen | Excel'in bozduğu EAN (`2,00E+12`) yakalanır, ürün ayrı tutulur |

Demo'daki markalar kurgusaldır ve EAN'lar GS1'in mağaza içi kullanıma ayrılmış `20…` önekiyle üretilir, yani gerçek bir ürünle çakışmaz. Fiyatlar, kargo, paketleme, iade ve reklam değerleri **örnektir**.

---

## 3. Veri kaynakları: doğrulanan ve doğrulanamayan (kontrol tarihi 2026-09-30)

### bol.com — Retailer API v10 ✅ resmî API
- OpenAPI tanımı: `https://api.bol.com/retailer/public/apispec/Retailer%20API%20-%20v10` · belgeler: https://api.bol.com/retailer/public/Retailer-API/
- Kimlik doğrulama: `POST https://login.bol.com/token?grant_type=client_credentials`, Basic auth; token yaklaşık 299 saniye geçerli.
- Kullanılan uç noktalar: `GET /retailer/products/{ean}/offers` (rakip teklifler, sayfa başına 50), `GET /retailer/products/{ean}/placement` (ürün URL'si), `GET /retailer/commission/{ean}?unit-price=` (EAN ve fiyata göre komisyon; `fixedAmount` ve `percentage` KDV dahil).
- Hız sınırı: uç nokta başına değişir ve yanıt başlıklarıyla bildirilir; 429 geldiğinde `Retry-After` başlığına uyulur. https://api.bol.com/retailer/public/Retailer-API/ratelimits.html
- **API'nin vermediği alanlar:** müşteriye kargo ücreti, satıcı adı (yalnızca retailerId gelir) ve bol.com'un kendisinin satıcı olup olmadığı. Bu alanlar "bilinmiyor" olarak gösterilir; uydurulmaz.
- **Komisyon:** Partnerplatform tablosu (https://partnerplatform.bol.com/nl/idp/commissie, `commissietabel_NL_Aug_26.xlsx`) kategori başına yalnızca **aralık** yayımlıyor (ör. Speelgoed %4,1–20,7, sabit €0,25–3,09). Tarifeler 9 Eylül 2026'da değişti. Kesin oran için ya **Commission API** bağlanmalı ya da oranı partner hesabınızdan girmelisiniz. Demo bol kuralları aralığın üst sınırıyla "doğrulanmadı" durumunda gelir ve hesaplar "tahmini" görünür.

### Amazon.nl — Selling Partner API ✅ resmî API
- Uç nokta `https://sellingpartnerapi-eu.amazon.com`, Amazon.nl marketplaceId `A1805IZSGTT6HS`, LWA token `https://api.amazon.com/auth/o2/token`.
- `GET /catalog/2022-04-01/items?identifiers={EAN}&identifiersType=EAN` → EAN'dan ASIN'e.
- `GET /products/pricing/v0/items/{Asin}/offers` → **0,5 istek/sn, burst 1**, en fazla 20 teklif. https://developer-docs.amazon.com/sp-api/reference/getitemoffers
- `POST /products/fees/v0/items/{Asin}/feesEstimate` → 0,5 istek/sn; Amazon'un kendi ücret tahmini. Garanti değildir.
- **Sınırlar:** Amazon'un perakende satıcı kimliği resmî olarak belgelenmemiştir. Kimliği bilmiyorsanız "Amazon satıcı mı?" alanı "bilinmiyor" kalır (`AMAZON_RETAIL_SELLER_IDS`). "Amazon tarafından gönderiliyor" (`IsFulfilledByAmazon`) ise ayrı bir alandır ve API'den gelir. SP-API **fiyat geçmişi ve satış hacmi vermez**; geçmiş yalnızca bu uygulamanın kendi ölçümlerinden oluşur.
- **Komisyon (referral fee):** https://sell.amazon.nl/en/prijsstelling adresinden doğrulandı. Demo 9 kategoriyi "Resmî kaynaktan doğrulandı" durumunda, kaynak ve tarihle yükler. Minimum €0,30'dur ve toplam fiyat (kargo dahil) üzerinden hesaplanır. Tabloda iki tür kademe var: *fiyat eşiği* (Home Products: ≤ €20 ise %8, üstü %15) ve *dilim bazında* (Electronic Accessories: €100'e kadar olan kısım %15, aşan kısım %8). FBA ücretlerine 17 Nisan 2026'dan itibaren **%1,5 yakıt ve enflasyon ek ücreti** uygulanıyor. Profesyonel plan €39/ay (KDV hariç). FBA birim ücretleri indirilebilir oran kartlarında yer aldığından ürün bazında girilir.
- Varsayım: Amazon ücret tutarları KDV hariç kabul edildi (sayfa abonelik ücreti için "excl. VAT" yazıyor). Farklıysa kuraldaki "KDV dahil" kutusunu işaretleyin.

### Kaufland, MediaMarkt Marketplace, eBay — ⚠ bu sürümde doğrulanmadı
API bağlayıcısı yazılmadı; bu kanallar **CSV ile** çalışır. Bağlayıcı eklemek için `connectors/base.py` içindeki `Connector` sınıfından türetin, `fetch_by_ean` ve (varsa) `get_commission` yöntemlerini yazın ve `connectors/__init__.py` içine kaydedin.

---

## 4. Kâr hesabı

Sonuç ekranında her platform için şu döküm gösterilir: **satış fiyatı → KDV hariç gelir → platform komisyonu → fulfilment/kargo → diğer giderler → alış + ithalat → net kâr.**

| Metrik | Formül |
|---|---|
| KDV hariç gelir | (fiyat + müşteriden alınan kargo) / (1 + satış KDV'si) |
| Net kâr | KDV hariç gelir − komisyon − fulfilment (+ ek ücret) veya satıcı kargosu − paketleme − iade payı − reklam − abonelik payı − alış (KDV hariç) − ithalat/gümrük [− indirilemeyen KDV] |
| Kâr marjı | net kâr / KDV hariç gelir |
| ROI | net kâr / toplam ürün maliyeti (alış + ithalat [+ indirilemeyen alış KDV'si]) |
| Başabaş fiyatı | net kârın ≥ 0 olduğu en düşük KDV dahil satış fiyatı. Kademeli komisyon nedeniyle tarama ve ikiye bölme yöntemiyle bulunur. |
| Brüt fiyat farkı | yüksek müşteri fiyatı − düşük müşteri fiyatı; **% değeri düşük fiyat baz alınarak** hesaplanır |

**Durum etiketleri**
- **Kesin net kâr:** Tüm maliyet kalemleri girilmiştir ve komisyon oranı *resmî kaynak*, *platform API'si* veya *kullanıcı girişi* ile belirlenmiştir.
- **Tahmini kâr:** Bir maliyet kalemi eksiktir (boş = bilinmiyor; 0 = gider yok) veya komisyon oranı doğrulanmamıştır. Eksik kalemler listelenir.
- **Komisyon oranı gerekli:** Komisyon zorunlu bir gider olduğundan kâr gösterilmez; ürün sayfasında oran giriş formu açılır.
- **Alış fiyatı yok:** Kâr gösterilmez; yalnızca "bu fiyatta en fazla şu alış fiyatında başabaş" bilgisi verilir.

KDV mükellefi (btw-ondernemer) varsayılandır: alış KDV'si ve platform ücretlerinin KDV'si indirilebilir. Bu ayar kapatılırsa bu KDV'ler gidere eklenir. Tüm oranlar **Ayarlar** sayfasından değiştirilir; hiçbiri kodda sabit değildir.

---

## 5. Fırsatlar, kısıtlar, uyarılar

- **Puan (0–100):** net kâr (en fazla 30), marj (en fazla 20), satıcı sayısı (en fazla 15), platformun kendi satışı (−15 ile +5 arası), veri güncelliği (en fazla 10), kısıt riski (−20 ile +10 arası) ve tahmini hesap (−5). Her bileşen gerekçesiyle gösterilir. Bilinmeyen veri puan kazandırmaz.
- **Yeni ürün:** Aramada bulunmayan ürün "arama sonucunda listelenmedi" olarak işaretlenir. Bu, ürünün o platformda hiç satılmadığı anlamına gelmez.
- **Kısıt kontrol listesi:** markalı mı, BOIP, EUIPO/TMview, yetkili satıcı şartı, Amazon listeleme kısıtı, bol.com kategori koşulları, ürün güvenliği/CE (GPSR 2023/988, oyuncak, LVD, EMC, RED, RoHS, kozmetik). Hiçbir madde otomatik "uygun" sayılmaz. Marka sicilinde "kayıt bulunamadı" sonucu **satış hakkı garantisi değildir**.
- **Uyarılar:** minimum net kâr, marj ve ROI, maksimum satıcı sayısı ve minimum fiyat farkı için eşik belirlenebilir. Bilinmeyen değer koşulu sağlamaz. Aynı ürün ve platform için yalnızca bir kez bildirim yapılır; "Sıfırla" ile yeniden bildirime izin verilir. SMTP ayarlı değilse e-posta `data/outbox/*.eml` dosyasına yazılır.

---

## 5b. Tedarikçi bulma ve tedarikçi fiyatları

**Sınır:** Toptan fiyatlar çoğunlukla herkese açık değildir. Ankorstore ve Orderchamp fiyatları yalnızca kayıtlı ve doğrulanmış perakendecilere gösterir; üretici ve distribütörler fiyatı teklif üzerine verir. Bu yüzden uygulama tedarikçi fiyatı **tahmin etmez ve siteleri taramaz**. Fiyatlar yalnızca sizin girdiğiniz veya CSV ile yüklediğiniz kayıtlardan gelir.

- **Tedarikçi bul (ürün sayfası):** Ankorstore, Orderchamp, Alibaba ve web araması için ürüne özel arama bağlantıları var. Bu bağlantıların biçimi 2026-09-30'da denendi. Faire ve Europages otomatik denemeyi engellediği için yalnızca ana sayfaya bağlanır. Verified by GS1 ile EAN'dan **marka sahibi** bulunur; markalı ürünlerde en güvenilir yol, marka sahibinden ya da Benelux distribütöründen teklif istemektir.
- **Teklif kaydı:** tedarikçi, fiyat türü (teklif / fiyat listesi / toptan platform / elle), alış fiyatı, KDV, ithalat/nakliye, MOQ, teslim süresi, fiyat tarihi, geçerlilik tarihi, kaynak bağlantısı ve not.
- **Karşılaştırma:** Her tedarikçi fiyatı için her platformda net kâr, marj ve ROI ayrı ayrı gösterilir. Hesapta birim maliyeti (alış + ithalat) en düşük tedarikçi kullanılır. Süresi dolmuş veya eski (varsayılan 90 gün) teklifler işaretlenir.
- **Tedarikçiler sayfası:** kayıtlı tedarikçiler ile her kaynağın fiyat görünürlüğü ve kullanım yolu.

## 6. Ürünü satmak isterseniz (MargeRadar)

1. **İsim ve logo:** "MargeRadar" adıyla çakışan bir yazılım ürünü web aramasında bulunamadı. Ancak bu bir marka araştırması **değildir**. Tescil öncesinde BOIP, EUIPO ve TMview'da 9. sınıf (yazılım) ve 35. sınıf (iş hizmetleri) için arama yapın; alan adını da kontrol edin. Logo dosyaları `app/static/logo-mark.svg` (kare) ve `logo-wordmark.svg` konumunda. Kelime logodaki yazı sistem yazı tipini kullanıyor; baskı ve tescil için yazıyı eğriye çevirin. Adı değiştirmek için `app/config.py` içindeki `APP_NAME` değerini düzenlemeniz yeterli.
2. **bol.com:** Başka satıcılar adına API kullanmak için **intermediary** yetkilendirmesi gerekir. Başvuruda kare logo (SVG veya PNG), callback URI ve uygulama adı istenir; her satıcı OAuth ile izin verir. https://api.bol.com/retailer/public/Retailer-API/intermediary-authorization.html
3. **Amazon:** Başka satıcılara hizmet veren uygulama için **public developer** kaydı, güvenlik soruları ve Selling Partner Appstore listelemesi gerekir. Her satıcı OAuth ile yetki verir. https://developer-docs.amazon.com/sp-api/docs/register-as-a-public-developer
4. **Mimari değişiklikler:** Uygulama şu an tek kullanıcılıdır. Satış için şunlar gerekir: çok kiracılı yapı (her satıcıya ait token ve veri ayrımı), giriş sistemi, AVG/GDPR kapsamında veri işleme sözleşmesi, PostgreSQL ve zamanlanmış senkronizasyon.
5. Bu uygulama vergi veya hukuk danışmanlığı değildir. KDV ve CE yükümlülüklerini ürün bazında bir uzmanla doğrulayın.

---

## 7. Bilinen sınırlar

- Rakip fiyatlar, API bağlandığında yalnızca `sync` çalıştırıldığı anda alınır. Zamanlama için Windows Görev Zamanlayıcı veya cron ile `python -m app.cli sync` komutunu çalıştırın. Hız sınırları nedeniyle Amazon'da ürün başına yaklaşık 2–4 saniye sürer.
- Satış hacmi, Buy Box payı ve dönüşüm tahmini **yapılmaz**, çünkü bu veriler bu kaynaklarda yok.
- API bağlayıcıları resmî belgelere ve örnek yanıt şemalarına göre yazıldı ve ayrıştırıcıları testlidir. **Gerçek hesap ile uçtan uca denenmedi**, çünkü erişim bilgileri henüz yok.
