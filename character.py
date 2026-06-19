"""角色管理：負責讀取 characters/ 資料夾裡的角色卡，並組裝 Claude 的 system prompt。

這個檔案刻意「不碰 Discord」，只處理角色資料。
把資料邏輯和 Discord 邏輯分開，之後要改 prompt 或換平台都比較輕鬆。
"""

import json
from pathlib import Path

# characters/ 資料夾的路徑。__file__ 是這支檔案的位置，
# .parent 取得它所在的資料夾，再接上 "characters"，
# 這樣不管你從哪個目錄執行 bot，路徑都正確。
CHARACTERS_DIR = Path(__file__).parent / "characters"


def list_characters() -> list[str]:
    """回傳 characters/ 裡所有角色的名字（也就是去掉 .json 的檔名）。"""
    if not CHARACTERS_DIR.exists():
        return []
    # glob("*.json") 找出所有 .json 檔；.stem 取得不含副檔名的檔名
    return sorted(p.stem for p in CHARACTERS_DIR.glob("*.json"))


def load_character(name: str) -> dict | None:
    """依名字讀取一張角色卡，回傳 dict；找不到就回傳 None。

    回傳 None 而不是直接丟錯，是為了讓呼叫端（bot.py）自己決定
    要怎麼回覆使用者（符合規格的錯誤訊息）。
    """
    path = CHARACTERS_DIR / f"{name}.json"
    if not path.exists():
        return None
    # encoding="utf-8" 很重要：角色卡有中文，不指定可能在 Windows 上讀成亂碼
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def build_system_prompt(character: dict) -> str:
    """把角色卡 dict 組裝成一段給 Claude 的 system prompt。

    system prompt 就是「給 AI 的角色設定說明書」，
    它不會出現在對話裡，但會決定 AI 怎麼扮演這個角色。
    """
    # 把 rules 清單變成「- 規則一\n- 規則二」這樣的條列字串
    rules_text = "\n".join(f"- {rule}" for rule in character.get("rules", []))

    # 用 f-string 多行字串把各欄位填進固定模板
    return f"""你是{character['name']}。{character['description']}。

個性：{character['personality']}

世界觀：{character['world']}

說話風格：{character['speech_style']}

規則：
{rules_text}"""
