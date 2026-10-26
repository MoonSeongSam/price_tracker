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


# ---------- 편집샵 (새 상품) ----------
BODY = dict(h=(177, 185), w=(76, 86))  # 후기에서 '비슷한 체형'으로 볼 범위


def _post_json(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"User-Agent": UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def musinsa(keyword, sort="POPULAR", pages=1):
    """무신사 남성 상품 검색. sort: POPULAR / NEW / DISCOUNT_RATE"""
    q = urllib.parse.quote(keyword)
    out = []
    for page in range(1, pages + 1):
        url = (f"https://api.musinsa.com/api2/dp/v1/plp/goods?keyword={q}&gf=M"
               f"&sortCode={sort}&page={page}&size=60&caller=SEARCH")
        for g in json.loads(_get(url))["data"]["list"]:
            if g.get("isSoldOut") or g.get("isAd"):
                continue
            out.append({
                "site": "무신사",
                "id": f"ms_{g['goodsNo']}",
                "no": g["goodsNo"],
                "brand": g.get("brand", ""),
                "brand_name": g.get("brandName", ""),
                "title": f"{g.get('brandName', '')} {g.get('goodsName', '')}",
                "content": "",
                "price": int(g.get("finalPrice") or g.get("price") or 0),
                "normal": int(g.get("normalPrice") or 0),
                "discount": int(g.get("finalDiscount") or g.get("saleRate") or 0),
                "reviews": int(g.get("reviewCount") or 0),
                "score": float(g.get("reviewScore") or 0),
                "url": g.get("goodsLinkUrl") or f"https://www.musinsa.com/products/{g['goodsNo']}",
                "where": "",
                "time": "",
            })
    return out


def c29(keyword, pages=1):
    """29CM 상품 검색."""
    q = urllib.parse.quote(keyword)
    out = []
    for page in range(1, pages + 1):
        url = f"https://search-api.29cm.co.kr/api/v4/products/search?keyword={q}&page={page}&size=50"
        for g in json.loads(_get(url))["data"]["products"]:
            if g.get("isSoldOut"):
                continue
            out.append({
                "site": "29CM",
                "id": f"c29_{g['itemNo']}",
                "no": g["itemNo"],
                "brand": (g.get("frontBrandNameEng") or "").lower(),
                "brand_name": g.get("frontBrandNameKor", ""),
                "title": f"{g.get('frontBrandNameKor', '')} {g.get('itemName', '')}",
                "content": "",
                "price": int(g.get("lastSalePrice") or g.get("consumerPrice") or 0),
                "normal": int(g.get("consumerPrice") or 0),
                "discount": int(g.get("lastSalePercent") or 0),
                "reviews": int(g.get("reviewCount") or 0),
                "score": float(g.get("reviewAveragePoint") or 0),
                "url": f"https://product.29cm.co.kr/catalog/{g['itemNo']}",
                "where": "",
                "time": "",
            })
    return out


def musinsa_stock(goods_no, want):
    """무신사: want(사이즈 이름)이 True인 사이즈 중 재고 있는 것."""
    data = json.loads(_get(f"https://goods-detail.musinsa.com/api2/goods/{goods_no}/options"))["data"]
    found = []
    for item in data.get("optionItems") or []:
        if item.get("isDeleted") or not item.get("activated", True):
            continue
        vals = item.get("optionValues") or []
        size = next((v["name"] for v in vals if re.search(r"size|사이즈", v.get("optionName", ""), re.I)),
                    vals[-1]["name"] if vals else "")
        if not size or size in found or not want(size):
            continue
        inv = _post_json(f"https://goods-detail.musinsa.com/api2/goods/{goods_no}/options/v2/prioritized-inventories",
                         {"optionValueNos": item["optionValueNos"]})
        if any(not x.get("outOfStock") for x in inv.get("data") or []):
            found.append(size)
    return found


def c29_stock(item_no, want):
    """29CM: want가 True인 사이즈 중 재고 있는 것 (색상·사이즈 겹옵션도 처리)."""
    d = json.loads(_get(f"https://cache.29cm.co.kr/item/detail/{item_no}/"))
    found = []

    def walk(lst):
        for x in lst or []:
            if x.get("list"):
                walk(x["list"])
                continue
            size = (x.get("title") or "").strip()
            ok = x.get("option_stock_status") == 1 and not (
                x.get("is_limited_qty") == "T" and (x.get("limited_qty") or 0) <= 0)
            if size and ok and want(size) and size not in found:
                found.append(size)

    opts = d.get("option_items") or {}
    if not opts.get("list") and d.get("use_option") in ("F", False):
        return ["FREE"] if want("FREE") else []
    walk(opts.get("list"))
    return found


def _similar(h, w):
    return BODY["h"][0] <= h <= BODY["h"][1] and BODY["w"][0] <= w <= BODY["w"][1]


def musinsa_fit(goods_no, pages=3):
    """이 상품 후기 중 비슷한 체형(177~185cm·76~86kg 남성)이 고른 사이즈 개수."""
    from collections import Counter
    c = Counter()
    for page in range(pages):
        url = (f"https://goods.musinsa.com/api2/review/v1/view/list?page={page}&pageSize=20"
               f"&goodsNo={goods_no}&sort=up_cnt_desc&myFilter=false&hasPhoto=false&isExperience=false")
        rows = json.loads(_get(url))["data"]["list"]
        if not rows:
            break
        for r in rows:
            u = r.get("userProfileInfo") or {}
            if u.get("reviewSex") == "남성" and _similar(u.get("userHeight") or 0, u.get("userWeight") or 0):
                c[(r.get("goodsOption") or "").split("·")[-1].split("/")[-1].strip()] += 1
    return c


def c29_fit(item_no, pages=3):
    from collections import Counter
    c = Counter()
    for page in range(pages):
        rows = json.loads(_get(f"https://review-api.29cm.co.kr/api/v4/reviews?itemId={item_no}&page={page}&size=20"))["data"]["results"]
        if not rows:
            break
        for r in rows:
            try:
                h = int(r["userSize"][0].replace("cm", ""))
                w = int(r["userSize"][1].replace("kg", ""))
            except Exception:
                continue
            if _similar(h, w):
                m = re.search(r"\]\s*([^\s\[]+)\s*$", " ".join(r.get("optionValue") or []))
                c[m.group(1) if m else ""] += 1
    return c


BJ_CONDITION = {"NEW": "새상품", "LIKE_NEW": "거의 새것", "LIGHTLY_USED": "사용감 적음",
                "USED": "사용감 있음", "HEAVILY_USED": "사용감 많음"}


def bunjang_detail(pid):
    """번장 상세: (본문 + 사이즈 옵션 글자, 상태)."""
    p = json.loads(_get(f"https://api.bunjang.co.kr/api/pms/v3/products-detail/{pid}?viewerUid=-1"))["data"]["product"]
    sizes = " ".join(f"사이즈 {o.get('optionValue', '')}" for o in p.get("options") or []
                     if "사이즈" in (o.get("optionGroupName") or ""))
    return (p.get("description") or "") + " " + sizes, BJ_CONDITION.get(p.get("condition"), "")
