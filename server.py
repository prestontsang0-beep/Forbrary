#!/usr/bin/env python3
"""Milton Academy dining menu + nutrition server.

Proxies Milton's Nutrislice menu API (which doesn't allow browser CORS
requests), normalizes the data, and serves the single-page front end.

    python3 server.py            # http://localhost:8000
    PORT=9000 python3 server.py

No third-party dependencies.
"""

import json
import os
import html
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

API = "https://milton.api.nutrislice.com/menu/api"
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
CACHE_TTL = 30 * 60  # seconds

SLUG_RE = re.compile(r"^[a-z0-9-]{1,40}$")
DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

# Nutrislice icon groups for Milton: 1813 = diet tags, 1154 = allergens.
DIET_GROUP = 1813
ALLERGEN_GROUP = 1154

_cache = {}
_cache_lock = threading.Lock()


def fetch_json(url):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(url)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "milton-menu/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    with _cache_lock:
        _cache[url] = (now, data)
    return data


def num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def serving_label(amount, unit):
    if not amount or not unit:
        return None
    a = num(amount)
    if a is None:
        return f"{amount} {unit}"
    if unit == "lbs":  # Nutrislice uses fractions of a pound; ounces read better
        a, unit = a * 16, "oz"
    elif unit == "ozw":
        unit = "oz"
    elif a < 0.1:  # e.g. "0.0156 cut" = one slice of a full sheet pan
        return "1 portion"
    fractions = {0.25: "¼", 0.5: "½", 0.75: "¾", 0.33: "⅓", 0.67: "⅔", 0.125: "⅛"}
    whole, frac = int(a), round(a - int(a), 2)
    if frac in fractions:
        text = (str(whole) if whole else "") + fractions[frac]
    else:
        text = f"{a:g}"
    return f"{text} {unit}"


def normalize_food(food):
    n = food.get("rounded_nutrition_info") or {}
    size = food.get("serving_size_info") or {}
    sizes = food.get("food_sizes") or []
    grams = num(sizes[0].get("grams")) if sizes else None
    diet, allergens = [], []
    for icon in (food.get("icons") or {}).get("food_icons") or []:
        name = icon.get("name")
        if not name:
            continue
        (allergens if icon.get("food_icon_group") == ALLERGEN_GROUP else diet).append(name)
    has_nutrition = bool(food.get("has_nutrition_info")) and num(n.get("calories")) is not None
    # Nutrislice sometimes lists a whole pan (a pound, a quart) as one "serving".
    amount, unit = num(size.get("serving_size_amount")) or 0, (size.get("serving_size_unit") or "").lower()
    bulk = (unit == "lbs" and amount >= 0.5) or unit in ("quarts", "gallons") or (unit in ("oz", "ozw") and amount >= 12) \
        or (grams is not None and grams > 450)
    # Raw amount + normalized unit so the app can turn "4 oz" into "one palm".
    unit_map = {"lbs": ("oz", 16), "ozw": ("oz", 1), "oz": ("oz", 1), "fl oz": ("floz", 1), "cups": ("cup", 1), "cup": ("cup", 1),
                "tbsp": ("tbsp", 1), "tsp": ("tsp", 1), "quarts": ("cup", 4), "pints": ("cup", 2), "gallons": ("cup", 16)}
    norm_unit, factor = unit_map.get(unit, (unit or None, 1))
    return {
        "bulk": bulk,
        "servingAmount": round(amount * factor, 3) if amount and norm_unit else None,
        "servingUnit": norm_unit,
        "id": food.get("id"),
        "name": (food.get("name") or "").strip(),
        "description": (food.get("description") or "").strip(),
        "category": food.get("food_category") or "",
        "image": food.get("image_url"),
        "serving": serving_label(size.get("serving_size_amount"), size.get("serving_size_unit")),
        "grams": grams,
        "hasNutrition": has_nutrition,
        "nutrition": {
            "calories": num(n.get("calories")),
            "protein": num(n.get("g_protein")),
            "carbs": num(n.get("g_carbs")),
            "fat": num(n.get("g_fat")),
            "satFat": num(n.get("g_saturated_fat")),
            "transFat": num(n.get("g_trans_fat")),
            "cholesterol": num(n.get("mg_cholesterol")),
            "sodium": num(n.get("mg_sodium")),
            "potassium": num(n.get("mg_potassium")),
            "fiber": num(n.get("g_fiber")),
            "sugar": num(n.get("g_sugar")),
            "addedSugar": num(n.get("g_added_sugar")),
            "calcium": num(n.get("mg_calcium")),
            "iron": num(n.get("mg_iron")),
            "vitaminC": num(n.get("mg_vitamin_c")),
            "vitaminD": num(n.get("mcg_vitamin_d")),
        },
        "diet": diet,
        "allergens": allergens,
        "ingredients": (food.get("ingredients") or "").strip(),
    }


def normalize_day(day):
    station_names = {}
    items, by_id = [], {}
    current = None
    for mi in day.get("menu_items") or []:
        if mi.get("is_station_header") or mi.get("is_section_title"):
            current = (mi.get("text") or "").strip() or current
            if mi.get("station_id") is not None and current:
                station_names[mi["station_id"]] = current
            continue
        food = mi.get("food")
        if not food:
            continue
        station = station_names.get(mi.get("station_id")) or current or "Other"
        fid = food.get("id")
        if fid in by_id:  # same dish served at several lines
            if station not in by_id[fid]["stations"]:
                by_id[fid]["stations"].append(station)
            continue
        item = normalize_food(food)
        item["stations"] = [station]
        item["featured"] = bool(mi.get("featured"))
        by_id[fid] = item
        items.append(item)
    return items


def get_menu(school, meal, date):
    y, m, d = DATE_RE.match(date).groups()
    url = f"{API}/weeks/school/{school}/menu-type/{meal}/{y}/{m}/{d}/"
    data = fetch_json(url)
    for day in data.get("days") or []:
        if day.get("date") == date:
            items = normalize_day(day)
            attach_photos(items)
            return items
    return []


# ---------------------------------------------------------------------------
# Food photos. Nutrislice publishes no pictures, so each dish gets a stock
# photo of the same kind of food from TheMealDB (free, no key): a plated dish
# from a name search or category, or a cut-out ingredient photo for sides.
# Picks are deterministic per dish name and cached to disk.
# ---------------------------------------------------------------------------
import concurrent.futures
import hashlib
from urllib.parse import quote

MEALDB = "https://www.themealdb.com"
PHOTO_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "photo-cache.json")

# (regex on dish name, kind, key). kind: meal = name search, cat = category, ing = ingredient photo.
PHOTO_RULES = [
    (r"burger|slider", "meal", "burger"), (r"pizza", "meal", "pizza"),
    (r"taco|nacho|enchilada|fajita|burrito|quesadilla|tamale", "meal", "taco"),
    (r"hot dog|sausage|bratwurst|kielbasa", "meal", "sausage"),
    (r"green bean|string bean", "ing", "Green Beans"), (r"snap pea|\bpeas?\b|edamame", "ing", "Peas"),
    (r"wing|drumstick|chicken|poultry", "meal", "chicken"), (r"turkey", "ing", "Turkey"),
    (r"meatball", "meal", "meatball"), (r"brisket", "ing", "Beef Brisket"),
    (r"steak|beef|pot roast|short rib", "meal", "beef"), (r"lamb", "meal", "lamb"),
    (r"bacon", "ing", "Bacon"), (r"\bham\b|prosciutto", "ing", "Ham"), (r"pork|pulled|carnitas", "meal", "pork"),
    (r"lobster", "ing", "Lobster"), (r"crab|scallop|mussel|clam|oyster|calamari", "cat", "Seafood"),
    (r"shrimp|scampi|prawn", "meal", "shrimp"), (r"sushi|maki", "meal", "sushi"), (r"salmon", "meal", "salmon"),
    (r"tuna", "ing", "Tuna"), (r"\bcod\b", "ing", "Cod"), (r"haddock", "ing", "Haddock"),
    (r"fish|tilapia|pollock|halibut|mahi", "meal", "fish"),
    (r"ramen|\bpho\b|udon|lo mein|noodle|miso", "meal", "noodle"),
    (r"soup|chowder|bisque|broth|stew|chili\b|gumbo", "meal", "soup"),
    (r"lasagn", "meal", "lasagna"), (r"\bmac\b|macaroni", "meal", "mac"), (r"alfredo", "meal", "alfredo"),
    (r"spaghetti|pasta|penne|ravioli|ziti|tortellini|linguine|fettuccine|gnocchi|carbonara|pesto|rigatoni|orzo|bolognese", "cat", "Pasta"),
    (r"fried rice", "meal", "fried rice"),
    (r"rice|risotto|arroz|pilaf|quinoa|couscous|grain|biryani|bulgur|farro", "meal", "rice"),
    (r"curry|masala|tikka|korma|\bdal\b|dahl", "meal", "curry"), (r"dumpling|gyoza|potsticker|wonton", "meal", "dumpling"),
    (r"teriyaki", "meal", "teriyaki"), (r"katsu|tempura", "meal", "katsu"),
    (r"paella|jambalaya|stir fry|stir-fry|skillet", "meal", "stir fry"),
    (r"falafel", "meal", "falafel"), (r"kebab|kabob|gyro|shawarma", "meal", "kebab"),
    (r"hard-cooked|hard cooked|boiled egg|deviled", "ing", "Eggs"), (r"omelet|frittata|quiche", "meal", "omelette"),
    (r"\beggs?\b|scramble", "meal", "egg"), (r"pancake|crepe", "meal", "pancake"), (r"waffle", "meal", "waffle"),
    (r"french toast", "meal", "toast"), (r"bagel|croissant|scone|danish|pastry|cinnamon roll", "cat", "Breakfast"),
    (r"muffin|cupcake", "ing", "Muffins"),
    (r"cookie|blondie|brownie|\bbar\b|cake|\bpie\b|cobbler|tart|crumble|pudding|custard|flan|ice cream|gelato|sundae|donut|doughnut|mousse|cheesecake|dessert", "cat", "Dessert"),
    (r"chocolate", "ing", "Chocolate"), (r"yogurt|parfait", "ing", "Greek Yogurt"), (r"granola|cereal|oat", "ing", "Oatmeal"),
    (r"sandwich|wrap|panini|\bsub\b|hoagie|grilled cheese|melt|\bblt\b", "meal", "sandwich"),
    (r"sweet potato|yam", "ing", "Sweet Potatoes"), (r"fries|tots|hash|potato", "meal", "potato"),
    (r"naan|pita", "ing", "Pita Bread"), (r"baguette|french bread", "ing", "Baguette"), (r"tortilla", "ing", "Tortilla"),
    (r"bread|roll|toast|biscuit|\bbun\b|focaccia|cornbread|crouton", "ing", "Bread"),
    (r"caesar|salad", "meal", "salad"), (r"arugula", "ing", "Rocket"),
    (r"lettuce|romaine|spring mix|greens|mesclun", "ing", "Lettuce"),
    (r"kale", "ing", "Kale"), (r"spinach", "ing", "Spinach"), (r"cabbage|slaw", "ing", "Cabbage"), (r"brussels", "ing", "Brussels Sprouts"),
    (r"broccoli", "ing", "Broccoli"), (r"cauliflower", "meal", "cauliflower"), (r"carrot", "ing", "Carrots"),
    (r"asparagus", "ing", "Asparagus"), (r"celery", "ing", "Celery"),
    (r"jalape|chili pepper|hot pepper|poblano", "ing", "Jalapeno"), (r"red pepper|roasted pepper", "ing", "Red Pepper"), (r"pepper", "ing", "Green Pepper"),
    (r"cherry tomato|grape tomato", "ing", "Cherry Tomatoes"), (r"tomato|marinara|salsa", "ing", "Tomatoes"),
    (r"corn", "ing", "Sweetcorn"), (r"scallion|green onion", "ing", "Spring Onions"), (r"red onion", "ing", "Red Onions"),
    (r"onion", "ing", "Onion"), (r"cucumber|\bpickles?\b", "ing", "Cucumber"), (r"zucchini|courgette", "ing", "Courgette"),
    (r"squash|butternut", "ing", "Butternut Squash"), (r"pumpkin", "ing", "Pumpkin"),
    (r"avocado|guac", "ing", "Avocado"), (r"mushroom", "ing", "Mushrooms"), (r"eggplant|aubergine", "ing", "Aubergine"),
    (r"beet", "ing", "Beetroot"), (r"radish", "ing", "Radish"),
    (r"scallion|green onion", "ing", "Spring Onions"), (r"red onion", "ing", "Red Onions"), (r"onion", "ing", "Onion"),
    (r"garlic", "ing", "Garlic"), (r"ginger", "ing", "Ginger"), (r"leek", "ing", "Leek"),
    (r"black bean", "ing", "Black Beans"), (r"pinto", "ing", "Pinto Beans"), (r"kidney", "ing", "Kidney Beans"),
    (r"cannellini|white bean", "ing", "Cannellini Beans"), (r"chickpea|garbanzo", "ing", "Chickpeas"), (r"hummus", "ing", "Hummus"),
    (r"lentil", "ing", "Lentils"), (r"tofu", "meal", "tofu"), (r"tempeh", "ing", "Tempeh"), (r"bean", "meal", "bean"),
    (r"feta", "ing", "Feta"), (r"mozzarella", "ing", "Mozzarella"), (r"parmesan", "ing", "Parmesan"),
    (r"cheddar", "ing", "Cheddar Cheese"), (r"cream cheese", "ing", "Cream Cheese"), (r"cheese|ricotta|queso", "ing", "Cheese"),
    (r"apple", "ing", "Apples"), (r"banana", "ing", "Banana"), (r"orange|clementine|mandarin", "ing", "Orange"),
    (r"lemon", "ing", "Lemon"), (r"lime", "ing", "Lime"), (r"blueberr", "ing", "Blueberries"), (r"raspberr", "ing", "Raspberries"),
    (r"strawberr|berr", "ing", "Strawberries"), (r"pineapple", "ing", "Pineapple"), (r"mango", "ing", "Mango"),
    (r"melon|grape|pear|peach|cherr|kiwi|fruit|plum", "meal", "fruit"),
    (r"smoothie|shake", "meal", "smoothie"), (r"milk", "ing", "Milk"), (r"coffee|latte|espresso|cocoa", "ing", "Coffee"),
    (r"\btea\b", "ing", "Tea"), (r"juice|lemonade", "ing", "Orange Juice"),
    (r"peanut butter", "ing", "Peanut Butter"), (r"honey", "ing", "Honey"), (r"maple|syrup", "ing", "Maple Syrup"), (r"butter", "ing", "Butter"),
    (r"hot sauce|sriracha|buffalo", "ing", "Hot Sauce"), (r"mayo", "ing", "Mayonnaise"), (r"ranch|crema|sour cream", "ing", "Sour Cream"),
    (r"mustard", "ing", "Mustard"), (r"soy sauce|teriyaki sauce", "ing", "Soy Sauce"), (r"tahini", "ing", "Tahini"), (r"\bjam\b|jelly|preserve", "ing", "Jam"),
    (r"sauce|dressing|gravy|vinaigrette|ketchup|glaze|relish|chutney|\bdip\b|aioli", "ing", "Olive Oil"),
    (r"almond", "ing", "Almonds"), (r"peanut", "ing", "Peanuts"), (r"walnut", "ing", "Walnuts"), (r"pecan", "ing", "Pecan Nuts"),
    (r"cashew", "ing", "Cashew Nuts"), (r"\bnuts?\b|seed", "ing", "Almonds"),
    (r"cilantro", "ing", "Cilantro"), (r"parsley", "ing", "Parsley"), (r"basil", "ing", "Basil"), (r"mint", "ing", "Mint"),
    (r"herb|thyme|rosemary|dill|chive", "ing", "Thyme"),
    (r"vegetable|veggie|medley", "cat", "Side"),
]
PHOTO_RULES = [(re.compile(p, re.I), k, key) for p, k, key in PHOTO_RULES]
CATEGORY_FALLBACK = {"side": ("cat", "Side"), "salad": ("meal", "salad"), "dessert": ("cat", "Dessert"), "bread": ("ing", "Bread"),
                     "soup": ("meal", "soup"), "entree": ("cat", "Miscellaneous"), "sandwich": ("meal", "sandwich"),
                     "pizza": ("meal", "pizza"), "appetizer": ("cat", "Starter"), "sauce_grvy": ("ing", "Olive Oil"), "condiment": ("ing", "Olive Oil")}

_photo_lists = {}
_photo_lock = threading.Lock()
try:
    with open(PHOTO_CACHE_FILE) as f:
        _photo_lists = json.load(f)
except Exception:
    _photo_lists = {}


def _save_photo_cache():
    try:
        with open(PHOTO_CACHE_FILE, "w") as f:
            json.dump(_photo_lists, f)
    except Exception:
        pass


def _lookup_photos(kind, key):
    """Return a list of image URLs for a (kind, key); empty list if none."""
    try:
        if kind == "ing":
            for suffix in ("-Small", ""):  # not every ingredient has the small variant
                url = f"{MEALDB}/images/ingredients/{quote(key)}{suffix}.png"
                req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "milton-menu/1.0"})
                try:
                    with urllib.request.urlopen(req, timeout=10):
                        return [url]
                except urllib.error.HTTPError:
                    continue
            return []
        path = f"/api/json/v1/1/search.php?s={quote(key)}" if kind == "meal" else f"/api/json/v1/1/filter.php?c={quote(key)}"
        req = urllib.request.Request(MEALDB + path, headers={"User-Agent": "milton-menu/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [m["strMealThumb"] for m in (data.get("meals") or []) if m.get("strMealThumb")]
    except Exception:
        return None  # network error: don't cache


# ---- Wikipedia article images: plain, encyclopedic photos of the food itself ----
WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_UA = {"User-Agent": "MiltonMenu/1.0 (student project; " + os.environ.get("PUSH_CONTACT", "mailto:milton-menu@example.com") + ")"}
WIKI_STOP = {"with", "and", "the", "of", "in", "on", "sliced", "chopped", "fresh", "mixed", "roasted", "grilled", "steamed", "sauteed",
             "sautéed", "baked", "fried", "crispy", "seared", "blanched", "shredded", "diced", "whole", "style", "house", "homemade",
             "classic", "nonfat", "low", "fat", "free", "hot", "cold", "garlic", "herb", "dijon", "smoky", "old", "bay", "best", "day",
             "catch", "red", "sheet", "crinkle", "elbow", "yellow", "summer", "asiago", "plain", "mini", "jumbo", "large", "small"}
WIKI_GENERIC = {"food", "dish", "dishe", "cuisine", "as"}
WIKI_ALIASES = [(r"chick'?n", "chicken"), (r"\bmac\b", "macaroni"), (r"\bbbq\b", "barbecue"), (r"\bveggie\b", "vegetable"),
                (r"\bpb\b", "peanut butter"), (r"\b\d+x\d+\b|®|™|\(.*?\)", " ")]
WIKI_FOODISH = re.compile(r"cuisine|dish|food|cheese|vegetable|fruit|meat|pasta|bread|dessert|soup|salad|sauce|drink|beverage|cooking|"
                          r"snack|seafood|poultry|egg|noodle|rice|bean|legume|nut|spice|condiment|yogurt|dairy|pizza|sandwich|cake|cookie|"
                          r"candy|breakfast|squash|potato|mushroom|fish|pork|beef|chicken|lamb|sausage|pastr|grain|cereal|tofu|soy", re.I)
WIKI_NOT_FOOD = re.compile(r"compan|brand|corporation|manufacturer|chain|people|places|anatomy|organ|species|genus|album|song|film|band|"
                           r"airport|airstrip|aviation|geograph|river|mountain|town|city|village|military|ship|vehicle", re.I)
WIKI_BAD_FILE = re.compile(r"\.svg|diagram|map|chart|logo|icon|topo|cut_|_cuts|scheme|drawing|illustration", re.I)
# Articles about the animal or the plant, where we want the one about it as food.
WIKI_TITLE_OVERRIDES = {"Chicken": "Roast chicken", "Turkey (bird)": "Turkey as food", "Fish": "Fish as food", "Tuna": "Tuna salad",
                        "Salmon": "Salmon as food", "Shrimp": "Shrimp and prawn as food", "Egg": "Egg as food", "Mushroom": "Edible mushroom",
                        "Cattle": "Beef", "Pig": "Pork", "Sheep": "Lamb and mutton", "Lamb": "Lamb and mutton", "Duck": "Duck as food",
                        "Crab": "Crab meat", "Squid": "Squid as food", "Cod": "Cod as food", "Goat": "Goat meat", "Rice": "Cooked rice",
                        "Portobello": "Agaricus bisporus", "Wheat": "Bread", "Soybean": "Tofu", "Potato": "Potato"}
# Names where the generic search lands somewhere unappetizing; checked first.
WIKI_SPECIFIC = [
    (r"chick\w* (strips?|tenders?|fingers?)|chicken strip", "Chicken fingers"), (r"chicken breast|sliced chicken|grilled chicken|chicken cutlet", "Roast chicken"),
    (r"roast(ed)? chicken|chicken thigh|chicken leg", "Roast chicken"), (r"^tuna$|tuna salad|tuna fish", "Tuna salad"), (r"turkey breast|sliced turkey|deli turkey", "Turkey as food"),
    (r"hard[- ]cooked egg|hard[- ]boiled egg|boiled egg", "Boiled egg"), (r"scrambled egg", "Scrambled eggs"), (r"flank steak", "Steak"),
    (r"portobello|portabella", "Agaricus bisporus"), (r"mac(aroni)? (&|and|n) cheese", "Macaroni and cheese"),
]
# When the dish name itself finds nothing, fall back to the article for its main food.
WIKI_KEYWORD_TITLES = [
    (r"broccoli rabe|rapini", "Rapini"), (r"catch of the day|\bfish\b|tilapia|cod\b|haddock|pollock", "Fish as food"), (r"salmon", "Salmon as food"),
    (r"tuna", "Tuna salad"), (r"shrimp|prawn|scampi", "Shrimp and prawn as food"), (r"wing", "Buffalo wing"), (r"chicken|poultry|drumstick", "Roast chicken"),
    (r"turkey", "Turkey as food"), (r"beef|steak|brisket|meatball", "Beef"), (r"pork|bacon|\bham\b|sausage|kielbasa", "Pork"), (r"lamb", "Lamb and mutton"),
    (r"\beggs?\b|omelet|frittata", "Egg as food"), (r"tofu|tempeh", "Tofu"), (r"shells|pasta|penne|ziti|ravioli|spaghetti|linguine|fettuccine|rigatoni|orzo", "Pasta"),
    (r"noodle|ramen|udon", "Noodle"), (r"\brice\b|risotto|pilaf", "Cooked rice"), (r"quinoa", "Quinoa"), (r"couscous", "Couscous"),
    (r"pizza", "Pizza"), (r"burger", "Hamburger"), (r"sandwich|panini|sub\b|hoagie|wrap", "Sandwich"), (r"taco|burrito|quesadilla", "Taco"),
    (r"salad|greens|lettuce|romaine|spring mix", "Salad"), (r"soup|chowder|bisque|stew|broth", "Soup"), (r"curry|masala|tikka", "Curry"),
    (r"fries|tots", "French fries"), (r"sweet potato|yam", "Sweet potato"), (r"potato|hash", "Potato"), (r"bread|roll|toast|biscuit|bun|pita|naan|focaccia|breadstick", "Bread"),
    (r"cheese", "Cheese"), (r"yogurt|parfait", "Yogurt"), (r"oat|granola|cereal", "Oatmeal"), (r"pepper", "Bell pepper"), (r"squash|zucchini", "Summer squash"),
    (r"mushroom", "Edible mushroom"), (r"bean|lentil|chickpea|hummus", "Legume"), (r"corn", "Sweet corn"), (r"carrot", "Carrot"), (r"tomato|salsa|marinara", "Tomato"),
    (r"cucumber|pickle", "Cucumber"), (r"onion|scallion", "Onion"), (r"beet", "Beetroot"), (r"fruit|apple|banana|orange|berr|melon|grape|pineapple|mango|peach|pear", "Fruit"),
    (r"cookie|blondie|brownie|bar\b", "Cookie"), (r"cake|cupcake|muffin", "Cake"), (r"pie|cobbler|tart", "Pie"), (r"pudding|custard", "Pudding"), (r"ice cream", "Ice cream"),
    (r"sauce|dressing|gravy|crema|aioli|vinaigrette|mayo|mustard|ketchup", "Sauce"), (r"milk|smoothie", "Milk"), (r"juice|lemonade", "Juice"), (r"dessert|sweet", "Dessert"),
    (r"vegetable|veggie|medley|asparagus|cauliflower|cabbage|spinach|kale|green bean|peas", "Vegetable"),
]


def _wiki_norm(name):
    n = name
    for rx, rep in WIKI_ALIASES:
        n = re.sub(rx, rep, n, flags=re.I)
    return re.sub(r"\s+", " ", n).strip()


def _wiki_toks(s):
    return [re.sub(r"s$", "", w) for w in re.findall(r"[a-z']+", s.lower()) if w not in WIKI_STOP and len(w) > 2]


def _wiki_query(params):
    q = {"action": "query", "prop": "pageimages|pageprops|categories", "piprop": "thumbnail", "pithumbsize": 600,
         "ppprop": "disambiguation", "cllimit": 500, "clshow": "!hidden", "redirects": 1, "format": "json", **params}
    for attempt in range(3):
        try:
            req = urllib.request.Request(WIKI_API + "?" + urllib.parse.urlencode(q), headers=WIKI_UA)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return sorted((data.get("query", {}).get("pages", {}) or {}).values(), key=lambda p: p.get("index", 99))
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503):
                return None
        except Exception:
            return None
        time.sleep(1.5 * (attempt + 1))
    return None


def _wiki_is_food(page):
    cats = " ".join(c.get("title", "") for c in page.get("categories") or [])
    if not WIKI_FOODISH.search(cats):
        return False
    return not (WIKI_NOT_FOOD.search(cats) and len(WIKI_FOODISH.findall(cats)) < 2)


def _wiki_title_thumb(title):
    pages = _wiki_query({"titles": title})
    p = next((p for p in pages or [] if p.get("thumbnail")), None)
    return p["thumbnail"]["source"] if p else None


def _wiki_pick(pages, want):
    """Best page whose title is made of the query's own words (ignoring 'as food' etc.) and is about food."""
    best, best_ov = None, 0
    for p in pages or []:
        if "disambiguation" in (p.get("pageprops") or {}) or p["title"].startswith(("List of", "Category:")):
            continue
        thumb = (p.get("thumbnail") or {}).get("source")
        if not thumb:
            continue
        if p["title"] in WIKI_TITLE_OVERRIDES:
            tt = set(_wiki_toks(p["title"])) - WIKI_GENERIC
            if tt and tt <= set(want):
                t2 = _wiki_title_thumb(WIKI_TITLE_OVERRIDES[p["title"]])
                if t2 and not WIKI_BAD_FILE.search(t2):
                    return t2
            continue
        if WIKI_BAD_FILE.search(thumb):
            continue
        tt = set(_wiki_toks(p["title"])) - WIKI_GENERIC
        if not tt or not tt <= set(want) or not _wiki_is_food(p):
            continue
        ov = len(tt & set(want))
        if ov < len(want) and ov < 2 and len(want) > 2:
            continue  # a one-word hit on a long name is too generic; a shorter query will find a better one
        if ov > best_ov:
            best, best_ov = thumb, ov
    return best


def wiki_photo(name):
    """Plain Wikipedia image for a dish name, or None. Cached (misses too)."""
    n = _wiki_norm(name)
    ck = "wiki|" + n.lower()
    with _photo_lock:
        if ck in _photo_lists:
            return (_photo_lists[ck] or [None])[0]
    url, errored = None, False
    low = n.lower()
    for rx, title in WIKI_SPECIFIC:  # dish-level overrides win over the generic search
        if re.search(rx, low):
            url = _wiki_title_thumb(title)
            break
    words = n.split()
    for q in ([n] + [" ".join(words[i:]) for i in range(1, len(words))]) if not url else []:
        want = _wiki_toks(q)
        if not want:
            continue
        pages = _wiki_query({"generator": "search", "gsrsearch": q, "gsrlimit": 6, "gsrnamespace": 0})
        if pages is None:
            errored = True
            continue
        url = _wiki_pick(pages, want)
        if url:
            break
        time.sleep(0.05)
    if not url:
        for rx, title in WIKI_KEYWORD_TITLES:
            if re.search(rx, low):
                url = _wiki_title_thumb(title)
                errored = errored or url is None
                break
    with _photo_lock:
        if url or not errored:  # a transient failure must not become a permanent miss
            _photo_lists[ck] = [url] if url else []
            _save_photo_cache()
    return url


def _photo_choice(item):
    for rx, kind, key in PHOTO_RULES:
        if rx.search(item["name"]):
            return kind, key
    return CATEGORY_FALLBACK.get(item.get("category") or "")


# ---- Your own photos win: drop a file into static/photos named after the dish
# (e.g. "sliced-chicken-breast.jpg"), or add a rule below for a family of names.
PHOTO_DIR = os.path.join(STATIC_DIR, "photos")
LOCAL_PHOTO_RULES = [
    (r"chicken breast", "sliced-chicken-breast.png"),
    (r"catch of the day", "https://wildgameandfish.com/wp-content/uploads/2025/03/Baked-Redfish-on-the-Half-Shell-with-Maque-Choux-Recipe-by-Jeff-Benda.jpg"),
]


def _slug(name):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.lower())).strip("-")


def local_photo(name):
    try:
        files = os.listdir(PHOTO_DIR)
    except OSError:
        return None
    slug = _slug(name)
    for f in files:
        if os.path.splitext(f)[0] == slug:
            return "/photos/" + f
    low = name.lower()
    for rx, f in LOCAL_PHOTO_RULES:
        if re.search(rx, low) and (f.startswith("http") or f in files):
            return f if f.startswith("http") else "/photos/" + f
    return None


def attach_photos(items):
    # 0) A photo of your own in static/photos, if there is one.
    for it in items:
        it["photo"], it["photoKind"] = None, None
        lp = local_photo(it["name"])
        if lp:
            it["photo"], it["photoKind"] = lp, "local"
    items_left = [it for it in items if not it["photo"]]
    _attach_remote_photos(items_left)


def _attach_remote_photos(items):
    # 1) Plain Wikipedia photo of the dish itself (a few lookups at a time; cached after the first time).
    names = sorted({it["name"] for it in items if it.get("hasNutrition")})
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        wiki = dict(zip(names, ex.map(wiki_photo, names)))
    # 2) Anything still missing: the keyword/category photo sources below.
    wanted = {}
    for it in items:
        w = wiki.get(it["name"])
        if w:
            it["photo"], it["photoKind"] = w, "wiki"
            continue
        choice = _photo_choice(it)
        if choice:
            wanted[it["id"]] = choice
    keys = {f"{k}|{key}" for k, key in wanted.values()}
    fb = {f"{k}|{key}" for k, key in CATEGORY_FALLBACK.values()}
    with _photo_lock:
        missing = [k for k in keys | fb if k not in _photo_lists]
    if missing:
        started = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            results = list(ex.map(lambda k: (k, _lookup_photos(*k.split("|", 1))), missing))
        failed = [k for k, lst in results if lst is None]
        with _photo_lock:
            for k, lst in results:
                if lst is not None:
                    _photo_lists[k] = lst
            _save_photo_cache()
        print(f"photos: looked up {len(missing)} keys in {time.time() - started:.1f}s"
              + (f", failed: {failed}" if failed else ""), flush=True)
    for it in items:
        choice = wanted.get(it["id"])
        if not choice:
            continue
        kind, key = choice
        lst = _photo_lists.get(f"{kind}|{key}") or []
        if not lst:  # e.g. a name search with no hits: fall back to the item's category
            fallback = CATEGORY_FALLBACK.get(it.get("category") or "")
            if fallback:
                kind, key = fallback
                lst = _photo_lists.get(f"{kind}|{key}") or []
        if lst:
            n = int(hashlib.md5(it["name"].lower().encode()).hexdigest(), 16) % len(lst)
            it["photo"] = lst[n]
            it["photoKind"] = "ing" if kind == "ing" else "meal"


def get_schools():
    data = fetch_json(f"{API}/schools/")
    out = []
    for s in data:
        meals = [{"slug": m["slug"], "name": m["name"]} for m in s.get("active_menu_types") or []]
        if meals:
            out.append({"slug": s["slug"], "name": s["name"], "meals": meals})
    return out


# ---------------------------------------------------------------------------
# Push notifications (Web Push / VAPID). Needs `pywebpush` (see requirements.txt);
# without it the server runs normally and push endpoints report "unavailable".
# Subscriptions live in push-subs.json; each carries the person's alert times,
# dining hall and favorite dishes so the digest can be personal.
# ---------------------------------------------------------------------------
import base64
import datetime

DATA_DIR = os.environ.get("DATA_DIR") or os.path.dirname(os.path.abspath(__file__))  # a persistent disk on hosts that wipe the code dir
PUSH_FILE = os.path.join(DATA_DIR, "push-subs.json")
VAPID_FILE = os.path.join(DATA_DIR, "vapid.json")
PUSH_CONTACT = os.environ.get("PUSH_CONTACT", "mailto:milton-menu@example.com")
try:
    from pywebpush import webpush, WebPushException
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    PUSH_OK = True
except ImportError:
    PUSH_OK = False

_push_lock = threading.Lock()
_subs = {}
try:
    with open(PUSH_FILE) as f:
        _subs = json.load(f)
except Exception:
    _subs = {}


def _save_subs():
    try:
        with open(PUSH_FILE, "w") as f:
            json.dump(_subs, f)
    except Exception:
        pass


def _vapid():
    """Load or create the VAPID key pair. Returns (private_pem, public_key_b64url).
    On a host with no persistent disk (Render, Fly), set VAPID_PRIVATE_KEY (the PEM, newlines
    may be written as \\n) and VAPID_PUBLIC_KEY so the keys survive restarts."""
    env_priv, env_pub = os.environ.get("VAPID_PRIVATE_KEY"), os.environ.get("VAPID_PUBLIC_KEY")
    if env_priv and env_pub:
        return env_priv.replace("\\n", "\n").strip() + "\n", env_pub.strip()
    try:
        with open(VAPID_FILE) as f:
            d = json.load(f)
            return d["private"], d["public"]
    except Exception:
        pass
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    public = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    with open(VAPID_FILE, "w") as f:
        json.dump({"private": private, "public": public}, f)
    return private, public


VAPID_PRIVATE, VAPID_PUBLIC = _vapid() if PUSH_OK else (None, None)
if PUSH_OK:
    from py_vapid import Vapid
    VAPID_SIGNER = Vapid.from_pem(VAPID_PRIVATE.encode())  # pywebpush takes a Vapid object, not PEM text

DEFAULT_TIMES = {"breakfast": "07:00", "brunch": "10:00", "lunch": "11:00", "dinner": "16:30"}


def push_send(endpoint, payload):
    """Send one notification. Returns True on success; drops dead subscriptions."""
    entry = _subs.get(endpoint)
    if not entry or not PUSH_OK:
        return False
    try:
        webpush(subscription_info=entry["subscription"], data=json.dumps(payload), ttl=6 * 3600,
                vapid_private_key=VAPID_SIGNER, vapid_claims={"sub": PUSH_CONTACT})
        return True
    except WebPushException as e:
        code = getattr(getattr(e, "response", None), "status_code", None)
        if code in (404, 410):  # subscription expired or was revoked
            with _push_lock:
                _subs.pop(endpoint, None)
                _save_subs()
        print(f"push: failed for {endpoint[:40]}… ({code or e})", flush=True)
        return False
    except Exception as e:
        print(f"push: error {e}", flush=True)
        return False


def digest(school, meal, date, favorites=()):
    """Title/body for one meal's menu, or None if nothing is published."""
    try:
        items = get_menu(school, meal, date)
    except Exception:
        return None
    with_n = [i for i in items if i["hasNutrition"] and (i["nutrition"]["calories"] or 0) >= 15]
    if not with_n:
        return None
    hall = school.capitalize() if school != "elliot" else "Elliott"
    mains = [i for i in with_n if not i.get("bulk") and (i["nutrition"]["calories"] or 0) >= 60
             and not re.search(r"sauce|dressing|gravy|crema|syrup", i["name"], re.I)]
    mains.sort(key=lambda i: -(i["nutrition"]["protein"] or 0))
    favs = {f.lower() for f in favorites}
    hits = [i["name"] for i in with_n if i["name"].lower() in favs]
    parts = [f"{len(with_n)} dishes"]
    if mains:
        parts.append(f"top protein: {mains[0]['name']} ({round(mains[0]['nutrition']['protein'] or 0)}g)")
    body = " · ".join(parts)
    if hits:
        body = "Your favorites: " + ", ".join(hits[:3]) + (f" +{len(hits) - 3}" if len(hits) > 3 else "") + " · " + body
    when = {"breakfast": "this morning", "brunch": "this morning", "lunch": "today", "dinner": "tonight"}.get(meal, "today")
    return {"title": f"{meal.capitalize()} at {hall} {when}", "body": body, "tag": f"{school}-{meal}-{date}",
            "url": f"./#today", "favorites": hits}


_sent = set()


def _scheduler():
    """Once a minute: send each subscriber the digests due at this minute (local time)."""
    while True:
        try:
            now = datetime.datetime.now()
            hhmm, today = now.strftime("%H:%M"), now.strftime("%Y-%m-%d")
            with _push_lock:
                snapshot = list(_subs.items())
            for endpoint, entry in snapshot:
                prefs = entry.get("prefs") or {}
                for meal, t in (prefs.get("times") or {}).items():
                    if t != hhmm or (today, meal, endpoint) in _sent:
                        continue
                    _sent.add((today, meal, endpoint))
                    d = digest(entry.get("school") or "forbes", meal, today, prefs.get("favorites") or [])
                    if not d:
                        continue
                    if prefs.get("favoritesOnly") and not d["favorites"]:
                        continue
                    push_send(endpoint, d)
            if len(_sent) > 5000:
                _sent.clear()
        except Exception as e:
            print(f"push: scheduler error {e}", flush=True)
        time.sleep(20)


if PUSH_OK:
    threading.Thread(target=_scheduler, daemon=True).start()


def _prewarm():
    """Resolve menus and photos for the coming week in the background so first opens are instant."""
    time.sleep(3)
    try:
        schools = get_schools()
    except Exception:
        return
    today = datetime.date.today()
    for offset in range(0, 7):
        day = (today + datetime.timedelta(days=offset)).isoformat()
        for s in schools:
            for m in s["meals"]:
                try:
                    get_menu(s["slug"], m["slug"], day)
                except Exception:
                    pass
                time.sleep(1)
    print("photos: week prewarmed", flush=True)


if os.environ.get("NO_PREWARM") != "1":
    threading.Thread(target=_prewarm, daemon=True).start()


PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://www.forbrary.com")
_domain_ok = False


def _watch_domain():
    """Only redirect to the real domain once it actually answers (certificates take a while to issue)."""
    global _domain_ok
    while True:
        try:
            with urllib.request.urlopen(PUBLIC_URL + "/api/schools", timeout=10) as r:
                _domain_ok = r.status == 200
        except Exception:
            _domain_ok = False
        time.sleep(600 if _domain_ok else 60)


if os.environ.get("NO_PREWARM") != "1":
    threading.Thread(target=_watch_domain, daemon=True).start()


def menu_page(base):
    """Today's menus for every hall and meal as plain HTML (search engines read this)."""
    today = datetime.date.today()
    parts = [f"<h1>Forbrary · Milton Academy menus for {html.escape(today.strftime('%A %d %B %Y'))}</h1>",
             f'<p>Calories and macros for every dish, station by station. <a href="{html.escape(base)}/">Open the app</a> to build a plate.</p>']
    try:
        schools = get_schools()
    except Exception:
        schools = []
    for sch in schools:
        parts.append(f"<h2>{html.escape(sch['name'])} Dining Hall</h2>")
        for m in sch["meals"]:
            try:
                items = [i for i in get_menu(sch["slug"], m["slug"], today.isoformat()) if i.get("hasNutrition")]
            except Exception:
                items = []
            if not items:
                continue
            parts.append(f"<h3>{html.escape(m['name'])}</h3><ul>")
            station = None
            for it in items:
                st = (it.get("stations") or [""])[0]
                if st != station:
                    station = st
                    parts.append(f"<li><strong>{html.escape(st)}</strong></li>")
                n = it.get("nutrition") or {}
                parts.append(f"<li>{html.escape(it['name'])} — {round(n.get('calories') or 0)} kcal, {round(n.get('protein') or 0)} g protein"
                             + (f", {html.escape(it['serving'])}" if it.get("serving") else "") + "</li>")
            parts.append("</ul>")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Today's Milton Academy menus · Forbrary</title><meta name="description" content="Today's Milton Academy dining hall menus (Forbes, Elliott, Junior) with calories and protein for every dish.">
<link rel="canonical" href="{html.escape(base)}/today.html">
<style>body{{margin:0;padding:24px 16px 60px;max-width:760px;margin-inline:auto;background:#101113;color:#f2ece2;font:16px/1.5 -apple-system,Helvetica,Arial,sans-serif}}a{{color:#efb08a}}h1{{font-size:28px;line-height:1.15}}h2{{margin-top:36px;border-top:1px solid rgba(242,236,226,.16);padding-top:18px}}ul{{padding-left:20px}}li{{margin:4px 0}}</style>
</head><body>{''.join(parts)}<p style="margin-top:40px;color:#9b978e;font-size:13px">Nutrition data is what Milton Dining publishes to Nutrislice.</p></body></html>"""


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".webp": "image/webp", ".webmanifest": "application/manifest+json"}

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 64_000:
            return None
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return None

    def do_POST(self):
        url = urlparse(self.path)
        body = self.read_json()
        if not url.path.startswith("/api/push/"):
            return self.send_json(404, {"error": "not found"})
        if not PUSH_OK:
            return self.send_json(503, {"error": "push unavailable: install pywebpush (see requirements.txt)"})
        if url.path == "/api/push/subscribe":
            sub = (body or {}).get("subscription") or {}
            endpoint = sub.get("endpoint")
            if not endpoint or not isinstance(sub.get("keys"), dict):
                return self.send_json(400, {"error": "subscription with endpoint and keys required"})
            prefs = (body or {}).get("prefs") or {}
            times = {m: t for m, t in (prefs.get("times") or {}).items() if m in DEFAULT_TIMES and re.match(r"^\d{2}:\d{2}$", str(t))}
            with _push_lock:
                old = _subs.get(endpoint) or {}
                _subs[endpoint] = {
                    "subscription": sub,
                    "school": (body or {}).get("school") or old.get("school") or "forbes",
                    "prefs": {"times": times if "times" in prefs else (old.get("prefs") or {}).get("times", {}),
                              "favorites": [str(f)[:60] for f in (prefs.get("favorites") or (old.get("prefs") or {}).get("favorites") or [])][:200],
                              "favoritesOnly": bool(prefs.get("favoritesOnly", (old.get("prefs") or {}).get("favoritesOnly", False)))},
                    "updated": time.time(),
                }
                _save_subs()
            return self.send_json(200, {"ok": True, "count": len(_subs)})
        if url.path == "/api/push/unsubscribe":
            endpoint = (body or {}).get("endpoint")
            with _push_lock:
                existed = _subs.pop(endpoint, None) is not None
                _save_subs()
            return self.send_json(200, {"ok": True, "removed": existed})
        if url.path == "/api/push/test":
            endpoint = (body or {}).get("endpoint")
            entry = _subs.get(endpoint)
            if not entry:
                return self.send_json(404, {"error": "not subscribed"})
            prefs = entry.get("prefs") or {}
            today = datetime.date.today().isoformat()
            d = None
            for meal in ("dinner", "lunch", "brunch", "breakfast"):
                d = digest(entry.get("school") or "forbes", meal, today, prefs.get("favorites") or [])
                if d:
                    break
            d = d or {"title": "Forbrary", "body": "Notifications are working. Menu digests arrive at the times you picked.", "url": "./#goals"}
            d["title"] = "Test · " + d["title"]
            ok = push_send(endpoint, d)
            return self.send_json(200 if ok else 502, {"ok": ok, "sent": d})
        return self.send_json(404, {"error": "not found"})
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def send_app_page(self):
        """index.html with the canonical address and, if set, Google's site-verification tag."""
        with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
            page = f.read()
        base = self.site_url()
        extra = f'<link rel="canonical" href="{html.escape(base)}/">\n<meta property="og:url" content="{html.escape(base)}/">\n'
        token = os.environ.get("GOOGLE_SITE_VERIFICATION", "").strip()
        if token:
            extra += f'<meta name="google-site-verification" content="{html.escape(token)}">\n'
        page = page.replace("</head>", extra + "</head>", 1)
        return self.send_text(200, page, "text/html; charset=utf-8", cache="no-cache")

    def end_headers(self):
        # Always revalidate the page and scripts so edits show up on a normal refresh.
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        if not self.path.startswith("/api/"):
            return
        super().log_message(fmt, *args)

    def send_text(self, status, body, ctype, cache="public, max-age=600"):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)

    def site_url(self):
        """The public address for canonical links and the sitemap: SITE_URL, else the real domain
        when we are on it or on the Render host, else whatever host the request came in on."""
        env = os.environ.get("SITE_URL", "").rstrip("/")
        if env:
            return env
        host = self.headers.get("Host", "localhost")
        if host.endswith("forbrary.com") or host.endswith("onrender.com"):
            return PUBLIC_URL
        proto = self.headers.get("X-Forwarded-Proto", "http").split(",")[0].strip()
        return f"{proto}://{host}"

    def redirect_to_domain(self, path):
        """Pages requested on the Render address or the bare domain go to www.forbrary.com (one address for Google)."""
        host = self.headers.get("Host", "")
        if _domain_ok and (host.endswith("onrender.com") or host == "forbrary.com") and not path.startswith("/api/"):
            self.send_response(301)
            self.send_header("Location", PUBLIC_URL + self.path)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True
        return False

    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if self.redirect_to_domain(url.path):
            return
        if url.path in ("/", "/index.html"):
            return self.send_app_page()
        if url.path == "/sitemap.xml":
            base, today = self.site_url(), datetime.date.today().isoformat()
            urls = "".join(f"<url><loc>{html.escape(base + p)}</loc><lastmod>{today}</lastmod><changefreq>daily</changefreq><priority>{pr}</priority></url>"
                           for p, pr in (("/", "1.0"), ("/today.html", "0.8")))
            return self.send_text(200, f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>', "application/xml")
        if url.path == "/today.html":
            return self.send_text(200, menu_page(self.site_url()), "text/html; charset=utf-8")
        if not url.path.startswith("/api/"):
            return super().do_GET()
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path == "/api/schools":
                return self.send_json(200, {"schools": get_schools()})
            if url.path == "/api/push/key":
                return self.send_json(200, {"enabled": PUSH_OK, "publicKey": VAPID_PUBLIC, "defaults": DEFAULT_TIMES})
            if url.path == "/api/menu":
                school, meal, date = q.get("school", ""), q.get("meal", ""), q.get("date", "")
                if not (SLUG_RE.match(school) and SLUG_RE.match(meal) and DATE_RE.match(date)):
                    return self.send_json(400, {"error": "school, meal and date (YYYY-MM-DD) are required"})
                return self.send_json(200, {"school": school, "meal": meal, "date": date,
                                            "items": get_menu(school, meal, date)})
            return self.send_json(404, {"error": "not found"})
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return self.send_json(200, {"items": [], "note": "No menu published"})
            return self.send_json(502, {"error": f"Nutrislice returned {e.code}"})
        except Exception as e:  # network errors, bad JSON
            return self.send_json(502, {"error": f"Could not reach Nutrislice: {e}"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "127.0.0.1")  # HOST=0.0.0.0 to open it to your phone on the same Wi-Fi
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Forbrary running at http://localhost:{port}" + (f" (and on this Mac's IP, port {port})" if host == "0.0.0.0" else ""))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
