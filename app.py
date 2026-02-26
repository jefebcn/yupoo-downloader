import streamlit as st
import requests
import zipfile
import io
import time
import re
import json
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

st.set_page_config(page_title="Yupoo Album Downloader", page_icon="📦", layout="wide")

# ── CSS ──────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@400;500;700&display=swap');
:root{--bg:#0d0d0d;--surf:#161616;--border:#2a2a2a;--acc:#e8ff47;--acc2:#47ffe8;--text:#f0f0f0;--muted:#888;}
html,body,.stApp{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;}
h1,h2,h3{font-family:'Space Mono',monospace;}
section[data-testid="stSidebar"]{background:var(--surf)!important;border-right:1px solid var(--border);}
.stButton>button{background:var(--acc)!important;color:#000!important;font-family:'Space Mono',monospace;font-weight:700;border:none;border-radius:2px;letter-spacing:.05em;}
.stDownloadButton>button{background:var(--acc2)!important;color:#000!important;font-family:'Space Mono',monospace;font-weight:700;border:none;border-radius:2px;}
.stTextInput input{background:var(--surf)!important;border:1px solid var(--border)!important;color:var(--text)!important;border-radius:2px;}
.stTextInput input:focus{border-color:var(--acc)!important;}
.card{background:var(--surf);border:1px solid var(--border);border-radius:4px;padding:1.2rem;margin-bottom:.8rem;}
.card-title{font-family:'Space Mono',monospace;font-size:.7rem;letter-spacing:.12em;color:var(--acc);text-transform:uppercase;margin-bottom:.6rem;}
.album-card{background:var(--surf);border:1px solid var(--border);border-radius:4px;padding:.8rem;margin-bottom:.5rem;display:flex;justify-content:space-between;align-items:center;}
.badge{display:inline-block;background:var(--border);color:var(--text);font-family:'Space Mono',monospace;font-size:.65rem;padding:.2rem .5rem;border-radius:2px;margin:.1rem;}
.badge-acc{background:var(--acc);color:#000;}
.badge-acc2{background:var(--acc2);color:#000;}
</style>
""", unsafe_allow_html=True)

# ── HEADERS ──────────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.yupoo.com/",
}

# ── HELPERS ──────────────────────────────────────────────────────────────────

def get_base(url: str) -> str:
    """Returns https://user.x.yupoo.com"""
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def fetch(url: str, retries=3) -> requests.Response | None:
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                return r
            time.sleep(1)
        except Exception:
            time.sleep(1)
    return None


def scrape_albums(base_url: str) -> list[dict]:
    """Returns list of {name, url, cover} for all albums across all pages."""
    albums = []
    page = 1
    while True:
        url = f"{base_url}/albums?page={page}"
        r = fetch(url)
        if not r:
            break
        soup = BeautifulSoup(r.text, "lxml")

        # Find album links
        found = 0
        for a in soup.find_all("a", href=True):
            href = a["href"]
            # Album links match /albums/DIGITS
            if re.match(r"^/albums/\d+", href):
                full_url = urljoin(base_url, href)
                if any(al["url"] == full_url for al in albums):
                    continue
                # Try to get album name
                name = ""
                title_el = a.find(class_=re.compile(r"album.*title|title.*album", re.I))
                if not title_el:
                    title_el = a.find(["span", "div", "p"])
                if title_el:
                    name = title_el.get_text(strip=True)
                if not name:
                    name = href.split("/")[-1]

                # Cover image
                cover = ""
                img = a.find("img")
                if img:
                    cover = img.get("src") or img.get("data-src") or ""

                albums.append({"name": name, "url": full_url, "cover": cover})
                found += 1

        if found == 0:
            break

        # Check for next page
        next_page = soup.find("a", string=re.compile(r"next|下一页|›|»", re.I))
        if not next_page:
            # Also check numbered pagination
            page_links = soup.find_all("a", href=re.compile(r"page=\d+"))
            max_page = max(
                (int(re.search(r"page=(\d+)", a["href"]).group(1)) for a in page_links),
                default=page
            )
            if page >= max_page:
                break
        page += 1

    return albums


def scrape_album_images(album_url: str) -> list[str]:
    """Returns list of direct image URLs for all pages of an album."""
    image_urls = []
    page = 1
    while True:
        url = f"{album_url}?page={page}" if page > 1 else album_url
        r = fetch(url)
        if not r:
            break
        soup = BeautifulSoup(r.text, "lxml")

        found = 0
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-original") or ""
            src = src.strip()
            if not src or src.startswith("data:"):
                continue
            # Yupoo images come from photo.yupoo.com or img.yupoo.com
            if any(d in src for d in ["yupoo.com", "photo.", "img.", ".jpg", ".png", ".webp", ".jpeg"]):
                # Get highest resolution: remove size constraints from URL
                src = re.sub(r"\?.*$", "", src)  # strip query params
                if src not in image_urls:
                    image_urls.append(src)
                    found += 1

        # Also check JSON data embedded in page (Yupoo sometimes uses __NEXT_DATA__)
        for script in soup.find_all("script", id="__NEXT_DATA__"):
            try:
                data = json.loads(script.string)
                # Walk the JSON looking for image URLs
                text = json.dumps(data)
                urls = re.findall(r'https://[^"\']+(?:\.jpg|\.png|\.webp|\.jpeg)[^"\']*', text)
                for u in urls:
                    clean = re.sub(r"\?.*$", "", u)
                    if clean not in image_urls:
                        image_urls.append(clean)
                        found += 1
            except Exception:
                pass

        if found == 0:
            break

        # Check next page
        next_el = soup.find("a", string=re.compile(r"next|下一页|›|»", re.I))
        if not next_el:
            page_links = soup.find_all("a", href=re.compile(r"page=\d+"))
            max_page = max(
                (int(re.search(r"page=(\d+)", a["href"]).group(1)) for a in page_links),
                default=page
            )
            if page >= max_page:
                break
        page += 1

    return image_urls


def download_image(url: str) -> bytes | None:
    try:
        r = requests.get(url, headers={**HEADERS, "Referer": "https://www.yupoo.com/"}, timeout=15)
        if r.status_code == 200 and len(r.content) > 500:
            return r.content
    except Exception:
        pass
    return None


def safe_name(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()[:60]


# ── SESSION STATE ─────────────────────────────────────────────────────────────
for k, v in [("albums", []), ("selected", []), ("zip_ready", None)]:
    if k not in st.session_state:
        st.session_state[k] = v

# ── UI ────────────────────────────────────────────────────────────────────────
st.markdown("""
<h1 style='margin-bottom:.2rem;'>Yupoo <span style='color:#e8ff47;'>Downloader</span></h1>
<p style='color:#888;font-size:.9rem;margin-bottom:1.5rem;'>
Scarica tutti gli album da qualsiasi pagina Yupoo · Organizzati per album
</p>
""", unsafe_allow_html=True)

# ── STEP 1 — URL input ────────────────────────────────────────────────────────
st.markdown("<div class='card'><div class='card-title'>① Inserisci URL Yupoo</div>", unsafe_allow_html=True)
url_input = st.text_input("", value="https://huskyreps.x.yupoo.com/albums",
                           placeholder="https://user.x.yupoo.com/albums",
                           label_visibility="collapsed")
st.markdown("</div>", unsafe_allow_html=True)

col1, col2 = st.columns([1, 4])
with col1:
    scan_btn = st.button("🔍 Scansiona Album")

if scan_btn and url_input:
    base = get_base(url_input)
    log_box = st.empty()

    # Test connessione
    log_box.info("🔌 Test connessione al sito…")
    test = fetch(base)
    if test is None:
        log_box.error("❌ Impossibile raggiungere il sito. Controlla l'URL o riprova.")
        st.stop()

    log_box.success(f"✅ Sito raggiungibile (HTTP {test.status_code}) — scansione album in corso…")
    time.sleep(0.4)

    # Scansione pagina per pagina con log live
    albums = []
    page = 1
    prog = st.progress(0, text="Scansione pagina 1…")
    detail = st.empty()

    while True:
        url = f"{base}/albums?page={page}"
        detail.markdown(f"📄 Scansione **pagina {page}** → `{url}`")
        r = fetch(url)

        if not r:
            detail.warning(f"⚠️ Pagina {page} non risponde — mi fermo qui.")
            break

        soup = BeautifulSoup(r.text, "lxml")
        found_this_page = 0

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if re.match(r"^/albums/\d+", href):
                full_url = urljoin(base, href)
                if any(al["url"] == full_url for al in albums):
                    continue
                name = ""
                title_el = a.find(class_=re.compile(r"album.*title|title.*album", re.I))
                if not title_el:
                    title_el = a.find(["span", "div", "p"])
                if title_el:
                    name = title_el.get_text(strip=True)
                if not name:
                    name = href.split("/")[-1]
                cover = ""
                img = a.find("img")
                if img:
                    cover = img.get("src") or img.get("data-src") or ""
                albums.append({"name": name, "url": full_url, "cover": cover})
                found_this_page += 1

        log_box.success(f"📦 Pagina {page}: +{found_this_page} album → totale: **{len(albums)}**")

        if found_this_page == 0:
            detail.info("ℹ️ Nessun nuovo album in questa pagina — scansione terminata.")
            break

        page_links = soup.find_all("a", href=re.compile(r"page=\d+"))
        max_page = max(
            (int(re.search(r"page=(\d+)", a["href"]).group(1)) for a in page_links),
            default=page
        )
        prog.progress(min(page / max(max_page, 1), 1.0), text=f"Pagina {page}/{max_page}")

        if page >= max_page:
            break
        page += 1
        time.sleep(0.3)

    prog.empty()
    detail.empty()

    if albums:
        st.session_state["albums"] = albums
        st.session_state["selected"] = [a["url"] for a in albums]
        log_box.success(f"✅ Scansione completata — **{len(albums)} album** trovati!")
    else:
        log_box.error("❌ Nessun album trovato — il sito usa probabilmente JavaScript dinamico.")
        st.warning("""
**💡 Cosa significa:** Yupoo carica i contenuti via JavaScript, quindi BeautifulSoup non li vede.

**Soluzioni:**
- Dimmi e aggiungo il supporto **Selenium / Playwright** (browser headless) che esegue il JS
- Oppure incolla l'URL di un **singolo album** (es. `https://huskyreps.x.yupoo.com/albums/12345`) per scaricare quello direttamente
        """)

# ── STEP 2 — Album list ────────────────────────────────────────────────────────
if st.session_state["albums"]:
    st.markdown("<div class='card-title' style='margin-top:1.5rem;'>② Seleziona gli album da scaricare</div>",
                unsafe_allow_html=True)

    cola, colb = st.columns([1, 1])
    with cola:
        if st.button("✅ Seleziona tutti"):
            st.session_state["selected"] = [a["url"] for a in st.session_state["albums"]]
    with colb:
        if st.button("❌ Deseleziona tutti"):
            st.session_state["selected"] = []

    # Show albums as checkboxes
    for album in st.session_state["albums"]:
        checked = album["url"] in st.session_state["selected"]
        c1, c2 = st.columns([0.05, 0.95])
        with c1:
            val = st.checkbox("", value=checked, key=f"chk_{album['url']}")
        with c2:
            st.markdown(f"""
            <div class='album-card'>
                <span style='font-size:.9rem;'>{album['name']}</span>
                <span class='badge'>{album['url'].split('/')[-1]}</span>
            </div>
            """, unsafe_allow_html=True)
        # Update selected list
        if val and album["url"] not in st.session_state["selected"]:
            st.session_state["selected"].append(album["url"])
        elif not val and album["url"] in st.session_state["selected"]:
            st.session_state["selected"].remove(album["url"])

    selected_albums = [a for a in st.session_state["albums"]
                       if a["url"] in st.session_state["selected"]]

    st.markdown(f"<br><span class='badge badge-acc'>{len(selected_albums)} album selezionati</span>",
                unsafe_allow_html=True)

    # ── STEP 3 — Download ─────────────────────────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("⬇️ Scarica tutti gli album selezionati (.zip)") and selected_albums:
        progress = st.progress(0, text="Inizio download…")
        status = st.empty()
        log = st.empty()

        zip_buf = io.BytesIO()
        total_images = 0
        errors = 0

        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for alb_idx, album in enumerate(selected_albums):
                folder = safe_name(album["name"]) or f"album_{alb_idx+1}"
                progress.progress(alb_idx / len(selected_albums),
                                   text=f"Scansione album {alb_idx+1}/{len(selected_albums)}: {folder}")
                status.markdown(f"📂 **{folder}** — recupero immagini…")

                img_urls = scrape_album_images(album["url"])
                log.markdown(f"🖼 Trovate **{len(img_urls)}** immagini in *{folder}*")

                for img_idx, img_url in enumerate(img_urls):
                    data = download_image(img_url)
                    if data:
                        ext = img_url.split(".")[-1][:4] or "jpg"
                        filename = f"{folder}/{img_idx+1:04d}.{ext}"
                        zf.writestr(filename, data)
                        total_images += 1
                    else:
                        errors += 1
                    time.sleep(0.05)  # be polite

        progress.progress(1.0, text="✅ Download completato!")
        status.empty()
        log.empty()

        st.session_state["zip_ready"] = zip_buf.getvalue()
        st.success(f"✅ {total_images} immagini scaricate da {len(selected_albums)} album"
                   + (f" · {errors} errori ignorati" if errors else ""))

    if st.session_state["zip_ready"]:
        st.download_button(
            label="💾 Clicca qui per scaricare lo ZIP",
            data=st.session_state["zip_ready"],
            file_name="yupoo_albums.zip",
            mime="application/zip",
            use_container_width=True,
        )
        st.markdown("""
        <div class='card' style='margin-top:1rem;'>
        <div class='card-title'>📂 Struttura dello ZIP</div>
        <pre style='color:#888;font-size:.8rem;font-family:Space Mono,monospace;'>
yupoo_albums.zip
├── NomeAlbum1/
│   ├── 0001.jpg
│   ├── 0002.jpg
│   └── ...
├── NomeAlbum2/
│   └── ...
└── ...
        </pre>
        </div>
        """, unsafe_allow_html=True)
