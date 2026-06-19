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


def save_character(data: dict) -> None:
    """把一張角色卡 dict 寫成 characters/{name}.json。

    給 /character create 指令用：玩家在 Discord 填好表單後，
    我們就把資料存成檔案，之後就能像內建角色一樣載入。
    """
    CHARACTERS_DIR.mkdir(exist_ok=True)
    path = CHARACTERS_DIR / f"{data['name']}.json"
    # ensure_ascii=False 讓中文正常寫出（不會變成 \uXXXX）；indent=2 讓檔案好讀
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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


def build_system_prompt(character: dict, co_stars: list[str] | None = None) -> str:
    """把角色卡 dict 組裝成一段給 AI 的 system prompt。

    system prompt 就是「給 AI 的角色設定說明書」，
    它不會出現在對話裡，但會決定 AI 怎麼扮演這個角色。

    co_stars：同一個場景裡的「其他角色」名字。多 AI 同場時用得到——
    告訴這個 AI「現場還有誰」，並且只演好自己、不要替別人發言。
    """
    # 把 rules 清單變成「- 規則一\n- 規則二」這樣的條列字串
    rules_text = "\n".join(f"- {rule}" for rule in character.get("rules", []))

    # 用 f-string 多行字串把各欄位填進固定模板
    prompt = f"""你是{character['name']}。{character['description']}。

個性：{character['personality']}

世界觀：{character['world']}

說話風格：{character['speech_style']}

規則：
{rules_text}"""

    # 如果同場還有別的角色，補一段說明，避免 AI 自己分飾多角
    if co_stars:
        names = "、".join(co_stars)
        prompt += (
            f"\n\n這個場景裡還有其他角色在場：{names}。"
            f"你只能扮演{character['name']}一個人，"
            f"只說{character['name']}會說的話，絕對不要替其他角色或玩家發言或代為旁白。"
        )

    return prompt
