from app.matching import attribute_match, normalize_ean, normalize_size, same_ean


def test_valid_ean13():
    # GS1 / Wikipedia örnek EAN-13
    assert normalize_ean("4006381333931") == ("4006381333931", True)


def test_invalid_check_digit():
    ean, valid = normalize_ean("4006381333932")
    assert valid is False


def test_upc12_and_gtin14_match_same_product():
    # UPC-A 036000291452 = EAN-13 0036000291452 = GTIN-14 00036000291452
    assert normalize_ean("036000291452") == ("0036000291452", True)
    assert same_ean("036000291452", "00036000291452")
    assert same_ean("0036000291452", " 0036000291452 ")


def test_excel_scientific_notation_is_rejected():
    assert normalize_ean("8.71E+12")[1] is False
    assert normalize_ean("2,00E+12")[1] is False


def test_missing_ean():
    assert normalize_ean("") == (None, None)
    assert normalize_ean(None) == (None, None)


def test_different_eans_are_not_same():
    assert not same_ean("4006381333931", "0036000291452")
    assert not same_ean("4006381333931", "")


def test_size_normalization():
    assert normalize_size("0,5 L") == normalize_size("500 ml") == "500ml"
    assert normalize_size("1kg") == normalize_size("1000 g") == "1000g"
    assert normalize_size("75 cl") == "750ml"


def test_attribute_full_match_is_only_candidate():
    a = dict(brand="Kookfix", model="SP-3", variant="grijs", size="500 ml", pack_qty=1)
    b = dict(brand="KOOKFIX", model="sp 3", variant="Grijs", size="0,5 l", pack_qty=1)
    m = attribute_match(a, b)
    assert m.status == "candidate"  # asla otomatik "aynı ürün" değil
    assert any("onay" in r for r in m.reasons)


def test_pack_quantity_mismatch():
    a = dict(brand="Aquafles", model="RVS-750", pack_qty=1)
    b = dict(brand="Aquafles", model="RVS-750", pack_qty=2)
    assert attribute_match(a, b).status == "mismatch"


def test_variant_mismatch():
    a = dict(brand="Aquafles", model="RVS-750", variant="zwart")
    b = dict(brand="Aquafles", model="RVS-750", variant="wit")
    assert attribute_match(a, b).status == "mismatch"


def test_size_mismatch():
    a = dict(brand="Aquafles", model="RVS", size="500 ml")
    b = dict(brand="Aquafles", model="RVS", size="750 ml")
    assert attribute_match(a, b).status == "mismatch"


def test_missing_variant_is_flagged_uncertain():
    a = dict(brand="Kookfix", model="SP-3", variant="grijs")
    b = dict(brand="Kookfix", model="SP-3")
    m = attribute_match(a, b)
    assert m.status == "candidate"
    assert any("belirsiz" in r for r in m.reasons)


def test_missing_brand_is_insufficient():
    assert attribute_match(dict(model="X1"), dict(brand="A", model="X1")).status == "insufficient"
