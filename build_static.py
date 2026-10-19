"""Build the static edition of Forbrary for drag-and-drop hosting (Netlify, Vercel, GitHub Pages).

    python3 build_static.py            # writes dist/ and Forbrary-netlify.zip
    DAYS=10 python3 build_static.py    # more days of menus

The static edition is the same app, but the menus come from JSON files written here
(today and the next DAYS-1 days, every hall and meal, photos already resolved) instead of
the Python API, so no server is needed. Push notifications need the server and are off
in this edition. Re-run and re-upload to refresh the menus.
"""
import datetime
import html
import json
import os
import shutil
import sys
import urllib.error
import zipfile

os.environ.setdefault("NO_PREWARM", "1")  # don't spin up the server's background threads
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import server  # noqa: E402  (reuses the menu fetching, normalizing and photo lookup)

DAYS = int(os.environ.get("DAYS", "7"))
SITE = (os.environ.get("SITE_URL") or os.environ.get("URL", "")).rstrip("/")  # Netlify sets URL; or set SITE_URL yourself
STATIC = os.path.join(HERE, "static")
DIST = os.path.join(HERE, "dist")
ZIP = os.path.join(HERE, "Forbrary-netlify.zip")

SKIP = {"index.html.bak", ".DS_Store"}


def copy_site():
    if os.path.isdir(DIST):
        shutil.rmtree(DIST)
    shutil.copytree(STATIC, DIST, ignore=lambda d, names: [n for n in names if n in SKIP])
    # mark the page as the static edition
    p = os.path.join(DIST, "index.html")
    with open(p, encoding="utf-8") as f:
        page = f.read()
    page = page.replace('<html lang="en">', '<html lang="en" data-static>', 1)
    with open(p, "w", encoding="utf-8") as f:
        f.write(page)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def menu(school, meal, date):
    try:
        return server.get_menu(school, meal, date)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise


def build_data():
    schools = server.get_schools()
    write_json(os.path.join(DIST, "data", "schools.json"), {"schools": schools})
    today = datetime.date.today()
    built, days = {}, [(today + datetime.timedelta(days=i)).isoformat() for i in range(DAYS)]
    for date in days:
        for sch in schools:
            for m in sch["meals"]:
                slug, meal = sch["slug"], m["slug"]
                try:
                    items = menu(slug, meal, date)
                except Exception as e:  # network trouble: leave that meal out rather than fail the build
                    print(f"  ! {slug} {meal} {date}: {e}", flush=True)
                    items = []
                write_json(os.path.join(DIST, "data", slug, date, f"{meal}.json"), {"school": slug, "meal": meal, "date": date, "items": items})
                built[(slug, meal, date)] = items
                print(f"  {date} {sch['name']:<12} {meal:<10} {len(items):>3} dishes", flush=True)
    return schools, days, built


def crawler_page(schools, days, built):
    """A plain page search engines can read: today's menus, every hall and meal."""
    today = days[0]
    parts = [f"<h1>Forbrary · Milton Academy menus for {html.escape(datetime.date.fromisoformat(today).strftime('%A %d %B %Y'))}</h1>",
             '<p>Calories and macros for every dish, station by station. <a href="./">Open the app</a> to build a plate.</p>']
    for sch in schools:
        parts.append(f"<h2>{html.escape(sch['name'])} Dining Hall</h2>")
        for m in sch["meals"]:
            items = [i for i in built.get((sch["slug"], m["slug"], today), []) if i.get("hasNutrition")]
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
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Today's menus · Forbrary</title><meta name="description" content="Today's Milton Academy dining hall menus with calories and protein for every dish.">
<link rel="canonical" href="{html.escape(SITE + '/today.html' if SITE else 'today.html')}">
<style>body{{margin:0;padding:24px 16px 60px;max-width:760px;margin-inline:auto;background:#101113;color:#f2ece2;font:16px/1.5 -apple-system,Helvetica,Arial,sans-serif}}a{{color:#efb08a}}h1{{font-size:28px;line-height:1.15}}h2{{margin-top:36px;border-top:1px solid rgba(242,236,226,.16);padding-top:18px}}ul{{padding-left:20px}}li{{margin:4px 0}}</style>
</head><body>{''.join(parts)}<p style="margin-top:40px;color:#9b978e;font-size:13px">Nutrition data is what Milton Dining publishes to Nutrislice. Built {html.escape(datetime.datetime.now().strftime('%Y-%m-%d %H:%M'))}.</p></body></html>"""
    with open(os.path.join(DIST, "today.html"), "w", encoding="utf-8") as f:
        f.write(page)


def write_meta(days):
    base = SITE or ""
    today = days[0]
    with open(os.path.join(DIST, "robots.txt"), "w") as f:
        f.write("User-agent: *\nAllow: /\nDisallow: /data/\n" + (f"Sitemap: {base}/sitemap.xml\n" if base else ""))
    urls = [("/", "daily", "1.0"), ("/today.html", "daily", "0.8")]
    with open(os.path.join(DIST, "sitemap.xml"), "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        for path, freq, pri in urls:
            f.write(f"  <url><loc>{html.escape(base + path)}</loc><lastmod>{today}</lastmod><changefreq>{freq}</changefreq><priority>{pri}</priority></url>\n")
        f.write("</urlset>\n")
    with open(os.path.join(DIST, "_headers"), "w") as f:
        f.write("/index.html\n  Cache-Control: no-cache\n/sw.js\n  Cache-Control: no-cache\n/data/*\n  Cache-Control: public, max-age=600\n/*\n  X-Content-Type-Options: nosniff\n")


def zip_site():
    if os.path.exists(ZIP):
        os.remove(ZIP)
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(DIST):
            for name in files:
                full = os.path.join(root, name)
                z.write(full, os.path.relpath(full, DIST))
    return os.path.getsize(ZIP)


if __name__ == "__main__":
    print("Copying the app…", flush=True)
    copy_site()
    print(f"Fetching {DAYS} days of menus and photos…", flush=True)
    schools, days, built = build_data()
    crawler_page(schools, days, built)
    write_meta(days)
    size = zip_site()
    print(f"\nDone. Upload the folder dist/ or the file Forbrary-netlify.zip ({size // 1024} KB) to Netlify.")
    print(f"Menus cover {days[0]} to {days[-1]}; run this again to refresh.")
