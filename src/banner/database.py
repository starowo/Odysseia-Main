"""
轮换通知数据库管理
负责轮换通知条目的持久化存储和管理
"""

import json
import pathlib
import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from enum import Enum


class ApplicationStatus(Enum):
    """申请状态"""
    PENDING = "pending"        # 待审核
    APPROVED = "approved"      # 已通过
    REJECTED = "rejected"      # 已拒绝
    ACTIVE = "active"          # 活跃中（已通过且在轮换列表中）
    WAITLISTED = "waitlisted"  # 等待列表中
    EXPIRED = "expired"        # 已过期


@dataclass
class BannerApplication:
    """轮换通知申请"""
    id: str
    applicant_id: int
    applicant_name: str
    title: str
    description: str
    location: str
    cover_image: Optional[str] = None
    status: ApplicationStatus = ApplicationStatus.PENDING
    created_at: str = None
    reviewed_at: Optional[str] = None
    reviewer_id: Optional[int] = None
    reviewer_name: Optional[str] = None
    rejection_reason: Optional[str] = None
    expires_at: Optional[str] = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.datetime.utcnow().isoformat()


@dataclass
class BannerItem:
    """轮换通知条目"""
    id: str
    title: str
    description: str
    location: str
    cover_image: Optional[str] = None
    created_at: Optional[str] = None
    expires_at: Optional[str] = None
    created_by: Optional[int] = None  # 创建者ID（用于区分管理员创建还是申请创建）
    application_id: Optional[str] = None  # 关联的申请ID
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.datetime.utcnow().isoformat()


@dataclass
class BannerConfig:
    """服务器轮换通知配置"""
    guild_id: int
    interval: int = 3600  # 默认1小时（秒）
    current_index: int = 0
    event_id: Optional[int] = None
    items: List[BannerItem] = None
    applications: List[BannerApplication] = None
    waitlist: List[BannerApplication] = None

    def __post_init__(self):
        if self.items is None:
            self.items = []
        if self.applications is None:
            self.applications = []
        if self.waitlist is None:
            self.waitlist = []


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
            
            # 转换 applications 为 BannerApplication 对象
            applications_data = data.get('applications', [])
            applications = []
            for app_data in applications_data:
                if isinstance(app_data.get('status'), str):
                    app_data['status'] = ApplicationStatus(app_data['status'])
                applications.append(BannerApplication(**app_data))
            
            # 转换 waitlist 为 BannerApplication 对象
            waitlist_data = data.get('waitlist', [])
            waitlist = []
            for app_data in waitlist_data:
                if isinstance(app_data.get('status'), str):
                    app_data['status'] = ApplicationStatus(app_data['status'])
                waitlist.append(BannerApplication(**app_data))
            
            return BannerConfig(
                guild_id=data.get('guild_id', guild_id),
                interval=data.get('interval', 3600),
                current_index=data.get('current_index', 0),
                event_id=data.get('event_id'),
                items=items,
                applications=applications,
                waitlist=waitlist
            )
        except Exception as e:
            print(f"加载配置失败: {e}")
            return BannerConfig(guild_id=guild_id)

    def save_config(self, config: BannerConfig) -> bool:
        """保存服务器配置"""
        config_path = self._get_config_path(config.guild_id)
        
        try:
            # 转换应用程序状态为字符串
            applications_data = []
            for app in config.applications:
                app_dict = asdict(app)
                app_dict['status'] = app.status.value
                applications_data.append(app_dict)
            
            waitlist_data = []
            for app in config.waitlist:
                app_dict = asdict(app)
                app_dict['status'] = app.status.value
                waitlist_data.append(app_dict)
            
            data = {
                'guild_id': config.guild_id,
                'interval': config.interval,
                'current_index': config.current_index,
                'event_id': config.event_id,
                'items': [asdict(item) for item in config.items],
                'applications': applications_data,
                'waitlist': waitlist_data
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

    # ========== 申请管理方法 ==========
    
    def add_application(self, guild_id: int, application: BannerApplication) -> bool:
        """添加申请"""
        config = self.load_config(guild_id)
        
        # 检查申请ID是否已存在
        if any(app.id == application.id for app in config.applications + config.waitlist):
            return False
        
        config.applications.append(application)
        return self.save_config(config)
    
    def get_application(self, guild_id: int, app_id: str) -> Optional[BannerApplication]:
        """获取指定申请"""
        config = self.load_config(guild_id)
        
        # 在申请列表中搜索
        for app in config.applications:
            if app.id == app_id:
                return app
        
        # 在等待列表中搜索
        for app in config.waitlist:
            if app.id == app_id:
                return app
        
        return None
    
    def update_application_status(self, guild_id: int, app_id: str, status: ApplicationStatus, 
                                  reviewer_id: Optional[int] = None, reviewer_name: Optional[str] = None,
                                  rejection_reason: Optional[str] = None) -> bool:
        """更新申请状态"""
        config = self.load_config(guild_id)
        
        # 在申请列表中搜索并更新
        for app in config.applications:
            if app.id == app_id:
                app.status = status
                app.reviewed_at = datetime.datetime.utcnow().isoformat()
                app.reviewer_id = reviewer_id
                app.reviewer_name = reviewer_name
                app.rejection_reason = rejection_reason
                return self.save_config(config)
        
        # 在等待列表中搜索并更新
        for app in config.waitlist:
            if app.id == app_id:
                app.status = status
                app.reviewed_at = datetime.datetime.utcnow().isoformat()
                app.reviewer_id = reviewer_id
                app.reviewer_name = reviewer_name
                app.rejection_reason = rejection_reason
                return self.save_config(config)
        
        return False
    
    def get_user_application_count(self, guild_id: int, user_id: int) -> int:
        """获取用户当前申请/活跃banner数量"""
        config = self.load_config(guild_id)
        
        count = 0
        
        # 统计申请列表中的待审核申请
        for app in config.applications:
            if app.applicant_id == user_id and app.status == ApplicationStatus.PENDING:
                count += 1
        
        # 统计等待列表中的申请
        for app in config.waitlist:
            if app.applicant_id == user_id:
                count += 1
        
        # 统计活跃的banner（由申请创建的）
        for item in config.items:
            if item.created_by == user_id and item.application_id:
                count += 1
        
        return count
    
    def approve_application(self, guild_id: int, app_id: str, duration_days: int = 7) -> bool:
        """通过申请，将其转换为banner项目"""
        config = self.load_config(guild_id)
        
        # 查找申请
        application = None
        app_index = -1
        
        for i, app in enumerate(config.applications):
            if app.id == app_id:
                application = app
                app_index = i
                break
        
        if not application or application.status != ApplicationStatus.PENDING:
            return False
        
        # 计算到期时间
        expires_at = datetime.datetime.utcnow() + datetime.timedelta(days=duration_days)
        
        # 创建banner项目
        banner_item = BannerItem(
            id=application.id,
            title=application.title,
            description=application.description,
            location=application.location,
            cover_image=application.cover_image,
            created_by=application.applicant_id,
            application_id=application.id,
            expires_at=expires_at.isoformat()
        )
        
        # 添加到banner列表
        config.items.append(banner_item)
        
        # 更新申请状态
        application.status = ApplicationStatus.ACTIVE
        application.expires_at = expires_at.isoformat()
        
        return self.save_config(config)
    
    def move_to_waitlist(self, guild_id: int, app_id: str) -> bool:
        """将申请移至等待列表"""
        config = self.load_config(guild_id)
        
        # 查找并移动申请
        for i, app in enumerate(config.applications):
            if app.id == app_id:
                app.status = ApplicationStatus.WAITLISTED
                config.waitlist.append(app)
                config.applications.pop(i)
                return self.save_config(config)
        
        return False
    
    def promote_from_waitlist(self, guild_id: int, count: int = 1) -> List[BannerApplication]:
        """从等待列表晋升申请到审核列表"""
        config = self.load_config(guild_id)
        
        promoted = []
        
        # 按创建时间排序，优先处理较早的申请
        config.waitlist.sort(key=lambda x: x.created_at)
        
        for _ in range(min(count, len(config.waitlist))):
            if config.waitlist:
                app = config.waitlist.pop(0)
                app.status = ApplicationStatus.PENDING
                config.applications.append(app)
                promoted.append(app)
        
        if promoted:
            self.save_config(config)
        
        return promoted
    
    def cleanup_expired(self, guild_id: int) -> int:
        """清理过期的banner项目并晋升等待列表"""
        expired_items = self.cleanup_expired_with_details(guild_id)
        return len(expired_items)
    
    def cleanup_expired_with_details(self, guild_id: int) -> List[BannerItem]:
        """清理过期的banner项目并返回过期项目详情"""
        config = self.load_config(guild_id)
        
        now = datetime.datetime.utcnow()
        expired_items = []
        
        # 查找过期的项目
        items_to_remove = []
        for item in config.items:
            if item.expires_at:
                try:
                    expires_at = datetime.datetime.fromisoformat(item.expires_at)
                    if now > expires_at:
                        items_to_remove.append(item)
                        expired_items.append(item)
                except:
                    pass  # 忽略无效的日期格式
        
        # 移除过期项目
        for item in items_to_remove:
            config.items.remove(item)
            
            # 更新相关申请状态为已过期
            for app in config.applications:
                if app.id == item.application_id:
                    app.status = ApplicationStatus.EXPIRED
                    break
        
        # 重置当前索引如果超出范围
        if config.current_index >= len(config.items):
            config.current_index = 0
        
        # 从等待列表晋升申请（每移除一个就晋升一个）
        promoted = self.promote_from_waitlist(guild_id, len(expired_items))
        
        if expired_items or promoted:
            self.save_config(config)
        
        return expired_items
    
    def get_pending_applications(self, guild_id: int) -> List[BannerApplication]:
        """获取待审核的申请列表"""
        config = self.load_config(guild_id)
        return [app for app in config.applications if app.status == ApplicationStatus.PENDING]
    
    def get_all_applications(self, guild_id: int) -> List[BannerApplication]:
        """获取所有申请（包括等待列表）"""
        config = self.load_config(guild_id)
        return config.applications + config.waitlist


