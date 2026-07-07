FROM zhayujie/chatgpt-on-wechat:1.7.3

COPY patches/chat_gpt_bot.py /app/bot/chatgpt/chat_gpt_bot.py
COPY patches/chat_channel.py /app/channel/chat_channel.py
