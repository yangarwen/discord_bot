"""Discord AI Roleplay Bot 主程式（支援多角色同場 + 對話存檔）。

流程：使用者 →（/chat 或 @bot）→ 這支程式 → Gemini API → 回覆貼回 Discord
一個頻道可以同時有多個角色，每次發話時，每個角色依序各回一句。
"""

import os

import discord
from discord import app_commands
from dotenv import load_dotenv
from google import genai
from google.genai import types

import character as character_module
import storage

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

MODEL = "gemini-2.5-flash"   # 要用的 Gemini 模型（免費額度可用）
MAX_HISTORY = 30             # 對話紀錄最多保留幾句（含玩家與所有角色）
MAX_TOKENS = 1024            # 單次回覆的長度上限

gemini = genai.Client(api_key=GEMINI_API_KEY)


# ---------------------------------------------------------------------------
# 每個頻道的狀態（記憶體裡的即時版本，硬碟存檔在 storage.py 負責）
# ---------------------------------------------------------------------------
# channel_characters：頻道 id → {角色名字: 角色卡 dict}，dict 會記住加入順序
channel_characters: dict[int, dict[str, dict]] = {}
# channel_transcript：頻道 id → 對話紀錄，每句是 {"name": 發話者, "text": 內容}
channel_transcript: dict[int, list[dict]] = {}


def save_state(channel_id: int) -> None:
    """把某頻道目前的狀態寫進硬碟（包一層，避免每個指令都寫一長串）。"""
    storage.save_channel(
        channel_id,
        list(channel_characters.get(channel_id, {}).keys()),
        channel_transcript.get(channel_id, []),
    )


def load_state() -> None:
    """bot 啟動時呼叫一次：把硬碟存檔讀回記憶體。"""
    for channel_id, saved in storage.load_all().items():
        chars: dict[str, dict] = {}
        for name in saved.get("characters", []):
            card = character_module.load_character(name)
            if card:                      # 卡片若被刪掉就跳過
                chars[name] = card
        channel_characters[channel_id] = chars
        channel_transcript[channel_id] = saved.get("transcript", [])


# ---------------------------------------------------------------------------
# Discord client 設定
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True          # 要讀 @bot 的訊息文字，必須開


class RoleplayBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        load_state()                    # 先把存檔讀回來
        await self.tree.sync()          # 再把斜線指令上傳 Discord


bot = RoleplayBot()


@bot.event
async def on_ready():
    print(f"已登入：{bot.user}（ID: {bot.user.id}）")


# ---------------------------------------------------------------------------
# 核心：讓某個角色根據目前對話，產生一句回覆
# ---------------------------------------------------------------------------
async def generate_for_character(channel_id: int, card: dict) -> str:
    """把整段對話當成劇本丟給 Gemini，請它只以這個角色的身分接一句話。

    多角色同場時，與其用 user/model 交替的格式（角色一多就很亂），
    不如把目前對話整理成一段「劇本文字」放進一個 user 訊息，
    再叫 AI「以 XXX 的身分接話」。這樣 1 個或 5 個角色都用同一套邏輯。
    """
    transcript = channel_transcript.get(channel_id, [])
    all_names = channel_characters.get(channel_id, {})
    co_stars = [n for n in all_names if n != card["name"]]   # 同場的其他角色

    # 把每句整理成「發話者：內容」，串成完整劇本
    scene = "\n".join(f"{line['name']}：{line['text']}" for line in transcript)
    instruction = (
        f"\n\n以上是目前的對話。請只以「{card['name']}」的身分，"
        f"自然地接著說一句話。只輸出你這個角色說的內容，不要加上名字前綴。"
    )

    response = await gemini.aio.models.generate_content(
        model=MODEL,
        contents=[{"role": "user", "parts": [{"text": scene + instruction}]}],
        config=types.GenerateContentConfig(
            system_instruction=character_module.build_system_prompt(card, co_stars),
            max_output_tokens=MAX_TOKENS,
        ),
    )
    return (response.text or "").strip()


async def run_conversation(channel_id: int, author_name: str, text: str, send) -> None:
    """處理一次玩家發話：記下來 → 讓每個角色依序回覆 → 存檔。

    send 是「怎麼把訊息送出去」的函式。/chat 和 @bot 送法不同，
    所以用參數傳進來，邏輯本體就能共用。
    """
    transcript = channel_transcript.setdefault(channel_id, [])
    transcript.append({"name": author_name, "text": text})   # 先記下玩家這句

    # 依加入順序，讓每個角色各回一句；後面的角色看得到前面角色剛說的話
    for char_name, card in list(channel_characters[channel_id].items()):
        try:
            reply = await generate_for_character(channel_id, card)
            transcript.append({"name": char_name, "text": reply})
            await send(f"**{char_name}**：{reply}")
        except Exception as e:
            print(f"[Gemini API 錯誤] {e}")
            await send(f"{char_name} 沉默了一下……（系統錯誤，請稍後再試）")

    # 只保留最近 MAX_HISTORY 句，避免劇本無限變長（也省 token）
    if len(transcript) > MAX_HISTORY:
        del transcript[:-MAX_HISTORY]

    save_state(channel_id)              # 對話有更新，寫回硬碟


# ---------------------------------------------------------------------------
# 建立角色卡用的填表視窗（Modal）
# ---------------------------------------------------------------------------
class CharacterCreateModal(discord.ui.Modal, title="建立角色卡"):
    # Modal 最多 5 個欄位；角色「名字」由指令參數帶入，這裡放其餘 5 項。
    # style=paragraph 是多行輸入框，適合寫長一點的設定。
    description = discord.ui.TextInput(label="簡介（他是誰）", style=discord.TextStyle.paragraph, max_length=300)
    personality = discord.ui.TextInput(label="個性", style=discord.TextStyle.paragraph, max_length=300)
    world = discord.ui.TextInput(label="世界觀", style=discord.TextStyle.paragraph, max_length=300)
    speech_style = discord.ui.TextInput(label="說話風格", style=discord.TextStyle.paragraph, max_length=300)
    rules = discord.ui.TextInput(label="規則（一行一條）", style=discord.TextStyle.paragraph, required=False, max_length=500)

    def __init__(self, char_name: str):
        super().__init__()
        self.char_name = char_name

    async def on_submit(self, interaction: discord.Interaction):
        data = {
            "name": self.char_name,
            "description": self.description.value,
            "personality": self.personality.value,
            "world": self.world.value,
            "speech_style": self.speech_style.value,
            # 把多行文字切成一條一條規則，空行略過
            "rules": [r.strip() for r in self.rules.value.splitlines() if r.strip()],
        }
        character_module.save_character(data)
        await interaction.response.send_message(
            f"已建立角色卡：**{self.char_name}**　用 /character set 或 /character add 載入。",
            ephemeral=True,   # 只有本人看得到，不洗頻
        )


# ---------------------------------------------------------------------------
# /character 指令群組
# ---------------------------------------------------------------------------
character_group = app_commands.Group(name="character", description="角色管理")


@character_group.command(name="set", description="設為此頻道唯一角色（會清掉其他角色與對話）")
@app_commands.describe(name="角色名稱")
async def character_set(interaction: discord.Interaction, name: str):
    data = character_module.load_character(name)
    if data is None:
        await interaction.response.send_message(
            f"找不到角色 {name}，請用 /character list 查看可用角色"
        )
        return
    channel_id = interaction.channel_id
    channel_characters[channel_id] = {name: data}   # 只留這一個角色
    channel_transcript[channel_id] = []             # 換場景，清空對話
    save_state(channel_id)
    await interaction.response.send_message(
        f"已載入角色：**{data['name']}**（單人場景）。用 /chat 或 @我 開始對話。"
    )


@character_group.command(name="add", description="再加一個角色進此頻道（多人同場）")
@app_commands.describe(name="角色名稱")
async def character_add(interaction: discord.Interaction, name: str):
    data = character_module.load_character(name)
    if data is None:
        await interaction.response.send_message(
            f"找不到角色 {name}，請用 /character list 查看可用角色"
        )
        return
    channel_id = interaction.channel_id
    chars = channel_characters.setdefault(channel_id, {})
    if name in chars:
        await interaction.response.send_message(f"{name} 已經在這個頻道裡了。")
        return
    chars[name] = data                  # 加入，但保留現有對話
    save_state(channel_id)
    others = "、".join(chars.keys())
    await interaction.response.send_message(
        f"已加入 **{data['name']}**。目前在場：{others}"
    )


@character_group.command(name="remove", description="移除此頻道的某個角色")
@app_commands.describe(name="角色名稱")
async def character_remove(interaction: discord.Interaction, name: str):
    chars = channel_characters.get(interaction.channel_id, {})
    if name not in chars:
        await interaction.response.send_message(f"{name} 不在這個頻道裡。")
        return
    del chars[name]
    save_state(interaction.channel_id)
    await interaction.response.send_message(f"已移除 **{name}**。")


@character_group.command(name="list", description="列出所有可用角色")
async def character_list(interaction: discord.Interaction):
    names = character_module.list_characters()
    if not names:
        await interaction.response.send_message("characters/ 資料夾裡目前沒有任何角色卡。")
        return
    listed = "\n".join(f"- {n}" for n in names)
    await interaction.response.send_message(f"可用角色：\n{listed}")


@character_group.command(name="info", description="顯示此頻道目前在場的角色")
async def character_info(interaction: discord.Interaction):
    chars = channel_characters.get(interaction.channel_id, {})
    if not chars:
        await interaction.response.send_message("請先用 /character set [name] 設定角色")
        return
    embed = discord.Embed(title="目前在場角色", description="、".join(chars.keys()))
    for data in chars.values():
        embed.add_field(
            name=data["name"],
            value=f"{data['description']}\n說話風格：{data['speech_style']}",
            inline=False,
        )
    await interaction.response.send_message(embed=embed)


@character_group.command(name="clear", description="清除此頻道的角色與對話記憶")
async def character_clear(interaction: discord.Interaction):
    channel_id = interaction.channel_id
    channel_characters.pop(channel_id, None)
    channel_transcript.pop(channel_id, None)
    storage.delete_channel(channel_id)
    await interaction.response.send_message("已清除此頻道的角色與對話記憶。")


@character_group.command(name="create", description="建立一張新的角色卡")
@app_commands.describe(name="新角色的名字")
async def character_create(interaction: discord.Interaction, name: str):
    if character_module.load_character(name) is not None:
        await interaction.response.send_message(
            f"角色 {name} 已存在，換個名字或先用 /character remove。", ephemeral=True
        )
        return
    # 跳出填表視窗讓玩家輸入其餘欄位
    await interaction.response.send_modal(CharacterCreateModal(name))


bot.tree.add_command(character_group)


# ---------------------------------------------------------------------------
# /chat 指令
# ---------------------------------------------------------------------------
@bot.tree.command(name="chat", description="和此頻道的角色說話")
@app_commands.describe(message="你想說的話")
async def chat(interaction: discord.Interaction, message: str):
    channel_id = interaction.channel_id
    if not channel_characters.get(channel_id):
        await interaction.response.send_message("請先用 /character set [name] 設定角色")
        return

    # 多角色各呼叫一次 API 會花好幾秒，先 defer 避免逾時。
    await interaction.response.defer()
    # interaction.followup.send 可以連續送多則，正好給多個角色各送一則。
    author = interaction.user.display_name
    await run_conversation(channel_id, author, message, interaction.followup.send)


# ---------------------------------------------------------------------------
# @bot 觸發
# ---------------------------------------------------------------------------
@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:      # 忽略自己，避免無限對話
        return
    if bot.user not in message.mentions:    # 只在被 @ 時反應
        return

    channel_id = message.channel.id
    if not channel_characters.get(channel_id):
        await message.channel.send("請先用 /character set [name] 設定角色")
        return

    content = message.content.replace(f"<@{bot.user.id}>", "").strip()
    if not content:
        return

    author = message.author.display_name
    async with message.channel.typing():    # 顯示「輸入中…」
        await run_conversation(channel_id, author, content, message.channel.send)


# ---------------------------------------------------------------------------
# 啟動
# ---------------------------------------------------------------------------
def main():
    if not DISCORD_TOKEN:
        raise RuntimeError("找不到 DISCORD_TOKEN，請把 .env.example 複製成 .env 並填入。")
    if not GEMINI_API_KEY:
        raise RuntimeError("找不到 GEMINI_API_KEY，請在 .env 裡填入。")
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
