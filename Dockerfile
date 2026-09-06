# 🐳 Telegram Bot Dockerfile
# Python 3.11 tabanlı hafif imaj
FROM python:3.11-slim

LABEL maintainer="Telegram Bot Developer"
LABEL version="1.0.0"
LABEL description="Telegram Message Sender Bot with Telethon"

# 🔧 Gerekli sistem paketlerini yükle
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# 👤 Bot için non-root kullanıcı oluştur
RUN useradd --create-home --shell /bin/bash telegrambot

# 📁 Çalışma dizini
WORKDIR /app

# 📦 Sanal ortam oluştur ve aktif et
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 📦 Requirements dosyasını kopyala ve bağımlılıkları yükle
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --use-pep517 -r requirements.txt

# 📂 Bot dosyalarını kopyala
COPY telegram_bot.py .

# 📁 Bot veri klasörleri oluştur
RUN mkdir -p bot_users && \
    chown -R telegrambot:telegrambot /app

# 👤 Non-root kullanıcıya geç
USER telegrambot

# 🌐 Port (isteğe bağlı)
EXPOSE 8080

# 🔥 Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# 🚀 Bot'u başlat
CMD ["python", "telegram_bot.py"]
