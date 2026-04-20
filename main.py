import argparse
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from scholarly import ProxyGenerator, scholarly

try:
    from deep_translator import GoogleTranslator
except Exception:
    GoogleTranslator = None


def log(msg: str) -> None:
    print(msg, flush=True)


def slugify(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    return s or "topic"


def ensure_dirs(base_dir: Path) -> Dict[str, Path]:
    cache_dir = base_dir / "cache"
    outputs_dir = base_dir / "outputs"
    cache_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)
    return {"cache": cache_dir, "outputs": outputs_dir}


def load_topic_cache(cache_path: Path) -> Dict[str, List[str]]:
    if not cache_path.exists():
        return {"seen_keys": []}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("seen_keys"), list):
            return data
    except Exception:
        pass
    return {"seen_keys": []}


def save_topic_cache(cache_path: Path, cache_data: Dict[str, List[str]]) -> None:
    cache_path.write_text(
        json.dumps(cache_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_paper_key(pub: dict) -> str:
    bib = pub.get("bib", {}) if isinstance(pub, dict) else {}
    title = (bib.get("title") or "").strip().lower()
    year = str(bib.get("pub_year") or "").strip()
    url = (pub.get("pub_url") or pub.get("eprint_url") or "").strip().lower()
    if url:
        return f"url::{url}"
    return f"title_year::{title}::{year}"


def format_paper_block(index: int, pub: dict) -> str:
    bib = pub.get("bib", {}) if isinstance(pub, dict) else {}

    title = bib.get("title") or "N/A"
    authors = bib.get("author") or "N/A"
    venue = bib.get("venue") or bib.get("journal") or bib.get("publisher") or "N/A"
    year = bib.get("pub_year") or "N/A"
    abstract = bib.get("abstract") or "N/A"
    abstract_source = bib.get("abstract_source") or "scholar"

    url = pub.get("pub_url") or pub.get("eprint_url") or pub.get("url_scholarbib") or "N/A"

    discussion = "N/A（Google Scholar 通常不直接提供 Discussion 字段）"

    return (
        f"## Paper {index}\n\n"
        f"- URL: {url}\n"
        f"- Title: {title}\n"
        f"- Authors: {authors}\n"
        f"- Venue: {venue}\n"
        f"- Year: {year}\n\n"
        f"- Abstract Source: {abstract_source}\n\n"
        f"### Abstract\n\n{abstract}\n\n"
        f"### Discussion\n\n{discussion}\n\n"
        f"---\n\n"
    )


def split_text_for_translation(text: str, chunk_size: int = 3500) -> List[str]:
    t = clean_text(text)
    if len(t) <= chunk_size:
        return [t]

    chunks: List[str] = []
    start = 0
    while start < len(t):
        end = min(start + chunk_size, len(t))
        if end < len(t):
            pivot = t.rfind(" ", start, end)
            if pivot > start + 500:
                end = pivot
        chunks.append(t[start:end].strip())
        start = end
    return [c for c in chunks if c]


def translate_to_zh(text: str, translator) -> str:
    src = clean_text(text)
    if not src or src == "N/A":
        return "N/A"
    if translator is None:
        return src

    try:
        parts = split_text_for_translation(src)
        translated_parts = [translator.translate(p) for p in parts]
        return clean_text(" ".join(translated_parts))
    except Exception:
        return src


def format_paper_block_zh(index: int, pub: dict, translator) -> str:
    bib = pub.get("bib", {}) if isinstance(pub, dict) else {}

    title = bib.get("title") or "N/A"
    authors = bib.get("author") or "N/A"
    venue = bib.get("venue") or bib.get("journal") or bib.get("publisher") or "N/A"
    year = bib.get("pub_year") or "N/A"
    abstract = bib.get("abstract") or "N/A"
    abstract_source = bib.get("abstract_source") or "scholar"

    title_zh = translate_to_zh(str(title), translator)
    venue_zh = translate_to_zh(str(venue), translator)
    abstract_zh = translate_to_zh(str(abstract), translator)

    url = pub.get("pub_url") or pub.get("eprint_url") or pub.get("url_scholarbib") or "N/A"
    discussion_zh = "N/A（Google Scholar 通常不直接提供 Discussion 字段）"

    return (
        f"## 论文 {index}\n\n"
        f"- 链接: {url}\n"
        f"- 标题: {title_zh}\n"
        f"- 作者: {authors}\n"
        f"- 期刊/会议: {venue_zh}\n"
        f"- 年份: {year}\n\n"
        f"- 摘要来源: {abstract_source}\n\n"
        f"### 摘要\n\n{abstract_zh}\n\n"
        f"### Discussion\n\n{discussion_zh}\n\n"
        f"---\n\n"
    )


def append_text_realtime(file_path: Path, text: str) -> None:
    with file_path.open("a", encoding="utf-8") as f:
        f.write(text)
        f.flush()


def configure_proxy(proxy_mode: str) -> None:
    if proxy_mode == "none":
        return

    pg = ProxyGenerator()
    ok = False
    if proxy_mode == "free":
        try:
            ok = pg.FreeProxies()
        except Exception:
            ok = False

    if ok:
        scholarly.use_proxy(pg)
        log(f"[INFO] Proxy enabled: {proxy_mode}")
    else:
        log(f"[WARN] Failed to enable proxy mode: {proxy_mode}. Continue without proxy.")


def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text


def is_abstract_insufficient(text: str) -> bool:
    t = clean_text(text)
    if not t or t == "N/A":
        return True
    # Scholar often returns short snippets instead of full abstracts.
    return len(t) < 350


def http_get_json(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "paper-reading-collector/1.0 (mailto:example@example.com)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read().decode("utf-8", errors="replace")
    return json.loads(data)


def abstract_from_inverted_index(inv_idx: dict) -> str:
    if not isinstance(inv_idx, dict) or not inv_idx:
        return ""
    max_pos = -1
    for positions in inv_idx.values():
        if isinstance(positions, list) and positions:
            max_pos = max(max_pos, max(positions))
    if max_pos < 0:
        return ""
    words = [""] * (max_pos + 1)
    for token, positions in inv_idx.items():
        if not isinstance(positions, list):
            continue
        for pos in positions:
            if isinstance(pos, int) and 0 <= pos < len(words):
                words[pos] = token
    return clean_text(" ".join(w for w in words if w))


def strip_html_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return clean_text(html.unescape(text))


def extract_doi(pub: dict) -> str:
    bib = pub.get("bib", {}) if isinstance(pub, dict) else {}
    candidates = [
        str(pub.get("pub_url") or ""),
        str(pub.get("eprint_url") or ""),
        str(pub.get("doi") or ""),
        str(bib.get("doi") or ""),
    ]
    doi_pattern = r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+"
    for c in candidates:
        m = re.search(doi_pattern, c)
        if m:
            return m.group(0).rstrip(" .,;)")
    return ""


def fetch_openalex_abstract(title: str, doi: str) -> str:
    try:
        if doi:
            doi_id = urllib.parse.quote(f"https://doi.org/{doi}", safe="")
            url = f"https://api.openalex.org/works/{doi_id}"
            data = http_get_json(url)
            ab = abstract_from_inverted_index(data.get("abstract_inverted_index"))
            if ab:
                return ab
        if title:
            q = urllib.parse.quote(title)
            url = f"https://api.openalex.org/works?search={q}&per-page=1"
            data = http_get_json(url)
            results = data.get("results") or []
            if results:
                ab = abstract_from_inverted_index(results[0].get("abstract_inverted_index"))
                if ab:
                    return ab
    except Exception:
        return ""
    return ""


def fetch_crossref_abstract(title: str) -> str:
    if not title:
        return ""
    try:
        q = urllib.parse.quote(title)
        url = f"https://api.crossref.org/works?query.title={q}&rows=1"
        data = http_get_json(url)
        items = ((data.get("message") or {}).get("items") or [])
        if not items:
            return ""
        abstract_html = items[0].get("abstract") or ""
        return strip_html_tags(abstract_html)
    except Exception:
        return ""


def enrich_abstract(pub: dict) -> dict:
    bib = pub.get("bib", {}) if isinstance(pub, dict) else {}
    abstract = clean_text(str(bib.get("abstract") or ""))
    title = clean_text(str(bib.get("title") or ""))
    doi = extract_doi(pub)

    bib["abstract_source"] = "scholar"
    if not is_abstract_insufficient(abstract):
        bib["abstract"] = abstract
        pub["bib"] = bib
        return pub

    openalex_abs = fetch_openalex_abstract(title, doi)
    if openalex_abs and len(openalex_abs) > max(len(abstract) + 60, 200):
        bib["abstract"] = openalex_abs
        bib["abstract_source"] = "openalex"
        pub["bib"] = bib
        return pub

    crossref_abs = fetch_crossref_abstract(title)
    if crossref_abs and len(crossref_abs) > max(len(abstract) + 60, 200):
        bib["abstract"] = crossref_abs
        bib["abstract_source"] = "crossref"
        pub["bib"] = bib
        return pub

    bib["abstract"] = abstract or "N/A"
    pub["bib"] = bib
    return pub


def start_search_with_retry(topic: str, retries: int, retry_wait: int):
    attempt = 0
    last_error: Optional[Exception] = None
    while attempt <= retries:
        attempt += 1
        try:
            log(f"[INFO] Start search attempt {attempt}/{retries + 1}")
            return scholarly.search_pubs(topic)
        except Exception as e:
            last_error = e
            log(f"[WARN] Search start failed on attempt {attempt}: {e}")
            if attempt <= retries:
                time.sleep(retry_wait)

    raise RuntimeError(
        "Cannot Fetch from Google Scholar after retries. "
        "Possible causes: Google rate-limit/captcha, blocked network, or no proxy."
    ) from last_error


def run(
    topic: str,
    max_results: int,
    retries: int,
    retry_wait: int,
    proxy_mode: str,
    max_scan_results: int,
    year: Optional[int],
) -> int:
    base_dir = Path(__file__).resolve().parent
    dirs = ensure_dirs(base_dir)

    topic_slug = slugify(topic)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    out_dir = dirs["outputs"] / f"{timestamp}_{topic_slug}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "papers.md"
    out_file_zh = out_dir / "papers_zh.md"

    cache_path = dirs["cache"] / f"{topic_slug}.json"
    cache_data = load_topic_cache(cache_path)
    seen_keys = set(cache_data.get("seen_keys", []))

    header = (
        f"# Search Topic: {topic}\n\n"
        f"- Run Time: {timestamp}\n"
        f"- Year Filter: {year if year is not None else 'None'}\n"
        f"- Target New Papers: {max_results}\n"
        f"- Max Scan Results: {max_scan_results}\n\n"
        f"---\n\n"
    )
    header_zh = (
        f"# 检索主题: {topic}\n\n"
        f"- 运行时间: {timestamp}\n"
        f"- 年份筛选: {year if year is not None else '无'}\n"
        f"- 目标新增论文数: {max_results}\n"
        f"- 最大扫描结果数: {max_scan_results}\n\n"
        f"---\n\n"
    )
    append_text_realtime(out_file, header)
    append_text_realtime(out_file_zh, header_zh)

    translator = None
    if GoogleTranslator is None:
        log("[WARN] deep-translator not installed, Chinese file will keep English text.")
    else:
        try:
            translator = GoogleTranslator(source="auto", target="zh-CN")
            log("[INFO] Chinese translation enabled.")
        except Exception as e:
            log(f"[WARN] Failed to initialize translator: {e}")

    log(f"[INFO] Topic: {topic}")
    log(f"[INFO] Output file: {out_file}")
    log(f"[INFO] Chinese output file: {out_file_zh}")
    log(f"[INFO] Cached papers for this topic: {len(seen_keys)}")
    log(f"[INFO] Retry: {retries}, Retry wait: {retry_wait}s, Proxy mode: {proxy_mode}")
    log(f"[INFO] Year filter: {year if year is not None else 'none'}")
    log(f"[INFO] Target new papers: {max_results}, Max scan results: {max_scan_results}")

    scanned = 0
    skipped = 0
    new_added = 0

    try:
        configure_proxy(proxy_mode)
        search_iter = start_search_with_retry(topic, retries, retry_wait)
    except Exception as e:
        err = f"[ERROR] Failed to start Google Scholar search: {e}"
        log(err)
        hint = (
            "- Try again later (Google may temporarily block frequent requests).\n"
            "- Use smaller --max-results (e.g., 5).\n"
            "- Try --proxy-mode free.\n"
            "- Check network connectivity for scholar.google.com.\n"
        )
        append_text_realtime(out_file, f"## Error\n\n{err}\n\n### Troubleshooting\n\n{hint}\n")
        append_text_realtime(out_file_zh, f"## 错误\n\n{err}\n\n### 排查建议\n\n{hint}\n")
        return 1

    while new_added < max_results and scanned < max_scan_results:
        try:
            pub = next(search_iter)
        except StopIteration:
            log("[INFO] No more results from Google Scholar.")
            break
        except Exception as e:
            err = f"[ERROR] Failed to fetch next search result: {e}"
            log(err)
            append_text_realtime(out_file, f"## Error\n\n{err}\n\n")
            append_text_realtime(out_file_zh, f"## 错误\n\n{err}\n\n")
            continue

        scanned += 1

        if year is not None:
            bib = pub.get("bib", {}) if isinstance(pub, dict) else {}
            pub_year = str(bib.get("pub_year") or "").strip()
            if pub_year != str(year):
                skipped += 1
                log(
                    f"[SKIP] Year mismatch ({pub_year or 'N/A'}) "
                    f"(scanned {scanned}, target new {new_added}/{max_results})"
                )
                continue

        key = build_paper_key(pub)

        if key in seen_keys:
            skipped += 1
            log(f"[SKIP] Already cached (scanned {scanned}, target new {new_added}/{max_results})")
            continue

        try:
            # Fill adds details like abstract when available.
            filled = scholarly.fill(pub)
            filled = enrich_abstract(filled)
            block = format_paper_block(new_added + 1, filled)
            block_zh = format_paper_block_zh(new_added + 1, filled, translator)
            append_text_realtime(out_file, block)
            append_text_realtime(out_file_zh, block_zh)

            seen_keys.add(key)
            new_added += 1

            log(f"[OK] Added paper {new_added} (scanned {scanned}, target {max_results})")
        except Exception as e:
            err = f"[ERROR] Failed to process one paper at scanned result #{scanned}: {e}"
            log(err)
            append_text_realtime(
                out_file,
                f"## Paper {new_added + 1} (Error)\n\n{err}\n\n---\n\n",
            )
            append_text_realtime(
                out_file_zh,
                f"## 论文 {new_added + 1}（错误）\n\n{err}\n\n---\n\n",
            )

    if new_added < max_results and scanned >= max_scan_results:
        warn = (
            f"[WARN] Reached scan cap ({max_scan_results}) before collecting "
            f"target new papers ({max_results})."
        )
        log(warn)
        append_text_realtime(out_file, f"## Warning\n\n{warn}\n\n")
        append_text_realtime(out_file_zh, f"## 警告\n\n{warn}\n\n")

    summary = (
        f"## Summary\n\n"
        f"- Year filter: {year if year is not None else 'None'}\n"
        f"- Target new papers: {max_results}\n"
        f"- Scanned results: {scanned}\n"
        f"- New papers added: {new_added}\n"
        f"- Skipped by cache: {skipped}\n"
    )
    summary_zh = (
        f"## 总结\n\n"
        f"- 年份筛选: {year if year is not None else '无'}\n"
        f"- 目标新增论文数: {max_results}\n"
        f"- 扫描结果数: {scanned}\n"
        f"- 新增论文数: {new_added}\n"
        f"- 缓存跳过数: {skipped}\n"
    )
    append_text_realtime(out_file, summary)
    append_text_realtime(out_file_zh, summary_zh)

    cache_data["seen_keys"] = sorted(seen_keys)
    save_topic_cache(cache_path, cache_data)

    log("[INFO] Done.")
    log(f"[INFO] New papers added: {new_added}")
    log(f"[INFO] Skipped by cache: {skipped}")
    log(f"[INFO] Cache saved: {cache_path}")

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search papers from Google Scholar and organize them in markdown."
    )
    parser.add_argument("--topic", required=True, help="English search topic")
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="Only keep papers from this publication year, e.g. 2024",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=20,
        help="Maximum number of results to process",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Retry times when starting Google Scholar search",
    )
    parser.add_argument(
        "--retry-wait",
        type=int,
        default=5,
        help="Seconds to wait between retries",
    )
    parser.add_argument(
        "--proxy-mode",
        choices=["none", "free"],
        default="none",
        help="Proxy mode for scholarly requests",
    )
    parser.add_argument(
        "--max-scan-results",
        type=int,
        default=500,
        help="Maximum number of Scholar results to scan when skipping cached papers",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.year is not None and not (1900 <= args.year <= datetime.now().year + 1):
        log(f"[ERROR] --year must be between 1900 and {datetime.now().year + 1}")
        sys.exit(2)
    if args.max_results <= 0:
        log("[ERROR] --max-results must be > 0")
        sys.exit(2)
    if args.retries < 0:
        log("[ERROR] --retries must be >= 0")
        sys.exit(2)
    if args.retry_wait < 0:
        log("[ERROR] --retry-wait must be >= 0")
        sys.exit(2)
    if args.max_scan_results <= 0:
        log("[ERROR] --max-scan-results must be > 0")
        sys.exit(2)

    sys.exit(
        run(
            args.topic,
            args.max_results,
            args.retries,
            args.retry_wait,
            args.proxy_mode,
            args.max_scan_results,
            args.year,
        )
    )
