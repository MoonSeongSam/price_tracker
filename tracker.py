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
    if raw:
        return json.loads(raw)
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


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


def send(text):
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
    for c in chunks:
        if c.strip():
            tg("sendMessage", chat_id=CHAT_ID, text=c, parse_mode="HTML",
               disable_web_page_preview="true")


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
HELP = ("사용법\n"
        "• <b>검색 키워드</b> → 번장·당근·다나와 최저가 10개\n"
        "• <b>목록</b> → 추적 중인 상품의 현재 최저가\n"
        "답장은 1시간 이내에 옵니다.")


def handle_commands(cfg):
    if DRY or not TOKEN:
        return
    off_path = STATE / "telegram_offset.json"
    offset = load_json(off_path, {"offset": 0})["offset"]
    res = tg("getUpdates", offset=offset, timeout=0)
    for u in res.get("result", []):
        offset = u["update_id"] + 1
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(CHAT_ID):
            continue  # 내 채팅방 말고는 무시
        text = (msg.get("text") or "").strip()
        if text.startswith("검색"):
            q = text[2:].strip()
            if q:
                reply_search(q, cfg)
        elif text.startswith("목록"):
            for item in cfg["items"]:
                reply_item(item, cfg)
        else:
            send(HELP)
    save_json(off_path, {"offset": offset})


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
            if errors:
                print("조회 실패:", errors)
                notify_errors(errors)

    if TOKEN or DRY:  # 텔레그램 연결 전 시험 실행은 '알린 매물'로 기록하지 않음
        save_json(STATE / "alerted.json", alerted)


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


def main():
    cfg = load_config()
    STATE.mkdir(exist_ok=True)
    try:
        handle_commands(cfg)
    except Exception as e:
        print("텔레그램 명령 처리 실패:", e)
    track(cfg)


if __name__ == "__main__":
    main()
