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
import urllib.request

REPO = "MoonSeongSam/price_tracker"


def secret(name, value):
    subprocess.run(["gh", "secret", "set", name, "-R", REPO], input=value.encode(), check=True)
    print(f"  ✓ {name} 저장")


cf = getpass.getpass("1) Cloudflare API 토큰 붙여넣기 + Enter (화면에 안 보임): ").strip()
req = urllib.request.Request("https://api.cloudflare.com/client/v4/accounts",
                             headers={"Authorization": "Bearer " + cf})
accounts = json.loads(urllib.request.urlopen(req, timeout=30).read())["result"]
if not accounts:
    raise SystemExit("Cloudflare 계정을 찾지 못했습니다. 토큰 권한(템플릿 'Edit Cloudflare Workers')을 확인해 주세요.")
print(f"  Cloudflare 계정 확인: {accounts[0]['name']}")

gh = getpass.getpass("2) GitHub 토큰(github_pat_...) 붙여넣기 + Enter (화면에 안 보임): ").strip()
req = urllib.request.Request(f"https://api.github.com/repos/{REPO}",
                             headers={"Authorization": "Bearer " + gh, "User-Agent": "price-bot"})
urllib.request.urlopen(req, timeout=30)  # 토큰이 저장소에 접근되는지 확인
print("  GitHub 토큰 확인")

secret("CF_API_TOKEN", cf)
secret("CF_ACCOUNT_ID", accounts[0]["id"])
secret("GH_DISPATCH_TOKEN", gh)
secret("QUEUE_KEY", secrets.token_urlsafe(32))
secret("TG_SECRET", secrets.token_urlsafe(32))
subprocess.run(["gh", "workflow", "run", "deploy-worker.yml", "-R", REPO], check=True)
print("완료. 1~2분 뒤 텔레그램에 '버튼 메뉴가 연결됐습니다' 메시지가 옵니다.")
