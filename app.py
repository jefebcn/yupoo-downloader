import streamlit as st
import requests
import zipfile
import io
import time
import re
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

st.set_page_config(page_title="Yupoo Downloader", page_icon="📦", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@400;500;700&display=swap');
:root{--bg:#0d0d0d;--surf:#161616;--border:#2a2a2a;--acc:#e8ff47;--acc2:#47ffe8;--text:#f0f0f0;--muted:#888;}
html,body,.stApp{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;}
h1,h2,h3{font-family:'Space Mono',monospace;}
section[data-testid="stSidebar"]{background:var(--surf)!important;border-right:1px solid var(--border);}
.stButton>button{background:var(--acc)!important;color:#000!important;font-family:'Space Mono',monospace;font-weight:700;border:none;border-radius:2px;letter-spacing:.05em;padding:.5rem 1.4rem;}
.stDownloadButton>button{background:var(--acc2)!important;color:#000!important;font-family:'Space Mono',monospace;font-weight:700;border:none;border-radius:2px;padding:.6rem 1.4rem;width:100%;}
.stTextInput input{background:var(--surf)!important;border:1px solid var(--border)!important;color:var(--text)!important;border-radius:2px;font-size:1rem;}
.card{background:var(--surf);border:1px solid var(--border);border-radius:4px;padding:1.2rem;margin-bottom:.8rem;}
.card-title{font-family:'Space Mono',monospace;font-size:.7rem;letter-spacing:.12em;color:var(--acc);text-transform:uppercase;margin-bottom:.6rem;}
.album-row{display:flex;align-items:center;gap:.8rem;padding:.45rem .8rem;background:var(--surf);border:1px solid var(--border);border-radius:3px;margin-bottom:.25rem;font-size:.88rem;}
.badge{display:inline-block;background:var(--border);color:var(--text);font-family:'Space Mono',monospace;font-size:.65rem;padding:.2rem .5rem;border-radius:2px;margin:.1rem;}
.badge-acc{background:var(--acc);color:#000;}
.badge-acc2{background:var(--acc2);color:#000;}
</style>
""", unsafe_allow_html=True)

# ── CONSTANTS ────────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# ── HELPERS ──────────────────────────────────────────────────────────────────

def get_base(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"

def safe_name(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "_", name).strip()
    name = re.sub(r'\s+', ' ', name)
    return name[:60] or "album"

def fetch_page(url: str, retries=3) -> requests.Response | None:
    for i in range(retries):
        try:
            r = SESSION.get(url, timeout=20)
            if r.status_code == 200:
                return r
            time.sleep(1.5)
        except Exception:
            time.sleep(1.5)
    return None

def download_image(url: str, referer: str = "") -> bytes | None:
    try:
        hdrs = {**HEADERS, "Referer": referer or "https://www.yupoo.com/"}
        r = requests.get(url, headers=hdrs, timeout=20)
        if r.status_code == 200 and len(r.content) > 300:
            return r.content
    except Exception:
        pass
    return None

def extract_album_links(soup: BeautifulSoup, base: str) -> list[dict]:
    """Extract all album links and names from a parsed page."""
    albums = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Match /albums/DIGITS (with optional query string)
        if not re.match(r"^/albums/\d+", href):
            continue
        # Normalize: strip query string for dedup, keep for URL
        full_url = urljoin(base, href)
        clean_url = re.sub(r"\?.*$", "", full_url)
        if clean_url in seen:
            continue
        seen.add(clean_url)

        # Extract name - try multiple strategies
        name = ""
        # Strategy 1: look for title/name class inside the anchor
        for cls_pat in [r"title", r"name", r"album"]:
            el = a.find(class_=re.compile(cls_pat, re.I))
            if el:
                name = el.get_text(strip=True)
                break
        # Strategy 2: any text inside the anchor
        if not name:
            name = a.get_text(separator=" ", strip=True)
        # Strategy 3: fallback to ID
        if not name or len(name) < 2:
            m = re.search(r"/albums/(\d+)", href)
            name = m.group(1) if m else "album"

        # Clean up name (remove price/emoji noise, keep first meaningful part)
        name = re.sub(r"[¥$€£]\s*[\d~\-\s]+", "", name).strip()
        name = re.sub(r"[\U0001F300-\U0001FFFF]+", "", name).strip()  # remove emoji
        name = name[:60].strip() or re.search(r"/albums/(\d+)", href).group(1)

        albums.append({"name": name, "url": clean_url})

    return albums

def scrape_all_albums(start_url: str, log_fn, prog_fn) -> list[dict]:
    """Scrape all album links from a Yupoo category or albums page, paginating."""
    base = get_base(start_url)
    all_albums = []
    seen_urls = set()
    page = 1

    # Determine base listing URL (strip page param if present)
    listing_base = re.sub(r"[?&]page=\d+", "", start_url).rstrip("&?")

    while True:
        url = f"{listing_base}{'&' if '?' in listing_base else '?'}page={page}" if page > 1 else listing_base
        log_fn(f"📄 Scansione pagina {page}: `{url}`")

        r = fetch_page(url)
        if not r:
            log_fn(f"⚠️ Pagina {page} non risponde.")
            break

        soup = BeautifulSoup(r.text, "lxml")
        found = extract_album_links(soup, base)

        new = [a for a in found if a["url"] not in seen_urls]
        for a in new:
            seen_urls.add(a["url"])
            all_albums.append(a)

        log_fn(f"✅ Pagina {page}: +{len(new)} album → totale **{len(all_albums)}**")
        prog_fn(page, len(all_albums))

        if len(new) == 0:
            break

        # Check for more pages
        page_links = soup.find_all("a", href=re.compile(r"[?&]page=\d+"))
        nums = []
        for pl in page_links:
            m = re.search(r"page=(\d+)", pl["href"])
            if m:
                nums.append(int(m.group(1)))
        max_page = max(nums) if nums else page
        if page >= max_page:
            break
        page += 1
        time.sleep(0.4)

    return all_albums


def scrape_album_images(album_url: str) -> list[str]:
    """Extract all image URLs from inside a single album, across all pages."""
    images = []
    seen = set()
    page = 1
    base = get_base(album_url)

    while True:
        url = f"{album_url}?page={page}" if page > 1 else album_url
        r = fetch_page(url)
        if not r:
            break

        soup = BeautifulSoup(r.text, "lxml")
        found = 0

        for img in soup.find_all("img"):
            src = (img.get("src") or img.get("data-src") or
                   img.get("data-original") or img.get("data-lazy-src") or "")
            src = src.strip()
            if not src or src.startswith("data:"):
                continue
            # Only real product images (filter out icons/avatars)
            if not any(d in src for d in ["photo.yupoo", "img.yupoo", ".jpg", ".jpeg", ".png", ".webp"]):
                continue
            if "avatar" in src.lower() or "logo" in src.lower():
                continue
            # Strip query params to get full resolution
            clean = re.sub(r"\?.*$", "", src)
            if not clean.startswith("http"):
                clean = urljoin(base, clean)
            if clean not in seen:
                seen.add(clean)
                images.append(clean)
                found += 1

        if found == 0:
            break

        # Check pagination
        page_links = soup.find_all("a", href=re.compile(r"[?&]page=\d+"))
        nums = [int(re.search(r"page=(\d+)", pl["href"]).group(1))
                for pl in page_links if re.search(r"page=(\d+)", pl["href"])]
        max_p = max(nums) if nums else page
        if page >= max_p:
            break
        page += 1
        time.sleep(0.3)

    return images


# ── SESSION STATE ─────────────────────────────────────────────────────────────
for k, v in [("albums", []), ("selected", set()), ("zip_ready", None)]:
    if k not in st.session_state:
        st.session_state[k] = v

# ── UI HEADER ─────────────────────────────────────────────────────────────────
st.markdown("""
<h1 style='margin-bottom:.2rem;'>Yupoo <span style='color:#e8ff47;'>Downloader</span></h1>
<p style='color:#888;font-size:.9rem;margin-bottom:1.5rem;'>
Incolla l'URL di una categoria o pagina album · Scarica tutto organizzato per prodotto
</p>
""", unsafe_allow_html=True)

# ── STEP 1 — URL ──────────────────────────────────────────────────────────────
st.markdown("<div class='card'><div class='card-title'>① Inserisci URL della pagina Yupoo</div>",
            unsafe_allow_html=True)
url_input = st.text_input(
    "", placeholder="es: https://huskyreps.x.yupoo.com/categories/5097225",
    label_visibility="collapsed"
)
st.markdown("</div>", unsafe_allow_html=True)

if st.button("🔍 Scansiona prodotti"):
    if not url_input:
        st.error("Inserisci un URL.")
        st.stop()

    log    = st.empty()
    prog   = st.progress(0, text="Connessione…")
    counts = st.empty()

    # Test connessione
    log.info("🔌 Verifico connessione…")
    test = fetch_page(get_base(url_input))
    if not test:
        log.error("❌ Sito non raggiungibile. Controlla l'URL.")
        st.stop()
    log.success(f"✅ Connesso (HTTP {test.status_code}) — avvio scansione…")

    def upd_log(msg): log.markdown(msg)
    def upd_prog(p, tot):
        prog.progress(min(p / 30, 0.99), text=f"Pagina {p} — {tot} album trovati")
        counts.markdown(f"<span class='badge badge-acc'>{tot} album trovati finora</span>",
                        unsafe_allow_html=True)

    albums = scrape_all_albums(url_input, upd_log, upd_prog)
    prog.empty()
    counts.empty()

    if albums:
        st.session_state["albums"]   = albums
        st.session_state["selected"] = {a["url"] for a in albums}
        st.session_state["zip_ready"] = None
        log.success(f"✅ Trovati **{len(albums)} prodotti/album**!")
    else:
        log.error("❌ Nessun album trovato in questa pagina.")
        st.warning("""
**Possibili cause:**
- La pagina usa JavaScript per caricare i contenuti (comune su Yupoo)
- L'URL potrebbe essere protetto o aver bisogno di login

**Prova con:**
- L'URL diretto di una singola categoria diversa
- Oppure l'URL di un singolo album (es. `https://huskyreps.x.yupoo.com/albums/220875748`)
        """)

# ── STEP 2 — Selezione ────────────────────────────────────────────────────────
if st.session_state["albums"]:
    st.markdown("---")
    st.markdown("<div class='card-title'>② Seleziona i prodotti da scaricare</div>",
                unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1:
        if st.button("✅ Seleziona tutti"):
            st.session_state["selected"] = {a["url"] for a in st.session_state["albums"]}
            st.rerun()
    with c2:
        if st.button("❌ Deseleziona tutti"):
            st.session_state["selected"] = set()
            st.rerun()

    st.markdown(f"<span class='badge badge-acc'>{len(st.session_state['selected'])} selezionati</span>",
                unsafe_allow_html=True)

    # Checkboxes per album
    for album in st.session_state["albums"]:
        checked = album["url"] in st.session_state["selected"]
        col_chk, col_info = st.columns([0.04, 0.96])
        with col_chk:
            val = st.checkbox("", value=checked, key=f"chk_{album['url']}")
        with col_info:
            st.markdown(
                f"<div class='album-row'>"
                f"<span style='flex:1'>{album['name']}</span>"
                f"<span class='badge'>{album['url'].split('/')[-1]}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
        if val:
            st.session_state["selected"].add(album["url"])
        else:
            st.session_state["selected"].discard(album["url"])

    selected_list = [a for a in st.session_state["albums"]
                     if a["url"] in st.session_state["selected"]]

    # ── STEP 3 — Download ─────────────────────────────────────────────────────
    st.markdown("---")
    if st.button(f"⬇️ Scarica {len(selected_list)} prodotti in ZIP"):
        if not selected_list:
            st.warning("Seleziona almeno un prodotto.")
            st.stop()

        prog2   = st.progress(0, text="Avvio…")
        status2 = st.empty()
        detail2 = st.empty()
        stats   = st.empty()

        zip_buf    = io.BytesIO()
        total_imgs = 0
        errors     = 0

        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, album in enumerate(selected_list):
                folder = safe_name(album["name"])
                pct = idx / len(selected_list)
                prog2.progress(pct, text=f"Prodotto {idx+1}/{len(selected_list)}")
                status2.markdown(f"📂 **{folder}**")

                # Trova immagini nell'album
                img_urls = scrape_album_images(album["url"])
                detail2.markdown(f"🖼 {len(img_urls)} immagini trovate")

                for i, img_url in enumerate(img_urls):
                    data = download_image(img_url, referer=album["url"])
                    if data:
                        ext = (img_url.split(".")[-1][:4] or "jpg").lower()
                        if ext not in ("jpg","jpeg","png","webp","gif"):
                            ext = "jpg"
                        zf.writestr(f"{folder}/{i+1:04d}.{ext}", data)
                        total_imgs += 1
                    else:
                        errors += 1
                    stats.markdown(
                        f"<span class='badge badge-acc'>{total_imgs} immagini scaricate</span> "
                        f"{'<span class=badge>'+str(errors)+' errori</span>' if errors else ''}",
                        unsafe_allow_html=True,
                    )
                    time.sleep(0.05)

        prog2.progress(1.0, text="✅ Download completato!")
        status2.empty()
        detail2.empty()

        st.session_state["zip_ready"] = zip_buf.getvalue()
        st.success(f"✅ **{total_imgs} immagini** scaricate da **{len(selected_list)} prodotti**"
                   + (f" · {errors} errori ignorati" if errors else ""))

# ── DOWNLOAD BUTTON ───────────────────────────────────────────────────────────
if st.session_state.get("zip_ready"):
    st.markdown("---")
    st.download_button(
        label="💾 Clicca qui per scaricare lo ZIP",
        data=st.session_state["zip_ready"],
        file_name="yupoo_prodotti.zip",
        mime="application/zip",
        use_container_width=True,
    )
    st.markdown("""
    <div class='card' style='margin-top:.8rem;'>
    <div class='card-title'>📂 Struttura ZIP</div>
    <pre style='color:#888;font-size:.78rem;font-family:Space Mono,monospace;line-height:1.7;margin:0;'>
yupoo_prodotti.zip
├── Nome Prodotto 1/
│   ├── 0001.jpg
│   ├── 0002.jpg
│   └── ...
├── Nome Prodotto 2/
│   └── ...
└── ...</pre>
    </div>
    """, unsafe_allow_html=True)
