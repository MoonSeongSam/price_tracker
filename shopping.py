"""브랜드·상품 추적: 사이즈 판단, 추적 목록 처리.

추적 목록은 state/watches.json (텔레그램 명령으로 추가·중단).
사이즈·동네는 비밀값 CONFIG_JSON에만 있음.
"""
import hashlib
import re
import time
from datetime import datetime, timedelta, timezone

import sources

KST = timezone(timedelta(hours=9))

def hid(s):
    """공개 저장소에 매물 주소가 드러나지 않게 짧은 해시로 저장."""
    return hashlib.sha1(s.encode()).hexdigest()[:12]


# ---------- 품목 구분 ----------
TOP = r"자켓|재킷|셔츠|셔켓|코트|블루종|점퍼|파카|패딩|다운|가디건|니트|스웨터|후드|후디|맨투맨|스웨트셔츠|티셔츠|반팔|긴팔|베스트|조끼|블레이저|폴로|탑|jacket|coat|shirt|blouson|cardigan|knit|hood|sweat|vest|blazer|tee|top"
BOTTOM = r"팬츠|슬랙스|데님팬츠|청바지|진$|쇼츠|반바지|트라우저|치노|카고|조거|버뮤다|바지|pants|trouser|jeans|shorts|slacks"
DRESS = r"구두|로퍼|더비|옥스포드|옥스퍼드|몽크|loafer|derby|oxford"
SHOES = r"스니커즈|운동화|신발|슈즈|부츠|샌들|뮬|슬리퍼|sneaker|boots?|shoes|sandal|mule"
GOODS = r"삭스|양말|가방|백팩|토트|숄더백|크로스백|모자|볼캡|비니|벨트|머플러|스카프|지갑|양말|목도리|장갑|키링|bag|cap|beanie|belt|muffler|scarf|wallet|socks"


def category(title):
    t = title.lower()
    for name, pat in (("잡화", GOODS), ("구두", DRESS), ("신발", SHOES), ("상의", TOP), ("하의", BOTTOM)):
        if re.search(pat, t, re.I):
            return name
    return "상의"


def wanted(cfg, brand, cat):
    """이 브랜드·품목에서 내가 입는 사이즈 목록. 잡화는 사이즈 무관(None)."""
    if cat == "잡화":
        return None
    if brand and cat in (brand.get("sizes") or {}):
        return brand["sizes"][cat]
    return cfg["sizes"].get(cat, [])


def norm_size(s):
    s = s.upper().replace("MM", "").strip()
    return [t for t in re.split(r"[^0-9A-Z]+", s) if t]


def option_ok(label, want):
    """편집샵 옵션 이름(예: 'XL', '05(XL)', '275mm', 'FREE')이 내 사이즈인지."""
    toks = norm_size(label)
    if any(t in ("FREE", "F", "OS", "ONESIZE") for t in toks):
        return True
    return any(w.upper() in toks for w in want)


# 중고 글에서 사이즈 찾기. 숫자는 가격·날짜와 헷갈리지 않게 경계를 엄격하게 봄
_B = r"(?<![0-9A-Za-z가-힣,.])"
_E = r"(?![0-9A-Za-z가-힣%]|[,.]\d)"
_CTX = r"(?:사이즈|size|sz|싸이즈)\s*[:：]?\s*"
LETTERS = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "3XL"]


def text_sizes(text):
    """글에 적힌 사이즈 표기들."""
    found = set()
    for s in LETTERS:
        if re.search(_B + s + _E, text, re.I):
            found.add(s)
        if re.search(_CTX + s + _E, text, re.I):
            found.add(s)
        if re.search(_B + s + r"\s*(?:사이즈|size|싸이즈)", text, re.I):
            found.add(s)
    # 신발 220~300, 허리 26~38, 한국식 90~110: 단독으로 써도 사이즈로 봄
    for m in re.finditer(_B + r"(2[2-9]\d|300|2[6-9]|3[0-8]|9[05]|10[05]|110)" + _E, text):
        found.add(m.group(1))
    for m in re.finditer(_B + r"(2[2-9][05]|300)\s*mm", text, re.I):
        found.add(m.group(1))
    # 1~5, 44~56(유럽식): '사이즈 3', '3사이즈', '(3)' 처럼 표시가 있을 때만
    for m in re.finditer(_CTX + r"([1-5]|4[4-9]|5[0-6])" + _E, text, re.I):
        found.add(m.group(1))
    for m in re.finditer(_B + r"([1-5]|4[4-9]|5[0-6])\s*(?:사이즈|size)", text, re.I):
        found.add(m.group(1))
    for m in re.finditer(r"\(\s*([1-5]|4[4-9]|5[0-6])\s*\)", text):
        found.add(m.group(1))
    return found


def used_size_match(text, want):
    """True: 내 사이즈 있음 / False: 다른 사이즈만 / None: 사이즈 안 적힘"""
    if want is None:
        return True
    found = text_sizes(text)
    if not found:
        return None
    return any(w.upper() in {f.upper() for f in found} for w in want)


# ---------- 브랜드 ----------
def all_brands(cfg, extra):
    return list(cfg.get("brands", [])) + list(extra)


def find_brand(brands, text):
    t = text.lower().replace(" ", "")
    for b in brands:
        for a in [b["name"]] + b.get("aliases", []):
            if a.lower().replace(" ", "") in t:
                return b
    return None


def strip_brand(query, brand):
    q = query
    if brand:
        for a in sorted([brand["name"]] + brand.get("aliases", []), key=len, reverse=True):
            q = re.sub(re.escape(a), " ", q, flags=re.I)
    return [w for w in q.split() if w]


def shop_brand_ok(p, brand):
    if not brand:
        return True
    if p["site"] == "무신사":
        return brand.get("musinsa") and p["brand"] == brand["musinsa"].lower()
    if p["site"] == "29CM":
        return brand.get("c29") and p["brand"] == brand["c29"].lower()
    return True


def has_words(p, words):
    t = (p["title"] + " " + p["content"]).lower().replace(" ", "")
    return all(w.lower() in t for w in words)


# ---------- 수집 ----------
def fetch(jobs, errors):
    out = {}
    for name, job in jobs:
        try:
            for p in job():
                out.setdefault(p["id"], p)
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}")
        time.sleep(1)
    return list(out.values())


def stock_check(p, want, cache):
    """편집샵 상품의 내 사이즈 재고. 6시간 동안은 저장된 결과 재사용."""
    key = p["id"]
    now = time.time()
    c = cache.get(key)
    if c and now - c["at"] < 6 * 3600:
        return c["sizes"]
    fn = sources.musinsa_stock if p["site"] == "무신사" else sources.c29_stock
    try:
        sizes = fn(p["no"], lambda s: want is None or option_ok(s, want))
    except Exception:
        return []
    cache[key] = {"at": now, "sizes": sizes}
    time.sleep(0.5)
    return sizes


def bunjang_check(p, cache):
    """번장 상세의 본문·사이즈 칸으로 사이즈 다시 판단. 6시간 동안 결과 재사용."""
    key = "bjd_" + p["id"]
    c = cache.get(key)
    if not c or time.time() - c["at"] >= 6 * 3600:
        try:
            text, cond = sources.bunjang_detail(p["id"][3:])
        except Exception:
            return None
        # 공개 저장소에 본문을 쌓지 않게 찾은 사이즈 표기만 저장
        c = {"at": time.time(), "sizes": sorted(text_sizes(p["title"] + " " + text)), "cond": cond}
        cache[key] = c
        time.sleep(0.5)
    p["cond"] = c["cond"]
    if c["cond"] == "사용감 많음":
        return False
    if p["_want"] is None:
        return True
    if not c["sizes"]:
        return None
    return any(w.upper() in {x.upper() for x in c["sizes"]} for w in p["_want"])


def fit_text(p):
    """알림에 붙일 '비슷한 체형 후기' 요약."""
    try:
        fn = sources.musinsa_fit if p["site"] == "무신사" else sources.c29_fit
        c = fn(p["no"])
    except Exception:
        return ""
    if not c:
        return ""
    return "비슷한 체형 후기: " + " · ".join(f"{k or '?'} {v}명" for k, v in c.most_common(3))


BUYING = ("삽니다", "구합니다", "구매합니다", "구매원", "구해요", "매입", "구함")
OUTER = r"자켓|재킷|코트|블루종|점퍼|파카|패딩|다운|블레이저|베스트|조끼|아노락|바람막이|jacket|coat|blouson|parka|blazer|anorak"


def cat_group(title):
    """메뉴 품목(아우터·상의·하의·신발·잡화)으로 묶기."""
    c = category(title)
    if c == "구두":
        return "신발"
    if c == "상의" and re.search(OUTER, title, re.I):
        return "아우터"
    return c


def used_jobs(q, cfg):
    jobs = [("번장", lambda: sources.bunjang(q))]
    for r in cfg["regions"]:
        jobs.append((f"당근 {r['name']}", lambda r=r: sources.daangn(q, r["id"])))
    return jobs


def is_buying(text):
    t = text.replace(" ", "")
    return any(x in t for x in BUYING)


def used_size(p, want, strict):
    """중고 매물 사이즈 판단 결과를 p에 기록. 버릴 매물이면 False."""
    m = used_size_match(p["title"] + " " + p["content"], want)
    if m is False:
        return False
    if m is None and p["site"] == "번장":
        p["need_detail"] = True  # 번장 목록엔 본문이 없어서 상세를 열어 봐야 함
        p["_want"] = want
    elif m is None and strict:
        return False
    p["size_note"] = "사이즈 미기재" if m is None else ""
    p["strict"] = strict
    return True


def run_watch(w, cfg, brands, cache):
    """추적 하나를 실행해 조건에 맞는 매물 목록을 돌려줌."""
    if w["kind"] == "brand":
        return run_brand(w, cfg, brands)
    if w["kind"] == "etc":
        return run_etc(w, cfg)
    return run_item(w, cfg, brands)


def run_item(w, cfg, brands):
    """트래킹: 옷 하나를 내 사이즈로."""
    errors = []
    brand = find_brand(brands, w["query"])
    words = strip_brand(w["query"], brand)
    shop_q = (brand["name"] + " " + " ".join(words)) if brand else w["query"]
    cond = w.get("cond", "a")
    jobs = used_jobs(w["query"], cfg) if cond != "n" else []
    if cond != "u":
        if not brand or brand.get("musinsa"):
            jobs.append(("무신사", lambda: sources.musinsa(shop_q)))
        if not brand or brand.get("c29"):
            jobs.append(("29CM", lambda: sources.c29(shop_q)))
    hits = []
    for p in fetch(jobs, errors):
        if p["price"] <= 0 or (w.get("max_price") and p["price"] > w["max_price"]):
            continue
        shop = p["site"] in ("무신사", "29CM")
        if shop and not shop_brand_ok(p, brand):
            continue
        if not shop and (is_buying(p["title"] + p["content"]) or (brand and not find_brand([brand], p["title"] + " " + p["content"]))):
            continue
        if not has_words(p, words):
            continue
        p["cat"] = category(" ".join(words) + " " + p["title"])
        want = wanted(cfg, brand, p["cat"])
        if shop:
            p["shop"], p["_want"] = True, want
        elif not used_size(p, want, strict=False):
            continue
        hits.append(p)
    return hits, errors


def good_review(p):
    if p["site"] == "무신사":
        return p.get("reviews", 0) >= 50 and p.get("score", 0) >= 96
    if p["site"] == "29CM":
        return p.get("reviews", 0) >= 20 and p.get("score", 0) >= 4.8
    return False


def run_brand(w, cfg, brands):
    """쇼핑: 고른 브랜드들에서 내 사이즈로 세일 크거나 후기 좋은 것 + 중고 매물."""
    errors, hits = [], []
    cats = set(w.get("cats") or ["아우터", "상의", "하의", "신발"])
    for name in w.get("brands") or [w.get("brand")]:
        brand = find_brand(brands, name)
        if not brand:
            continue
        jobs = used_jobs(brand["name"], cfg)
        if brand.get("musinsa"):
            jobs.append(("무신사", lambda b=brand: sources.musinsa(b["name"], sort="DISCOUNT_RATE", pages=2)))
            jobs.append(("무신사", lambda b=brand: sources.musinsa(b["name"], sort="REVIEW")))
        if brand.get("c29"):
            jobs.append(("29CM", lambda b=brand: sources.c29(b.get("eng") or b["name"], pages=2)))
        for p in fetch(jobs, errors):
            if p["price"] <= 0 or (w.get("max_price") and p["price"] > w["max_price"]):
                continue
            group = cat_group(p["title"])
            if group not in cats:
                continue
            p["cat"] = category(p["title"])
            want = wanted(cfg, brand, p["cat"])
            if p["site"] in ("무신사", "29CM"):
                if not shop_brand_ok(p, brand):
                    continue
                sale = w.get("min_discount", 0) and p.get("discount", 0) >= w["min_discount"]
                if not sale and w.get("min_discount", 0) == 0:
                    sale = p.get("discount", 0) > 0
                review = good_review(p)
                if not (sale or review):
                    continue
                p["why"] = "⭐후기 좋음" if review and not sale else ("💸세일" if not review else "💸세일·⭐후기")
                p["shop"], p["_want"] = True, want
            else:
                if is_buying(p["title"] + p["content"]) or not find_brand([brand], p["title"] + " " + p["content"]):
                    continue
                if not used_size(p, want, strict=True):
                    continue
            hits.append(p)
    return hits, errors


def run_etc(w, cfg):
    """기타 물건: 사이즈 없이 상품명·뺄 말·꼭 들어갈 말·상한가로."""
    errors = []
    cond = w.get("cond", "a")
    jobs = used_jobs(w["query"], cfg) if cond != "n" else []
    if cond != "u":
        jobs.append(("다나와", lambda: sources.danawa(w["query"])))
    # '에어팟 프로 2' → ['에어팟', '프로2']: 숫자만 있는 단어는 앞 단어에 붙여서 비교 (1세대 섞임 방지)
    words = []
    for x in w["query"].split():
        if x.isdigit() and words:
            words[-1] += x
        else:
            words.append(x)
    exclude = [x.lower().replace(" ", "") for x in w.get("exclude", [])]
    include = [x.lower().replace(" ", "") for x in w.get("include", [])]
    ok = []
    for p in fetch(jobs, errors):
        t = (p["title"] + " " + p["content"]).lower().replace(" ", "")
        if p["price"] <= 0 or is_buying(t):
            continue
        if not all(x.lower() in t for x in words):
            continue
        if any(x in t for x in exclude) or not all(x in t for x in include):
            continue
        ok.append(p)
    # 같은 이름의 액세서리(케이스·칩 등)를 빼려고 중간값의 40% 미만은 제외
    if len(ok) >= 5:
        prices = sorted(p["price"] for p in ok)
        med = prices[len(prices) // 2]
        ok = [p for p in ok if p["price"] >= med * 0.4]
    hits = [p for p in ok if not (w.get("max_price") and p["price"] > w["max_price"])]
    return hits, errors


def finalize(hits, cache, alerted, fresh_only=True):
    """편집샵 재고 확인 + 이미 알린 것 거르기. 재고 확인은 새 후보만 해서 호출 수를 줄임."""
    out = []
    for p in sorted(hits, key=lambda x: x["price"]):
        k = hid(p["id"])
        p["key"] = k
        if fresh_only and k in alerted and alerted[k]["price"] <= p["price"]:
            continue
        if p.get("need_detail"):
            m = bunjang_check(p, cache)
            if m is False or (m is None and p.get("strict")):
                continue
            p["size_note"] = "사이즈 미기재" if m is None else ""
            if p.get("cond"):
                p["size_note"] = (p["size_note"] + " · " if p["size_note"] else "") + p["cond"]
        if p.get("shop"):
            sizes = stock_check(p, p["_want"], cache)
            if not sizes:
                continue
            p["size_note"] = "재고 " + "·".join(sizes)
        p["dropped"] = fresh_only and k in alerted
        out.append(p)
    return out


def now_iso():
    return datetime.now(KST).isoformat()
