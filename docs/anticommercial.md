# 反商业化 `/aff` 模块配置与使用说明

本模块提供跨服务器累计次数的合规渠道 `aff` 阶梯处罚、记录查询、历史补录与撤销功能。所有身份组、档位时长与私信文案均支持在 `config.json` 中配置并热加载生效。

## 1. 功能命令（均归属 `/aff` 命令组）

本服配置的 `anticommercial.role_ids`（反商委员身份组）与本服 `admins` / `senior_admins` / `anticommercial.admin_role_ids`（管理组）均可使用以下全部命令：

- **`/aff 处罚 成员 [原因] [公示图片] [留痕原图]`**
  - 自动统计目标用户在 `shared_guild_ids` 关联服务器合计的未撤销有效次数，匹配 `tiers` 阶梯档位并弹出二次确认面板：
    - 默认第 1 次（有效前科 `0` 次）：**警告 3 天**（不执行禁言）
    - 默认第 2 次及以上（有效前科 `>= 1` 次）：**禁言 3 天 + 警告 30 天**
  - 支持双图分离：若同时上传 `公示图片`（打码）与 `留痕原图`（未打码），公示区发送打码图，留痕频道（`punish_archive_channel_id`）自动发送未打码原图。
- **`/aff 查询 [成员] [用户id]`**
  - 私密展示目标用户在关联服合并的有效累计次数、下次触发档位、有效记录明细及最近已撤销记录。
- **`/aff 补录 [成员] [用户id] [原因]`**
  - 仅在 SQLite 数据库中补记 1 次有效前科（不触发实时禁言、警告身份组或处罚公示），并写入管理日志频道。
- **`/aff 撤销 处罚id [原因]`**
  - 仅限撤销 `/aff` 产生的正式处罚或补录记录（带二次确认）：
    - 撤销**正式处罚**时：自动在原服及同步服解除禁言、移除警告身份组（若无其他未到期警告）、发布撤销公示，并将 SQLite 记录标为 `revoked`（有效次数 `-1`）。
    - 撤销**补录记录**时：直接标为 `revoked`（有效次数 `-1`）并记录管理日志。
  - 若高级管理使用原有的 `/管理 撤销处罚 <处罚ID>`，同样会自动联动将该笔 `aff` 记录标为 `revoked`。

---

## 2. 主副双服配置示例

将以下片段中的示例服务器 ID 与身份组 ID 替换为实际 ID 后合并至 `config.json`（或通过 `/管理机器人 覆盖配置` 应用）：

```json
{
  "cogs": {
    "anticommercial": {
      "enabled": true,
      "description": "反商业化 aff 阶梯处罚与跨服计次模块"
    }
  },
  "anticommercial": {
    "shared_guild_ids": [
      123456789012345678,
      987654321098765432
    ],
    "announce_in_command_channel": true,
    "default_reason": "违规发布带推广/返利性质（aff）的内容",
    "tiers": [
      { "mute": "0d", "warn_days": 3 },
      { "mute": "3d", "warn_days": 30 }
    ],
    "dm_title_warn": "⚠️ 警告处罚（推广/aff 违规）",
    "dm_title_mute": "🔇 禁言与警告处罚（推广/aff 违规）",
    "dm_template": "您在 **{guild_name}** 因 **{reason}** 被处以 **{punish_summary}**（当前累计有效记录：**第 {offense_no} 次**）。\n\n📌 **说明**\n社区允许正常交流合规平台与工具，但严禁附带任何个人邀请码、返利后缀（如 `aff` / `ref` 等）变相获利。后续分享合规渠道时，请去除推广参数、仅发送纯净链接。\n\n⚠️ **累犯提示**\n{next_tier_tip}\n\n请遵守[社区规则]({rules_url})（{appeal_tip} 处罚ID：`{record_id}`）。",
    "next_tier_tip_first": "若后续再次发布带 aff/返利性质的内容，处罚将升级为：**{next_punish_summary}**。",
    "next_tier_tip_repeat": "您已多次触发相关违规记录；若继续违规，管理团队将视情节介入从重处理。",
    "rules_url": "https://discord.com/channels/123456789012345678/234567890123456789/345678901234567890",
    "appeal_tip": "请勿直接回复此私信；如认为存在事实性误判，请阅读规则后前往投诉频道申诉。"
  },
  "server_override": {
    "123456789012345678": {
      "anticommercial": {
        "role_ids": [111111111111111111],
        "admin_role_ids": [222222222222222222]
      }
    },
    "987654321098765432": {
      "anticommercial": {
        "role_ids": [333333333333333333],
        "admin_role_ids": [444444444444444444]
      }
    }
  }
}
```
