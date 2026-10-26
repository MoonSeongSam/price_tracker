"""가격 추적 + 텔레그램 알림 + 수시 검색.

GitHub Actions가 1시간마다 실행:
  1) 텔레그램에 온 "검색 ~" 메시지에 답장
  2) 추적 상품의 새 매물·가격 하락을 확인해 조건에 맞으면 알림
  3) 가격 기록을 data/history.csv에 저장

로컬 시험: python tracker.py --dry   (텔레그램으로 보내지 않고 화면에 출력)
"""
import csv
import hashlib
import html
import json
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import re

import shopping
import sources

ROOT = Path(__file__).parent
STATE = ROOT / "state"
DATA = ROOT / "data"
KST = timezone(timedelta(hours=9))
DRY = "--dry" in sys.argv

# 매입글·액세서리·고장품 등 제외 (모든 검색 공통)
BASE_EXCLUDE = ["매입", "삽니다", "구합니다", "구매합니다", "구매원", "구매희망", "구함", "구해요",
                "사요", "대여", "렌탈", "교환원"]


# ---------- 설정 ----------
def load_config():
    """비밀값 CONFIG_JSON(공개 저장소용) → 없으면 로컬 config.json."""
    raw = os.environ.get("CONFIG_JSON")
    cfg = json.loads(raw) if raw else json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    off = set(load_json(STATE / "fixed_off.json", []))  # 메뉴에서 중단한 기본 추적
    cfg["items"] = [i for i in cfg.get("items", []) if i["name"] not in off]
    return cfg


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, obj):
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def hid(s):
    """공개 저장소에 매물 주소가 드러나지 않게 짧은 해시로 저장."""
    return hashlib.sha1(s.encode()).hexdigest()[:12]


# ---------- 텔레그램 ----------
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def tg(method, **params):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(url, data=data, timeout=30) as r:
        return json.loads(r.read().decode())


def send(text, markup=None):
    if DRY:
        print("----- [텔레그램 메시지] -----\n" + text + "\n")
        return
    if not TOKEN:
        return  # 공개 저장소 실행 기록에 매물·동네가 찍히지 않게 출력하지 않음
    # 텔레그램 한 메시지 4096자 제한 → 나눠 보냄
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) > 3800:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    chunks.append(cur)
    chunks = [c for c in chunks if c.strip()]
    for i, c in enumerate(chunks):
        extra = {"reply_markup": json.dumps(markup)} if (markup and i == len(chunks) - 1) else {}
        tg("sendMessage", chat_id=CHAT_ID, text=c, parse_mode="HTML",
           disable_web_page_preview="true", **extra)


def fmt(p):
    title = html.escape(p["title"][:40])
    where = f" ({html.escape(p['where'])})" if p["where"] else ""
    tag = " 🎮동봉" if p.get("bundle") else ""
    return f'• <b>{p["price"]:,}원</b> {p["site"]}{where}{tag}\n  <a href="{p["url"]}">{title}</a>'


# ---------- 수집 ----------
def collect(queries, regions, used=True, new=True):
    found, errors = {}, []
    for q in queries:
        jobs = []
        if used:
            jobs.append(("번장", lambda q=q: sources.bunjang(q)))
            for r in regions:
                jobs.append((f"당근 {r['name']}", lambda q=q, r=r: sources.daangn(q, r["id"])))
        if new:
            jobs.append(("다나와", lambda q=q: sources.danawa(q)))
        for name, job in jobs:
            try:
                for p in job():
                    found.setdefault(p["id"], p)
            except Exception as e:  # 한 사이트가 막혀도 나머지는 계속
                errors.append(f"{name}: {type(e).__name__}")
            time.sleep(1)  # 사이트에 부담 주지 않게
    return list(found.values()), sorted(set(errors))


def text_of(p):
    return (p["title"] + " " + p["content"]).lower().replace(" ", "")


def matches(p, item):
    t = text_of(p)
    words = [w.lower().replace(" ", "") for w in BASE_EXCLUDE + item.get("exclude", [])]
    if any(w in t for w in words):
        return False
    title = p["title"].lower().replace(" ", "")
    if item.get("must_any") and not any(w.lower().replace(" ", "") in title
                                        for w in item["must_any"]):
        return False
    return p["price"] >= item.get("min_price", 0)


def is_bundle(p, item):
    """게임 동봉 판단: 제목의 게임 단어 또는 본문의 확실한 표현만 인정."""
    norm = lambda s: s.lower().replace(" ", "")
    title, content = norm(p["title"]), norm(p["content"])
    return (any(norm(w) in title for w in item.get("bundle_title_words", []))
            or any(norm(w) in content for w in item.get("bundle_content_words", [])))


# ---------- 1) 텔레그램 명령 처리 ----------
HELP = ("<b>사용법</b> (답장은 1시간 안에 옵니다)\n"
        "• <b>추적 쿠어 블레이저 15만</b> → 이 상품을 내 사이즈로 계속 찾아서 알림 (가격은 생략 가능)\n"
        "• <b>쇼핑 쿠어</b> → 쿠어 전체: 내 사이즈 중고 새 매물 + 편집샵 20% 이상 세일\n"
        "   <b>쇼핑 1순위</b> → 1순위 브랜드 전부 / <b>쇼핑 쿠어 30%</b>, <b>쇼핑 쿠어 10만</b>도 됨\n"
        "• <b>목록</b> → 추적 중인 것 보기·중단 버튼\n"
        "• <b>중단 3</b> → 3번 추적 끝내기\n"
        "• <b>검색 에어팟 프로</b> → 한 번만 최저가 검색\n"
        "• <b>브랜드</b> → 등록된 브랜드 / <b>브랜드추가 아워레가시 2순위</b>\n"
        "• <b>가격</b> → 닌텐도 스위치 2 현재 최저가")
KEYBOARD = {"keyboard": [[{"text": "목록"}, {"text": "브랜드"}, {"text": "도움말"}]],
            "resize_keyboard": True, "is_persistent": True}


def parse_price(text):
    """'15만', '15만원', '150000원' → 150000. 없으면 None."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*만\s*원?", text)
    if m:
        return int(float(m.group(1)) * 10000), text.replace(m.group(0), " ")
    m = re.search(r"(?<!\d)(\d{4,})\s*원?(?!\d)", text)
    if m:
        return int(m.group(1)), text.replace(m.group(0), " ")
    return None, text


def watches_load():
    return load_json(STATE / "watches.json", {"next_id": 1, "list": []})


def extra_brands():
    return load_json(STATE / "brands.json", [])


def worker_url():
    try:
        return (STATE / "worker_url.txt").read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def handle_queue(cfg):
    """버튼 메뉴(Cloudflare)가 쌓아 둔 요청을 가져와 처리."""
    url, key = worker_url(), os.environ.get("QUEUE_KEY", "")
    if not url or not key or DRY:
        return False
    req = urllib.request.Request(url + "/queue", headers={"X-Key": key, "User-Agent": "price-bot"})
    with urllib.request.urlopen(req, timeout=30) as r:
        queue = json.loads(r.read().decode())
    brands = shopping.all_brands(cfg, extra_brands())
    for cmd in queue:
        try:
            a = cmd.get("action")
            if a == "add":
                add_watch(dict(cmd["watch"]), cfg, brands)
            elif a == "stop":
                stop_watch(int(cmd["id"]))
            elif a == "stop_fixed":
                off = load_json(STATE / "fixed_off.json", [])
                if cmd["name"] not in off:
                    off.append(cmd["name"])
                save_json(STATE / "fixed_off.json", off)
                cfg["items"] = [i for i in cfg["items"] if i["name"] != cmd["name"]]
                send(f"⏹ {html.escape(cmd['name'])} 추적 중단")
            elif a == "search":
                reply_search(cmd["q"], cfg)
            elif a == "add_brand":
                add_brand(cmd["name"], brands)
                brands = shopping.all_brands(cfg, extra_brands())
        except Exception as e:
            send(f"⚠️ 요청 처리 중 오류: {html.escape(type(e).__name__)}")
    return True


def handle_commands(cfg):
    if DRY or not TOKEN:
        return
    if handle_queue(cfg):
        return  # 버튼 메뉴를 쓰면 텔레그램 메시지는 Cloudflare가 받음
    off_path = STATE / "telegram_offset.json"
    offset = load_json(off_path, {"offset": 0})["offset"]
    res = tg("getUpdates", offset=offset, timeout=0)
    for u in res.get("result", []):
        offset = u["update_id"] + 1
        save_json(off_path, {"offset": offset})  # 먼저 저장: 중간에 죽어도 같은 명령을 반복하지 않게
        cb = u.get("callback_query")
        msg = (cb or {}).get("message") or u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(CHAT_ID):
            continue  # 내 채팅방 말고는 무시
        try:
            if cb:
                try:
                    tg("answerCallbackQuery", callback_query_id=cb["id"])
                except Exception:
                    pass  # 1시간 지난 버튼은 응답 기한이 지나 실패할 수 있음
                if (cb.get("data") or "").startswith("stop:"):
                    stop_watch(int(cb["data"][5:]))
                continue
            dispatch((msg.get("text") or "").strip(), cfg)
        except Exception as e:
            send(f"⚠️ 명령 처리 중 오류: {html.escape(type(e).__name__)}")


def dispatch(text, cfg):
    brands = shopping.all_brands(cfg, extra_brands())
    if text.startswith("검색"):
        if text[2:].strip():
            reply_search(text[2:].strip(), cfg)
    elif text.startswith("추적"):
        price, rest = parse_price(text[2:])
        q = rest.strip()
        if q:
            add_watch({"kind": "item", "query": q, "max_price": price}, cfg, brands)
    elif text.startswith("쇼핑"):
        price, rest = parse_price(text[2:])
        m = re.search(r"(\d+)\s*%", rest)
        disc = int(m.group(1)) if m else 20
        rest = re.sub(r"\d+\s*%", " ", rest).strip()
        t = re.match(r"([123])\s*순위", rest)
        if t:
            targets = [b for b in brands if b.get("tier") == int(t.group(1))]
        else:
            targets = [b for b in [shopping.find_brand(brands, rest)] if b]
        if not targets:
            send(f"'{html.escape(rest)}' 브랜드를 못 찾았습니다. <b>브랜드</b>로 목록을 보거나 "
                 f"<b>브랜드추가 {html.escape(rest)}</b>로 등록해 주세요.")
        for b in targets:
            add_watch({"kind": "brand", "query": b["name"], "brand": b["name"],
                       "max_price": price, "min_discount": disc}, cfg, brands)
    elif text.startswith("목록"):
        show_list(cfg)
    elif text.startswith("중단"):
        m = re.search(r"\d+", text)
        if m:
            stop_watch(int(m.group()))
    elif text.startswith("브랜드추가") or text.startswith("브랜드 추가"):
        add_brand(re.sub(r"^브랜드\s*추가", "", text).strip(), brands)
    elif text.startswith("브랜드"):
        lines = ["<b>등록된 브랜드</b>"]
        for tier in (1, 2, 3):
            names = [b["name"] for b in brands if b.get("tier") == tier]
            if names:
                lines.append(f"{tier}순위: " + ", ".join(names))
        send("\n".join(lines))
    elif text.startswith("가격"):
        for item in cfg["items"]:
            reply_item(item, cfg)
    else:
        send(HELP, markup=KEYBOARD)


def watch_name(w):
    if w["kind"] == "brand":
        names = w.get("brands") or [w.get("brand")]
        disc = w.get("min_discount", 0)
        s = f"쇼핑 {', '.join(names)} · {'/'.join(w.get('cats') or [])} · " + \
            (f"세일 {disc}%↑·후기 좋은 것" if disc else "세일·후기 좋은 것")
    else:
        cond = {"n": " · 새 상품만", "u": " · 중고만"}.get(w.get("cond", "a"), "")
        s = w["query"] + cond
        if w.get("exclude"):
            s += " · 제외 " + ",".join(w["exclude"])
        if w.get("include"):
            s += " · 포함 " + ",".join(w["include"])
    if w.get("max_price"):
        s += f" · {w['max_price']:,}원 이하"
    return s


def add_watch(w, cfg, brands):
    st = watches_load()
    w["id"] = st["next_id"]
    w["created"] = shopping.now_iso()
    w["title"] = watch_name(w)
    st["next_id"] += 1
    st["list"].append(w)
    save_json(STATE / "watches.json", st)
    # 시작하자마자 지금 있는 매물을 보여주고, 그건 '이미 알림'으로 표시
    seen = load_json(STATE / "watch_seen.json", {})
    cache = load_json(STATE / "stock_cache.json", {})
    hits, errors = shopping.run_watch(w, cfg, brands, cache)
    final = shopping.finalize(hits, cache, {}, fresh_only=False)
    seen[str(w["id"])] = {p["key"]: {"price": p["price"], "at": shopping.now_iso()} for p in final}
    if TOKEN or DRY:
        save_json(STATE / "watch_seen.json", seen)
        save_json(STATE / "stock_cache.json", cache)
    lines = [f"▶️ <b>추적 시작 #{w['id']}</b> {html.escape(watch_name(w))}",
             f"지금 조건에 맞는 {len(final)}건. 앞으로는 새로 뜨거나 가격이 내리면 알림"]
    lines += fmt_sections(w, final) or ["  아직 없음"]
    if errors:
        lines.append("⚠️ 조회 실패: " + ", ".join(errors))
    send("\n".join(lines),
         markup={"inline_keyboard": [[{"text": f"#{w['id']} 중단", "callback_data": f"stop:{w['id']}"}]]})


def stop_watch(wid):
    st = watches_load()
    before = len(st["list"])
    st["list"] = [w for w in st["list"] if w["id"] != wid]
    save_json(STATE / "watches.json", st)
    seen = load_json(STATE / "watch_seen.json", {})
    seen.pop(str(wid), None)
    save_json(STATE / "watch_seen.json", seen)
    send(f"⏹ #{wid} 추적 중단" if len(st["list"]) < before else f"#{wid} 추적은 이미 없습니다")


def show_list(cfg):
    st = watches_load()
    lines = ["<b>추적 중</b>"]
    for item in cfg["items"]:
        lines.append(f"• {html.escape(item['name'])} (기본, 본체 {item['max_body']:,}원 이하)")
    buttons = []
    for w in st["list"]:
        lines.append(f"• #{w['id']} {html.escape(watch_name(w))}")
        buttons.append([{"text": f"#{w['id']} 중단", "callback_data": f"stop:{w['id']}"}])
    if not st["list"]:
        lines.append("텔레그램으로 추가한 추적은 없습니다. '도움말'을 눌러 보세요.")
    send("\n".join(lines), markup={"inline_keyboard": buttons} if buttons else None)


def add_brand(text, brands):
    m = re.search(r"([123])\s*순위", text)
    tier = int(m.group(1)) if m else 2
    name = re.sub(r"[123]\s*순위", "", text).strip()
    if not name:
        return
    if shopping.find_brand(brands, name):
        send(f"{html.escape(name)}은(는) 이미 등록돼 있습니다.")
        return
    b = {"name": name, "aliases": [], "tier": tier}
    key = name.replace(" ", "")
    try:
        from collections import Counter
        ms = Counter(p["brand"] for p in sources.musinsa(name)
                     if p["brand_name"].replace(" ", "") == key)
        if ms:
            b["musinsa"] = ms.most_common(1)[0][0]
            b["aliases"].append(b["musinsa"])
        cm = Counter(p["brand"] for p in sources.c29(name)
                     if p["brand_name"].replace(" ", "") == key)
        if cm:
            b["c29"] = b["eng"] = cm.most_common(1)[0][0]
            b["aliases"].append(b["c29"])
    except Exception:
        pass
    extra = extra_brands()
    extra.append(b)
    save_json(STATE / "brands.json", extra)
    shops = [s for s, k in (("무신사", "musinsa"), ("29CM", "c29")) if b.get(k)]
    send(f"✅ {html.escape(name)} {tier}순위로 등록 (편집샵: {', '.join(shops) or '못 찾음, 중고만'})\n"
         f"사이즈는 기본값(상의 XL·하의 31·32·M·L)으로 봅니다. 다르면 말씀해 주세요.")


def fmt_sections(w, ps, n=10):
    """쇼핑은 편집샵(세일·후기 좋음)과 중고를 나눠서, 나머지는 싼 순으로."""
    if w.get("kind") != "brand":
        return fmt_hits(ps[:n])
    shop = [p for p in ps if p.get("shop")]
    used = [p for p in ps if not p.get("shop")]
    # 편집샵은 할인 큰 순(같으면 싼 순), 중고는 싼 순
    shop.sort(key=lambda p: (-p.get("discount", 0), p["price"]))
    out = []
    if shop:
        out += ["<b>🏬 편집샵 (세일·후기 좋은 것)</b>"] + fmt_hits(shop[:7])
    if used:
        out += ["<b>♻️ 중고 (내 사이즈)</b>"] + fmt_hits(used[:7])
    return out


def fmt_hits(ps):
    """편집샵 상품 앞 3개에만 '비슷한 체형 후기'를 붙임 (후기 조회 호출 줄이기)."""
    out, n = [], 0
    for p in ps:
        fit = bool(p.get("shop")) and n < 3
        n += fit
        out.append(fmt_watch(p, with_fit=fit))
    return out


def fmt_watch(p, with_fit=False):
    title = html.escape(p["title"][:45])
    bits = [p["site"]]
    if p.get("why"):
        bits.insert(0, p["why"])
    if p.get("where"):
        bits.append(html.escape(p["where"]))
    if p.get("discount"):
        bits.append(f"{p['discount']}% 할인")
    if p.get("size_note"):
        bits.append(html.escape(p["size_note"]))
    line = f'• <b>{p["price"]:,}원</b> {" · ".join(bits)}\n  <a href="{p["url"]}">{title}</a>'
    if p.get("dropped"):
        line += "  ⬇️가격 내림"
    if with_fit and p.get("shop"):
        ft = shopping.fit_text(p)
        if ft:
            line += f"\n  {html.escape(ft)}"
    return line


def reply_search(q, cfg):
    found, errors = collect([q], cfg["regions"])
    found = [p for p in found if p["price"] > 0
             and not any(w in text_of(p) for w in BASE_EXCLUDE)]
    if found:
        # 액세서리 걸러내기: 중간값의 40% 미만 가격은 제외
        med = statistics.median(p["price"] for p in found)
        found = [p for p in found if p["price"] >= med * 0.4]
    found.sort(key=lambda p: p["price"])
    lines = [f"🔎 <b>{html.escape(q)}</b> 최저가 (중간값의 40% 미만은 액세서리로 보고 제외)"]
    lines += [fmt(p) for p in found[:10]] or ["결과 없음"]
    if errors:
        lines.append("⚠️ 조회 실패: " + ", ".join(errors))
    send("\n".join(lines))


def reply_item(item, cfg):
    found, errors = collect(item["queries"], cfg["regions"])
    ok = [p for p in found if matches(p, item)]
    for p in ok:
        p["bundle"] = is_bundle(p, item)
    lines = [f"📋 <b>{html.escape(item['name'])}</b> 현재 최저가"]
    for label, cond in [("새상품", lambda p: p["site"].startswith("새상품")),
                        ("중고", lambda p: not p["site"].startswith("새상품"))]:
        group = sorted([p for p in ok if cond(p)], key=lambda p: p["price"])[:5]
        lines.append(f"[{label}]")
        lines += [fmt(p) for p in group] or ["  없음"]
    if errors:
        lines.append("⚠️ 조회 실패: " + ", ".join(errors))
    send("\n".join(lines))


# ---------- 2) 정기 추적 ----------
def track(cfg):
    alerted = load_json(STATE / "alerted.json", {})
    now = datetime.now(KST)
    # 30일 지난 기록은 정리
    cutoff = (now - timedelta(days=30)).isoformat()
    alerted = {k: v for k, v in alerted.items() if v["at"] >= cutoff}

    DATA.mkdir(exist_ok=True)
    hist = DATA / "history.csv"
    new_file = not hist.exists()
    with hist.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["시각", "상품", "구분", "최저가", "매물수"])

        all_errors = []
        for item in cfg["items"]:
            found, errors = collect(item["queries"], cfg["regions"])
            ok = [p for p in found if matches(p, item)]
            for p in ok:
                p["bundle"] = is_bundle(p, item)
            # 실행 기록용: 사이트별 개수만 (공개 기록이라 제목·동네는 안 찍음)
            sites = {}
            for p in found:
                key = p["site"].split("·")[0]
                sites[key] = sites.get(key, 0) + 1
            print(f"수집 {sites}, 조건 통과 {len(ok)}건")

            # 가격 기록
            for label, cond in [("새상품", lambda p: p["site"].startswith("새상품")),
                                ("중고", lambda p: not p["site"].startswith("새상품"))]:
                group = [p for p in ok if cond(p)]
                if group:
                    w.writerow([now.strftime("%Y-%m-%d %H:%M"), item["name"], label,
                                min(p["price"] for p in group), len(group)])

            # 알림 대상: 목표가 이하 + (처음 보거나, 지난 알림보다 싸졌을 때)
            hits = []
            for p in ok:
                limit = item["max_bundle"] if p["bundle"] else item["max_body"]
                if p["price"] > limit:
                    continue
                k = hid(p["id"])
                if k in alerted and alerted[k]["price"] <= p["price"]:
                    continue
                p["dropped"] = k in alerted
                hits.append(p)
                alerted[k] = {"price": p["price"], "at": now.isoformat()}

            if hits:
                hits.sort(key=lambda p: p["price"])
                lines = [f"🔔 <b>{html.escape(item['name'])}</b> 목표가 이하 매물 {len(hits)}건",
                         f"(본체 {item['max_body']:,}원 / 게임 동봉 {item['max_bundle']:,}원 이하)"]
                for p in hits[:15]:
                    lines.append(fmt(p) + ("  ⬇️가격 내림" if p["dropped"] else ""))
                send("\n".join(lines))
            all_errors += errors

    if TOKEN or DRY:  # 텔레그램 연결 전 시험 실행은 '알린 매물'로 기록하지 않음
        save_json(STATE / "alerted.json", alerted)
    return all_errors


def track_watches(cfg):
    """텔레그램으로 추가한 추적들: 새 매물·가격 내림만 알림."""
    st = watches_load()
    if not st["list"]:
        return []
    brands = shopping.all_brands(cfg, extra_brands())
    seen = load_json(STATE / "watch_seen.json", {})
    cache = load_json(STATE / "stock_cache.json", {})
    cutoff = (datetime.now(KST) - timedelta(days=30)).isoformat()
    all_errors = []
    for w in st["list"]:
        ws = {k: v for k, v in seen.get(str(w["id"]), {}).items() if v["at"] >= cutoff}
        hits, errors = shopping.run_watch(w, cfg, brands, cache)
        all_errors += errors
        final = shopping.finalize(hits, cache, ws, fresh_only=True)
        print(f"추적 #{w['id']}: 후보 {len(hits)}, 새 알림 {len(final)}")
        if final:
            for p in final:
                ws[p["key"]] = {"price": p["price"], "at": shopping.now_iso()}
            lines = [f"🔔 <b>#{w['id']} {html.escape(watch_name(w))}</b> 새 매물 {len(final)}건"]
            lines += fmt_sections(w, final)
            send("\n".join(lines))
        seen[str(w["id"])] = ws
    # 6시간 넘은 재고 기록은 정리
    now = time.time()
    cache = {k: v for k, v in cache.items() if now - v["at"] < 6 * 3600}
    if TOKEN or DRY:
        save_json(STATE / "watch_seen.json", seen)
        save_json(STATE / "stock_cache.json", cache)
    return all_errors


def notify_errors(errors):
    """같은 사이트가 하루 넘게 계속 막히면 한 번만 알려줌."""
    path = STATE / "errors.json"
    st = load_json(path, {})
    now = datetime.now(KST)
    for e in errors:
        site = e.split(":")[0]
        rec = st.setdefault(site, {"since": now.isoformat(), "told": False})
        if not rec["told"] and now - datetime.fromisoformat(rec["since"]) > timedelta(hours=24):
            send(f"⚠️ {html.escape(site)} 조회가 24시간 넘게 실패 중입니다 ({html.escape(e)})")
            rec["told"] = True
    failed = {e.split(":")[0] for e in errors}
    st = {k: v for k, v in st.items() if k in failed}
    save_json(path, st)


def write_menu_data(cfg):
    brands = shopping.all_brands(cfg, extra_brands())
    save_json(STATE / "menu_data.json", {
        "brands": [{"name": b["name"], "tier": b.get("tier", 2)} for b in brands],
        "fixed": [i["name"] for i in cfg.get("items", [])],
    })


def main():
    cfg = load_config()
    STATE.mkdir(exist_ok=True)
    if "--queue-only" in sys.argv:  # 버튼으로 깨운 실행: 요청만 빨리 처리
        try:
            handle_commands(cfg)
        finally:
            write_menu_data(cfg)
        return
    try:
        handle_commands(cfg)
    except Exception as e:
        print("텔레그램 명령 처리 실패:", type(e).__name__)
    errors = track(cfg)
    try:
        errors += track_watches(cfg)
    except Exception as e:
        print("추적 처리 실패:", type(e).__name__)
    if errors:
        print("조회 실패:", sorted(set(errors)))
    notify_errors(sorted(set(errors)))
    write_menu_data(cfg)


if __name__ == "__main__":
    main()
