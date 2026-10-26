"""버튼 메뉴(Cloudflare) 연결용 1회 설정 도구.

사용법 (VS Code 터미널에서):  python setup_worker.py
- Cloudflare API 토큰과 GitHub 토큰을 붙여넣으면 (화면에 안 보임)
  GitHub 비밀값으로 저장하고, Cloudflare 배포를 시작합니다.
- 키는 파일에 저장하지 않습니다.
"""
import getpass
import json
import secrets
import subprocess
import urllib.error
import urllib.request

REPO = "MoonSeongSam/price_tracker"


def secret(name, value):
    subprocess.run(["gh", "secret", "set", name, "-R", REPO], input=value.encode(), check=True)
    print(f"  ✓ {name} 저장")


def ask(prompt):
    # 붙여넣기 중 섞인 공백·줄바꿈·보이지 않는 글자 제거
    return "".join(ch for ch in getpass.getpass(prompt) if ch.isprintable() and not ch.isspace())


def cf_get(path, token):
    req = urllib.request.Request("https://api.cloudflare.com/client/v4" + path,
                                 headers={"Authorization": "Bearer " + token})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return {"success": False, "errors": [{"message": f"HTTP {e.code}"}]}


for attempt in range(3):
    cf = ask("1) Cloudflare API 토큰 붙여넣기 + Enter (화면에 안 보임): ")
    print(f"  입력된 글자 수: {len(cf)} (보통 40자 정도)")
    v = cf_get("/user/tokens/verify", cf)
    if v.get("success"):
        accounts = cf_get("/accounts", cf).get("result") or []
        if accounts:
            print(f"  Cloudflare 계정 확인: {accounts[0]['name']}")
            break
        print("  토큰은 맞는데 계정을 못 읽습니다. 'Edit Cloudflare Workers' 템플릿으로 만들었는지, "
              "Account Resources에 내 계정을 넣었는지 확인해 주세요.")
    else:
        msg = "; ".join(e.get("message", "") for e in v.get("errors") or [])
        print(f"  ❌ Cloudflare가 토큰을 거절: {msg}")
        print("  → 'API Tokens' 화면에서 만든 토큰인지 확인해 주세요 (Global API Key·Origin CA Key 아님). "
              "붙여넣기는 마우스 오른쪽 클릭으로 해 보세요.")
else:
    raise SystemExit("세 번 실패해서 멈춥니다. 위 안내를 확인한 뒤 다시 실행해 주세요.")

for attempt in range(3):
    gh = ask("2) GitHub 토큰(github_pat_...) 붙여넣기 + Enter (화면에 안 보임): ")
    print(f"  입력된 글자 수: {len(gh)} (보통 93자), 앞부분: {gh[:11]}")
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}",
                                 headers={"Authorization": "Bearer " + gh, "User-Agent": "price-bot"})
    try:
        urllib.request.urlopen(req, timeout=30)  # 토큰이 저장소에 접근되는지 확인
        print("  GitHub 토큰 확인")
        break
    except urllib.error.HTTPError as e:
        print(f"  ❌ GitHub가 토큰을 거절 (HTTP {e.code}). price_tracker 저장소를 골랐는지 확인해 주세요.")
else:
    raise SystemExit("세 번 실패해서 멈춥니다.")

secret("CF_API_TOKEN", cf)
secret("CF_ACCOUNT_ID", accounts[0]["id"])
secret("GH_DISPATCH_TOKEN", gh)
secret("QUEUE_KEY", secrets.token_urlsafe(32))
secret("TG_SECRET", secrets.token_urlsafe(32))
subprocess.run(["gh", "workflow", "run", "deploy-worker.yml", "-R", REPO], check=True)
print("완료. 1~2분 뒤 텔레그램에 '버튼 메뉴가 연결됐습니다' 메시지가 옵니다.")
