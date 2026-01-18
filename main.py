import discord
from discord import app_commands
from discord.ext import commands

import os
import dotenv

dotenv.load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

# 봇 설정
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

# 봇이 준비되었을 때
@bot.event
async def on_ready():
    print(f'{bot.user}로 로그인했습니다!')
    try:
        # 슬래시 명령어를 디스코드에 동기화
        synced = await bot.tree.sync()
        print(f'{len(synced)}개의 슬래시 명령어가 동기화되었습니다.')
    except Exception as e:
        print(f'명령어 동기화 오류: {e}')

# 슬래시 명령어 예시 1: 기본 명령어
@bot.tree.command(name="안녕", description="봇이 인사합니다")
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f'안녕하세요, {interaction.user.mention}님!')

# 슬래시 명령어 예시 2: 매개변수가 있는 명령어
@bot.tree.command(name="더하기", description="두 숫자를 더합니다")
@app_commands.describe(
    숫자1="첫 번째 숫자",
    숫자2="두 번째 숫자"
)
async def add(interaction: discord.Interaction, 숫자1: int, 숫자2: int):
    result = 숫자1 + 숫자2
    await interaction.response.send_message(f'{숫자1} + {숫자2} = {result}')

# 슬래시 명령어 예시 3: 선택지가 있는 명령어
@bot.tree.command(name="주사위", description="주사위를 굴립니다")
@app_commands.describe(면="주사위 면 개수 (기본값: 6)")
@app_commands.choices(면=[
    app_commands.Choice(name="6면", value=6),
    app_commands.Choice(name="20면", value=20),
    app_commands.Choice(name="100면", value=100)
])
async def dice(interaction: discord.Interaction, 면: int = 6):
    import random
    result = random.randint(1, 면)
    await interaction.response.send_message(f'🎲 {면}면 주사위 결과: **{result}**')

# 슬래시 명령어 예시 4: 정보 명령어
@bot.tree.command(name="정보", description="봇 정보를 표시합니다")
async def info(interaction: discord.Interaction):
    embed = discord.Embed(
        title="봇 정보",
        description="디스코드 슬래시 명령어 봇입니다",
        color=discord.Color.blue()
    )
    embed.add_field(name="서버 수", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="핑", value=f"{round(bot.latency * 1000)}ms", inline=True)
    await interaction.response.send_message(embed=embed)


if __name__ == '__main__':
    bot.run(BOT_TOKEN)