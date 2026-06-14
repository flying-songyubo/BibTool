import re
import requests
import time
import threading
import tkinter as tk
from tkinter import scrolledtext
from queue import Queue
from rapidfuzz import fuzz
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# =========================
# APIs
# =========================
CROSSREF = "https://api.crossref.org/works"
CROSSREF_DOI = "https://api.crossref.org/works/"

# =========================
# 匹配配置
# =========================
CROSSREF_ROWS = 12
MIN_MATCH_SCORE = 60
MIN_TITLE_SIMILARITY = 60
REJECTED_TYPES = {'book', 'book-chapter', 'reference-entry', 'reference-book', 'standard'}
REJECTED_TITLE_PATTERNS = [
    r'encyclopedia of', r'advances in', r'synthesis lectures',
    r'proceedings of the', r'ieee.*conference proceedings', r'acm.*conference proceedings',
    r'lecture notes in', r'studies in', r'series in'
]
FINAL_SIMILARITY_THRESHOLD = 65   # 最终标题相似度阈值

STOP_WORDS = {'the', 'a', 'an', 'and', 'of', 'to', 'in', 'for', 'on', 'with', 'by', 'at', 'from', 'is', 'are', 'was', 'were', 'be', 'been', 'being'}

# =========================
# GLOBAL STATE
# =========================
event_q = Queue()
cache_cr = {}
cache_cr_author = {}
cache_oa_title = {}
seen = {}
title_seen = set()
UI_FPS = 6

stats_lock = threading.Lock()
stats = {
    'total': 0,
    'completed': 0,
    'success': 0,
    'fail': 0,
    'duplicate': 0,
    'failures': []
}

processing_active = False
refs_queue = []
current_task_index = 0

# =========================
# UI DISPATCH
# =========================
def dispatch():
    try:
        while True:
            t, msg = event_q.get_nowait()
            if t == "log":
                log_box.insert(tk.END, msg + "\n")
                log_box.see(tk.END)
            elif t == "bib":
                bib_box.insert(tk.END, msg)
                bib_box.see(tk.END)
            elif t == "stat":
                stat_box.insert(tk.END, msg + "\n")
                stat_box.see(tk.END)
            elif t == "stat_clear":
                stat_box.delete("1.0", tk.END)
            elif t == "stat_set":
                stat_box.delete("1.0", tk.END)
                stat_box.insert("1.0", msg + "\n")
    except:
        pass
    root.after(int(1000 / UI_FPS), dispatch)

# =========================
# UTILITIES
# =========================
def clean_line(t):
    return re.sub(r"\s+", " ", t.strip())

def split_refs(text):
    lines = text.strip().splitlines()
    refs = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        cleaned = re.sub(r'^\s*\d+[\.\、]\s*', '', line)
        if cleaned:
            refs.append(clean_line(cleaned))
    return refs

def norm(t):
    return re.sub(r"\W+", "", t.lower())

def normalize_author_lastname(author_name):
    """提取作者姓氏并规范化（去重音、转小写、去除非字母数字）"""
    if not author_name:
        return "unknown"
    surname = author_name.split()[-1].strip()
    surname = surname.replace('-', '')
    import unicodedata
    surname = unicodedata.normalize('NFKD', surname).encode('ASCII', 'ignore').decode('ASCII')
    surname = re.sub(r'[^a-zA-Z0-9]', '', surname)
    return surname.lower()

def make_short_key(title, year, first_author=None):
    """生成谷歌学术风格标签: 第一作者姓氏 + 年份 + 标题第一个单词"""
    year_str = str(year) if year and str(year).isdigit() else ""
    words = re.findall(r'\b[a-zA-Z0-9]{2,}\b', title.lower())
    first_word = words[0] if words else "paper"
    author_part = "unknown"
    if first_author and isinstance(first_author, str) and first_author.strip():
        author_part = normalize_author_lastname(first_author)
    key = f"{author_part}{year_str}{first_word}"
    # 限制总长度不超过50字符
    if len(key) > 50:
        key = key[:50]
    return key

def normalize_title(t):
    t = re.sub(r'[^\w\s]', ' ', t)
    return re.sub(r'\s+', ' ', t).strip().lower()

def get_first_keyword(title):
    words = normalize_title(title).split()
    for w in words:
        if w not in STOP_WORDS and len(w) > 2:
            return w
    return words[0] if words else ""

def extract_paper_title(ref_str):
    """改进标题提取，支持句点分隔，并能正确截断会议/期刊信息"""
    ref_str = re.sub(r'^\s*\d+[\.\、]\s*', '', ref_str).strip()
    # 先尝试双引号/单引号
    m = re.search(r'[“"](.+?)[”"]', ref_str)
    if m:
        return m.group(1).strip()
    m = re.search(r"‘(.+?)'", ref_str)
    if m:
        return m.group(1).strip()
    # 按句号分割，取第一部分之后的内容
    parts = ref_str.split('.', 1)
    if len(parts) < 2:
        return ref_str[:150]
    after_author = parts[1].strip()
    # 增强停止模式：匹配常见会议/期刊关键词
    stop_pattern = r'\b(In:|in:|Proceedings of|IEEE|ACM|Springer|Elsevier|Conference|Symposium|Journal|Transactions|Magazine|IFIP/IEEE|International Symposium|Workshop|\([0-9]{4}\)|,\s+[0-9]{4}\b)'
    match = re.search(stop_pattern, after_author, re.I)
    if match:
        title = after_author[:match.start()].strip()
        # 去除末尾的年份数字（如 "2021"）以及前面的句点或空格
        title = re.sub(r'\s+\d{4}\s*$', '', title)
        title = re.sub(r'[.,;:]$', '', title).strip()
        if len(title) > 10:
            return title
    # 否则取逗号前
    title = after_author.split(',')[0].strip()
    title = re.sub(r'\s+\d{4}\s*$', '', title)
    title = re.sub(r'[.,;:]$', '', title).strip()
    if len(title) > 10:
        return title
    # 最后尝试：如果仍然包含明显的会议词，截断
    for kw in ['Proceedings', 'Conference', 'Symposium', 'Workshop', 'IFIP', 'IEEE']:
        if kw.lower() in title.lower():
            idx = title.lower().find(kw.lower())
            title = title[:idx].strip()
            title = re.sub(r'\s+\d{4}\s*$', '', title)
            title = re.sub(r'[.,;:]$', '', title).strip()
            if len(title) > 10:
                return title
    return after_author[:150].strip()

def extract_author_hint(ref_str):
    clean_ref = re.sub(r'^\s*\d+[\.\、]\s*', '', ref_str)
    skip_words = {'IN', 'PROCEEDINGS', 'IEEE', 'ACM', 'SPRINGER', 'ELSEVIER',
                  'THE', 'A', 'AN', 'OF', 'AND', 'FOR', 'ON', 'WITH', 'FROM', 'BY'}
    first_part = re.split(r'[,;:\.]\s*', clean_ref)[0]
    words = re.findall(r'\b([A-Z][a-z]{1,})\b', first_part)
    for w in words:
        if w.upper() not in skip_words and len(w) >= 2:
            return w
    words = re.findall(r'\b([A-Z][a-zA-Z\.\-]{1,})\b', clean_ref)
    for w in words:
        if w.upper() not in skip_words and len(w) >= 2:
            return w.rstrip('.')
    return ""

def extract_venue(ref_str):
    ref_clean = re.sub(r'^\s*\d+[\.\、]\s*', '', ref_str)
    patterns = [
        r'(Proceedings\s+of\s+[^\.]+?(?=\s+\(?\d{4}|\.\s+\d{4}|\s+\d{4}\s*[:;]|$))',
        r'(International\s+Conference\s+on\s+[^\.]+?(?=\s+\(?\d{4}|\.\s+\d{4}|\s+\d{4}\s*[:;]|$))',
        r'(IEEE\s+[^\.]+?(?:Conference|Symposium|Workshop)[^\.]*?(?=\s+\(?\d{4}|\.\s+\d{4}|\s+\d{4}\s*[:;]|$))',
        r'(ACM\s+[^\.]+?(?:Conference|Symposium)[^\.]*?(?=\s+\(?\d{4}|\.\s+\d{4}|\s+\d{4}\s*[:;]|$))',
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:Journal|Transactions|Magazine|Letters)[^\.]*?(?=\s+\(?\d{4}|\.\s+\d{4}|\s+\d{4}\s*[:;]|$))'
    ]
    for pat in patterns:
        m = re.search(pat, ref_clean, re.I)
        if m:
            venue = m.group(1).strip()
            venue = re.sub(r'[.,;:\s]+$', '', venue)
            if len(venue) > 5 and not venue.isdigit():
                return venue
    keywords = ['Proceedings of', 'In:', 'IEEE', 'ACM', 'Springer', 'Elsevier']
    year_match = re.search(r'\b(19|20)\d{2}\b', ref_clean)
    if year_match:
        before_year = ref_clean[:year_match.start()]
        for kw in keywords:
            if kw.lower() in before_year.lower():
                start = before_year.lower().find(kw.lower())
                venue = before_year[start:].strip()
                venue = re.sub(r'[.,;:\s]+$', '', venue)
                if len(venue) > 5:
                    return venue
    return ""

def create_robust_session():
    session = requests.Session()
    retry_strategy = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=20, pool_maxsize=20)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    })
    return session

# ---------- API 查询函数 ----------
def crossref_title_with_author(session, title, author, rows=CROSSREF_ROWS):
    if not author:
        return crossref_title(session, title, rows)
    cache_key = f"{title}||{author}||{rows}"
    if cache_key in cache_cr_author:
        event_q.put(("log", f"  [缓存] 标题+作者查询命中 (作者: {author})"))
        return cache_cr_author[cache_key]
    params = {"query.title": title, "query.author": author, "rows": rows}
    try:
        r = session.get(CROSSREF, params=params, timeout=(8, 15))
        if r.status_code == 200:
            items = r.json().get("message", {}).get("items", [])
            cache_cr_author[cache_key] = items
            return items
    except Exception as e:
        event_q.put(("log", f"  [HTTP] 标题+作者查询异常: {type(e).__name__}"))
    return []

def crossref_title(session, title, rows=CROSSREF_ROWS):
    cache_key = f"{title}||{rows}"
    if cache_key in cache_cr:
        event_q.put(("log", f"  [缓存] 标题查询命中"))
        return cache_cr[cache_key]
    try:
        r = session.get(CROSSREF, params={"query.title": title, "rows": rows}, timeout=(8, 15))
        if r.status_code == 200:
            items = r.json().get("message", {}).get("items", [])
            cache_cr[cache_key] = items
            return items
    except Exception as e:
        event_q.put(("log", f"  [HTTP] 标题查询异常: {type(e).__name__}"))
    return []

def crossref_doi(session, doi):
    try:
        r = session.get(CROSSREF_DOI + doi, timeout=(8, 15))
        if r.status_code == 200:
            return r.json().get("message", {})
    except Exception:
        pass
    return None

def fetch_openalex_by_doi(doi):
    url = f"https://api.openalex.org/works/https://doi.org/{doi}"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; Bibtool/1.0; mailto:your-email@example.com)"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            title = data.get("title", "")
            authors = []
            for au in data.get("authorships", []):
                author = au.get("author")
                if author and isinstance(author, dict):
                    display_name = author.get("display_name", "")
                    if display_name:
                        authors.append(display_name)
            year = data.get("publication_year")
            venue = data.get("host_venue", {}).get("display_name", "")
            if not venue and data.get("primary_location"):
                primary = data.get("primary_location")
                if primary and isinstance(primary, dict):
                    source = primary.get("source")
                    if source and isinstance(source, dict):
                        venue = source.get("display_name", "")
            return {
                "title": title,
                "authors": authors,
                "year": year,
                "venue": venue,
                "doi": doi,
                "volume": "",
                "number": "",
                "pages": "",
                "publisher": ""
            }
    except Exception as e:
        event_q.put(("log", f"  [OpenAlex] 请求失败: {e}"))
    return None

def fetch_openalex_by_title_search(title, author_hint=None, ref_year=None):
    cache_key = f"{title}||{author_hint}||{ref_year}"
    if cache_key in cache_oa_title:
        event_q.put(("log", f"  [缓存] OpenAlex 标题搜索命中 (结果: {'有' if cache_oa_title[cache_key] else '无'})"))
        cached = cache_oa_title[cache_key]
        if cached:
            event_q.put(("log", f"  [缓存] 缓存的论文标题: {cached.get('title', '')[:60]}"))
        return cached

    def clean_title(t):
        t = re.sub(r'[^\w\s]', ' ', t)
        return re.sub(r'\s+', ' ', t).strip()

    clean_full = clean_title(title)
    if ':' in title:
        short_part = title.split(':', 1)[0].strip()
        clean_short = clean_title(short_part)
    else:
        clean_short = clean_full

    queries = [clean_full, clean_short] if clean_short != clean_full else [clean_full]
    queries = [q for q in queries if len(q) > 5]

    best_paper = None
    best_overall_score = -1
    best_work = None
    best_authors = None
    best_venue = None
    best_year = None

    for query in queries:
        url = "https://api.openalex.org/works"
        params = {"search": query, "per-page": 20}
        headers = {"User-Agent": "Mozilla/5.0 (compatible; Bibtool/1.0)"}
        event_q.put(("log", f"  [OpenAlex] 搜索: '{query[:60]}' ..."))
        try:
            r = requests.get(url, params=params, headers=headers, timeout=10)
            if r.status_code != 200:
                event_q.put(("log", f"  [OpenAlex] 请求失败，状态码 {r.status_code}"))
                continue
            data = r.json()
            results = data.get("results", [])
            event_q.put(("log", f"  [OpenAlex] 返回 {len(results)} 条候选"))
            if not results:
                continue

            for work in results:
                cand_title = work.get("title", "")
                if not cand_title:
                    continue
                authors = []
                for au in work.get("authorships", []):
                    author = au.get("author")
                    if author and isinstance(author, dict):
                        name = author.get("display_name", "")
                        if name:
                            authors.append(name)
                cand_year = work.get("publication_year")
                venue = ""
                host_venue = work.get("host_venue")
                if host_venue and isinstance(host_venue, dict):
                    venue = host_venue.get("display_name", "")
                if not venue:
                    primary = work.get("primary_location")
                    if primary and isinstance(primary, dict):
                        source = primary.get("source")
                        if source and isinstance(source, dict):
                            venue = source.get("display_name", "")

                norm_query = normalize_title(title)
                norm_cand = normalize_title(cand_title)
                title_sim = fuzz.token_sort_ratio(norm_query, norm_cand)
                author_ok = True
                if author_hint:
                    author_ok = any(author_hint.lower() in a.lower() for a in authors)
                year_diff = abs(cand_year - ref_year) if (ref_year and cand_year) else 999
                score = title_sim
                if year_diff <= 1:
                    score += 15
                elif year_diff <= 2:
                    score += 8
                if author_ok:
                    score += 10
                if clean_short and clean_short.lower() in norm_cand:
                    score += 15
                event_q.put(("log", f"    [候选] '{cand_title[:50]}...' 相似度={title_sim:.1f} 得分={score:.1f} 作者匹配={author_ok} 年份差={year_diff}"))
                if score > best_overall_score and score >= MIN_MATCH_SCORE:
                    best_overall_score = score
                    best_work = work
                    best_authors = authors
                    best_venue = venue
                    best_year = cand_year
        except Exception as e:
            event_q.put(("log", f"  [OpenAlex] 搜索异常: {type(e).__name__} - {str(e)}"))
            continue

    if best_overall_score >= MIN_MATCH_SCORE:
        event_q.put(("log", f"  [OpenAlex] 最佳匹配得分={best_overall_score:.1f}"))
        paper = {
            "title": best_work.get("title", ""),
            "authors": best_authors,
            "year": best_year,
            "venue": best_venue,
            "doi": best_work.get("doi", ""),
            "volume": "",
            "number": "",
            "pages": "",
            "publisher": ""
        }
        biblio = best_work.get("biblio")
        if biblio and isinstance(biblio, dict):
            paper["volume"] = biblio.get("volume", "")
            paper["number"] = biblio.get("issue", "")
            paper["pages"] = biblio.get("pages", "")
        cache_oa_title[cache_key] = paper
        return paper
    else:
        event_q.put(("log", f"  [OpenAlex] 未找到合格匹配 (最高分={best_overall_score:.1f})"))
        cache_oa_title[cache_key] = None
        return None

def is_rejected_item(item):
    if not item:
        return True
    typ = item.get('type', '').lower()
    if typ in REJECTED_TYPES:
        return True
    title = " ".join(item.get('title', [])).lower()
    for pat in REJECTED_TITLE_PATTERNS:
        if re.search(pat, title):
            return True
    return False

def author_matches(item, author_hint, source='crossref'):
    if not author_hint:
        return True
    if source == 'crossref':
        authors = item.get("author", [])
        for a in authors:
            family = a.get("family", "")
            given = a.get("given", "")
            full = f"{given} {family}".strip()
            if (family and author_hint.lower() in family.lower()) or \
               (given and author_hint.lower() in given.lower()) or \
               (full and author_hint.lower() in full.lower()):
                return True
        return False
    return False

def enrich_short_title(candidate, session, user_title, author_hint, ref_year, query_venue):
    doi = candidate.get("DOI")
    if not doi:
        return candidate, False

    # 1. 先尝试 OpenAlex 通过 DOI
    oa_paper = fetch_openalex_by_doi(doi)
    if oa_paper and oa_paper.get("title"):
        title_len = len(oa_paper["title"].split())
        if title_len > 3:
            sim = fuzz.token_sort_ratio(normalize_title(user_title), normalize_title(oa_paper["title"]))
            if sim >= 50:
                event_q.put(("log", f"  [短标题补全] 通过 DOI {doi} 从 OpenAlex 获取完整标题 (相似度 {sim:.1f}): {oa_paper['title'][:80]}"))
                return oa_paper, True
            else:
                event_q.put(("log", f"  [短标题补全] OpenAlex 标题与用户标题相似度低 ({sim:.1f})，放弃"))
        else:
            event_q.put(("log", f"  [短标题补全] OpenAlex 返回标题仍过短 ({title_len} 词)，尝试备用方案"))

    # 2. 备用：用短标题（原始候选标题） + author_hint 搜索 CrossRef
    short_title = candidate.get("title", "")
    if isinstance(short_title, list):
        short_title = " ".join(short_title)
    if len(short_title.split()) <= 5 and author_hint:
        cr_list = crossref_title_with_author(session, short_title, author_hint, rows=10)
        if cr_list:
            best_cr = None
            best_score = 0
            for it in cr_list:
                raw_title = it.get("title")
                if isinstance(raw_title, list):
                    cand_full_title = " ".join(raw_title)
                else:
                    cand_full_title = raw_title or ""
                if len(cand_full_title.split()) <= 3:
                    continue
                # 必须包含用户原始标题的第一个关键词（强约束）
                first_keyword = get_first_keyword(user_title)
                if first_keyword and first_keyword.lower() not in normalize_title(cand_full_title):
                    continue
                sim = fuzz.token_sort_ratio(normalize_title(user_title), normalize_title(cand_full_title))
                authors = it.get("author", [])
                author_ok = any(author_hint.lower() in a.get("family", "").lower() or author_hint.lower() in a.get("given", "").lower() for a in authors)
                cand_year = None
                try:
                    cand_year = it.get("issued", {}).get("date-parts", [[None]])[0][0]
                except:
                    pass
                year_ok = True
                if ref_year and cand_year:
                    year_ok = abs(cand_year - ref_year) <= 2
                score = sim
                if author_ok:
                    score += 15
                if year_ok:
                    score += 10
                if score > best_score and score >= 50:
                    best_score = score
                    best_cr = it
            if best_cr:
                raw_title = best_cr.get("title")
                if isinstance(raw_title, list):
                    full_title = " ".join(raw_title)
                else:
                    full_title = raw_title or ""
                event_q.put(("log", f"  [短标题补全] 通过短标题 '{short_title}' + 作者搜索找到最佳匹配 (得分 {best_score:.1f}): {full_title[:80]}"))
                paper = {
                    "title": full_title,
                    "authors": [],
                    "year": None,
                    "venue": "",
                    "doi": best_cr.get("DOI", ""),
                    "volume": best_cr.get("volume", ""),
                    "number": best_cr.get("issue", ""),
                    "pages": best_cr.get("page", ""),
                    "publisher": best_cr.get("publisher", "")
                }
                if best_cr.get("author"):
                    for a in best_cr["author"][:10]:
                        name = f"{a.get('family','')}, {a.get('given','')}".strip(", ")
                        if name:
                            paper["authors"].append(name)
                try:
                    paper["year"] = best_cr.get("issued", {}).get("date-parts", [[None]])[0][0]
                except:
                    pass
                venue = best_cr.get("container-title")
                if venue:
                    paper["venue"] = venue[0] if isinstance(venue, list) else venue
                return paper, True

    return candidate, False

def best(items, query_title, author_hint=None, enforce_author=True, source='crossref', ref_year=None, query_venue=None, session=None, user_original_title=None):
    best_item = None
    best_score = 0
    norm_query_title = normalize_title(query_title)
    first_keyword = get_first_keyword(query_title)
    user_title_for_enrich = user_original_title if user_original_title else query_title

    # 调试输出候选列表
    event_q.put(("log", f"  [候选列表] 共 {len(items)} 条待评分"))
    for idx, it in enumerate(items):
        raw_title = it.get("title")
        if isinstance(raw_title, list):
            cand_title = " ".join(raw_title)
        else:
            cand_title = raw_title or ""
        authors = it.get("author", [])
        auth_str = ", ".join([a.get("family","") for a in authors])
        event_q.put(("log", f"    {idx+1}. {cand_title[:60]} | 作者: {auth_str}"))

    for it in items:
        if is_rejected_item(it):
            continue
        if enforce_author and author_hint:
            if not author_matches(it, author_hint, source):
                event_q.put(("log", f"    [过滤] 作者不匹配: {author_hint}"))
                continue

        if source == 'crossref':
            raw_title = it.get("title")
            if isinstance(raw_title, list):
                cand_title = " ".join(raw_title).strip()
            else:
                cand_title = raw_title or ""
            if not cand_title:
                event_q.put(("log", f"    [警告] 候选标题为空，跳过"))
                continue
            cand_venue = ""
            cv = it.get("container-title")
            if cv:
                cand_venue = cv[0] if isinstance(cv, list) else cv
            doi = it.get("DOI")
        else:
            continue

        # === 短标题强制补全逻辑 ===
        is_short = len(cand_title.split()) <= 3
        enriched = False
        if is_short and doi and session:
            enriched_paper, success = enrich_short_title(it, session, user_title_for_enrich, author_hint, ref_year, query_venue)
            if success and enriched_paper.get("title") and len(enriched_paper["title"].split()) > 3:
                cand_title = enriched_paper["title"]
                cand_venue = enriched_paper.get("venue", "")
                norm_cand_title = normalize_title(cand_title)
                title_score = fuzz.token_sort_ratio(norm_query_title, norm_cand_title)
                score = title_score + 30
                if first_keyword and first_keyword in norm_cand_title:
                    score += 10
                if ref_year and enriched_paper.get("year"):
                    year_diff = abs(enriched_paper["year"] - ref_year)
                    if year_diff <= 1:
                        score += 10
                    elif year_diff <= 2:
                        score += 5
                if author_hint and any(author_hint.lower() in a.lower() for a in enriched_paper.get("authors", [])):
                    score += 5
                temp_item = {
                    "title": cand_title,
                    "container-title": [cand_venue] if cand_venue else [],
                    "issued": {"date-parts": [[enriched_paper.get("year")]]} if enriched_paper.get("year") else {},
                    "author": [{"family": a.split()[-1], "given": " ".join(a.split()[:-1])} for a in enriched_paper.get("authors", [])],
                    "DOI": enriched_paper.get("doi", "")
                }
                it = temp_item
                enriched = True
                event_q.put(("log", f"    [短标题补全] 成功补全，新标题: {cand_title[:80]}, 重算得分 {score:.1f}"))
                if score > best_score and score >= MIN_MATCH_SCORE:
                    best_score = score
                    best_item = it
                continue

        # 正常评分流程
        norm_cand_title = normalize_title(cand_title)
        title_score = fuzz.token_sort_ratio(norm_query_title, norm_cand_title)
        score = title_score

        if not enriched and len(cand_title.split()) <= 3 and first_keyword and first_keyword in norm_cand_title:
            score = max(score, 70) + 10
            event_q.put(("log", f"    [截断补偿] 候选标题过短但包含关键词 '{first_keyword}'，提升至 {score:.1f}"))
        else:
            if first_keyword and first_keyword in norm_cand_title:
                score += 10
                event_q.put(("log", f"    [首词匹配] +10分，当前得分 {score:.1f}"))

        cand_year = None
        try:
            cand_year = it.get('issued', {}).get('date-parts', [[None]])[0][0]
        except:
            pass
        if ref_year and cand_year:
            year_diff = abs(cand_year - ref_year)
            if year_diff <= 1:
                score += 10
            elif year_diff <= 2:
                score += 5

        if author_hint:
            score += 5

        if query_venue and cand_venue and len(query_venue) > 5 and len(cand_venue) > 5 and score < 80:
            venue_score = fuzz.token_sort_ratio(normalize_title(query_venue), normalize_title(cand_venue))
            if venue_score > 50:
                score = title_score * 0.85 + venue_score * 0.15
                event_q.put(("log", f"    [venue匹配] 综合={score:.1f}"))

        short_title_display = cand_title[:80] + "..." if len(cand_title) > 80 else cand_title
        event_q.put(("log", f"    [候选] '{short_title_display}' 得分: {score:.1f} (标题:{title_score:.1f})"))
        if score > best_score and score >= MIN_MATCH_SCORE:
            best_score = score
            best_item = it

    if best_item:
        event_q.put(("log", f"  [匹配] 最佳得分: {best_score:.1f} (>= {MIN_MATCH_SCORE})"))
    else:
        event_q.put(("log", f"  [匹配] 无合格匹配（最高分 {best_score:.1f} < {MIN_MATCH_SCORE}）"))
    return best_item, best_score

def map_type(cr):
    if not cr:
        return "article"
    t = cr.get('type', '').lower()
    if t == 'journal-article':
        return 'article'
    if 'proceedings' in t or 'conference' in t:
        return 'inproceedings'
    if 'book' in t:
        return 'book'
    if 'book-chapter' in t:
        return 'incollection'
    return 'article'

def merge_with_crossref(oa_paper, session):
    doi = oa_paper.get("doi")
    if doi:
        cr_data = crossref_doi(session, doi)
        if cr_data:
            oa_paper["volume"] = cr_data.get("volume", "")
            oa_paper["number"] = cr_data.get("issue", "")
            oa_paper["pages"] = cr_data.get("page", "")
            oa_paper["publisher"] = cr_data.get("publisher", "")
            event_q.put(("log", f"  [CrossRef] 通过 DOI 补全卷期页码"))
            return True
    title = oa_paper["title"]
    author_hint = oa_paper["authors"][0].split()[-1] if oa_paper["authors"] else ""
    cr_list = crossref_title_with_author(session, title, author_hint, rows=5)
    if cr_list:
        best_item, _ = best(cr_list, title, author_hint=author_hint, enforce_author=True, source='crossref', session=session)
        if best_item:
            oa_paper["volume"] = best_item.get("volume", "")
            oa_paper["number"] = best_item.get("issue", "")
            oa_paper["pages"] = best_item.get("page", "")
            oa_paper["publisher"] = best_item.get("publisher", "")
            event_q.put(("log", f"  [CrossRef] 通过标题+作者补全卷期页码"))
            return True
    event_q.put(("log", f"  [CrossRef] 无法补全卷期页码"))
    return False

def build_from_crossref(cr, user_title=None, session=None):
    p = {}
    doi = cr.get("DOI") if cr else ""
    oa_data = None
    if doi:
        oa_data = fetch_openalex_by_doi(doi)
    final_title = None
    if oa_data and oa_data.get("title"):
        final_title = oa_data["title"]
        event_q.put(("log", f"  [OpenAlex] 获取到完整标题: {final_title[:80]}..."))
    elif user_title and len(user_title) > 20:
        final_title = user_title
    else:
        raw_title = cr.get("title")
        if isinstance(raw_title, list):
            final_title = " ".join(raw_title)
        else:
            final_title = raw_title or ""
    final_title = re.sub(r'\s+', ' ', final_title).strip()
    p["title"] = final_title
    try:
        p["year"] = cr.get("issued", {}).get("date-parts", [[None]])[0][0] if cr else None
    except:
        p["year"] = None
    if oa_data and oa_data.get("year"):
        p["year"] = oa_data["year"]
    p["doi"] = doi
    p["venue"] = ""
    if cr and cr.get("container-title"):
        p["venue"] = cr["container-title"][0]
    if oa_data and oa_data.get("venue"):
        p["venue"] = oa_data["venue"]
    p["publisher"] = cr.get("publisher", "") if cr else ""
    p["volume"] = cr.get("volume", "") if cr else ""
    p["number"] = cr.get("issue", "") if cr else ""
    p["pages"] = cr.get("page", "") if cr else ""
    p["authors"] = []
    if oa_data and oa_data.get("authors"):
        p["authors"] = oa_data["authors"]
    elif cr and cr.get("author"):
        for a in cr["author"][:10]:
            name = f"{a.get('family','')}, {a.get('given','')}".strip(", ")
            if name:
                p["authors"].append(name)
    return p

def to_bib(p, btype, key):
    bib = f"@{btype}{{{key},\n"
    def add(k, v):
        nonlocal bib
        if v:
            bib += f"  {k} = {{{v}}},\n"
    add("author", " and ".join(p.get("authors", [])))
    add("title", p.get("title", ""))
    if btype == "article":
        add("journal", p.get("venue", ""))
    else:
        add("booktitle", p.get("venue", ""))
    add("volume", p.get("volume", ""))
    add("number", p.get("number", ""))
    add("pages", p.get("pages", ""))
    add("year", p.get("year", ""))
    add("publisher", p.get("publisher", ""))
    add("doi", p.get("doi", ""))
    bib += "}\n\n"
    return bib

def update_stats(index, status, reason=None):
    with stats_lock:
        stats['completed'] += 1
        if status == 'success':
            stats['success'] += 1
        elif status == 'fail':
            stats['fail'] += 1
            if reason and len(stats['failures']) < 10:
                stats['failures'].append((index, reason))
        elif status == 'duplicate':
            stats['duplicate'] += 1
        completed = stats['completed']
        total = stats['total']
        success = stats['success']
        fail = stats['fail']
        dup = stats['duplicate']
        msg = f"进度: {completed}/{total} | ✅成功:{success} ❌失败:{fail} 🔄重复:{dup}"
        event_q.put(("stat_set", msg))
        if completed == total:
            final_msg = f"\n========== 最终统计 ==========\n总条目: {total}\n成功: {success}\n失败: {fail}\n重复合并: {dup}\n"
            if stats['failures']:
                final_msg += "\n失败条目详情:\n"
                for idx, reason in stats['failures']:
                    final_msg += f"  #{idx}: {reason}\n"
            event_q.put(("stat", final_msg))
            event_q.put(("log", "所有任务处理完毕。"))

def worker(i, total, raw_ref):
    global title_seen, seen
    session = create_robust_session()
    try:
        event_q.put(("log", f"[{i}/{total}] 开始处理: {raw_ref[:80]}..."))
        time.sleep(2)

        year_match = re.search(r'\b(19|20)\d{2}\b', raw_ref)
        ref_year = int(year_match.group(0)) if year_match else None

        user_title = extract_paper_title(raw_ref)
        if not user_title or len(user_title) < 8:
            user_title = raw_ref[:120]
        author_hint = extract_author_hint(raw_ref)
        ref_venue = extract_venue(raw_ref)
        event_q.put(("log", f"[{i}] 提取标题: {user_title[:80]}"))
        if author_hint:
            event_q.put(("log", f"[{i}] 提取作者: {author_hint}"))
        else:
            event_q.put(("log", f"[{i}] 未能提取作者"))
        if ref_venue:
            event_q.put(("log", f"[{i}] 提取会议/期刊: {ref_venue}"))
        else:
            event_q.put(("log", f"[{i}] 未能提取会议/期刊"))

        # DOI 直接查询
        doi_match = re.search(r'10\.\d{4,9}/[-._;()/:A-Z0-9]+', raw_ref, re.I)
        if doi_match:
            doi = doi_match.group(0)
            event_q.put(("log", f"[{i}] 发现DOI: {doi}，尝试直接查询"))
            full = crossref_doi(session, doi)
            if full and not is_rejected_item(full):
                paper = build_from_crossref(full, user_title=None, session=session)
                extracted_title = extract_paper_title(raw_ref)
                if extracted_title:
                    sim = fuzz.token_sort_ratio(normalize_title(extracted_title), normalize_title(paper["title"]))
                    if sim < MIN_TITLE_SIMILARITY:
                        event_q.put(("log", f"[{i}] DOI标题相似度过低({sim})，拒绝"))
                        update_stats(i, 'fail', reason=f"DOI标题相似度{sim}<{MIN_TITLE_SIMILARITY}")
                        return
                btype = map_type(full)
                first_author = paper.get("authors", [""])[0] if paper.get("authors") else None
                key = make_short_key(paper["title"], paper.get("year"), first_author)
                full_title_key = norm(paper["title"])
                with stats_lock:
                    if full_title_key in title_seen:
                        event_q.put(("log", f"[{i}] ⚠️ 重复条目，已合并"))
                        update_stats(i, 'duplicate')
                        return
                    title_seen.add(full_title_key)
                    seen[key] = paper
                bib = to_bib(paper, btype, key)
                event_q.put(("bib", bib))
                event_q.put(("log", f"[{i}] ✅ DOI 命中 (OpenAlex 增强)"))
                update_stats(i, 'success')
                return
            else:
                event_q.put(("log", f"[{i}] DOI查询失败或被拒绝"))

        # OpenAlex 标题搜索
        oa_paper = fetch_openalex_by_title_search(user_title, author_hint, ref_year)
        oa_score = -1
        oa_candidate = None
        if oa_paper:
            norm_oa_title = normalize_title(oa_paper["title"])
            norm_query_title = normalize_title(user_title)
            title_sim_oa = fuzz.token_sort_ratio(norm_query_title, norm_oa_title)
            venue_sim_oa = 0
            if ref_venue and oa_paper["venue"]:
                venue_sim_oa = fuzz.token_sort_ratio(normalize_title(ref_venue), normalize_title(oa_paper["venue"]))
            oa_score = title_sim_oa
            first_kw = get_first_keyword(user_title)
            if first_kw and first_kw in norm_oa_title:
                oa_score += 10
            if venue_sim_oa > 50:
                oa_score = title_sim_oa * 0.85 + venue_sim_oa * 0.15
            if ref_year and oa_paper["year"]:
                year_diff = abs(oa_paper["year"] - ref_year)
                if year_diff <= 1:
                    oa_score += 10
                elif year_diff <= 2:
                    oa_score += 5
            if author_hint and any(author_hint.lower() in a.lower() for a in oa_paper["authors"]):
                oa_score += 5
            event_q.put(("log", f"[{i}] OpenAlex 候选综合得分: {oa_score:.1f}"))
            if oa_score >= MIN_MATCH_SCORE:
                oa_candidate = oa_paper
            else:
                event_q.put(("log", f"[{i}] OpenAlex 得分低于阈值，放弃"))

        # CrossRef 搜索
        cr_best = None
        cr_score = -1
        if author_hint:
            cr_list = crossref_title_with_author(session, user_title, author_hint, rows=CROSSREF_ROWS)
            event_q.put(("log", f"[{i}] CrossRef 标题+作者查询返回 {len(cr_list)} 条候选"))
            cr_best, cr_score = best(cr_list, user_title, author_hint=author_hint, enforce_author=True,
                                      source='crossref', ref_year=ref_year, query_venue=ref_venue, session=session,
                                      user_original_title=user_title)
        if not cr_best and author_hint:
            cr_list = crossref_title(session, user_title, rows=CROSSREF_ROWS)
            event_q.put(("log", f"[{i}] 纯标题查询返回 {len(cr_list)} 条候选，强制作者过滤"))
            cr_best, cr_score = best(cr_list, user_title, author_hint=author_hint, enforce_author=True,
                                      source='crossref', ref_year=ref_year, query_venue=ref_venue, session=session,
                                      user_original_title=user_title)
        if not cr_best and not author_hint:
            cr_list = crossref_title(session, user_title, rows=CROSSREF_ROWS)
            event_q.put(("log", f"[{i}] 无作者信息，无约束纯标题查询"))
            cr_best, cr_score = best(cr_list, user_title, author_hint=None, enforce_author=False,
                                      source='crossref', ref_year=ref_year, query_venue=ref_venue, session=session,
                                      user_original_title=user_title)

        # 选择最佳
        final_paper = None
        final_source = None
        if oa_candidate and (cr_best is None or oa_score >= cr_score):
            final_paper = oa_candidate
            final_source = "OpenAlex"
            event_q.put(("log", f"[{i}] 选择 OpenAlex 结果 (得分 {oa_score:.1f} vs CrossRef {cr_score:.1f})"))
            merge_with_crossref(final_paper, session)
        elif cr_best:
            final_paper = build_from_crossref(cr_best, user_title=user_title, session=session)
            final_source = "CrossRef"
            event_q.put(("log", f"[{i}] 选择 CrossRef 结果 (得分 {cr_score:.1f})"))

        if not final_paper:
            event_q.put(("log", f"[{i}] ❌ 所有途径均未找到合格匹配"))
            update_stats(i, 'fail', reason="无合格匹配")
            return

        if not final_paper.get("title"):
            event_q.put(("log", f"[{i}] ⚠️ 空标题"))
            update_stats(i, 'fail', reason="空标题")
            return

        # 最终验证：计算最终论文标题与用户原始标题的相似度
        final_title_sim = fuzz.token_sort_ratio(normalize_title(user_title), normalize_title(final_paper["title"]))
        event_q.put(("log", f"[{i}] 最终标题相似度: {final_title_sim:.1f}"))
        if final_title_sim < FINAL_SIMILARITY_THRESHOLD:
            event_q.put(("log", f"[{i}] ❌ 最终论文标题相似度过低 ({final_title_sim:.1f} < {FINAL_SIMILARITY_THRESHOLD})，拒绝输出"))
            update_stats(i, 'fail', reason=f"标题相似度{final_title_sim:.1f}<{FINAL_SIMILARITY_THRESHOLD}")
            return

        first_author = final_paper.get("authors", [""])[0] if final_paper.get("authors") else None
        key = make_short_key(final_paper["title"], final_paper.get("year"), first_author)
        full_title_key = norm(final_paper["title"])
        with stats_lock:
            if full_title_key in title_seen:
                event_q.put(("log", f"[{i}] ⚠️ 重复条目，已合并"))
                update_stats(i, 'duplicate')
                return
            title_seen.add(full_title_key)
            seen[key] = final_paper

        btype = 'article'
        if final_paper.get("venue"):
            if re.search(r'proceedings|conference|workshop|symposium', final_paper["venue"], re.I):
                btype = 'inproceedings'
        bib = to_bib(final_paper, btype, key)
        event_q.put(("bib", bib))
        event_q.put(("log", f"[{i}] 🎉 完成 (来源: {final_source})"))
        update_stats(i, 'success')

    except Exception as e:
        event_q.put(("log", f"[{i}] 💥 未捕获异常: {type(e).__name__} - {str(e)}"))
        update_stats(i, 'fail', reason=f"异常: {type(e).__name__}")
    finally:
        session.close()

def start_next_task():
    global current_task_index, processing_active, refs_queue
    if not processing_active:
        return
    if current_task_index >= len(refs_queue):
        event_q.put(("log", "所有任务已提交完毕。"))
        processing_active = False
        return
    i = current_task_index
    ref = refs_queue[i]
    current_task_index += 1
    t = threading.Thread(target=worker_wrapper, args=(i+1, len(refs_queue), ref))
    t.daemon = True
    t.start()

def worker_wrapper(i, total, raw_ref):
    try:
        worker(i, total, raw_ref)
    except Exception as e:
        event_q.put(("log", f"[{i}] 致命错误: {type(e).__name__} - {str(e)}"))
        update_stats(i, 'fail', reason=f"致命错误: {type(e).__name__}")
    finally:
        start_next_task()

def run():
    global processing_active, refs_queue, current_task_index, seen, stats, title_seen
    log_box.delete("1.0", tk.END)
    bib_box.delete("1.0", tk.END)
    event_q.put(("stat_clear", None))

    seen = {}
    title_seen = set()
    with stats_lock:
        stats = {
            'total': 0,
            'completed': 0,
            'success': 0,
            'fail': 0,
            'duplicate': 0,
            'failures': []
        }

    raw_text = input_box.get("1.0", tk.END)
    refs = split_refs(raw_text)
    if not refs:
        event_q.put(("log", "未检测到参考文献，请确保每行以数字序号开头（如 '1. ...')"))
        return

    total = len(refs)
    with stats_lock:
        stats['total'] = total

    event_q.put(("log", f"共发现 {total} 条参考文献"))
    event_q.put(("log", "匹配策略: OpenAlex 标题搜索 + CrossRef 降级，综合评分包含标题、期刊/会议、年份、作者，支持短标题自动补全"))
    event_q.put(("stat_set", f"总任务: {total} | 完整元数据模式"))

    processing_active = True
    refs_queue = refs
    current_task_index = 0
    start_next_task()

# =========================
# GUI
# =========================
root = tk.Tk()
root.title("BibTeX 生成器")
root.geometry("1500x950")

tk.Label(root, text="将论文里的参考文献列表拷贝到下方区域（每行一条，格式：序号 内容）").pack()
input_box = scrolledtext.ScrolledText(root, height=10)
input_box.pack(fill=tk.BOTH, expand=False, pady=5)

btn_frame = tk.Frame(root)
btn_frame.pack()
tk.Button(btn_frame, text="▶ 运行查询，自动根据文献信息进行网络搜寻抓去论文元素", command=run, bg="#4CAF50", fg="white", padx=20).pack(pady=5)

frame = tk.Frame(root)
frame.pack(fill=tk.BOTH, expand=True, pady=5)

log_box = scrolledtext.ScrolledText(frame, width=60, bg="#1e1e1e", fg="#d4d4d4", insertbackground="white")
log_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

bib_box = scrolledtext.ScrolledText(frame, width=80, bg="#fefefe", fg="#000000")
bib_box.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

stat_box = scrolledtext.ScrolledText(root, height=8, bg="#fffacd")
stat_box.pack(fill=tk.BOTH, pady=5)

root.after(int(1000 / UI_FPS), dispatch)
root.mainloop()