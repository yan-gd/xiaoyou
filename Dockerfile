FROM python:3.10-slim-bullseye

ARG TZ=Asia/Shanghai
ENV BUILD_PREFIX=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

LABEL org.opencontainers.image.title="Xiaoyou" \
      org.opencontainers.image.description="Xiaoyou built from a vendored chatgpt-on-wechat 1.7.3 source snapshot" \
      org.opencontainers.image.source="https://github.com/yan-gd/xiaoyou" \
      org.opencontainers.image.version="chatgpt-on-wechat-1.7.3-xiaoyou" \
      org.opencontainers.image.base.name="python:3.10-slim-bullseye" \
      org.opencontainers.image.revision="22d67b3a596f8c96cc2f8b2e5ed58a47c8bb53bb"

# Build the former zhayujie/chatgpt-on-wechat:1.7.3 base directly from the
# immutable source snapshot committed in this repository.  No zhayujie image
# is pulled during this build.
COPY vendor/chatgpt-on-wechat-1.7.3/ ${BUILD_PREFIX}/

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        espeak \
        ffmpeg \
        libavcodec-extra \
    && cd ${BUILD_PREFIX} \
    && cp config-template.json config.json \
    && python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip install --no-cache-dir -r requirements-optional.txt \
    && python -m pip install --no-cache-dir azure-cognitiveservices-speech \
    && rm -rf /var/lib/apt/lists/* /root/.cache/pip

# Preserve the framework plugin package before ./plugins is mounted over it,
# then apply Xiaoyou's framework-level patches.
COPY patches /tmp/cow_patches
RUN mkdir -p /app/plugins_core/plugins \
    && for f in __init__.py event.py plugin.py plugin_manager.py config.json.template README.md; do if [ -f "/app/plugins/$f" ]; then cp -a "/app/plugins/$f" "/app/plugins_core/plugins/$f"; fi; done \
    && python /tmp/cow_patches/patch_app_imports.py \
    && cp /tmp/cow_patches/chat_gpt_bot.py /app/bot/chatgpt/chat_gpt_bot.py \
    && cp /tmp/cow_patches/chat_channel.py /app/channel/chat_channel.py \
    && python -m py_compile /app/app.py /app/bot/chatgpt/chat_gpt_bot.py /app/channel/chat_channel.py \
    && rm -rf /tmp/cow_patches

COPY vendor/chatgpt-on-wechat-1.7.3/docker/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh \
    && mkdir -p /home/noroot \
    && groupadd -r noroot \
    && useradd -r -g noroot -s /bin/bash -d /home/noroot noroot \
    && chown -R noroot:noroot /home/noroot ${BUILD_PREFIX} /usr/local/lib

WORKDIR ${BUILD_PREFIX}
USER noroot
ENTRYPOINT ["/entrypoint.sh"]
