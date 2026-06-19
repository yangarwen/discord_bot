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
import story as story_module
import storage

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

MODEL = "gemini-2.5-flash"   # 要用的 Gemini 模型（免費額度可用）
MAX_HISTORY = 30             # 對話紀錄最多保留幾句（含玩家與所有角色）
MAX_TOKENS = 2048            # 單次回覆的長度上限（夠講一段完整故事）

gemini = genai.Client(api_key=GEMINI_API_KEY)


# ---------------------------------------------------------------------------
# 每個頻道的狀態（記憶體裡的即時版本，硬碟存檔在 storage.py 負責）
# ---------------------------------------------------------------------------
# channel_characters：頻道 id → {角色名字: 角色卡 dict}，dict 會記住加入順序
channel_characters: dict[int, dict[str, dict]] = {}
# channel_transcript：頻道 id → 對話紀錄，每句是 {"name": 發話者, "text": 內容}
channel_transcript: dict[int, list[dict]] = {}
# channel_story：頻道 id → 劇情進度 {"id": 劇情id, "node": 目前節點id}；沒在玩劇情就沒這個 key
channel_story: dict[int, dict] = {}


def save_state(channel_id: int) -> None:
    """把某頻道目前的狀態寫進硬碟（包一層，避免每個指令都寫一長串）。"""
    storage.save_channel(
        channel_id,
        list(channel_characters.get(channel_id, {}).keys()),
        channel_transcript.get(channel_id, []),
        channel_story.get(channel_id),
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
        if saved.get("story"):            # 有劇情進度才存，避免一堆空值
            channel_story[channel_id] = saved["story"]


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
        f"\n\n以上是目前的對話。請只以「{card['name']}」的身分自然地接話，"
        f"回應的長度與風格依你的角色設定而定（被要求講故事或唱歌時就完整說出來）。"
        f"只輸出你這個角色說的內容，不要加上名字前綴。"
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


async def generate_story_reply(card: dict, player_text: str) -> str:
    """劇情裡「自由對話」節點專用：玩家打一句話，讓指定角色即時回一句。

    這是整個劇情系統裡唯一會呼叫 LLM 的地方。其餘所有按鈕選項都是
    讀事先寫好的文字，不碰 API，所以不用擔心 Gemini 限流。
    """
    scene = (
        f"玩家對你說：「{player_text}」\n\n"
        f"請只以「{card['name']}」的身分自然地回應這句話，"
        f"長度與風格依你的角色設定而定。只輸出你說的內容，不要加名字前綴。"
    )
    response = await gemini.aio.models.generate_content(
        model=MODEL,
        contents=[{"role": "user", "parts": [{"text": scene}]}],
        config=types.GenerateContentConfig(
            system_instruction=character_module.build_system_prompt(card),
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
# 劇情模式：事先寫好的分支劇情，按按鈕推進（不呼叫 LLM）
# 只有節點裡標 free 的選項，按下去才會叫一次 Gemini（自由對話）
# ---------------------------------------------------------------------------
def render_node(channel_id: int) -> tuple[str, discord.ui.View | None] | None:
    """組出目前節點要顯示的文字 + 按鈕。沒在玩劇情或進度壞掉就回 None。"""
    state = channel_story.get(channel_id)
    if not state:
        return None
    story = story_module.load_story(state["id"])
    if story is None:
        return None
    node = story_module.get_node(story, state["node"])
    if node is None:
        return None

    speaker = node.get("speaker")
    text = node.get("text", "")
    body = f"**{speaker}**：{text}" if speaker else text

    choices = node.get("choices", [])
    if not choices:                       # 沒有選項 = 劇情結尾
        body += "\n\n*（這段劇情結束了。用 /story play 重新開始或換一個。）*"
        return body, None
    return body, StoryView(channel_id, choices)


class StoryButton(discord.ui.Button):
    """一個劇情選項按鈕。按下去要嘛跳到下一節點，要嘛開啟自由對話。"""

    def __init__(self, choice: dict):
        super().__init__(label=choice["label"][:80], style=discord.ButtonStyle.secondary)
        self.choice = choice

    async def callback(self, interaction: discord.Interaction):
        channel_id = interaction.channel_id
        if channel_id not in channel_story:   # bot 重開後舊按鈕會失效
            await interaction.response.send_message(
                "這段劇情的按鈕已過期，請用 /story resume 繼續。", ephemeral=True
            )
            return

        # free 選項：跳出輸入框，等玩家打字後才呼叫 LLM
        if self.choice.get("free"):
            await interaction.response.send_modal(FreeTalkModal(self.choice))
            return

        # 一般選項：純跳節點，完全不碰 LLM，瞬間完成
        channel_story[channel_id]["node"] = self.choice["goto"]
        save_state(channel_id)
        rendered = render_node(channel_id)
        if rendered is None:
            await interaction.response.edit_message(content="（劇情資料有誤）", view=None)
            return
        body, view = rendered
        await interaction.response.edit_message(content=body, view=view)


class StoryView(discord.ui.View):
    """一個節點的所有按鈕。timeout 設 15 分鐘；過期後用 /story resume 重新叫出來。"""

    def __init__(self, channel_id: int, choices: list[dict]):
        super().__init__(timeout=900)
        self.channel_id = channel_id
        for choice in choices:
            self.add_item(StoryButton(choice))


class FreeTalkModal(discord.ui.Modal, title="自由對話"):
    """free 選項按下去跳出的輸入框。送出後呼叫一次 LLM，讓指定角色即時回應。"""

    message = discord.ui.TextInput(
        label="你想說什麼？", style=discord.TextStyle.paragraph, max_length=300
    )

    def __init__(self, choice: dict):
        super().__init__()
        self.choice = choice

    async def on_submit(self, interaction: discord.Interaction):
        channel_id = interaction.channel_id
        card = character_module.load_character(self.choice.get("character", ""))
        if card is None:
            await interaction.response.send_message(
                "這個自由對話指定的角色卡不存在。", ephemeral=True
            )
            return

        await interaction.response.defer()    # 先回應互動，爭取 API 的時間
        try:
            reply = await generate_story_reply(card, self.message.value)
        except Exception as e:
            print(f"[Gemini API 錯誤] {e}")
            reply = "（對方愣了一下，似乎沒聽清楚……系統忙線，請稍後再試。）"
        await interaction.followup.send(
            f"**你**：{self.message.value}\n**{card['name']}**：{reply}"
        )

        # 對話結束後，回到這個選項指定的節點（通常是回原處繼續玩）
        goto = self.choice.get("goto")
        if goto:
            channel_story[channel_id]["node"] = goto
            save_state(channel_id)
        rendered = render_node(channel_id)
        if rendered is not None:
            body, view = rendered
            await interaction.followup.send(body, view=view)


story_group = app_commands.Group(name="story", description="劇情模式（按按鈕玩，不吃 AI 額度）")


@story_group.command(name="list", description="列出所有可玩的劇情")
async def story_list(interaction: discord.Interaction):
    stories = story_module.list_stories()
    if not stories:
        await interaction.response.send_message("stories/ 資料夾裡目前沒有任何劇情。")
        return
    listed = "\n".join(f"- `{sid}`　{title}" for sid, title in stories)
    await interaction.response.send_message(f"可玩的劇情：\n{listed}\n\n用 `/story play [id]` 開始。")


@story_group.command(name="play", description="開始一段劇情（會從頭開始）")
@app_commands.describe(story_id="劇情 id（用 /story list 查）")
async def story_play(interaction: discord.Interaction, story_id: str):
    story = story_module.load_story(story_id)
    if story is None:
        await interaction.response.send_message(f"找不到劇情 `{story_id}`，用 /story list 查看。")
        return
    channel_id = interaction.channel_id
    channel_story[channel_id] = {"id": story_id, "node": story.get("start", "")}
    rendered = render_node(channel_id)
    if rendered is None:                   # 起始節點寫錯之類的
        channel_story.pop(channel_id, None)
        await interaction.response.send_message(f"劇情 `{story_id}` 的起始節點有誤，無法開始。")
        return
    save_state(channel_id)
    body, view = rendered
    await interaction.response.send_message(body, view=view)


@story_group.command(name="resume", description="重新叫出目前的劇情畫面（按鈕過期或重開後用）")
async def story_resume(interaction: discord.Interaction):
    rendered = render_node(interaction.channel_id)
    if rendered is None:
        await interaction.response.send_message("這個頻道沒有進行中的劇情。用 /story play 開始一個。")
        return
    body, view = rendered
    await interaction.response.send_message(body, view=view)


@story_group.command(name="quit", description="結束目前的劇情")
async def story_quit(interaction: discord.Interaction):
    channel_id = interaction.channel_id
    if channel_story.pop(channel_id, None) is None:
        await interaction.response.send_message("目前沒有在玩劇情。")
        return
    save_state(channel_id)
    await interaction.response.send_message("已結束劇情。")


bot.tree.add_command(story_group)


# ---------------------------------------------------------------------------
# /chat 指令
# ---------------------------------------------------------------------------
@bot.tree.command(name="chat", description="和此頻道的角色說話（其實直接打字就會回，這個是備用）")
@app_commands.describe(message="你想說的話")
async def chat(interaction: discord.Interaction, message: str):
    channel_id = interaction.channel_id
    if not channel_characters.get(channel_id):
        await interaction.response.send_message("請先用 /character set [name] 設定角色")
        return

    author = interaction.user.display_name
    # 先把玩家的話「顯示」出來，否則 /chat 的輸入只有自己看得到。
    # 這同時也立刻回應了互動，不會卡到 3 秒逾時。
    await interaction.response.send_message(f"**{author}**：{message}")
    # 之後每個角色的回覆用 followup 連續送出。
    await run_conversation(channel_id, author, message, interaction.followup.send)


# ---------------------------------------------------------------------------
# 一般訊息觸發：只要這個頻道設了角色，直接打字就會觸發（不必 /chat 也不必 @）
# ---------------------------------------------------------------------------
@bot.event
async def on_message(message: discord.Message):
    # 忽略所有機器人（包含自己），避免互相無限對話
    if message.author.bot:
        return

    channel_id = message.channel.id
    mentioned = bot.user in message.mentions
    # 訊息文字；若有 @我，把那段 mention 標記去掉
    content = message.content
    if mentioned:
        content = content.replace(f"<@{bot.user.id}>", "").strip()

    # 這個頻道還沒設角色：
    #   被 @ 才提示一下；一般閒聊不出聲，免得在非扮演頻道一直插話
    if not channel_characters.get(channel_id):
        if mentioned:
            await message.channel.send("請先用 /character set [name] 設定角色")
        return

    # 以 // 開頭的訊息當成「出戲聊天(OOC)」，bot 不回應，方便你私下討論
    if content.startswith("//") or not content.strip():
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
