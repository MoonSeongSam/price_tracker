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


def naver_shop(query, client_id, client_secret, display=40):
    """네이버 쇼핑 검색 API (새 상품, 무료 하루 25,000회). 키가 없으면 빈 목록."""
    if not client_id or not client_secret:
        return []
    q = urllib.parse.quote(query)
    url = (f"https://openapi.naver.com/v1/search/shop.json?query={q}"
           f"&display={display}&sort=sim&exclude=used:rental:cbshop")
    data = json.loads(_get(url, {"X-Naver-Client-Id": client_id,
                                 "X-Naver-Client-Secret": client_secret}))
    out = []
    for it in data.get("items", []):
        title = unescape(re.sub(r"<[^>]+>", "", it.get("title", "")))
        out.append({
            "site": "새상품·" + (it.get("mallName") or "네이버"),
            "id": f"nv_{it.get('productId')}",
            "title": title,
            "content": "",
            "price": int(it.get("lprice") or 0),
            "url": it.get("link", ""),
            "where": "",
            "time": "",
        })
    return out
