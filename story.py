"""劇情系統：讀取 stories/ 資料夾裡的「分支劇情檔」。

和 character.py 一樣，這支檔案刻意不碰 Discord，只處理劇情資料。

設計重點（為什麼這樣能省 LLM、不被 Gemini 限流）：
    每個劇情是一棵「節點樹」。每個節點都有事先寫好的文字和幾個選項，
    玩家按按鈕就跳到下一個節點——這個過程「完全不呼叫 LLM」，
    所以瞬間出現、不會卡伺服器、也不會吃 API 額度。

    只有節點裡標記 "free": true 的選項，按下去才會叫一次 Gemini
    做即時自由對話（這就是「混合式」裡少量、可控的 LLM 用量）。

劇情檔長這樣（stories/tavern.json 是現成範例）：
    {
      "title": "旅館的夜晚",
      "start": "start",                  # 第一個節點的 id
      "nodes": {
        "start": {
          "speaker": "旁白",             # 誰在說話，可省略
          "text": "你推開旅館的門……",
          "choices": [
            {"label": "走向吧台", "goto": "bar"},
            {"label": "自己開口問他", "free": true, "character": "bryn", "goto": "bryn"}
          ]
        }
      }
    }

選項欄位：
    label     按鈕上的文字（必填）
    goto      按下去要跳到的節點 id
    free      true 代表這個選項會跳出輸入框、呼叫一次 LLM（自由對話）
    character free 選項用：要由哪張角色卡（characters/ 裡的檔名）來回應
"""

import json
from pathlib import Path

STORIES_DIR = Path(__file__).parent / "stories"


def list_stories() -> list[tuple[str, str]]:
    """回傳 [(劇情id, 標題), ...]。id 是檔名（去掉 .json），標題取自檔案裡的 title。"""
    if not STORIES_DIR.exists():
        return []
    out: list[tuple[str, str]] = []
    for path in sorted(STORIES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue                      # 壞掉的檔案就跳過，不讓整個列表掛掉
        out.append((path.stem, data.get("title", path.stem)))
    return out


def load_story(story_id: str) -> dict | None:
    """依 id 讀取一個完整劇情檔；找不到就回傳 None。"""
    path = STORIES_DIR / f"{story_id}.json"
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def get_node(story: dict, node_id: str) -> dict | None:
    """從劇情裡取出某個節點；找不到回傳 None。"""
    return story.get("nodes", {}).get(node_id)
