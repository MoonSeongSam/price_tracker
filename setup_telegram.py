"""텔레그램 키를 GitHub 비밀값으로 등록하는 1회용 설정 도구.

사용법 (VS Code 터미널에서):
  1) 새 봇에게 텔레그램으로 아무 메시지나 하나 보냄
  2) python setup_telegram.py
키는 화면에 보이지 않게 입력받고, 파일에도 저장하지 않음.
"""
import getpass
import json
import subprocess
import urllib.parse
import urllib.request

REPO = "MoonSeongSam/price_tracker"


def secret(name, value):
    subprocess.run(["gh", "secret", "set", name, "-R", REPO], input=value.encode(), check=True)
    print(f"  ✓ {name} 등록")


token = getpass.getpass("텔레그램 봇 토큰 붙여넣기 (화면에 안 보임) + Enter: ").strip()
res = json.loads(urllib.request.urlopen(
    f"https://api.telegram.org/bot{token}/getUpdates", timeout=30).read())
chats = [u["message"]["chat"]["id"] for u in res.get("result", []) if "message" in u]
if not chats:
    raise SystemExit("봇에게 온 메시지가 없습니다. 텔레그램에서 새 봇에게 아무 말이나 보낸 뒤 다시 실행해 주세요.")
chat_id = str(chats[-1])

msg = "✅ 가격 추적 봇 연결 완료. '검색 키워드' 또는 '목록'을 보내 보세요 (답장은 1시간 이내)."
urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage",
                       data=urllib.parse.urlencode({"chat_id": chat_id, "text": msg}).encode())

secret("TELEGRAM_TOKEN", token)
secret("TELEGRAM_CHAT_ID", chat_id)
print("완료. 텔레그램에 연결 메시지가 왔는지 확인해 주세요.")
