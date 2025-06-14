# -*- coding: utf-8 -*-
"""
授权协议助手 (LicenseCog)

本模块实现了一个 Discord Cog，旨在帮助服务器内的创作者管理其作品的内容授权协议。
主要功能包括：
- 在指定论坛频道中，当有新帖子（作品）发布时，自动向作者发送交互式提醒。
- 允许用户通过斜杠命令 (`/`) 或交互式按钮创建、编辑、查看和管理自己的默认授权协议。
- 支持标准的 Creative Commons (CC) 协议模板和完全自定义的协议。
- 提供精细的机器人行为设置，如启用/禁用、自动发布、发布前确认等。
- 所有交互均通过现代的 discord.py UI 组件（Views, Modals）实现，提供流畅的用户体验。

设计核心：
- 数据持久化：用户配置存储在 `data/licenses/` 目录下的 JSON 文件中，以用户ID命名。
- 缓存机制：`LicenseDB` 类实现了内存缓存，以减少频繁的磁盘I/O。
- 模块化UI：每个交互界面（如主面板、编辑中心、设置面板）都被封装在独立的 `discord.ui.View` 类中。
- 回调驱动逻辑：UI组件间的复杂流程通过传递回调函数 (callback) 来解耦和驱动，例如，一个视图完成其任务后，会调用传入的回调函数来触发下一步操作（如保存数据或切换到另一个视图）。
"""

from discord import app_commands
from discord.ext import commands

from src.license.ui_factory import prepare_edit_hub, prepare_confirmation_flow
from src.license.utils import *
from src.license.view.view_main import InitialActionView, FirstTimeSetupView
from src.license.view.view_setting import SettingsView


# --- 主 Cog 类 ---
class LicenseCog(commands.Cog):
    """
    授权协议助手的主Cog类。
    负责监听事件、注册斜杠命令，并将所有业务逻辑串联起来。
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = getattr(bot, 'logger', None)  # 优雅地获取注入的logger
        self.name = SIGNATURE_HELPER
        self.db = LicenseDB()  # 初始化数据库访问层
        # 读取并存储全局商业化开关的状态
        self.commercial_use_allowed = False  # 默认值

        # 从主配置文件 `config.json` 加载要监控的论坛频道ID列表
        config_path = Path('config.json')
        self.monitored_channel_ids = []
        if config_path.exists():
            with config_path.open('r', encoding='utf-8') as f:
                app_config = json.load(f)
                self.monitored_channel_ids = app_config.get('license_cog', {}).get('monitored_channels', [])
                self.commercial_use_allowed = app_config.get('license_cog', {}).get('allow_commercial_use', False)

    @commands.Cog.listener()
    async def on_ready(self):
        """当Cog加载并准备好时，在日志中打印信息。"""
        if self.logger:
            status = "已启用" if self.commercial_use_allowed else "已禁用"
            self.logger.info(f"✅ {SIGNATURE_HELPER}(LicenseCog)已加载，商业化选项：{status}")
            self.logger.info(f"✅ {SIGNATURE_HELPER}(LicenseCog)已加载，监控 {len(self.monitored_channel_ids)} 个论坛频道。")

    # --- 私有辅助方法 ---

    async def _handle_auto_post(self, thread: discord.Thread, config: LicenseConfig):
        """
        通过创建 View 实例和发送一个“占位符”消息，来完全复用已有的流程。
        """

        # # 2. 发送一个临时的“占位符”消息
        # if config.require_confirmation:
        #     placeholder_message = await thread.send(f"正在为 {thread.owner.mention} 自动准备授权协议...")
        # else:
        #     placeholder_message = await thread.send(f"正在为 {thread.owner.mention} 自动发送授权协议...")

        if config.require_confirmation:
            # === 自动进入预览确认流程 ===

            # 1. 定义在此上下文中，确认和取消的“最终动作”
            async def do_post_auto(interaction: discord.Interaction, final_embeds: List[discord.Embed]):
                """确认=发帖并关闭面板"""
                await interaction.edit_original_response(content="✅ 协议已发布。", embed=None, view=None)
                await thread.send(embeds=final_embeds)

            async def do_cancel_auto(interaction: discord.Interaction):
                """取消=返回到标准的主交互面板"""
                # 从自动流程无缝切换到手动流程
                main_view = InitialActionView(
                    cog=self,
                    db=self.db,
                    config=config,
                    thread=thread,
                    commercial_use_allowed=self.commercial_use_allowed
                )
                main_embed = await main_view.get_original_embed()
                # 用主面板替换掉当前的确认面板
                await interaction.edit_original_response(content=None, embed=main_embed, view=main_view)

            # 2. 调用工厂函数来准备预览UI
            preview_content, preview_embeds, confirm_view = await prepare_confirmation_flow(
                cog=self,
                thread=thread,
                config=config,
                author=thread.owner,
                on_confirm_action=do_post_auto,
                on_cancel_action=do_cancel_auto
            )

            # 3. 呈现UI
            await thread.send(content=preview_content, embeds=preview_embeds, view=confirm_view)
        else:
            # === 直接发布 ===
            await thread.send(embeds=build_license_embeds(config, thread.owner, self.commercial_use_allowed))

    async def _find_existing_license_message(self, thread: discord.Thread) -> discord.Message | None:
        """
        侦察方法：在帖子中查找已经发布的最终协议。

        通过查找由机器人发送、且页脚包含特定文本的Embed来识别。
        这可以精确地将“最终协议”与“交互面板”区分开。

        Returns:
            如果找到，返回对应的 discord.Message 对象，否则返回 None。
        """
        try:
            async for message in thread.history(limit=100):
                # 必须是机器人自己发的，且有embed
                if message.author.id != self.bot.user.id or not message.embeds:
                    continue

                embed = message.embeds[0]
                # 关键识别逻辑：通过我们刚刚在 utils.py 中设置的独特页脚文本来识别
                if embed.footer and embed.footer.text and SIGNATURE_LICENSE in embed.footer.text:
                    return message
        except discord.HTTPException as e:
            if self.logger:
                self.logger.warning(f"侦察现有协议时出错 (HTTPException): {e}")
        return None

    async def _save_and_confirm_callback(self, interaction: discord.Interaction, new_details: dict):
        """
        一个标准化的回调函数，用于处理从UI编辑流程中传来的数据。
        它的职责是：保存数据，并向用户发送操作成功的确认消息。
        """
        config = self.db.get_config(interaction.user)
        config.license_details = new_details
        self.db.save_config(config)

        try:
            # 使用 followup.send 发送私密确认消息，以避免与原始交互（如Modal提交）冲突
            await interaction.followup.send("✅ 你的默认协议已更新并保存！", ephemeral=True)
            # 尝试清理发起此流程的UI消息（如编辑枢纽面板）
            if not interaction.is_expired():
                await interaction.edit_original_response(content="✅ 操作完成！", view=None, embed=None)
        except discord.NotFound:
            # 如果原始消息已被删除或找不到了，就忽略
            pass
        except Exception as e:
            if self.logger:
                self.logger.warning(f"在_save_and_confirm_callback中发送确认消息时出错: {e}")

    async def cleanup_previous_helpers(self, thread: discord.Thread):
        """
        清理指定帖子中所有由本机器人发送的、过时的交互面板。
        这在用户请求“重新发送提醒”时非常有用，可以避免界面混乱。
        """
        try:
            # 异步遍历帖子历史消息
            async for message in thread.history(limit=50):
                # 检查消息作者是否是机器人自己
                if message.author.id == self.bot.user.id:
                    # 条件1：消息内容以机器人签名开头 (处理纯文本或混合消息)
                    is_text_helper = message.content.startswith(SIGNATURE_HELPER)
                    # 条件2：消息的Embed页脚包含机器人签名 (处理交互面板Embed)
                    is_embed_helper = False
                    if message.embeds:
                        embed = message.embeds[0]
                        if embed.footer and embed.footer.text and SIGNATURE_HELPER in embed.footer.text:
                            is_embed_helper = True

                    # 如果满足任一条件，则删除
                    if is_text_helper or is_embed_helper:
                        await message.delete()
        except discord.HTTPException as e:
            if self.logger:
                self.logger.warning(f"清理助手消息时出错 (HTTPException): {e}")
        except Exception as e:
            if self.logger:
                self.logger.error(f"清理助手消息时发生未知错误: {e}")

    async def _send_helper_message(self, thread: discord.Thread, is_reauthorization: bool = False):
        """
        向指定帖子发送核心的交互式助手消息。
        现在增加了一个参数 `is_reauthorization` 来处理重新授权的场景。
        """
        author_id = thread.owner_id
        author = await get_member_async_thread(thread, author_id)
        if not author:
            if self.logger: self.logger.info(f"无法找到帖子作者 {author_id}。")
            return

        config = self.db.get_config(author)

        # --- 1. 准备交互文本 (Content) ---
        # 根据是否“重新授权”来决定引导语
        if is_reauthorization:
            content = (
                f"{author.mention} 检测到本帖中已存在一份授权协议。\n\n"
                f"你可以发布一份新的协议，它将适用于你**接下来**在本帖中发布的内容。\n"
                f"**请注意：** 旧内容的授权协议将保持不变。"
            )
        else:
            # 首次发布的场景，只 mention 用户即可，具体的欢迎语在 Embed 里
            content = author.mention

        # --- 新/老用户判断逻辑 ---
        user_config_file = self.db._get_user_file(author_id)
        # 判断是新用户还是老用户
        if not user_config_file.exists():
            # 即使用户是“重新授权”，但如果他们删除了数据，也会被视为新用户，
            # 从而进入正确的“首次设置”流程。

            # 为新用户准备欢迎 Embed
            embed = discord.Embed(
                title=f"欢迎, {author.display_name}！我是内容授权助手",
                description=(
                    "我可以帮助你在每次发布作品后，轻松附上你的授权协议，保护你的创作权益。\n\n"
                    "点击下方按钮，开始创建你的第一份默认协议吧！"
                ),
                color=discord.Color.magenta(),
            )
            embed.set_footer(text=build_footer_text(SIGNATURE_HELPER))
            view = FirstTimeSetupView(db=self.db, cog=self, owner_id=author_id, thread=thread, commercial_use_allowed=self.commercial_use_allowed)
        else:
            # 老用户流程：发送标准的主操作面板
            view = InitialActionView(self, self.db, config, thread, commercial_use_allowed=self.commercial_use_allowed)
            # 对于老用户，Embed 是由 InitialActionView 内部构建的
            embed = await view.get_original_embed()

        # --- 3. 发送消息 ---
        # 将准备好的 content, embed, view 一起发送
        await thread.send(content=content, embed=embed, view=view)

    # --- 事件监听器 ---

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        """
        当在被监控的论坛频道中创建新帖子时触发。
        """
        # 检查1: 是否是受监控的频道
        # 检查2: 发帖人不是机器人自己
        if thread.parent_id not in self.monitored_channel_ids or thread.owner_id == self.bot.user.id:
            return

        # 将 bot_enabled 检查移到这里，因为它只属于自动触发的逻辑！
        config = self.db.get_config(thread.owner)
        if not config.bot_enabled:
            return  # 如果用户禁用了自动提醒，就在这里静默退出。

        # 稍作延迟，避免机器人响应过快显得突兀，或在Discord API事件传播中出现竞争条件
        await asyncio.sleep(2)

        # 调用核心发送逻辑
        if config.auto_post:
            await self._handle_auto_post(thread, config)
        else:
            await self._send_helper_message(thread)

    # --- 斜杠命令组 ---
    license_group = app_commands.Group(
        name=ACTIVE_COMMAND_CONFIG["group"]["name"],
        description=ACTIVE_COMMAND_CONFIG["group"]["description"]
    )

    @license_group.command(
        name=ACTIVE_COMMAND_CONFIG["panel"]["name"],
        description=ACTIVE_COMMAND_CONFIG["panel"]["description"]
    )
    async def panel_me(self, interaction: discord.Interaction):
        """
        命令：在当前帖子中重新召唤协议助手面板。
        """
        await safe_defer(interaction)
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.followup.send("❌ 此命令只能在帖子（子区）中使用。", ephemeral=True)
            return

        thread = interaction.channel
        # 收紧权限：只有帖子所有者可以执行此命令。
        if interaction.user.id != thread.owner_id:
            await interaction.followup.send("❌ 你不是该帖子的所有者，无法执行此操作。", ephemeral=True)
            return

        await interaction.followup.send("✅ 好的，正在为你准备新的授权面板...", ephemeral=True)

        # 1. 执行侦察
        existing_license = await self._find_existing_license_message(thread)

        # 2. 清理旧的 *交互式* 面板
        await self.cleanup_previous_helpers(thread)

        # 3. 根据侦察结果，调用带有正确情景参数的核心发送逻辑
        await self._send_helper_message(thread, is_reauthorization=(existing_license is not None))

        await safe_delete_original_response(interaction, 2)

    @license_group.command(
        name=ACTIVE_COMMAND_CONFIG["edit"]["name"],
        description=ACTIVE_COMMAND_CONFIG["edit"]["description"]
    )
    async def edit_license(self, interaction: discord.Interaction):
        """命令：打开一个私密的面板来编辑用户的默认授权协议。"""
        await safe_defer(interaction)
        config = self.db.get_config(interaction.user)

        # 1. 定义此场景下的“成功”和“取消”回调
        async def on_edit_complete(edit_interaction: discord.Interaction, new_details: dict):
            # 对于斜杠命令，成功就是保存并确认
            await self._save_and_confirm_callback(edit_interaction, new_details)

        async def on_edit_cancel(cancel_interaction: discord.Interaction):
            # 对于斜杠命令，取消就是编辑消息提示已取消
            await cancel_interaction.edit_original_response(content="操作已取消。", view=None, embed=None)

        # 2. 调用工厂函数来构建UI组件
        content, hub_view = prepare_edit_hub(
            db=self.db,
            config=config,
            on_success_callback=on_edit_complete,
            on_cancel_callback=on_edit_cancel,
            commercial_use_allowed=self.commercial_use_allowed,
            is_temporary=False,
            owner_id=interaction.user.id,
        )

        # 3. 在自己的上下文中呈现UI (发送一条新的私密消息)
        # 将纯文本的 content 包装进一个标准的 embed 中
        # 从而与其他入口点的UI保持一致
        hub_embed = create_helper_embed(
            title="📝 编辑默认协议 (永久)",
            description=content
        )

        await interaction.followup.send(
            embed=hub_embed,  # 使用 embed 而不是 content
            view=hub_view,
            ephemeral=True
        )

    @license_group.command(
        name=ACTIVE_COMMAND_CONFIG["settings"]["name"],
        description=ACTIVE_COMMAND_CONFIG["settings"]["description"]
    )
    async def settings(self, interaction: discord.Interaction):
        """命令：打开一个私密的机器人行为设置面板。"""
        await safe_defer(interaction)
        config = self.db.get_config(interaction.user)
        # 使用新的工厂函数创建Embed
        embed = build_settings_embed(config)
        view = SettingsView(self.db, config, self)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @license_group.command(
        name=ACTIVE_COMMAND_CONFIG["show"]["name"],
        description=ACTIVE_COMMAND_CONFIG["show"]["description"]
    )
    async def show_license(self, interaction: discord.Interaction):
        """命令：以私密消息的方式显示用户当前的默认协议。"""
        await safe_defer(interaction)
        config = self.db.get_config(interaction.user)
        embeds = build_license_embeds(
            config,
            interaction.user,
            commercial_use_allowed=self.commercial_use_allowed,
            title_override="👀 你的当前默认协议预览",
            footer_override=build_footer_text(SIGNATURE_HELPER)
        )
        await interaction.followup.send(embeds=embeds, ephemeral=True)


async def setup(bot: commands.Bot):
    """标准的Cog加载入口点。"""
    await bot.add_cog(LicenseCog(bot))
