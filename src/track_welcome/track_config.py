import json
with open('config.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
admins = data.get("admins", [])
guild_id = data.get("logging", {}).get("guild_id")

CHANGE_TIME= 60 * 60 #更新的时间
NOTI_TEXT = f"""
格式化后的消息
"""#自定义提醒消息，可用占位符:{{current_members}}服务器当前人数,{{welcome新增人数}},{{实际增长人数}}
MODE=1 #(0 or 1,0为增加TARGET的人数后发送通知，1为人数达到TARGET时发送通知)
TARGET=10000 #int,目标到达的人数
NOTI_CHANNEL=<channel_id> #在什么频道发送通知
NOTI_IDENTITY_GROUP=<group_id> #需要通知的身份组，以逗号分割