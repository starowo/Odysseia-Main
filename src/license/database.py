import json
from pathlib import Path

import discord
from discord import Member, User


def get_default_license_details(member: discord.Member) -> dict:
    """
    为新用户或重置用户生成一份默认的授权协议详情。
    Args:
        member: 用户，用于设置默认的署名。
    Returns:
        一个包含默认协议内容的字典。
    """
    if isinstance(member, discord.Member):
        attribution = f"需保留创作者 <@{member.id}> ({member.display_name}) 的署名"
    else:
        attribution = "需保留原作者署名"

    return {
        "type": "custom",  # 默认类型为自定义
        "reproduce": "需联系作者获得授权",
        "derive": "需联系作者获得授权",
        "commercial": "禁止",
        "attribution": attribution,  # 默认署名为@用户
        "notes": "无",  # 附加条款
        "personal_statement": "无"  # 个人宣言字段
    }


# --- 数据模型与存储层 ---

class LicenseConfig:
    """
    数据类，用于封装单个用户的所有授权相关配置。
    它代表了从JSON文件加载或即将存入JSON文件的完整数据结构。
    """

    def __init__(self, member: Member | User, data: dict = None):
        """
        初始化一个用户的配置对象。
        Args:
            member: 用户。
            data: 从JSON文件加载的原始字典数据。如果为None，则使用默认值。
        """
        if data is None:
            data = {}
        self.user_id: int = member.id
        # 用户是否启用本功能。如果禁用，则机器人不会在用户发帖时主动提醒。
        self.bot_enabled: bool = data.get('bot_enabled', True)
        # 是否自动发布协议。如果为True，发帖提醒时将不提供交互按钮，直接发布默认协议。
        # 注意：当前实现中，此选项未被完全利用，而是提供了“发布默认协议”按钮。
        self.auto_post: bool = data.get('auto_post', False)
        # 发布协议前是否需要用户二次确认。
        self.require_confirmation: bool = data.get('require_confirmation', True)
        # 协议的具体内容。
        self.license_details: dict = data.get('license_details', get_default_license_details(member))


class LicenseDB:
    """
    数据访问层，负责处理用户授权配置的读写操作。
    它抽象了对文件系统的直接访问，并实现了一个简单的内存缓存以提高性能。
    """

    def __init__(self):
        self.data_path = Path("data/licenses")
        self.data_path.mkdir(parents=True, exist_ok=True)
        # 缓存: {user_id: LicenseConfig}。避免每次请求都读取文件。
        self._cache: dict[int, LicenseConfig] = {}

    def _get_user_file(self, user_id: int) -> Path:
        """获取指定用户ID对应的JSON文件路径。"""
        return self.data_path / f"{user_id}.json"

    def get_config(self, member: Member | User) -> LicenseConfig:
        """
        获取用户的配置对象。这是获取配置的唯一入口。
        流程:
        1. 检查缓存中是否存在该用户的配置，如果存在则直接返回。
        2. 如果缓存未命中，则尝试从文件加载。
        3. 如果文件不存在或解析失败，则创建一个新的默认配置。
        4. 将加载或创建的配置存入缓存，然后返回。
        """
        # 1. 查缓存
        user_id = member.id
        if user_id in self._cache:
            return self._cache[user_id]

        # 2. 缓存未命中，从文件加载
        user_file = self._get_user_file(user_id)
        if not user_file.exists():
            config = LicenseConfig(member)  # 文件不存在，创建新的默认配置
        else:
            try:
                with user_file.open('r', encoding='utf-8') as f:
                    data = json.load(f)
                config = LicenseConfig(member, data)
            except (json.JSONDecodeError, IOError):
                config = LicenseConfig(member)  # 文件损坏或读取错误，使用默认配置

        # 3. 存入缓存
        self._cache[user_id] = config
        return config

    def save_config(self, config: LicenseConfig):
        """
        将用户的配置对象保存到文件，并同步更新缓存。
        这是保证数据一致性的关键：任何保存操作必须同时影响持久化存储和内存缓存。
        """
        user_file = self._get_user_file(config.user_id)
        data = {
            "bot_enabled": config.bot_enabled,
            "auto_post": config.auto_post,
            "require_confirmation": config.require_confirmation,
            "license_details": config.license_details
        }
        with user_file.open('w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

        # 关键：同时更新缓存
        self._cache[config.user_id] = config

    def delete_config(self, user_id: int):
        """
        删除用户的配置文件，并从缓存中移除。
        同样需要保证文件系统和缓存的一致性。
        """
        # 1. 删除文件
        user_file = self._get_user_file(user_id)
        if user_file.exists():
            try:
                user_file.unlink()
            except OSError as e:
                # 记录错误，但继续尝试清理缓存
                print(f"Error deleting file {user_file}: {e}")

        # 2. 从缓存中移除
        if user_id in self._cache:
            del self._cache[user_id]
