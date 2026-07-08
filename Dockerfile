FROM zhayujie/chatgpt-on-wechat:1.7.3

COPY patches /tmp/cow_patches
RUN mkdir -p /app/plugins_core/plugins \
    && for f in __init__.py event.py plugin.py plugin_manager.py config.json.template README.md; do if [ -f "/app/plugins/$f" ]; then cp -a "/app/plugins/$f" "/app/plugins_core/plugins/$f"; fi; done \
    && python /tmp/cow_patches/patch_app_imports.py \
    && cp /tmp/cow_patches/chat_gpt_bot.py /app/bot/chatgpt/chat_gpt_bot.py \
    && cp /tmp/cow_patches/chat_channel.py /app/channel/chat_channel.py \
    && python -m py_compile /app/app.py /app/bot/chatgpt/chat_gpt_bot.py /app/channel/chat_channel.py
