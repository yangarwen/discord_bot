"""對話存檔：把每個頻道的「目前角色 + 對話紀錄」存到 data/ 資料夾。

為什麼要這個？因為原本狀態都放在記憶體（Python 的 dict）裡，
bot 一重開就全部消失。存到硬碟後，重開能讀回來，對話就不會斷。

每個頻道存成一個檔：data/{channel_id}.json
內容長這樣：{"characters": ["Aldric", "Bryn"], "transcript": [...]}
（只存角色「名字」，真正的設定每次從 characters/ 重新讀，方便你改卡片）
"""

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"


def save_channel(channel_id: int, character_names: list[str], transcript: list[dict]) -> None:
    """把單一頻道的狀態寫進 data/{channel_id}.json。"""
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / f"{channel_id}.json"
    payload = {"characters": character_names, "transcript": transcript}
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def delete_channel(channel_id: int) -> None:
    """刪掉某頻道的存檔（/character clear 時用）。"""
    # missing_ok=True：檔案本來就不存在也不會報錯
    (DATA_DIR / f"{channel_id}.json").unlink(missing_ok=True)


def load_all() -> dict[int, dict]:
    """讀回所有頻道的存檔，回傳 {channel_id: {"characters", "transcript"}}。

    bot 啟動時呼叫一次，把硬碟上的紀錄載入記憶體。
    """
    result: dict[int, dict] = {}
    if not DATA_DIR.exists():
        return result
    for path in DATA_DIR.glob("*.json"):
        try:
            channel_id = int(path.stem)   # 檔名就是頻道 id
        except ValueError:
            continue                      # 不是數字檔名就跳過
        with path.open(encoding="utf-8") as f:
            result[channel_id] = json.load(f)
    return result
