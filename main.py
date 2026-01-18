import os
import dotenv
import asyncio
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

dotenv.load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

# 봇 설정
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='/', intents=intents)

# 일정 데이터 저장 (실제 서비스에서는 DB 사용 권장)
schedules = {}  # 대기 중 및 취소될 일정
activated_schedules = {}  # 확정된 일정 (알람 대기 중)


# 날짜/시간 형식 변환기
class DateTimeTransformer(app_commands.Transformer):
    async def transform(self, interaction: discord.Interaction, value: str) -> str:
        formats = [
            "%Y-%m-%d %H:%M",
            "%Y/%m/%d %H:%M",
            "%Y.%m.%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
        ]

        for fmt in formats:
            try:
                datetime.strptime(value, fmt)
                return value
            except ValueError:
                continue

        raise app_commands.AppCommandError(
            f"❌ 올바르지 않은 날짜/시간 형식입니다.\n\n"
            f"**지원하는 형식:**\n"
            f"• `YYYY-MM-DD HH:MM` (예: 2026-01-25 18:00)\n"
            f"• `YYYY/MM/DD HH:MM` (예: 2026/01/25 18:00)\n"
            f"• `YYYY.MM.DD HH:MM` (예: 2026.01.25 18:00)\n\n"
            f"**입력하신 값:** `{value}`"
        )


class AttendanceButton(discord.ui.View):
    def __init__(self, schedule_id: str):
        super().__init__(timeout=None)
        self.schedule_id = schedule_id

    @discord.ui.button(label="참석", style=discord.ButtonStyle.green, custom_id="attend_yes")
    async def attend_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_response(interaction, True)

    @discord.ui.button(label="불참", style=discord.ButtonStyle.red, custom_id="attend_no")
    async def attend_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_response(interaction, False)

    async def handle_response(self, interaction: discord.Interaction, attending: bool):
        # 대기 중 일정과 활성화된 일정 모두 확인
        schedule = schedules.get(self.schedule_id) or activated_schedules.get(self.schedule_id)

        if not schedule:
            await interaction.response.send_message("일정을 찾을 수 없습니다.", ephemeral=True)
            return

        user_id = interaction.user.id

        # 이미 응답한 경우
        if user_id in schedule['responses']:
            await interaction.response.send_message("이미 응답하셨습니다.", ephemeral=True)
            return

        # 일정이 이미 확정되거나 취소된 경우
        if schedule.get('activated') or schedule.get('cancelled'):
            status_text = "확정" if schedule.get('activated') else "취소"
            await interaction.response.send_message(f"이 일정은 이미 {status_text}되었습니다.", ephemeral=True)
            return

        # 응답 저장
        schedule['responses'][user_id] = attending

        # DM 메시지의 버튼 제거
        status = "참석" if attending else "불참"

        try:
            current_embed = interaction.message.embeds[0] if interaction.message.embeds else None

            if current_embed:
                current_embed.color = discord.Color.green() if attending else discord.Color.red()
                current_embed.set_footer(text=f"✅ {status}으로 응답 완료")

            await interaction.response.edit_message(embed=current_embed, view=None)
        except Exception as e:
            print(f"DM 메시지 업데이트 오류: {e}")
            await interaction.response.send_message(f"'{schedule['title']}' 일정에 **{status}**으로 응답하셨습니다!",
                                                    ephemeral=True)

        # 현재 참석자 수 계산
        attending_count = sum(1 for v in schedule['responses'].values() if v)
        no_response_count = len([u for u in schedule['mentioned_users'] if u not in schedule['responses']])

        # 일정 확정 확인
        if attending_count >= schedule['min_participants'] and not schedule['activated']:
            schedule['activated'] = True
            await self.move_to_activated_queue(schedule)
            await self.notify_activation(schedule)
            await self.update_schedule_message(schedule)
        # 일정 취소 확인
        elif attending_count + no_response_count < schedule['min_participants'] and not schedule.get('cancelled'):
            schedule['cancelled'] = True
            await self.notify_cancellation(schedule)
            await self.update_schedule_message(schedule)
            await self.remove_cancelled_schedule(schedule)
        else:
            await self.update_schedule_message(schedule)

    async def move_to_activated_queue(self, schedule):
        """확정된 일정을 activated_schedules 큐로 이동"""
        schedule_id = schedule['id']
        activated_schedules[schedule_id] = schedule

        # 대기 큐에서 제거
        if schedule_id in schedules:
            del schedules[schedule_id]

        print(f"✅ 일정 '{schedule['title']}'이 확정 큐로 이동되었습니다.")

    async def remove_cancelled_schedule(self, schedule):
        """취소된 일정을 큐에서 삭제"""
        schedule_id = schedule['id']

        if schedule_id in schedules:
            del schedules[schedule_id]
            print(f"❌ 취소된 일정 '{schedule['title']}'이 삭제되었습니다.")

    async def update_schedule_message(self, schedule):
        """그룹 채팅방의 일정 메시지 업데이트"""
        try:
            channel = bot.get_channel(schedule['channel_id'])
            message = await channel.fetch_message(schedule['message_id'])

            embed = self.create_schedule_embed(schedule)
            await message.edit(embed=embed)
        except Exception as e:
            print(f"메시지 업데이트 오류: {e}")

    def create_schedule_embed(self, schedule):
        """일정 정보 임베드 생성"""
        # 일정 취소된 경우
        if schedule.get('cancelled'):
            embed = discord.Embed(
                title=f"❌ {schedule['title']} (취소됨)",
                description=schedule['description'],
                color=discord.Color.red(),
                timestamp=datetime.now()
            )
            embed.add_field(name="📍 날짜/시간", value=schedule['datetime'], inline=False)
            embed.add_field(name="👥 최소 인원", value=f"{schedule['min_participants']}명", inline=True)
            embed.add_field(name="🚫 취소 사유", value="최소 인원을 충족할 수 없습니다.", inline=False)
            embed.set_footer(text=f"생성자: {schedule['creator_name']}")
            return embed

        # 일정 확정된 경우
        if schedule.get('activated'):
            embed = discord.Embed(
                title=f"✅ {schedule['title']} (확정)",
                description=schedule['description'],
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
        else:
            # 대기 중
            embed = discord.Embed(
                title=f"📅 {schedule['title']}",
                description=schedule['description'],
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )

        # 일정 정보
        embed.add_field(name="📍 날짜/시간", value=schedule['datetime'], inline=False)
        embed.add_field(name="👥 최소 인원", value=f"{schedule['min_participants']}명", inline=True)

        # 참석자 현황
        attending = []
        not_attending = []
        no_response = []

        for user_id in schedule['mentioned_users']:
            if user_id in schedule['responses']:
                user = bot.get_user(user_id)
                if schedule['responses'][user_id]:
                    attending.append(user.mention if user else f"<@{user_id}>")
                else:
                    not_attending.append(user.mention if user else f"<@{user_id}>")
            else:
                user = bot.get_user(user_id)
                no_response.append(user.mention if user else f"<@{user_id}>")

        attending_text = "\n".join(attending) if attending else "없음"
        not_attending_text = "\n".join(not_attending) if not_attending else "없음"
        no_response_text = "\n".join(no_response) if no_response else "없음"

        embed.add_field(name=f"✅ 참석 ({len(attending)}명)", value=attending_text, inline=True)
        embed.add_field(name=f"❌ 불참 ({len(not_attending)}명)", value=not_attending_text, inline=True)
        embed.add_field(name=f"⏳ 미응답 ({len(no_response)}명)", value=no_response_text, inline=True)

        # 활성화 상태
        if schedule.get('activated'):
            embed.add_field(name="🎉 상태", value="**일정이 확정되었습니다!**", inline=False)
        else:
            remaining = schedule['min_participants'] - len(attending)
            embed.add_field(name="⏰ 상태", value=f"확정까지 {remaining}명 더 필요합니다.", inline=False)

        embed.set_footer(text=f"생성자: {schedule['creator_name']}")

        return embed

    async def notify_activation(self, schedule):
        """일정 활성화 시 참석자들에게 DM 전송"""
        for user_id in schedule['mentioned_users']:
            if schedule['responses'].get(user_id, False):
                try:
                    user = await bot.fetch_user(user_id)
                    embed = discord.Embed(
                        title="🎉 일정이 확정되었습니다!",
                        description=f"**{schedule['title']}** 일정이 최소 인원을 충족하여 확정되었습니다.",
                        color=discord.Color.green()
                    )
                    embed.add_field(name="📍 날짜/시간", value=schedule['datetime'], inline=False)
                    embed.add_field(name="📝 설명", value=schedule['description'], inline=False)
                    await user.send(embed=embed)
                except Exception as e:
                    print(f"DM 전송 실패 (User {user_id}): {e}")

    async def notify_cancellation(self, schedule):
        """일정 취소 시 모든 참석자들에게 DM 전송"""
        for user_id in schedule['mentioned_users']:
            try:
                user = await bot.fetch_user(user_id)
                embed = discord.Embed(
                    title="❌ 일정이 취소되었습니다",
                    description=f"**{schedule['title']}** 일정이 최소 인원을 충족하지 못해 취소되었습니다.",
                    color=discord.Color.red()
                )
                embed.add_field(name="📍 날짜/시간", value=schedule['datetime'], inline=False)
                embed.add_field(name="📝 설명", value=schedule['description'], inline=False)
                embed.add_field(name="🚫 취소 사유", value="참석 가능 인원이 최소 인원에 미달했습니다.", inline=False)
                await user.send(embed=embed)
            except Exception as e:
                print(f"DM 전송 실패 (User {user_id}): {e}")


@tasks.loop(minutes=1)
async def check_reminders():
    """1분마다 확정된 일정을 확인하고 10분 전 알람 전송"""
    now = datetime.now()
    schedules_to_remove = []

    for schedule_id, schedule in activated_schedules.items():
        try:
            # 일정 시간 파싱
            formats = ["%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M:%S"]
            schedule_time = None

            for fmt in formats:
                try:
                    schedule_time = datetime.strptime(schedule['datetime'], fmt)
                    break
                except ValueError:
                    continue

            if not schedule_time:
                print(f"⚠️ 일정 '{schedule['title']}'의 시간 형식을 파싱할 수 없습니다.")
                continue

            # 10분 전 시간 계산
            reminder_time = schedule_time - timedelta(minutes=10)

            # 현재 시간이 알람 시간을 지났고, 아직 알람을 보내지 않았다면
            if now >= reminder_time and not schedule.get('reminder_sent'):
                await send_reminder(schedule)
                schedule['reminder_sent'] = True
                schedules_to_remove.append(schedule_id)
                print(f"⏰ 일정 '{schedule['title']}'에 대한 알람이 전송되었습니다.")

        except Exception as e:
            print(f"알람 확인 중 오류 (일정 ID: {schedule_id}): {e}")

    # 알람을 보낸 일정들을 큐에서 제거
    for schedule_id in schedules_to_remove:
        del activated_schedules[schedule_id]
        print(f"🗑️ 알람 전송 완료된 일정 '{activated_schedules.get(schedule_id, {}).get('title', schedule_id)}'이 삭제되었습니다.")


@tasks.loop(minutes=1)
async def check_expired_schedules():
    """1분마다 대기 중인 일정을 확인하고 시간이 지난 일정 자동 취소"""
    now = datetime.now()
    schedules_to_cancel = []

    for schedule_id, schedule in list(schedules.items()):
        # 이미 취소된 일정은 스킵
        if schedule.get('cancelled'):
            continue

        try:
            # 일정 시간 파싱
            formats = ["%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M:%S"]
            schedule_time = None

            for fmt in formats:
                try:
                    schedule_time = datetime.strptime(schedule['datetime'], fmt)
                    break
                except ValueError:
                    continue

            if not schedule_time:
                print(f"⚠️ 일정 '{schedule['title']}'의 시간 형식을 파싱할 수 없습니다.")
                continue

            # 일정 시간이 지났다면 자동 취소
            if now >= schedule_time:
                schedules_to_cancel.append((schedule_id, schedule))
                print(f"⏱️ 일정 '{schedule['title']}'의 시간이 지나 자동 취소됩니다.")

        except Exception as e:
            print(f"만료 일정 확인 중 오류 (일정 ID: {schedule_id}): {e}")

    # 시간이 지난 일정들 취소 처리
    for schedule_id, schedule in schedules_to_cancel:
        schedule['cancelled'] = True
        await auto_cancel_schedule(schedule)

        # 큐에서 제거
        if schedule_id in schedules:
            del schedules[schedule_id]
            print(f"🗑️ 만료된 일정 '{schedule['title']}'이 삭제되었습니다.")


async def auto_cancel_schedule(schedule):
    """시간 만료로 자동 취소된 일정 처리"""
    # 그룹 채팅방 메시지 업데이트
    try:
        channel = bot.get_channel(schedule['channel_id'])
        message = await channel.fetch_message(schedule['message_id'])

        embed = discord.Embed(
            title=f"❌ {schedule['title']} (자동 취소됨)",
            description=schedule['description'],
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        embed.add_field(name="📍 날짜/시간", value=schedule['datetime'], inline=False)
        embed.add_field(name="👥 최소 인원", value=f"{schedule['min_participants']}명", inline=True)
        embed.add_field(name="🚫 취소 사유", value="일정 시간이 지났으나 최소 인원을 충족하지 못했습니다.", inline=False)

        # 참석자 현황
        attending_count = sum(1 for v in schedule['responses'].values() if v)
        embed.add_field(name="📊 최종 현황", value=f"참석 응답: {attending_count}명 / 최소 필요: {schedule['min_participants']}명",
                        inline=False)
        embed.set_footer(text=f"생성자: {schedule['creator_name']}")

        await message.edit(embed=embed)
    except Exception as e:
        print(f"메시지 업데이트 오류: {e}")

    # 참석자들에게 DM 전송
    for user_id in schedule['mentioned_users']:
        try:
            user = await bot.fetch_user(user_id)
            embed = discord.Embed(
                title="❌ 일정이 자동 취소되었습니다",
                description=f"**{schedule['title']}** 일정이 시간 만료로 자동 취소되었습니다.",
                color=discord.Color.red()
            )
            embed.add_field(name="📍 날짜/시간", value=schedule['datetime'], inline=False)
            embed.add_field(name="📝 설명", value=schedule['description'], inline=False)
            embed.add_field(name="🚫 취소 사유", value="일정 시간이 지났으나 최소 인원을 충족하지 못했습니다.", inline=False)

            attending_count = sum(1 for v in schedule['responses'].values() if v)
            embed.add_field(name="📊 최종 현황", value=f"참석 응답: {attending_count}명 / 최소 필요: {schedule['min_participants']}명",
                            inline=False)

            await user.send(embed=embed)
        except Exception as e:
            print(f"DM 전송 실패 (User {user_id}): {e}")


async def send_reminder(schedule):
    """일정 10분 전 알람을 참석자들에게 전송"""
    for user_id in schedule['mentioned_users']:
        if schedule['responses'].get(user_id, False):  # 참석으로 응답한 사람만
            try:
                user = await bot.fetch_user(user_id)
                embed = discord.Embed(
                    title="⏰ 일정 알림 (10분 전)",
                    description=f"**{schedule['title']}** 일정이 곧 시작됩니다!",
                    color=discord.Color.orange()
                )
                embed.add_field(name="📍 날짜/시간", value=schedule['datetime'], inline=False)
                embed.add_field(name="📝 설명", value=schedule['description'], inline=False)
                embed.add_field(name="⏰", value="10분 후 시작 예정입니다.", inline=False)
                await user.send(embed=embed)
            except Exception as e:
                print(f"알람 DM 전송 실패 (User {user_id}): {e}")


@bot.event
async def on_ready():
    print(f'{bot.user}로 로그인했습니다!')

    # 백그라운드 태스크 시작
    if not check_reminders.is_running():
        check_reminders.start()
        print("⏰ 알람 체크 태스크가 시작되었습니다.")

    if not check_expired_schedules.is_running():
        check_expired_schedules.start()
        print("⏱️ 만료 일정 체크 태스크가 시작되었습니다.")

    try:
        synced = await bot.tree.sync()
        print(f'{len(synced)}개의 슬래시 명령어가 동기화되었습니다.')
    except Exception as e:
        print(f'명령어 동기화 오류: {e}')


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.AppCommandError):
        await interaction.response.send_message(str(error), ephemeral=True)
    else:
        await interaction.response.send_message(f"오류가 발생했습니다: {str(error)}", ephemeral=True)


@bot.tree.command(name="일정생성", description="새로운 일정을 생성합니다")
@app_commands.describe(
    제목="일정 제목",
    설명="일정 설명",
    날짜시간="날짜와 시간 (예: 2026-01-25 18:00)",
    최소인원="일정 확정을 위한 최소 인원",
    참석자="참석자 멘션 (공백으로 구분, 예: @user1 @user2)"
)
async def create_schedule(
        interaction: discord.Interaction,
        제목: str,
        설명: str,
        날짜시간: app_commands.Transform[str, DateTimeTransformer],
        최소인원: int,
        참석자: str
):
    # 날짜/시간 유효성 검사 (과거 시간 체크)
    formats = ["%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M:%S"]
    schedule_time = None

    for fmt in formats:
        try:
            schedule_time = datetime.strptime(날짜시간, fmt)
            break
        except ValueError:
            continue

    if schedule_time and schedule_time <= datetime.now():
        await interaction.response.send_message(
            f"❌ 일정 시간은 현재 시간보다 이후여야 합니다.\n\n"
            f"**입력한 시간:** {날짜시간}\n"
            f"**현재 시간:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            ephemeral=True
        )
        return

    # 멘션된 사용자 파싱
    mentioned_users = []
    for word in 참석자.split():
        if word.startswith('<@') and word.endswith('>'):
            user_id = int(word.strip('<@!>'))
            mentioned_users.append(user_id)

    if not mentioned_users:
        await interaction.response.send_message("참석자를 올바르게 멘션해주세요. (예: @user1 @user2)", ephemeral=True)
        return

    if 최소인원 <= 0:
        await interaction.response.send_message("최소 인원은 1명 이상이어야 합니다.", ephemeral=True)
        return

    if len(mentioned_users) < 최소인원:
        await interaction.response.send_message("지정한 인원 수가 최소 인원 수를 넘지 않습니다.", ephemeral=True)
        return

    # 일정 ID 생성
    schedule_id = f"{interaction.guild.id}_{interaction.channel.id}_{datetime.now().timestamp()}"

    # 일정 데이터 저장
    schedule_data = {
        'id': schedule_id,
        'title': 제목,
        'description': 설명,
        'datetime': 날짜시간,
        'min_participants': 최소인원,
        'mentioned_users': mentioned_users,
        'responses': {},
        'activated': False,
        'cancelled': False,
        'reminder_sent': False,
        'creator_id': interaction.user.id,
        'creator_name': interaction.user.name,
        'channel_id': interaction.channel.id,
        'message_id': None
    }

    schedules[schedule_id] = schedule_data

    # 그룹 채팅방에 일정 메시지 게시
    view = AttendanceButton(schedule_id)
    embed = view.create_schedule_embed(schedule_data)

    await interaction.response.send_message(embed=embed)

    # 메시지 ID 저장
    message = await interaction.original_response()
    schedule_data['message_id'] = message.id

    # 참석자들에게 DM 전송
    for user_id in mentioned_users:
        try:
            user = await bot.fetch_user(user_id)
            dm_embed = discord.Embed(
                title=f"📅 새로운 일정 초대",
                description=f"**{제목}**에 초대되었습니다!",
                color=discord.Color.blue()
            )
            dm_embed.add_field(name="📝 설명", value=설명, inline=False)
            dm_embed.add_field(name="📍 날짜/시간", value=날짜시간, inline=False)
            dm_embed.add_field(name="👥 최소 인원", value=f"{최소인원}명", inline=False)
            dm_embed.set_footer(text=f"생성자: {interaction.user.name}")

            dm_view = AttendanceButton(schedule_id)
            await user.send(embed=dm_embed, view=dm_view)
        except discord.Forbidden:
            print(f"DM 전송 실패: {user_id} (DM 차단됨)")
        except Exception as e:
            print(f"DM 전송 오류: {e}")


@bot.tree.command(name="일정목록", description="현재 진행 중인 일정 목록을 확인합니다")
async def list_schedules(interaction: discord.Interaction):
    # 대기 중 일정과 확정된 일정 모두 가져오기
    channel_schedules = [s for s in schedules.values() if s['channel_id'] == interaction.channel.id]
    channel_activated = [s for s in activated_schedules.values() if s['channel_id'] == interaction.channel.id]

    if not channel_schedules and not channel_activated:
        await interaction.response.send_message("진행 중인 일정이 없습니다.", ephemeral=True)
        return

    embed = discord.Embed(
        title="📋 일정 목록",
        color=discord.Color.blue()
    )

    # 대기 중 일정
    for schedule in channel_schedules:
        attending_count = sum(1 for v in schedule['responses'].values() if v)

        if schedule.get('cancelled'):
            status = "❌ 취소됨"
        else:
            status = f"⏰ 대기 ({attending_count}/{schedule['min_participants']})"

        embed.add_field(
            name=f"{schedule['title']} - {status}",
            value=f"📍 {schedule['datetime']}\n👥 최소 {schedule['min_participants']}명",
            inline=False
        )

    # 확정된 일정
    for schedule in channel_activated:
        attending_count = sum(1 for v in schedule['responses'].values() if v)
        status = "✅ 확정"

        embed.add_field(
            name=f"{schedule['title']} - {status}",
            value=f"📍 {schedule['datetime']}\n👥 참석 {attending_count}명",
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


# 봇 실행
if __name__ == '__main__':
    bot.run(BOT_TOKEN)