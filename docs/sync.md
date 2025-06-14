# 服务器同步模块

## 功能概述

服务器同步模块允许机器人在多个Discord服务器之间同步身份组操作和处罚操作，为管理多个相关服务器提供了便利。

## 主要功能

### 1. 身份组同步
- 自动同步身份组的添加和移除操作
- 支持在多个服务器间建立身份组映射关系
- 用户可以一键同步自己的身份组到其他配置的服务器

### 2. 处罚同步
- 同步禁言、警告、封禁等处罚操作
- 提供确认机制，管理员需要手动确认后才会执行处罚
- 撤销处罚时自动同步到所有参与的服务器

## 配置说明

### 启用同步模块

在主配置文件 `config.json` 中添加：
```json
{
  "cogs": {
    "sync": {
      "enabled": true,
      "description": "服务器同步功能"
    }
  }
}
```

### 同步配置文件

同步配置保存在 `config/server_sync/config.json` 中：

```json
{
  "enabled": true,
  "servers": {
    "服务器ID1": {
      "name": "服务器名称",
      "roles": {
        "身份组名称": 身份组ID
      },
      "punishment_sync": true,
      "punishment_announce_channel": 频道ID,
      "punishment_confirm_channel": 频道ID
    }
  },
  "role_mapping": {
    "身份组名称": {
      "服务器ID1": 身份组ID,
      "服务器ID2": 身份组ID
    }
  },
  "punishment_sync": {
    "enabled": true,
    "servers": {
      "服务器ID1": true,
      "服务器ID2": true
    }
  }
}
```

## 使用指南

### 基本设置

1. **添加服务器到同步列表**
   ```
   /同步管理 添加服务器
   ```

2. **添加身份组映射**
   ```
   /同步管理 身份组 <名字> <@身份组>
   ```

3. **启用处罚同步**
   ```
   /同步管理 处罚同步 开
   ```

4. **设置处罚频道**
   ```
   /同步管理 处罚公示频道 <#频道>
   /同步管理 处罚确认频道 <#频道>
   ```

### 身份组同步

**手动同步身份组：**
```
/同步 身份组同步
```
这会将您在当前服务器拥有的可同步身份组添加到其他配置的服务器中。

**自动同步：**
当管理员使用以下命令操作身份组时，会自动同步到其他服务器：
- `/管理 身份组 添加/移除`
- `/管理 批量转移身份组`
- `/答题处罚`
- 答题验证通过

### 处罚同步

**支持的处罚类型：**
- 禁言（包括警告）
- 永久封禁

**同步流程：**
1. 管理员在源服务器执行处罚
2. 系统自动向其他服务器的确认频道发送确认消息
3. 其他服务器的管理员点击"确认执行"或"拒绝执行"
4. 确认后自动执行相同的处罚

**撤销处罚：**
撤销处罚会直接同步到所有服务器，无需确认。

## 命令列表

### 同步指令
- `/同步 身份组同步` - 同步可同步的身份组到配置中的全部子服务器

### 同步管理指令
- `/同步管理 添加服务器` - 将当前服务器添加到同步列表
- `/同步管理 删除服务器` - 从同步列表中删除当前服务器
- `/同步管理 身份组 <名字> <@身份组>` - 将身份组添加到同步列表
- `/同步管理 处罚同步 开/关` - 此服务器是否参与处罚同步
- `/同步管理 处罚公示频道 <选择频道>` - 设置此服务器的处罚公示频道
- `/同步管理 处罚确认频道 <选择频道>` - 设置此服务器的处罚同步确认频道

## 权限要求

- 只有管理员身份组的用户可以使用同步管理命令
- 机器人需要在所有参与同步的服务器中拥有相应权限：
  - 管理身份组（Manage Roles）
  - 禁言成员（Timeout Members）
  - 封禁成员（Ban Members）
  - 发送消息（Send Messages）

## 注意事项

1. **身份组权限层级**：机器人只能操作比自己权限低的身份组
2. **跨服务器同步**：确保所有服务器都正确配置了身份组映射
3. **处罚确认**：处罚同步需要目标服务器管理员手动确认，确保不会误执行
4. **配置备份**：建议定期备份同步配置文件
5. **网络延迟**：跨服务器操作可能存在延迟，请耐心等待

## 故障排除

### 常见问题

**Q: 身份组同步失败**
A: 检查以下项目：
- 身份组映射是否正确配置
- 机器人在目标服务器是否有足够权限
- 目标服务器中是否存在对应的身份组

**Q: 处罚同步没有发送确认消息**
A: 检查：
- 目标服务器是否启用了处罚同步
- 处罚确认频道是否正确配置
- 机器人是否有在确认频道发送消息的权限

**Q: 同步操作超时**
A: 这通常是由于网络延迟或服务器响应慢导致的，可以稍后重试。

## 安全建议

1. 定期审查同步配置，确保只有可信的服务器参与同步
2. 谨慎设置身份组映射，避免权限提升漏洞
3. 监控处罚同步的使用情况，防止滥用
4. 为确认频道设置适当的权限，只允许管理员查看和操作 