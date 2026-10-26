"""사이트별 검색. 모두 표준 라이브러리만 사용 (설치 시간 절약)."""
import json
import re
import urllib.parse
import urllib.request
from html import unescape

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def _get(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")


def bunjang(query, size=60):
    """번개장터 최신순 판매중 매물."""
    q = urllib.parse.quote(query)
    url = ("https://api.bunjang.co.kr/api/search/v8/web/search"
           f"?q={q}&size={size}&policyKey=pw.product.keyword&sort=latest")
    data = json.loads(_get(url, {"Accept": "application/json"}))
    rows = data["data"]["responses"]["mainGrid"]["searchResponse"]["data"]
    out = []
    for p in rows:
        if p.get("type") != "PRODUCT" or p.get("ad") or p.get("status") != "SELLING":
            continue
        out.append({
            "site": "번장",
            "id": f"bj_{p['pid']}",
            "title": p.get("name", ""),
            "content": "",
            "price": int(p.get("price") or 0),
            "url": f"https://m.bunjang.co.kr/products/{p['pid']}",
            "where": "",
            "time": p.get("updatedAt", ""),
        })
    return out


def daangn(query, region_id):
    """당근 동네 검색 (주변 동네 포함). region_id 예: 228(신수동)."""
    q = urllib.parse.quote(query)
    url = f"https://www.daangn.com/kr/buy-sell/?in=x-{region_id}&search={q}"
    html = _get(url)
    i = html.find('"buySellArticles":')
    if i < 0:
        return []
    arr, _ = json.JSONDecoder().raw_decode(html, i + len('"buySellArticles":'))
    out = []
    for a in arr:
        if a.get("status") != "Ongoing":
            continue
        out.append({
            "site": "당근",
            "id": "dg_" + a["href"].rstrip("/").rsplit("-", 1)[-1],
            "title": a.get("title", ""),
            "content": a.get("content", ""),
            "price": int(float(a.get("price") or 0)),
            "url": "https://www.daangn.com" + a["href"],
            "where": (a.get("region") or {}).get("name", ""),
            "time": a.get("createdAt", ""),
        })
    return out


def _unesc(s):
    """다나와 데이터 속 유니코드 표기(역슬래시 u003c 등)를 원래 글자로."""
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)


def danawa(query):
    """다나와 가격비교 검색 (새 상품 최저가). 네이버 쇼핑 API는 2026-07-31 종료."""
    q = urllib.parse.quote(query)
    t = _get(f"https://search.danawa.com/dsearch.php?query={q}").replace('\\"', '"')
    marks = [m for m in re.finditer(r'"productSeq":"(\d+)"', t)]
    out, seen = [], set()
    for n, m in enumerate(marks):
        end = marks[n + 1].start() if n + 1 < len(marks) else m.start() + 8000
        seg = t[m.start():end]
        name = re.search(r'"productName":"([^"]*)"', seg)
        price = re.search(r'"minPriceNumber":(\d+)', seg)
        if not name or not price or int(price.group(1)) == 0 or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        title = _unesc(name.group(1))
        title = unescape(re.sub(r"<[^>]+>", "", title))
        bundle = re.search(r'"bundleName":"([^"]*)"', seg)
        if bundle and bundle.group(1):
            b = _unesc(bundle.group(1))
            title += f" ({unescape(re.sub(r'<[^>]+>', '', b))})"
        out.append({
            "site": "새상품·다나와",
            "id": f"dw_{m.group(1)}",
            "title": title,
            "content": "",
            "price": int(price.group(1)),
            "url": f"https://prod.danawa.com/info/?pcode={m.group(1)}",
            "where": "",
            "time": "",
        })
    return out
