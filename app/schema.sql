-- NL Fırsat Bulucu — SQLite şeması
-- Her veri noktası kaynağını (source) ve zamanını (captured_at / updated_at / checked_at) taşır.

PRAGMA foreign_keys = ON;

-- Kanonik ürün: eşleştirilmiş ürünün tek kaydı
CREATE TABLE IF NOT EXISTS products (
    id            INTEGER PRIMARY KEY,
    ean           TEXT UNIQUE,              -- GTIN-13 normalize edilmiş (yoksa NULL)
    brand         TEXT,
    model         TEXT,
    variant       TEXT,
    size          TEXT,
    pack_qty      INTEGER DEFAULT 1,
    title         TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

-- Tedarikçi katalog satırı (CSV veya elle)
CREATE TABLE IF NOT EXISTS supplier_items (
    id                       INTEGER PRIMARY KEY,
    supplier                 TEXT NOT NULL,
    supplier_sku             TEXT,
    ean                      TEXT,
    ean_valid                INTEGER,       -- 1/0, EAN yoksa NULL
    brand                    TEXT,
    model                    TEXT,
    variant                  TEXT,
    size                     TEXT,
    pack_qty                 INTEGER DEFAULT 1,
    title                    TEXT NOT NULL,
    purchase_price_excl_vat  REAL,          -- NULL = alış fiyatı bilinmiyor
    purchase_vat_rate        REAL,          -- ör. 0.21
    import_cost_per_unit     REAL,          -- ithalat/gümrük; NULL = bilinmiyor, 0 = yok
    product_id               INTEGER REFERENCES products(id),
    match_status             TEXT NOT NULL DEFAULT 'unmatched',  -- ean | confirmed | candidate | unmatched
    match_note               TEXT,
    candidate_product_id     INTEGER REFERENCES products(id),
    price_basis              TEXT,          -- quote (teklif) | catalog (fiyat listesi) | platform (toptan platform fiyatı) | manual
    moq                      INTEGER,       -- minimum sipariş adedi
    lead_time_days           INTEGER,
    quote_date               TEXT,          -- fiyatın alındığı tarih
    valid_until              TEXT,
    source_url               TEXT,          -- teklif / katalog / platform sayfası
    notes                    TEXT,
    source                   TEXT NOT NULL,
    updated_at               TEXT NOT NULL,
    UNIQUE (supplier, supplier_sku)
);

-- Bir ürünün bir platformdaki ilanı
CREATE TABLE IF NOT EXISTS listings (
    id              INTEGER PRIMARY KEY,
    product_id      INTEGER NOT NULL REFERENCES products(id),
    platform        TEXT NOT NULL,           -- bol | amazon_nl | kaufland | ...
    external_id     TEXT,                    -- ASIN, bol EAN, vb.
    url             TEXT,
    platform_category TEXT,                  -- komisyon kuralı anahtarı (commission_rules.category_key)
    match_method    TEXT NOT NULL,           -- ean | attributes_confirmed | candidate (onay bekler, analize girmez)
    UNIQUE (product_id, platform)
);

-- Fiyat/teklif anlık görüntüsü = fiyat geçmişi
CREATE TABLE IF NOT EXISTS offer_snapshots (
    id                          INTEGER PRIMARY KEY,
    listing_id                  INTEGER NOT NULL REFERENCES listings(id),
    captured_at                 TEXT NOT NULL,  -- fiyatın alındığı an (UTC ISO)
    price                       REAL NOT NULL,  -- öne çıkan teklif, KDV dahil
    shipping_cost               REAL,           -- müşteriye kargo; NULL = kaynak vermiyor
    delivery_min_days           INTEGER,
    delivery_max_days           INTEGER,
    offer_count                 INTEGER,
    seller_count                INTEGER,
    featured_seller_id          TEXT,
    featured_seller_name        TEXT,
    featured_is_platform        INTEGER,        -- öne çıkan teklifin satıcısı platformun kendisi mi (1/0/NULL)
    featured_fulfilled_by_platform INTEGER,     -- FBA / LVB ile mi gönderiliyor (1/0/NULL)
    platform_sells              INTEGER,        -- platform bu ürünü kendisi satıyor mu (1/0/NULL)
    source                      TEXT NOT NULL,  -- bol_retailer_api_v10 | amazon_sp_api | csv:<dosya>
    note                        TEXT
);
CREATE INDEX IF NOT EXISTS ix_snap_listing ON offer_snapshots(listing_id, captured_at);

-- Tedarikçi ürününün pazaryerinde aranma sonucu
CREATE TABLE IF NOT EXISTS search_results (
    id                INTEGER PRIMARY KEY,
    supplier_item_id  INTEGER NOT NULL REFERENCES supplier_items(id),
    platform          TEXT NOT NULL,
    searched_at       TEXT NOT NULL,
    result            TEXT NOT NULL,   -- found | not_listed_in_search
    query             TEXT,
    source            TEXT NOT NULL,
    UNIQUE (supplier_item_id, platform)
);

-- Komisyon kuralları: platform + kategori başına, ayarlardan düzenlenebilir
CREATE TABLE IF NOT EXISTS commission_rules (
    id                  INTEGER PRIMARY KEY,
    platform            TEXT NOT NULL,
    category_key        TEXT NOT NULL,
    category_label      TEXT NOT NULL,
    tier_mode           TEXT NOT NULL,     -- flat | threshold (tüm fiyata tek oran) | portion (dilim dilim)
    tiers_json          TEXT NOT NULL,     -- [{"up_to": 20, "pct": 8}, {"up_to": null, "pct": 15}]
    min_fee             REAL DEFAULT 0,    -- satış başı minimum komisyon
    fixed_fee_bands_json TEXT DEFAULT '[]',-- [{"up_to": 10, "fee": 0.2}, {"up_to": null, "fee": 0.5}]
    amounts_include_vat INTEGER NOT NULL DEFAULT 0,
    base                TEXT NOT NULL DEFAULT 'price_incl_shipping', -- price | price_incl_shipping
    status              TEXT NOT NULL,     -- official_verified | api | user_entered | unverified
    source_url          TEXT,
    checked_at          TEXT,
    notes               TEXT,
    UNIQUE (platform, category_key)
);

-- Platform maliyet profili (fulfilment modeli, kargo, iade, reklam...)
CREATE TABLE IF NOT EXISTS platform_costs (
    platform                    TEXT PRIMARY KEY,
    fulfilment_model            TEXT NOT NULL,   -- FBM | FBA | FBR | FBB
    sale_vat_rate               REAL,
    seller_shipping_per_unit    REAL,            -- FBM/FBR: satıcının ödediği kargo
    fulfilment_fee_per_unit     REAL,            -- FBA/FBB: platform fulfilment ücreti
    fulfilment_surcharge_pct    REAL DEFAULT 0,  -- ör. Amazon FBA yakıt/enflasyon ek ücreti
    packaging_per_unit          REAL,
    return_allowance_pct        REAL,            -- KDV hariç gelirin %'si
    ad_spend_pct                REAL,            -- KDV hariç gelirin %'si
    transaction_fixed_fee       REAL DEFAULT 0,  -- kural dışı ek sabit işlem ücreti
    monthly_plan_fee            REAL DEFAULT 0,  -- abonelik (KDV hariç)
    expected_monthly_units      INTEGER,         -- abonelik payı için
    fee_source_note             TEXT,
    updated_at                  TEXT NOT NULL
);

-- Ürün × platform bazında maliyet geçersiz kılma (NULL = profil değerini kullan)
CREATE TABLE IF NOT EXISTS product_cost_overrides (
    product_id                  INTEGER NOT NULL REFERENCES products(id),
    platform                    TEXT NOT NULL,
    fulfilment_fee_per_unit     REAL,
    seller_shipping_per_unit    REAL,
    packaging_per_unit          REAL,
    ad_spend_pct                REAL,
    sale_vat_rate               REAL,            -- ör. 0.09 düşük oranlı ürünler için
    source                      TEXT,            -- user | amazon_fees_estimate
    updated_at                  TEXT NOT NULL,
    PRIMARY KEY (product_id, platform)
);

-- Genel ayarlar (anahtar/değer)
CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

-- Uyum/kısıt kontrol maddeleri
CREATE TABLE IF NOT EXISTS compliance_checks (
    id          INTEGER PRIMARY KEY,
    product_id  INTEGER NOT NULL REFERENCES products(id),
    check_key   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'not_checked', -- not_checked | ok | risk | manual_review
    note        TEXT,
    updated_at  TEXT NOT NULL,
    UNIQUE (product_id, check_key)
);

-- Uyarılar; fingerprint ile aynı fırsat için tekrar bildirim engellenir
CREATE TABLE IF NOT EXISTS alerts (
    id           INTEGER PRIMARY KEY,
    product_id   INTEGER NOT NULL REFERENCES products(id),
    platform     TEXT NOT NULL,
    fingerprint  TEXT NOT NULL UNIQUE,
    message      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    emailed_at   TEXT,
    email_status TEXT,
    is_read      INTEGER NOT NULL DEFAULT 0
);
