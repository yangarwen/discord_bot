"""Discord AI Roleplay Bot 主程式。

流程大致是：
  使用者 → Discord（/chat 或 @bot）→ 這支程式 → Claude API → 回覆貼回 Discord
"""

import os

import anthropic
import discord
from discord import app_commands
from dotenv import load_dotenv

import character as character_module

# load_dotenv() 會去讀 .env 檔，把裡面的變數塞進「環境變數」，
# 之後就能用 os.getenv("名字") 取出來。金鑰寫在 .env、不寫進程式碼，才不會外洩。
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

MODEL = "claude-sonnet-4-6"   # 要用的 Claude 模型
MAX_HISTORY = 20              # 每個頻道最多保留幾則訊息（user + assistant 各算一則）
MAX_TOKENS = 1024             # 單次回覆的長度上限（token ≈ 字的單位）

# AsyncAnthropic 是「非同步」版本的客戶端。
# discord.py 本身跑在 asyncio 事件迴圈上，如果用同步版會「卡住」整個 bot，
# 用 Async 版搭配 await，等 API 回應時 bot 還能處理其他事情。
claude = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


# ---------------------------------------------------------------------------
# 每個頻道各自獨立的狀態（存在記憶體裡，bot 重開就會清空）
# ---------------------------------------------------------------------------
# channel_characters：頻道 id → 該頻道目前載入的角色卡 dict
channel_characters: dict[int, dict] = {}
# channel_history：頻道 id → 這個頻道的對話歷史（list，每筆是 {"role", "content"}）
channel_history: dict[int, list[dict]] = {}


# ---------------------------------------------------------------------------
# Discord client 設定
# ---------------------------------------------------------------------------
# intents 是「權限意圖」，告訴 Discord 我們要接收哪些事件。
# message_content 一定要開，否則 @bot 時讀不到訊息文字。
intents = discord.Intents.default()
intents.message_content = True


class RoleplayBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        # CommandTree 是放「斜線指令(/)」的地方
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # setup_hook 在 bot 啟動時跑一次。
        # tree.sync() 把我們定義的斜線指令上傳到 Discord，指令才會出現在輸入框。
        await self.tree.sync()


bot = RoleplayBot()


@bot.event
async def on_ready():
    print(f"已登入：{bot.user}（ID: {bot.user.id}）")


# ---------------------------------------------------------------------------
# 核心：呼叫 Claude 產生回覆
# ---------------------------------------------------------------------------
async def generate_reply(channel_id: int, user_message: str) -> str:
    """把使用者訊息丟給 Claude，回傳角色的回覆字串。

    這個函式被 /chat 和 @bot 兩種觸發方式共用，避免重複程式碼。
    """
    character = channel_characters[channel_id]

    # 取出（或建立）這個頻道的對話歷史
    history = channel_history.setdefault(channel_id, [])
    # 先把這次的使用者訊息加進歷史
    history.append({"role": "user", "content": user_message})

    # 呼叫 Claude 的 Messages API：
    #   system   = 角色設定（用 character.py 組好的 prompt）
    #   messages = 整段對話歷史，Claude 會看著它接話
    response = await claude.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=character_module.build_system_prompt(character),
        messages=history,
    )
    # 回應的文字在 content[0].text
    reply = response.content[0].text

    # 把 AI 的回覆也存進歷史，下一輪對話才有上下文
    history.append({"role": "assistant", "content": reply})

    # 只保留最近 MAX_HISTORY 則，避免歷史無限變長（也省 token）
    if len(history) > MAX_HISTORY:
        del history[:-MAX_HISTORY]

    return reply


# ---------------------------------------------------------------------------
# /character 指令群組
# ---------------------------------------------------------------------------
# app_commands.Group 讓我們做出 /character set、/character list 這種「子指令」
character_group = app_commands.Group(name="character", description="角色管理")


@character_group.command(name="set", description="在此頻道載入指定角色")
@app_commands.describe(name="角色名稱（用 /character list 查看）")
async def character_set(interaction: discord.Interaction, name: str):
    data = character_module.load_character(name)
    if data is None:
        # 規格指定的錯誤訊息
        await interaction.response.send_message(
            f"找不到角色 {name}，請用 /character list 查看可用角色"
        )
        return

    channel_id = interaction.channel_id
    channel_characters[channel_id] = data
    # 換角色時把舊的對話記憶清掉，避免角色串味
    channel_history.pop(channel_id, None)
    await interaction.response.send_message(
        f"已在此頻道載入角色：**{data['name']}**　現在可以用 /chat 或 @我 開始對話。"
    )


@character_group.command(name="list", description="列出所有可用角色")
async def character_list(interaction: discord.Interaction):
    names = character_module.list_characters()
    if not names:
        await interaction.response.send_message("characters/ 資料夾裡目前沒有任何角色卡。")
        return
    listed = "\n".join(f"- {n}" for n in names)
    await interaction.response.send_message(f"可用角色：\n{listed}")


@character_group.command(name="info", description="顯示目前角色的設定")
async def character_info(interaction: discord.Interaction):
    data = channel_characters.get(interaction.channel_id)
    if data is None:
        await interaction.response.send_message("請先用 /character set [name] 設定角色")
        return
    # discord.Embed 是排版漂亮的訊息卡片
    embed = discord.Embed(title=data["name"], description=data["description"])
    embed.add_field(name="個性", value=data["personality"], inline=False)
    embed.add_field(name="世界觀", value=data["world"], inline=False)
    embed.add_field(name="說話風格", value=data["speech_style"], inline=False)
    embed.add_field(name="規則", value="\n".join(f"- {r}" for r in data["rules"]), inline=False)
    await interaction.response.send_message(embed=embed)


@character_group.command(name="clear", description="清除此頻道的角色與對話記憶")
async def character_clear(interaction: discord.Interaction):
    channel_id = interaction.channel_id
    channel_characters.pop(channel_id, None)
    channel_history.pop(channel_id, None)
    await interaction.response.send_message("已清除此頻道的角色與對話記憶。")


# 把整個群組註冊到指令樹
bot.tree.add_command(character_group)


# ---------------------------------------------------------------------------
# /chat 指令
# ---------------------------------------------------------------------------
@bot.tree.command(name="chat", description="和目前角色說話")
@app_commands.describe(message="你想說的話")
async def chat(interaction: discord.Interaction, message: str):
    channel_id = interaction.channel_id
    if channel_id not in channel_characters:
        await interaction.response.send_message("請先用 /character set [name] 設定角色")
        return

    # 呼叫 API 可能要好幾秒，Discord 規定 3 秒內要先回應，
    # defer() 先送出「思考中…」狀態，避免指令逾時失敗。
    await interaction.response.defer()
    try:
        reply = await generate_reply(channel_id, message)
        await interaction.followup.send(reply)
    except Exception as e:
        # 出錯時用「保持沉浸感」的訊息，名字用目前角色的
        name = channel_characters[channel_id]["name"]
        print(f"[Claude API 錯誤] {e}")   # 真正的錯誤印在後台，方便你除錯
        await interaction.followup.send(f"{name} 沉默了一下……（系統錯誤，請稍後再試）")


# ---------------------------------------------------------------------------
# @bot 觸發：在頻道裡 @機器人 也能對話
# ---------------------------------------------------------------------------
@bot.event
async def on_message(message: discord.Message):
    # 忽略自己發的訊息，否則會無限自我對話
    if message.author == bot.user:
        return
    # 只有在被 @mention 時才反應
    if bot.user not in message.mentions:
        return

    channel_id = message.channel.id
    if channel_id not in channel_characters:
        await message.channel.send("請先用 /character set [name] 設定角色")
        return

    # 把訊息裡的 mention 標記（<@123...>）拿掉，只留下真正想說的話
    content = message.content.replace(f"<@{bot.user.id}>", "").strip()
    if not content:
        return

    # async with channel.typing()：對話期間顯示「對方正在輸入…」，比較有臨場感
    async with message.channel.typing():
        try:
            reply = await generate_reply(channel_id, content)
            await message.channel.send(reply)
        except Exception as e:
            name = channel_characters[channel_id]["name"]
            print(f"[Claude API 錯誤] {e}")
            await message.channel.send(f"{name} 沉默了一下……（系統錯誤，請稍後再試）")


# ---------------------------------------------------------------------------
# 啟動
# ---------------------------------------------------------------------------
def main():
    if not DISCORD_TOKEN:
        raise RuntimeError("找不到 DISCORD_TOKEN，請把 .env.example 複製成 .env 並填入。")
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("找不到 ANTHROPIC_API_KEY，請在 .env 裡填入。")
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
