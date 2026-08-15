import re
import json
import os
import requests

DATASET_URLS = [
    "https://raw.githubusercontent.com/dokkarrr/final_animgeg_embed-scraper/refs/heads/main/output/animegg_series.json",
    "https://raw.githubusercontent.com/dokkarrr/final_animgeg_embed-scraper/refs/heads/main/output/animegg_series2.json",
    "https://raw.githubusercontent.com/dokkarrr/final_animgeg_embed-scraper/refs/heads/main/output/animegg_series3.json",
    "https://raw.githubusercontent.com/dokkarrr/final_animgeg_embed-scraper/refs/heads/main/output/animegg_series4.json",
    "https://raw.githubusercontent.com/dokkarrr/final_animgeg_embed-scraper/refs/heads/main/output/animegg_series5.json",
    "https://raw.githubusercontent.com/dokkarrr/final_animgeg_embed-scraper/refs/heads/main/output/animegg_series6.json",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Referer": "https://www.animegg.org/",
}

MAX_FILE_SIZE  = 30 * 1024 * 1024
OUTPUT_DIR     = "output"
OUTPUT_BASE    = os.path.join(OUTPUT_DIR, "animegg_streams")
ERROR_LOG_FILE = os.path.join(OUTPUT_DIR, "streamsnotfound_error_facing.json")

_dataset_cache = None

os.makedirs(OUTPUT_DIR, exist_ok=True)


# ── Error log ──────────────────────────────────────────────────────────────────

def load_error_log():
    if os.path.exists(ERROR_LOG_FILE):
        with open(ERROR_LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_error_log(entries):
    with open(ERROR_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def log_error(mal_id, title, ep_num, lang, embed_url, reason):
    entries = load_error_log()
    for e in entries:
        if e.get("embed_url") == embed_url:
            return
    entries.append({
        "mal_id":    mal_id,
        "title":     title,
        "episode":   ep_num,
        "lang":      lang,
        "embed_url": embed_url,
        "reason":    reason,
    })
    save_error_log(entries)
    print(f"    [error logged] {lang.upper()} ep {ep_num} — {reason}")


# ── Dataset helpers ────────────────────────────────────────────────────────────

def load_all_datasets():
    global _dataset_cache
    if _dataset_cache:
        return _dataset_cache
    print("[*] Loading AnimeGG datasets from GitHub...")
    combined = []
    for i, url in enumerate(DATASET_URLS, 1):
        try:
            r = requests.get(url, timeout=10)
            if r.ok:
                data = r.json()
                combined.extend(data)
                print(f"    Dataset {i}: {len(data)} entries")
        except Exception as e:
            print(f"    Dataset {i}: failed ({e})")
    _dataset_cache = combined
    print(f"[+] Total loaded: {len(combined)} anime entries\n")
    return combined


def find_anime(mal_id):
    datasets = load_all_datasets()
    for item in datasets:
        if item.get("mal_id") == int(mal_id):
            return item
    return None


# ── Stream extraction ──────────────────────────────────────────────────────────

def fetch_embed_page(embed_url):
    r = requests.get(embed_url, headers=HEADERS, timeout=10)
    if r.ok and "videoSources" in r.text:
        return r.text
    raise Exception(f"HTTP {r.status_code} or videoSources not found")


def extract_sources(embed_url):
    html = fetch_embed_page(embed_url)
    vs_match = re.search(r'var videoSources\s*=\s*(\[.*?\])\s*;', html, re.DOTALL)
    if not vs_match:
        return {}
    block = vs_match.group(1)
    sources = {}
    for match in re.finditer(r'\{file:\s*"([^"]+)".*?label:\s*"([^"]+)"', block, re.DOTALL):
        file_url, label = match.group(1), match.group(2)
        if file_url.startswith("/"):
            file_url = "https://www.animegg.org" + file_url
        sources[label] = file_url
    return sources


def build_record(serial, anime_entry):
    mal_id   = anime_entry.get("mal_id")
    title    = anime_entry.get("title", "")
    episodes = anime_entry.get("episodes", [])

    record = {"serial": serial, "mal_id": mal_id, "title": title}

    for ep in sorted(episodes, key=lambda e: e.get("ep", 0)):
        ep_num    = ep.get("ep")
        dub_embed = ep.get("dub")
        sub_embed = ep.get("sub")

        if dub_embed:
            try:
                sources = extract_sources(dub_embed)
                if sources:
                    for label, url in sources.items():
                        record[f"dub_ep_{ep_num}_{label.replace(' ', '_')}"] = url
                else:
                    log_error(mal_id, title, ep_num, "dub", dub_embed, "videoSources empty — no streams found")
            except Exception as ex:
                log_error(mal_id, title, ep_num, "dub", dub_embed, str(ex))

        if sub_embed:
            try:
                sources = extract_sources(sub_embed)
                if sources:
                    for label, url in sources.items():
                        record[f"sub_ep_{ep_num}_{label.replace(' ', '_')}"] = url
                else:
                    log_error(mal_id, title, ep_num, "sub", sub_embed, "videoSources empty — no streams found")
            except Exception as ex:
                log_error(mal_id, title, ep_num, "sub", sub_embed, str(ex))

    return record


# ── File I/O with auto-split ───────────────────────────────────────────────────

def get_filename(part):
    return f"{OUTPUT_BASE}.json" if part == 1 else f"{OUTPUT_BASE}_{part}.json"


def load_existing():
    records = []
    part = 1
    while True:
        fname = get_filename(part)
        if not os.path.exists(fname):
            break
        with open(fname, "r", encoding="utf-8") as f:
            records.extend(json.load(f))
        part += 1
    return records


def save_records(all_records):
    part, chunk, chunk_size = 1, [], 0
    for record in all_records:
        line_size = len(json.dumps(record, ensure_ascii=False).encode("utf-8"))
        if chunk and chunk_size + line_size > MAX_FILE_SIZE:
            with open(get_filename(part), "w", encoding="utf-8") as f:
                json.dump(chunk, f, ensure_ascii=False, indent=2)
            part += 1
            chunk, chunk_size = [], 0
        chunk.append(record)
        chunk_size += line_size
    if chunk:
        with open(get_filename(part), "w", encoding="utf-8") as f:
            json.dump(chunk, f, ensure_ascii=False, indent=2)
    while os.path.exists(get_filename(part + 1)):
        os.remove(get_filename(part + 1))
        part += 1


def show_saved_files():
    part = 1
    while os.path.exists(get_filename(part)):
        fname = get_filename(part)
        size  = os.path.getsize(fname) / 1024 / 1024
        with open(fname, "r", encoding="utf-8") as f:
            count = len(json.load(f))
        print(f"  [saved] {fname}  ({size:.2f} MB, {count} records)")
        part += 1
    if os.path.exists(ERROR_LOG_FILE):
        with open(ERROR_LOG_FILE, "r", encoding="utf-8") as f:
            err_count = len(json.load(f))
        print(f"  [errors] {ERROR_LOG_FILE}  ({err_count} failed embed URLs)")


# ── Extraction runners ─────────────────────────────────────────────────────────

def run_single(mal_id):
    anime = find_anime(mal_id)
    if not anime:
        print(f"[!] MAL ID {mal_id} not found in datasets.")
        return
    existing = load_existing()
    done_ids = {r["mal_id"]: i for i, r in enumerate(existing)}
    serial   = done_ids.get(int(mal_id), len(existing)) + 1
    print(f"[*] Extracting: {anime.get('title')} (MAL {mal_id})")
    record = build_record(serial, anime)
    if int(mal_id) in done_ids:
        existing[done_ids[int(mal_id)]] = record
    else:
        existing.append(record)
    save_records(existing)
    print(f"[✓] Done.")
    show_saved_files()


def run_multiple(mal_ids):
    existing    = load_existing()
    done_ids    = {r["mal_id"]: i for i, r in enumerate(existing)}
    all_records = list(existing)
    serial      = len(all_records) + 1
    for mal_id in mal_ids:
        mal_id = int(mal_id)
        anime  = find_anime(mal_id)
        if not anime:
            print(f"[!] MAL ID {mal_id} not found, skipping.")
            continue
        print(f"[{serial}] {anime.get('title')} (MAL {mal_id})")
        record = build_record(serial, anime)
        if mal_id in done_ids:
            all_records[done_ids[mal_id]] = record
        else:
            all_records.append(record)
            done_ids[mal_id] = len(all_records) - 1
            serial += 1
        save_records(all_records)
    print(f"\n[✓] Done.")
    show_saved_files()


def run_all():
    existing    = load_existing()
    done_ids    = {r["mal_id"] for r in existing}
    all_records = list(existing)
    serial      = len(all_records) + 1
    datasets    = load_all_datasets()
    pending     = [a for a in datasets if a.get("mal_id") not in done_ids]
    print(f"[*] Remaining to process: {len(pending)} / {len(datasets)}\n")
    for anime in pending:
        mal_id = anime.get("mal_id")
        print(f"[{serial}] {anime.get('title')} (MAL {mal_id})")
        record = build_record(serial, anime)
        all_records.append(record)
        done_ids.add(mal_id)
        serial += 1
        save_records(all_records)
    print(f"\n[✓] Done. Total: {len(all_records)} records")
    show_saved_files()


# ── Menu ───────────────────────────────────────────────────────────────────────

def menu():
    print("=" * 50)
    print("  AnimeGG Stream Extractor")
    print("=" * 50)
    print("  1. Single MAL ID")
    print("  2. Multiple MAL IDs")
    print("  3. All database")
    print("=" * 50)
    choice = input("Select option (1/2/3): ").strip()

    if choice == "1":
        mal_id = input("Enter MAL ID: ").strip()
        if not mal_id.isdigit():
            print("[!] Invalid MAL ID.")
            return
        run_single(int(mal_id))

    elif choice == "2":
        raw = input("Enter MAL IDs separated by commas (e.g. 20, 1735, 1535): ").strip()
        mal_ids = [x.strip() for x in raw.split(",") if x.strip().isdigit()]
        if not mal_ids:
            print("[!] No valid MAL IDs entered.")
            return
        run_multiple(mal_ids)

    elif choice == "3":
        confirm = input("This will extract ALL anime in the database. Continue? (y/n): ").strip().lower()
        if confirm == "y":
            run_all()
        else:
            print("Cancelled.")

    else:
        print("[!] Invalid option.")


if __name__ == "__main__":
    menu()
