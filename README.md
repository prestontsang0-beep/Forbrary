# Forbrary

Forbrary combines Forbes Dining Hall and library: a place to explore dining menus and plan meals.

A phone-first web app (installable PWA) for Milton Academy's dining halls: every dish on today's
Forbes / Elliott / Junior menu with calories, macros and a full nutrition label, plus a one-tap
optimal plate builder, a whole-day planner, favorites and saved plates.

## Design

Charcoal field with brass details. The home page opens on a full-bleed painted sky with the FORBRARY
wordmark sized by JavaScript to run edge to edge, then scrolls straight into the menu. The sky is John
Constable's "A Cloud Study, Sunset" (c. 1821, public domain), hot-linked from Wikimedia Commons with a
`srcset` so phones get a smaller file; it crossfades to the Forbes dining hall and then, when it has a photo, today's top dish
as you scroll, and drifts slowly like the video on the reference site. Along the cover's bottom edge a
ticker glides through tonight's top dishes with protein and calories (tap one to open its viewer), the
dish and station count, and a scroll cue; it pauses on hover. Three references shaped the
screens: that cover and the big type (Atlas Motion Systems);
flat color-block station tiles and colored station bands on the Menu screen, one stable color per station
(Menu Durable); and a numbered, horizontally scrolling image strip for the top picks, with
drag-to-scroll, arrow keys and a mouse wheel that moves it sideways until it reaches an end (Norte Studio).
It has no zoom slider, counter or arrow buttons: a plain swipe does the job.
On a computer there is no sidebar or docked plate: the app is one long page. The cover fills the first
screen edge to edge, then Menu, the quote band, the top-protein strip, the stats, Plate, Day and Goals follow
as you scroll; the section links fade into the top bar once the cover has scrolled by, and each link glides
(or jumps, if far) to its section. Station pages still open on their own. On phones the bottom tabs switch
between screens as before, and the cover is the first thing under the header. The Menu tab scrolls to the menu section of the home page rather than
opening a separate screen; the Today/Menu tab highlight follows the scroll position. The home page shows the
station tiles only; the full searchable dish list (with station, diet, favorite and allergen filters) lives under
"Add dishes" on the My plate tab, which "Build my plate" opens after the optimizer runs. Between the tiles
and the top-protein strip sits a dark quote band with animated light streaks ("Don't focus on how much you
eat. Focus on what you eat.") whose lines rise into view as you scroll. Each station tile
(All stations, Hot Bar, Salad Bar, …) opens its own page at `#station/<name>`: that station's dishes as
large tiles (photos pan toward the pointer; a dish without a photo is a plain card in the station's color),
with the + to plate on each. Tapping any tile there, a card in the top-protein strip, or a thumbnail in the
list opens the dish viewer: big photo or plain card (tap a photo to zoom, swipe or arrow
keys for the next dish), calories and macros, plate servings, favorite, serving guide and the nutrition label.
On every dish card (top-protein strip, station pages, the dish list, plate rows, day plans and the viewer)
protein and calories are the largest text, through one helper, `dishNums()`. A dish without a sure photo
gets a plain card in its station's color instead of a stand-in picture. An About footer at the end of the
page says where the data comes from, how photos are chosen, that portions are estimates, and who built it. Type: Archivo for the big headings, Josefin Sans for labels, Jost for body. The Plate, Day and Goals
screens switch to a second scheme modeled on a car-launch page: near-black with slow red and blue light
streaks behind the content, white hairlines and panels, white block buttons with arrows, a small-caps
overline above a condensed all-caps headline. It is done with scoped CSS variables on those screens (the
`sv` class) plus an `sv-mode` class on the shell, so Today keeps the charcoal-and-brass look.

## Run

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # once
.venv/bin/python server.py
```

Open http://localhost:8000. The only dependency is `pywebpush` (for notifications); without it the
server still runs and the app just reports that alerts aren't set up.
(Nutrislice blocks direct browser requests, so `server.py` fetches the menu for the page.)

To use it on your phone over the same Wi-Fi, run `HOST=0.0.0.0 python3 server.py` and open
`http://<your Mac's IP>:8000` on the phone. Then "Add to Home Screen" (Safari: Share → Add to
Home Screen; Chrome: menu → Install app) to get it as a full-screen app with its own icon.
Note: Chrome only offers the install prompt over HTTPS or localhost; iOS Safari adds to the
home screen from any address.

## Layout

- `static/index.html` — the whole app: Today, Menu, Plate, Day, Goals tabs.
- `static/manifest.webmanifest`, `static/sw.js`, `static/icons/` — PWA bits (installable, offline shell,
  last menu cached).
- `server.py` — proxies and normalizes the Nutrislice menu, sends Web Push notifications (meal digests at the
  times each subscriber picked, with their favorites flagged; subscriptions in `push-subs.json`, VAPID keys
  generated into `vapid.json` on first run), flags bulk-pan "servings", and attaches a
  photo only when it matches the whole dish: one of your own in `static/photos/` (mostly real Forbes plates), else the Wikipedia
  article image when the article title is the whole dish name (`WIKI_SPECIFIC` for a few curated names),
  else a TheMealDB ingredient cut-out when the ingredient is the whole dish (`PHOTO_RULES`). Partial matches
  (a roast chicken for "Chicken Broccoli Alfredo"), sauces and toppings (`NO_PHOTO_RE`) and names that are a
  bare protein such as "Grilled Chicken" (`BARE_PROTEIN`) get no photo. Every word of a Wikipedia title must
  appear in the dish name, so "Chopped cheese" can't stand in for "Grilled Cheese". The page
  shows a plain station-colored card. Results are cached in `photo-cache.json`, and the coming week's
  menus are resolved in the background at startup. To override a photo, drop your own image into
  `static/photos/` named after the dish (`sliced-chicken-breast.jpg`), or add a rule to `LOCAL_PHOTO_RULES`.
- `archive/` — earlier versions (dashboard, landing pages, carousel/TextPressure ports). Not served.

## How the optimizer works

- Goals: daily calories and protein/carbs/fat, typed in or calculated from body stats
  (Mifflin-St Jeor × activity). Each meal gets a share of the day (breakfast 25%, lunch 35%,
  dinner 40% by default, adjustable).
- It picks dishes in half-serving steps to get as close as possible to the meal target. Falling
  short on protein counts against a plate more than going over. Sodium is capped, fiber earns a
  small bonus, and it can require a vegetable.
- Diet and allergen filters apply. Garnishes, sauces/dressings and bulk-pan items are skipped.
- Lock (🔒) a dish on your plate and the optimizer builds around it.
