"""
轮换通知数据库管理
负责轮换通知条目的持久化存储和管理
"""

import json
import pathlib
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict


@dataclass
class BannerItem:
    """轮换通知条目"""
    id: str
    title: str
    description: str
    location: str
    cover_image: Optional[str] = None


@dataclass
class BannerConfig:
    """服务器轮换通知配置"""
    guild_id: int
    interval: int = 3600  # 默认1小时（秒）
    current_index: int = 0
    event_id: Optional[int] = None
    items: List[BannerItem] = None

    def __post_init__(self):
        if self.items is None:
            self.items = []


class BannerDatabase:
    """轮换通知数据库管理类"""

    def __init__(self):
        self.data_dir = pathlib.Path("data/banner")
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _get_config_path(self, guild_id: int) -> pathlib.Path:
        """获取服务器配置文件路径"""
        return self.data_dir / f"{guild_id}.json"

    def load_config(self, guild_id: int) -> BannerConfig:
        """加载服务器配置"""
        config_path = self._get_config_path(guild_id)
        
        if not config_path.exists():
            return BannerConfig(guild_id=guild_id)
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            # 转换 items 为 BannerItem 对象
            items = [BannerItem(**item) for item in data.get('items', [])]
            
            return BannerConfig(
                guild_id=data.get('guild_id', guild_id),
                interval=data.get('interval', 3600),
                current_index=data.get('current_index', 0),
                event_id=data.get('event_id'),
                items=items
            )
        except Exception as e:
            print(f"加载配置失败: {e}")
            return BannerConfig(guild_id=guild_id)

    def save_config(self, config: BannerConfig) -> bool:
        """保存服务器配置"""
        config_path = self._get_config_path(config.guild_id)
        
        try:
            data = {
                'guild_id': config.guild_id,
                'interval': config.interval,
                'current_index': config.current_index,
                'event_id': config.event_id,
                'items': [asdict(item) for item in config.items]
            }
            
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            return True
        except Exception as e:
            print(f"保存配置失败: {e}")
            return False

    def add_item(self, guild_id: int, item: BannerItem) -> bool:
        """添加轮换通知条目"""
        config = self.load_config(guild_id)
        
        # 检查ID是否已存在
        if any(i.id == item.id for i in config.items):
            return False
        
        config.items.append(item)
        return self.save_config(config)

    def remove_item(self, guild_id: int, item_id: str) -> bool:
        """删除轮换通知条目"""
        config = self.load_config(guild_id)
        
        original_len = len(config.items)
        config.items = [i for i in config.items if i.id != item_id]
        
        if len(config.items) == original_len:
            return False
        
        # 如果删除的是当前显示的条目，重置索引
        if config.current_index >= len(config.items):
            config.current_index = 0
        
        return self.save_config(config)

    def update_item(self, guild_id: int, item: BannerItem) -> bool:
        """更新轮换通知条目"""
        config = self.load_config(guild_id)
        
        for i, existing_item in enumerate(config.items):
            if existing_item.id == item.id:
                config.items[i] = item
                return self.save_config(config)
        
        return False

    def get_item(self, guild_id: int, item_id: str) -> Optional[BannerItem]:
        """获取指定ID的轮换通知条目"""
        config = self.load_config(guild_id)
        
        for item in config.items:
            if item.id == item_id:
                return item
        
        return None

    def get_all_items(self, guild_id: int) -> List[BannerItem]:
        """获取所有轮换通知条目"""
        config = self.load_config(guild_id)
        return config.items

    def get_next_item(self, guild_id: int) -> Optional[BannerItem]:
        """获取下一个要显示的条目"""
        config = self.load_config(guild_id)
        
        if not config.items:
            return None
        
        item = config.items[config.current_index]
        
        # 更新索引到下一个条目
        config.current_index = (config.current_index + 1) % len(config.items)
        self.save_config(config)
        
        return item

    def set_interval(self, guild_id: int, interval: int) -> bool:
        """设置轮换间隔"""
        config = self.load_config(guild_id)
        config.interval = interval
        return self.save_config(config)

    def set_event_id(self, guild_id: int, event_id: Optional[int]) -> bool:
        """设置当前event ID"""
        config = self.load_config(guild_id)
        config.event_id = event_id
        return self.save_config(config)

