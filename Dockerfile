# 这是基于旧版本 1a43cc6 的 Dockerfile，并手动注入了社区数据修复方案
FROM python:3.10-slim-bookworm AS builder

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libatk1.0-0 libatk-bridge2.0-0 libcups2 libdbus-1-3 libdrm2 libgbm1 libgtk-3-0 libnspr4 libnss3 libx11-6 libx11-xcb1 libxcb1 libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 libxrender1 libxtst6 ca-certificates fonts-liberation libasound2 libpangocairo-1.0-0 libpango-1.0-0 libu2f-udev \
    supervisor curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app_builder
COPY requirements.txt ./

# 保持旧版的安装方式，让它安装最新的 camoufox
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -U "camoufox[geoip]" && \
    pip install --no-cache-dir -r requirements.txt

FROM python:3.10-slim-bookworm
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libatk1.0-0 libatk-bridge2.0-0 libcups2 libdbus-1-3 libdrm2 libgbm1 libgtk-3-0 libnspr4 libnss3 libx11-6 libx11-xcb1 libxcb1 libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 libxrender1 libxtst6 ca-certificates fonts-liberation libasound2 libpangocairo-1.0-0 libpango-1.0-0 libu2f-udev \
    supervisor curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
RUN groupadd -r appgroup && useradd -r -g appgroup -s /bin/bash -d /app appuser
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.10/site-packages/ /usr/local/lib/python3.10/site-packages/
COPY --from=builder /usr/local/bin/ /usr/local/bin/
COPY . .

# --- 社区提供的解决方案 START ---
# 在 fetch 和 install playwright 之前，先修复数据问题
RUN python update_browserforge_data.py
# --- 社区提供的解决方案 END ---

RUN camoufox fetch && \
    python -m playwright install firefox && \
    python -m playwright install-deps firefox
RUN mkdir -p /app/logs && \
    mkdir -p /app/auth_profiles/active && \
    mkdir -p /app/auth_profiles/saved && \
    mkdir -p /app/certs && \
    mkdir -p /home/appuser/.cache/ms-playwright && \
    mkdir -p /home/appuser/.mozilla && \
    chown -R appuser:appgroup /app && \
    chown -R appuser:appgroup /home/appuser
COPY supervisord.conf /etc/supervisor/conf.d/app.conf
RUN mkdir -p /var/cache/camoufox && \
    cp -a /root/.cache/camoufox/* /var/cache/camoufox/ || true && \
    mkdir -p /app/.cache && \
    ln -s /var/cache/camoufox /app/.cache/camoufox

# 我们为PaaS部署新增的修改
COPY entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["entrypoint.sh"]

EXPOSE 2048
EXPOSE 3120
USER appuser
ENV HOME=/app
ENV PLAYWRIGHT_BROWSERS_PATH=/home/appuser/.cache/ms-playwright
ENV PYTHONUNBUFFERED=1
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/app.conf"]