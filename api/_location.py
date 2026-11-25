"""Does the page say where the business is?

An AI tool that reads a page as text cannot place a business that never says where it is.
It may treat the business as unrelated to a local question, or put it in the wrong city.
This module measures that from the page itself: what the visible text says (what most AI
tools read) and what the hidden structured data (JSON-LD) says (what mostly search engines read).

Pure functions, no network. Works for Hebrew and English pages first, and for the common
street formats of other languages. A place name that is not on our lists is still found when
the page prints a street address, a label such as "Address:" or an <address> block.
"""
from __future__ import annotations

import re
from urllib.parse import unquote

from bs4 import BeautifulSoup

MAX_NODES = 400
MAX_BODY_CHARS = 80_000  # the place-name scan reads the first 60k and the last 20k characters


def _clean(s) -> str:
    return re.sub(r"\s+", " ", str(s if s is not None else "")).strip()


def _as_list(v) -> list:
    return v if isinstance(v, list) else [] if v is None else [v]


# ---------------------------------------------------------------------------
# Place names. Short lists on purpose: towns where small businesses are, and big cities.
# ---------------------------------------------------------------------------
PLACES_HE = """
תל אביב|תל אביב יפו|יפו|ירושלים|חיפה|באר שבע|ראשון לציון|פתח תקווה|פתח תקוה|אשדוד|נתניה|בני ברק|חולון|רמת גן|בת ים|אשקלון
רחובות|הרצליה|הרצליה פיתוח|כפר סבא|חדרה|מודיעין|מודיעין מכבים רעות|מודיעין עילית|נצרת|נצרת עילית|נוף הגליל|לוד|רמלה|רעננה
נס ציונה|קריית גת|קרית גת|קריית שמונה|קרית שמונה|אילת|עפולה|נהריה|עכו|כרמיאל|טבריה|צפת|אור יהודה|יהוד|יהוד מונוסון|גבעתיים
רמת השרון|הוד השרון|כפר יונה|אור עקיבא|זכרון יעקב|זיכרון יעקב|פרדס חנה|פרדס חנה כרכור|כרכור|בנימינה|גבעת עדה|קיסריה
יקנעם|יקנעם עילית|מגדל העמק|בית שאן|קצרין|מעלות תרשיחא|טירת כרמל|טירת הכרמל|קריית ים|קרית ים|קריית ביאליק|קרית ביאליק
קריית מוצקין|קרית מוצקין|קריית אתא|קרית אתא|קריית טבעון|קרית טבעון|טבעון|קריות|דלית אל כרמל|עוספיא|קדימה צורן
מזכרת בתיה|גדרה|קריית מלאכי|קרית מלאכי|נתיבות|אופקים|דימונה|ירוחם|ערד|מצפה רמון|באר יעקב|ראש העין
ראש פינה|כפר תבור|בית שמש|מבשרת ציון|מעלה אדומים|גבעת זאב|ביתר עילית|גני תקווה|גן שמואל|אבן יהודה|תל מונד
קלנסווה|טייבה|כפר קאסם|באקה אל גרבייה|אום אל פחם|סכנין|שפרעם|עראבה|כפר כנא|יפיע|רהט|תל שבע|כסייפה|סביון|כפר שמריהו
בית דגן|באר טוביה|קריית עקרון|קרית עקרון|גן חיים|כפר ורדים|קריית אונו|קרית אונו|גבעת שמואל|כוכב יאיר|צור יגאל
כפר חב"ד|נס עמים|חצור הגלילית|ראש הנקרה|בית ג'ן|מג'דל שמס|עין גדי|מצדה|ים המלח|הערבה|הגולן|רמת הגולן|הגליל|הגליל העליון
הגליל התחתון|הנגב|השרון|אזור השרון|השפלה|שפלת יהודה|עמק יזרעאל|עמק חפר|עמק הירדן|עמק האלה|עמק בית שאן|גוש דן|מישור החוף
הכרמל|חוף הכרמל|גוש עציון|מטה יהודה|חבל מודיעין|מרכז הארץ|צפון הארץ|דרום הארץ|עמק זבולון|קריית חיים|קרית חיים|קריית שרת|הקריות
""".replace("\n", "|").split("|")
PLACES_HE = [p.strip() for p in PLACES_HE if p.strip()]

PLACES_EN = """
Tel Aviv|Tel Aviv Yafo|Jaffa|Yafo|Jerusalem|Haifa|Beer Sheva|Be'er Sheva|Beersheba|Rishon LeZion|Rishon Lezion|Rishon Le Zion
Petah Tikva|Petach Tikva|Petah Tikvah|Ashdod|Netanya|Bnei Brak|Holon|Ramat Gan|Bat Yam|Ashkelon|Rehovot|Herzliya|Kfar Saba|Hadera
Modi'in|Modiin|Nazareth|Lod|Ramla|Ramle|Ra'anana|Raanana|Nes Ziona|Kiryat Gat|Kiryat Shmona|Eilat|Afula|Nahariya|Akko|Karmiel
Tiberias|Safed|Tzfat|Givatayim|Ramat HaSharon|Ramat Hasharon|Hod HaSharon|Hod Hasharon|Kfar Yona|Zichron Yaakov|Zikhron Ya'akov
Pardes Hanna|Pardes Hanna Karkur|Pardes Hana|Karkur|Binyamina|Caesarea|Yokneam|Rosh HaAyin|Rosh Haayin|Beit Shemesh|Ma'ale Adumim
Maale Adumim|Mevaseret Zion|Dimona|Sderot|Kiryat Ono|Or Yehuda|Gedera|Savyon|Kfar Shmaryahu|Dead Sea
New York|New York City|Los Angeles|Chicago|Houston|Philadelphia|San Antonio|San Diego|Dallas|San Jose|Austin|Jacksonville|Fort Worth
San Francisco|Indianapolis|Seattle|Denver|Boston|El Paso|Nashville|Detroit|Oklahoma City|Portland|Las Vegas
Memphis|Louisville|Baltimore|Milwaukee|Albuquerque|Tucson|Fresno|Sacramento|Kansas City|Atlanta|Miami|Orlando|Tampa|Minneapolis
Cleveland|Pittsburgh|Cincinnati|St. Louis|Saint Louis|Brooklyn|Manhattan|Honolulu|Salt Lake City|New Orleans|Raleigh|Toronto
Montreal|Vancouver|Calgary|Ottawa|Edmonton|Winnipeg|London|Manchester|Birmingham|Leeds|Glasgow|Liverpool|Edinburgh|Bristol|Sheffield
Cardiff|Belfast|Newcastle|Nottingham|Leicester|Brighton|Oxford|Cambridge|Dublin|Sydney|Melbourne|Brisbane|Perth|Adelaide
Canberra|Auckland|Wellington|Paris|Lyon|Marseille|Berlin|Munich|Hamburg|Frankfurt|Cologne|Stuttgart|Madrid|Barcelona|Valencia
Lisbon|Porto|Rome|Milan|Naples|Turin|Florence|Venice|Amsterdam|Rotterdam|The Hague|Brussels|Antwerp|Vienna|Zurich|Geneva|Stockholm
Oslo|Copenhagen|Helsinki|Warsaw|Krakow|Prague|Budapest|Athens|Istanbul|Bucharest|Sofia|Kyiv|Kiev|Moscow|Tokyo|Osaka|Seoul|Beijing
Shanghai|Shenzhen|Hong Kong|Singapore|Bangkok|Kuala Lumpur|Jakarta|Manila|Mumbai|Delhi|New Delhi|Bangalore|Bengaluru|Hyderabad
Chennai|Kolkata|Dubai|Abu Dhabi|Riyadh|Doha|Cairo|Lagos|Nairobi|Johannesburg|Cape Town|Mexico City|Sao Paulo|São Paulo
Rio de Janeiro|Buenos Aires|Bogota|Bogotá|Santiago
""".replace("\n", "|").split("|")
PLACES_EN = [p.strip() for p in PLACES_EN if p.strip()]

COUNTRIES_HE = ["ישראל"]
COUNTRIES_EN = ["Israel", "United States", "USA", "United Kingdom", "UK", "Canada", "Australia", "Ireland", "New Zealand"]

_QUOTES = str.maketrans({"״": '"', "”": '"', "“": '"', "׳": "'", "’": "'", "‘": "'", "־": " ", "–": "-"})


def _norm(t: str) -> str:
    """Quotes and hyphens made plain, so "תל-אביב" and "Tel-Aviv" match the lists."""
    return t.translate(_QUOTES).replace("-", " ")


def _he_rx(names: list[str]) -> re.Pattern:
    alt = "|".join(re.escape(_norm(n)) for n in sorted(set(names), key=len, reverse=True))
    return re.compile(r"(?<![א-ת])[בהוכלמש]{0,3}(" + alt + r")(?![א-ת\"'])")


def _en_rx(names: list[str]) -> re.Pattern:
    alt = "|".join(re.escape(_norm(n)) for n in sorted(set(names), key=len, reverse=True))
    return re.compile(r"(?<![A-Za-z])(" + alt + r")(?![A-Za-z])")


_HE_PLACE = _he_rx(PLACES_HE)
_EN_PLACE = _en_rx(PLACES_EN)
_HE_COUNTRY = _he_rx(COUNTRIES_HE)
_EN_COUNTRY = _en_rx(COUNTRIES_EN)
_HEB = re.compile(r"[א-ת]")
_LAT = re.compile(r"[A-Z]")


def _scan(rx_he: re.Pattern, rx_en: re.Pattern, text: str, limit: int) -> list[str]:
    t = _norm(text)
    found: list[str] = []
    for rx, ok in ((rx_he, bool(_HEB.search(t))), (rx_en, bool(_LAT.search(t)))):
        if not ok:
            continue
        for m in rx.finditer(t):
            n = m.group(1)
            if n not in found:
                found.append(n)
                if len(found) >= limit:
                    return found
    return found


def places(text: str, limit: int = 6) -> list[str]:
    """Towns and big cities named in the text."""
    return _scan(_HE_PLACE, _EN_PLACE, text, limit)


def countries(text: str, limit: int = 3) -> list[str]:
    return _scan(_HE_COUNTRY, _EN_COUNTRY, text, limit)


# ---------------------------------------------------------------------------
# What a page can print to say where it is
# ---------------------------------------------------------------------------
_STREET = [
    # Hebrew: street word, name, number
    re.compile(r"(?:רחוב|רח['\"׳’]?|שדרות|שד['׳’]|כיכר|סמטת)\s+[א-ת\"'׳’\- ]{2,28}?\s*\d{1,4}(?!\d)"),
    # English: 12 Main Street, 5 Rothschild Blvd
    re.compile(r"\b\d{1,5}[A-Za-z]?\s+(?:[A-Z][\w'’.-]*\s+){1,3}(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Way|Court|Ct|Place|Pl|Square|Sq|Highway|Hwy|Parkway|Pkwy|Terrace|Trail)\b\.?"),
    # English, number last: Herzl Street 5
    re.compile(r"\b(?:[A-Z][\w'’.-]*\s+){1,3}(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd)\.?,?\s+\d{1,4}\b"),
    # French
    re.compile(r"\b\d{1,4}\s?(?:bis|ter)?,?\s+(?:[Rr]ue|[Aa]venue|[Bb]oulevard|[Cc]hemin|[Ii]mpasse|[Aa]llée|[Qq]uai|[Rr]oute|[Pp]lace)\s+(?:d[eu]\s+|des\s+|de\s+la\s+|l['’])?[A-ZÀ-Ý][\wÀ-ÿ'’-]+"),
    # German and Dutch
    re.compile(r"\b[A-ZÄÖÜ][a-zäöüß-]+(?:straße|strasse|str\.|allee|platz|gasse)\s+\d{1,4}[a-z]?\b"),
    re.compile(r"\b[A-Z][a-z]+(?:straat|laan|gracht|singel|plein)\s+\d{1,4}[a-z]?\b"),
    # Spanish, Italian, Portuguese
    re.compile(r"\b(?:Calle|Avenida|Avda\.?|Paseo|Plaza|Via|Viale|Piazza|Corso|Rua|Travessa)\s+(?:d[eoai]s?\s+|del\s+|della\s+|dei\s+)?[A-ZÀ-Ý][\wÀ-ÿ'’ -]{1,30}?,?\s+(?:n[º°.]?\s*)?\d{1,4}\b"),
    # Russian
    re.compile(r"\bул(?:ица|\.)\s*[А-ЯЁ][а-яё.-]{2,25},?\s*(?:д\.?\s*)?\d{1,4}"),
]
_POBOX = re.compile(r"(?:\bP\.?\s?O\.?\s?Box\b|\bPost\s+Office\s+Box\b|תיבת\s+דואר|ת\.\s?ד\.?|ת[\"״]ד)\s*:?\s*\d{1,7}", re.I)
_POSTAL = [
    re.compile(r"(?:מיקוד|\bzip(?:\s*code)?|\bpostal\s*code|\bpostcode)\s*:?\s*[A-Z0-9][A-Z0-9 -]{2,9}\b", re.I),
    re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b"),
    re.compile(r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)*,?\s+(?:A[LKZR]|C[AOT]|D[EC]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|N[CDEHJMVY]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY])\s+\d{5}(?:-\d{4})?\b"),
]
_LABEL = re.compile(
    r"(?:כתובת|כתובתנו|מיקום|איפה\s+אנחנו|הוראות\s+הגעה"
    r"|(?i:address|our\s+address|location|find\s+us|visit\s+us|where\s+we\s+are|our\s+(?:store|shop|studio|clinic|office|showroom)))"
    r"\s*[:\-–]\s*(?P<v>[^\n]{3,90})")
_LOCATED = [
    re.compile(r"(?:ממוקמ(?:ים|ת|ות)?|שוכנ(?:ים|ת|ות)?)\s+(?:ב|ליד|בסמוך\s+ל|סמוך\s+ל|באזור\s+)\s?[א-ת]"),
    re.compile(r"(?i:\b(?:located|based|situated|headquartered)\s+(?:right\s+)?(?:in|at|near|on|just\s+outside|outside)\s+(?:the\s+)?)[A-Z][\w'’.-]+"),
    re.compile(r"(?i:\b(?:serving|serves)\s+(?:the\s+)?(?:greater\s+)?)[A-Z][a-z]+"),
]
_AREA = re.compile(
    r"בכל\s+רחבי\s+(?:הארץ|ישראל)|ברחבי\s+(?:הארץ|ישראל)|בכל\s+הארץ|לכל\s+הארץ|לכל\s+רחבי\s+(?:הארץ|ישראל)|פריסה\s+ארצית|שירות\s+ארצי"
    r"|משלוחים?\s+(?:לכל|ברחבי|בכל)\s+[א-ת]+|מגיעים\s+(?:אליכם|לכל|עד\s+אליכם)|אזורי?\s+(?:שירות|חלוקה|פעילות)|שטח\s+הארץ"
    r"|(?i:\bnationwide\b|\bcountrywide\b|\bwe\s+(?:deliver|ship|serve|travel)\s+(?:to|in|across|throughout|within|anywhere)\b"
    r"|\bareas?\s+we\s+serve\b|\bservice\s+areas?\b|\bdelivery\s+areas?\b|\bacross\s+the\s+(?:country|nation)\b)")

# Signs that this is a business with a place or a delivery area (a local business)
_HOURS = re.compile(
    r"שעות\s+(?:ה)?(?:פעילות|פתיחה|עבודה|קבלה)|(?i:\bopening\s+hours\b|\bstore\s+hours\b|\bhours\s+of\s+operation\b"
    r"|\bwe(?:'re|\s+are)\s+open\b|\bopen\s+(?:daily|7\s+days|mon\w*|sun\w*)\b)")
_VISIT = re.compile(
    r"סניף(?:ים|י)?|בואו\s+לבקר|קביעת\s+תור|קבעו\s+תור|תיאום\s+(?:פגישה|תור)|הזמנת\s+שולחן|חניה|הוראות\s+הגעה|איתור\s+סניף"
    r"|(?i:\bour\s+branches\b|\bbranch\s+locations?\b|\bfind\s+a\s+(?:store|branch|location)\b|\bstore\s+locator\b|\bvisit\s+us\b"
    r"|\bbook\s+(?:an\s+)?appointment\b|\breserve\s+a\s+table\b|\breservations?\b|\bour\s+(?:store|shop|showroom|studio|clinic)s?\b"
    r"|\bshowroom\b|\bget\s+directions\b|\bfree\s+parking\b|\bwalk-?ins?\b)")
_DELIVERY = re.compile(
    r"משלוח(?:ים)?|איסוף\s+עצמי|איסוף\s+מ(?:ה)?(?:חנות|סניף)|(?i:\b(?:free|same[- ]day|next[- ]day|home|local|fast|express)\s+(?:delivery|shipping)\b"
    r"|\bwe\s+deliver\b|\bdelivery\s+(?:area|zone|fee|options?|available|within)\b|\bpick[- ]?up\b|\bcurbside\b|\bclick\s*(?:&|and)\s*collect\b)")
_CART = re.compile(
    r"הוסף(?:ה)?\s+ל(?:סל|עגלה)|לעגלה|לסל\s+הקניות|סל\s+קניות|עגלת\s+קניות|במלאי"
    r"|(?i:add\s+to\s+(?:cart|basket|bag)|shopping\s+cart|view\s+cart|in\s+stock|out\s+of\s+stock)")
_PHONE = re.compile(
    r"(?<!\d)(?:\+?972[-\s]?|0)(?:[23489]|5\d|7\d)[-\s]?\d{3}[-\s]?\d{4}(?!\d)|(?<!\d)1[-\s]?[57]00[-\s]?\d{3}[-\s]?\d{3}(?!\d)"
    r"|\+\d{1,3}[\s.-]?\(?\d{1,4}\)?[\s.-]?\d{3}[\s.-]?\d{3,4}|\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}")
_MAP_HOST = re.compile(r"google\.[a-z.]+/maps|maps\.google\.|goo\.gl/maps|maps\.app\.goo\.gl|g\.page/|waze\.com|openstreetmap\.org|maps\.apple\.com|bing\.com/maps|mapbox\.com|yandex\.[a-z]+/maps", re.I)
_CONTACT_LINK = re.compile(r"contact|צרו\s*קשר|צור\s*קשר|יצירת\s*קשר|about|אודות|מי\s*אנחנו|find-?us|location|branches|סניפים|איפה", re.I)
_ZONE_CLASS = re.compile(r"(?:^|[\s_-])(?:footer|contact|address|location|find-?us|branch|hours|colophon|site-?info|vcard|adr)(?:$|[\s_-])", re.I)
_OTHER_LABELS = re.compile(r"^\s*(?:טלפון|נייד|אימייל|דוא\"ל|מייל|שם|phone|tel|email|e-mail|name|message|הודעה)\b", re.I)


def _any(patterns, text: str) -> str | None:
    for rx in patterns:
        m = rx.search(text)
        if m:
            return _clean(m.group(0))[:60]
    return None


# ---------------------------------------------------------------------------
# Structured data
# ---------------------------------------------------------------------------
_LOCAL_TYPE_WORDS = """
localbusiness store restaurant foodestablishment cafeorcoffeeshop bakery barorpub fastfoodrestaurant icecreamshop winery brewery distillery
grocerystore supermarket convenience store liquorstore hardwarestore shoestore clothingstore jewelrystore bookstore florist furniturestore gardenstore
hobbyshop homegoodsstore mensclothingstore mobilephonestore musicstore petstore sportinggoodsstore toystore wholesalestore departmentstore electronicsstore
computerstore bikestore autopartsstore outletstore pawnshop tireshop movierentalstore officeequipmentstore shoppingcenter
automotivebusiness autobodyshop autodealer autorental autorepair autowash gasstation motorcycledealer motorcyclerepair childcare dentist drycleaningorlaundry
emergencyservice firestation hospital policestation employmentagency entertainmentbusiness amusementpark artgallery casino comedyclub movietheater nightclub
financialservice accountingservice bankorcreditunion insuranceagency governmentoffice postoffice healthandbeautybusiness beautysalon dayspa hairsalon healthclub
nailsalon tattooparlor homeandconstructionbusiness electrician generalcontractor hvacbusiness housepainter locksmith movingcompany plumber roofingcontractor
internetcafe legalservice attorney notary library lodgingbusiness bedandbreakfast campground hostel hotel motel resort skiresort vacationrental medicalbusiness
medicalclinic optician pharmacy physician physiotherapy psychiatric professionalservice realestateagent recyclingcenter selfstorage sportsactivitylocation
bowlingalley exercisegym golfcourse publicswimmingpool sportsclub stadiumorarena tenniscomplex touristinformationcenter travelagency museum touristattraction
educationalorganization school preschool collegeoruniversity placeofworship synagogue church mosque animalshelter
""".split()
_LOCAL_TYPES = frozenset(_LOCAL_TYPE_WORDS)
_ORG_TYPES = frozenset({"organization", "corporation", "ngo", "onlinebusiness", "onlinestore"})


def collect_nodes(obj, out: list[dict], depth: int = 0) -> None:
    """Flatten JSON-LD into a list of dict nodes, nested nodes included. Capped, so a huge page costs little."""
    if len(out) >= MAX_NODES or depth > 8:
        return
    if isinstance(obj, list):
        for x in obj:
            collect_nodes(x, out, depth + 1)
    elif isinstance(obj, dict):
        out.append(obj)
        for v in obj.values():
            if isinstance(v, (dict, list)):
                collect_nodes(v, out, depth + 1)


def _types(node: dict) -> list[str]:
    return [str(t) for t in _as_list(node.get("@type")) if t]


def _is_local_type(t: str) -> bool:
    k = t.lower()
    if k in ("onlinestore", "onlinebusiness"):  # sells online: not tied to a place
        return False
    return k in _LOCAL_TYPES or k.endswith(("store", "shop", "salon", "clinic", "restaurant", "hotel", "studio"))


def _address(a) -> dict:
    out: dict = {}
    if isinstance(a, str):
        s = _clean(a)
        if s:
            out["street"] = s
    elif isinstance(a, dict):
        def g(key: str) -> str:
            v = a.get(key)
            if isinstance(v, dict):
                v = v.get("name")
            return _clean(v) if v is not None else ""
        out = {"street": g("streetAddress"), "city": g("addressLocality"), "region": g("addressRegion"),
               "postal": g("postalCode"), "country": g("addressCountry")}
    return {k: v for k, v in out.items() if v}


def _geo(node: dict) -> str:
    """"ok" with real coordinates, "empty" when a geo block has none (a common plugin mistake), "none" when absent."""
    seen = False
    for g in _as_list(node.get("geo")):
        if not isinstance(g, dict):
            continue
        seen = True
        try:
            lat, lon = float(str(g.get("latitude")).replace(",", ".")), float(str(g.get("longitude")).replace(",", "."))
        except (TypeError, ValueError):
            continue
        if -90 <= lat <= 90 and -180 <= lon <= 180 and (lat or lon):
            return "ok"
    return "empty" if seen else "none"


def _names(v, out: list[str]) -> None:
    for x in _as_list(v):
        if isinstance(x, str):
            s = _clean(x)
            if s:
                out.append(s)
        elif isinstance(x, dict):
            n = x.get("name")
            if isinstance(n, str) and _clean(n):
                out.append(_clean(n))


def schema_facts(nodes: list[dict]) -> dict:
    """The business place as the hidden structured data tells it."""
    best, best_key = None, (-1, -1)
    local_type = None
    area: list[str] = []
    for n in nodes or []:
        types = _types(n)
        low = [t.lower() for t in types]
        if "person" in low:
            continue
        loc = next((t for t in types if _is_local_type(t)), None)
        if loc and not local_type:
            local_type = loc
        if "areaServed" in n:
            _names(n.get("areaServed"), area)
        addr = _address(n.get("address"))
        if not addr and isinstance(n.get("location"), (dict, list)):
            for p in _as_list(n.get("location")):
                if isinstance(p, dict) and _address(p.get("address")):
                    addr = _address(p.get("address"))
                    break
        if not (addr or loc):
            continue
        rank = 2 if loc else 1 if set(low) & _ORG_TYPES else 0
        key = (rank, len(addr))
        if key > best_key:
            best, best_key = (n, addr, loc or (types[0] if types else None)), key
    country_only = bool(area) and all(a in COUNTRIES_EN or a in COUNTRIES_HE or (len(a) == 2 and a.isupper()) for a in area)
    out = {"found": False, "type": local_type, "local_type": bool(local_type), "name": "", "street": "", "city": "", "region": "",
           "postal": "", "country": "", "has_address": False, "geo": "none", "area_served": area[:8], "area_country_only": country_only,
           "phone": "", "email": "", "hours": False}
    if best:
        n, addr, typ = best
        out.update(found=True, type=local_type or typ, name=_clean(n.get("name")) if isinstance(n.get("name"), str) else "",
                   geo=_geo(n), phone=_clean(n.get("telephone")) if n.get("telephone") else "",
                   email=_clean(n.get("email")) if n.get("email") else "",
                   hours=bool(n.get("openingHoursSpecification") or n.get("openingHours")))
        out.update({k: addr.get(k, "") for k in ("street", "city", "region", "postal", "country")})
        out["has_address"] = bool(addr.get("street") or addr.get("city") or addr.get("region"))
    elif not out["has_address"]:
        for n in nodes or []:  # a place for opening hours even when no address was found
            if n.get("openingHoursSpecification") or n.get("openingHours"):
                out["hours"] = True
                break
    return out


# ---------------------------------------------------------------------------
# The page text
# ---------------------------------------------------------------------------
def _zone_text(soup: BeautifulSoup) -> str:
    """Text of the header, footer, <address> and contact-like blocks: the places where a business prints its address."""
    seen: set[str] = set()
    parts: list[str] = []
    total = 0
    els = list(soup.find_all(["footer", "address", "header"]))
    els += [e for e in soup.find_all(True, attrs={"class": _ZONE_CLASS}) if e.name not in ("body", "html", "main")]
    els += [e for e in soup.find_all(True, attrs={"id": _ZONE_CLASS}) if e.name not in ("body", "html", "main")]
    for el in els[:80]:
        cap = 6000 if el.name in ("footer", "address") else 1500
        t = _clean(el.get_text(" "))[:cap]
        if len(t) < 3 or t in seen:
            continue
        seen.add(t)
        parts.append(t)
        total += len(t)
        if total > 40_000:
            break
    return " ".join(parts)


def _body_sample(md: str) -> str:
    if len(md) <= MAX_BODY_CHARS:
        return md
    return md[:60_000] + " " + md[-20_000:]


def _labelled(text: str) -> str | None:
    for m in _LABEL.finditer(text):
        v = m.group("v")
        if _OTHER_LABELS.match(v):
            continue
        if re.search(r"\d", v) or places(v, 1):
            return _clean(m.group(0))[:60]
    return None


def location_facts(soup: BeautifulSoup, md: str, head_text: str, nodes: list[dict]) -> dict:
    """Everything the report needs to know about where the page says the business is.

    Call it on the parsed page before scripts are removed. md is the page as an AI tool reads it (text plus links)."""
    schema = schema_facts(nodes)
    body = _body_sample(md or "")
    h1 = " ".join(_clean(h.get_text(" ")) for h in soup.find_all("h1")[:3])
    zone = _zone_text(soup)
    key = _clean(" ".join((head_text or "", h1, zone)))
    scan = key + " " + body

    # --- what the page prints
    ev: list[str] = []
    addr_tag = next((e for e in soup.find_all("address") if len(_clean(e.get_text(" "))) >= 6), None)
    if addr_tag is None:
        addr_tag = soup.find(attrs={"itemprop": re.compile(r"^(?:streetAddress|addressLocality|postalCode)$", re.I)})
        if addr_tag is not None and len(_clean(addr_tag.get_text(" "))) < 3:
            addr_tag = None
    if addr_tag is not None:
        ev.append("address block")
    street = _any(_STREET, scan)
    if street:
        ev.append("street address")
    if _POBOX.search(scan):
        ev.append("P.O. box")
    postal = _any(_POSTAL, scan)
    if postal:
        ev.append("postal code")
    label = _labelled(scan)
    if label:
        ev.append("address label")
    located = _any(_LOCATED, scan)
    if located:
        ev.append("located-in phrase")
    key_places = places(key)
    if key_places:
        ev.append("place in title, headings, header or footer")
    body_places = [p for p in places(body) if p not in key_places]
    area = _AREA.search(scan)
    area_hits = [_clean(area.group(0))[:40]] if area else []
    country = countries(scan)

    hrefs = [(a.get("href") or "") for a in soup.find_all("a", href=True)]
    map_link = any(_MAP_HOST.search(h) for h in hrefs)
    map_embed = any(_MAP_HOST.search((i.get("src") or i.get("data-src") or "")) for i in soup.find_all("iframe"))
    contact_link = any(_CONTACT_LINK.search(unquote(a.get("href") or "") + " " + _clean(a.get_text(" "))) for a in soup.find_all("a", href=True)[:400])
    has_phone = any(h.lower().startswith("tel:") for h in hrefs) or bool(_PHONE.search(scan))

    stated = bool(addr_tag is not None or street or _POBOX.search(scan) or postal or label or located or key_places)
    if stated:
        status = "ok"
    elif area_hits or country:
        status = "area"
    elif body_places or map_link or map_embed:
        status = "mention"
    elif schema["has_address"]:
        status = "hidden"
    else:
        status = "none"

    # --- is this a business that depends on a place or a delivery area?
    pts, sig = 0, []

    def add(name: str, w: int) -> None:
        nonlocal pts
        pts += w
        sig.append(name)

    if schema["local_type"]:
        add("local business data", 3)
    hours = schema["hours"] or bool(_HOURS.search(scan))
    if hours:
        add("opening hours", 2)
    visit = bool(_VISIT.search(scan))
    if visit:
        add("a place to visit", 1)
    if _DELIVERY.search(scan):
        add("delivery or pickup", 1)
    if has_phone:
        add("phone number", 1)
    if _CART.search(scan):
        add("online shop", 1)
    if map_link or map_embed:
        add("map", 1)
    if schema["local_type"] or (hours and pts >= 3) or pts >= 5:
        level = "strong"
    elif pts >= 3:
        level = "likely"
    else:
        level = "none"

    return {
        "local": {"level": level, "points": pts, "signals": sig},
        "visible": {
            "status": status,
            "evidence": ev,
            "places": (key_places + body_places)[:5],
            "area": area_hits + country[:2],
            "map": "link" if map_link else "embed" if map_embed else "",
            "contact_link": contact_link,
            "phone": has_phone,
        },
        "schema": schema,
    }
