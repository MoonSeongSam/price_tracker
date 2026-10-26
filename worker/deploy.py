"""Cloudflare Worker 배포 (Node.js 없이 Cloudflare API로 직접 올림).

GitHub Actions(deploy-worker.yml)에서 실행. 필요한 환경변수:
  CF_API_TOKEN, CF_ACCOUNT_ID, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
  GH_DISPATCH_TOKEN, QUEUE_KEY, TG_SECRET
하는 일: KV 저장소 준비 → 코드 업로드 → workers.dev 주소 켜기 → 텔레그램 웹훅 연결
→ 주소를 state/worker_url.txt에 기록 (주소 자체는 비밀 아님. 대기열·웹훅은 키로 보호)
"""
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

NAME = "price-bot"
HERE = Path(__file__).parent
ACC = os.environ.get("CF_ACCOUNT_ID", "")
API = f"https://api.cloudflare.com/client/v4/accounts/{ACC}"
HDR = {"Authorization": "Bearer " + os.environ.get("CF_API_TOKEN", "")}


def cf(method, path, body=None, raw=None, ctype=None):
    data, headers = None, dict(HDR)
    if raw is not None:
        data, headers["Content-Type"] = raw, ctype
    elif body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode() or "{}") | {"_status": e.code}


def kv_namespace():
    res = cf("GET", "/storage/kv/namespaces?per_page=100")
    for ns in res.get("result") or []:
        if ns["title"] == NAME:
            return ns["id"]
    res = cf("POST", "/storage/kv/namespaces", {"title": NAME})
    assert res.get("success"), res
    return res["result"]["id"]


def upload(kv_id):
    env = os.environ
    meta = {
        "main_module": "worker.js",
        "compatibility_date": "2024-09-01",
        "bindings": [
            {"type": "kv_namespace", "name": "KV", "namespace_id": kv_id},
            {"type": "plain_text", "name": "CHAT_ID", "text": env["TELEGRAM_CHAT_ID"]},
            {"type": "secret_text", "name": "TELEGRAM_TOKEN", "text": env["TELEGRAM_TOKEN"]},
            {"type": "secret_text", "name": "GH_TOKEN", "text": env["GH_DISPATCH_TOKEN"]},
            {"type": "secret_text", "name": "QUEUE_KEY", "text": env["QUEUE_KEY"]},
            {"type": "secret_text", "name": "TG_SECRET", "text": env["TG_SECRET"]},
        ],
    }
    b = "----pricebot" + secrets.token_hex(8)
    code = (HERE / "worker.js").read_bytes()
    body = (
        f"--{b}\r\nContent-Disposition: form-data; name=\"metadata\"\r\nContent-Type: application/json\r\n\r\n"
        + json.dumps(meta) + "\r\n"
        + f"--{b}\r\nContent-Disposition: form-data; name=\"worker.js\"; filename=\"worker.js\"\r\n"
          "Content-Type: application/javascript+module\r\n\r\n"
    ).encode() + code + f"\r\n--{b}--\r\n".encode()
    res = cf("PUT", f"/workers/scripts/{NAME}", raw=body, ctype=f"multipart/form-data; boundary={b}")
    assert res.get("success"), json.dumps(res, ensure_ascii=False)[:1500]


def workers_dev_url():
    res = cf("GET", "/workers/subdomain")
    sub = (res.get("result") or {}).get("subdomain")
    if not sub:
        sub = "pricebot" + secrets.token_hex(3)
        res = cf("PUT", "/workers/subdomain", {"subdomain": sub})
        assert res.get("success"), res
    cf("POST", f"/workers/scripts/{NAME}/subdomain", {"enabled": True, "previews_enabled": False})
    return f"https://{NAME}.{sub}.workers.dev"


def set_webhook(url):
    params = urllib.parse.urlencode({
        "url": url + "/tg",
        "secret_token": os.environ["TG_SECRET"],
        "allowed_updates": json.dumps(["message", "callback_query"]),
    }).encode()
    tok = os.environ["TELEGRAM_TOKEN"]
    with urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/setWebhook", data=params, timeout=30) as r:
        res = json.loads(r.read().decode())
    assert res.get("ok"), res
    # 아래쪽 고정 버튼 '🛍 메뉴' 달아 주기
    body = urllib.parse.urlencode({
        "chat_id": os.environ["TELEGRAM_CHAT_ID"],
        "text": "✅ 버튼 메뉴가 연결됐습니다. 아래 🛍 메뉴 버튼을 눌러 보세요.",
        "reply_markup": json.dumps({"keyboard": [[{"text": "🛍 메뉴"}]], "resize_keyboard": True, "is_persistent": True}),
    }).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=body, timeout=30)


if __name__ == "__main__":
    if not os.environ.get("CF_API_TOKEN"):
        print("Cloudflare 설정 전이라 건너뜀 (setup_worker.py 실행 후 배포됨)")
        raise SystemExit(0)
    kv = kv_namespace()
    upload(kv)
    url = workers_dev_url()
    set_webhook(url)
    (HERE.parent / "state").mkdir(exist_ok=True)
    (HERE.parent / "state" / "worker_url.txt").write_text(url + "\n", encoding="utf-8")
    print("배포 완료")
