# 🐳 Telegram Bot Docker Kurulum Kılavuzu

## 📋 Genel Bakış
Bu kılavuz, `telegram_bot.py` dosyanızı Docker ile çalıştırmanız için gerekli tüm dosyaları içerir.

## 📁 Oluşturulan Dosyalar
- `requirements.txt` - Python bağımlılıkları
- `Dockerfile` - Docker image tanımı
- `docker-compose.yml` - Servis yapılandırması
- `.dockerignore` - Build'den hariç tutulacak dosyalar
- `docker-run.sh` - Linux/Mac script'i
- `docker-run.bat` - Windows script'i

## 🛠️ Gereksinimler
- Docker Desktop yüklü olmalı
- Docker Compose destekli

## 🚀 Hızlı Başlangıç

### Windows'ta:
```batch
# Tüm işlemleri otomatik yap
docker-run.bat full

# Veya adım adım:
docker-run.bat build
docker-run.bat run
```

### Linux/Mac'te:
```bash
# Script'i çalıştırılabilir yap
chmod +x docker-run.sh

# Tüm işlemleri otomatik yap
./docker-run.sh full

# Veya adım adım:
./docker-run.sh build
./docker-run.sh run
```

## 📊 Manuel Komutlar

### 1️⃣ Image Oluşturma
```bash
docker build -t telegram-bot:latest .
```

### 2️⃣ Container Başlatma
```bash
docker-compose up -d
```

### 3️⃣ Logları İzleme
```bash
docker-compose logs -f telegram-bot
```

### 4️⃣ Container Durdurma
```bash
docker-compose down
```

## 📂 Veri Kalıcılığı
Bot verileriniz şu klasörlerde saklanır:
- `./bot_users` - Kullanıcı verileri ve session dosyaları
- `./logs` - Log dosyaları

Bu klasörler host makinenizde kalıcı olarak saklanır.

## ⚙️ Yapılandırma

### 🔧 Environment Variables
`docker-compose.yml` dosyasında:
```yaml
environment:
  - TZ=Europe/Istanbul        # Zaman dilimi
  - PYTHONUNBUFFERED=1       # Buffer'sız output
  - PYTHONIOENCODING=utf-8   # UTF-8 encoding
```

### 💾 Resource Limits
```yaml
deploy:
  resources:
    limits:
      memory: 512M    # Maksimum RAM
      cpus: '0.5'     # Maksimum CPU
```

## 🔧 Bot Token Ayarlama
1. `telegram_bot.py` dosyasını düzenleyin
2. `BOT_TOKEN` değerini kendi token'ınızla değiştirin
3. `ADMIN_ID` değerini kendi Telegram ID'nizle değiştirin

## 📊 İzleme ve Yönetim

### Container Durumu
```bash
docker-compose ps
```

### Resource Kullanımı
```bash
docker stats telegram_message_bot
```

### Container İçine Erişim
```bash
docker-compose exec telegram-bot bash
```

## 🔄 Güncellemeler

### 1️⃣ Kodu Güncelleme
```bash
# Container'ı durdur
docker-compose down

# Image'ı yeniden oluştur
docker build -t telegram-bot:latest .

# Container'ı başlat
docker-compose up -d
```

### 2️⃣ Script ile Güncelleme
```bash
# Windows
docker-run.bat restart

# Linux/Mac
./docker-run.sh restart
```

## 🧹 Temizlik

### Kullanılmayan Image'ları Sil
```bash
docker image prune -f
```

### Tüm Docker Sistemini Temizle
```bash
docker system prune -f
```

### Script ile Temizlik
```bash
# Windows
docker-run.bat cleanup

# Linux/Mac
./docker-run.sh cleanup
```

## 🩺 Sorun Giderme

### 1️⃣ Container Başlamıyor
```bash
# Logları kontrol et
docker-compose logs telegram-bot

# Build hatalarını kontrol et
docker build -t telegram-bot:latest . --no-cache
```

### 2️⃣ Port Çakışması
`docker-compose.yml` dosyasında port numarasını değiştirin:
```yaml
ports:
  - "8081:8080"  # 8080 yerine 8081 kullan
```

### 3️⃣ Permission Hataları
```bash
# Bot_users klasörü izinleri
chmod 755 bot_users/
chown $USER:$USER bot_users/
```

## 🔐 Güvenlik Notları

### 1️⃣ Token Güvenliği
- Bot token'ını asla public repository'de saklamayın
- Environment variable kullanmayı düşünün:
```yaml
environment:
  - BOT_TOKEN=${BOT_TOKEN}
```

### 2️⃣ Network Güvenliği
- Container'ı sadece gerekli portlarla çalıştırın
- Firewall kurallarını kontrol edin

### 3️⃣ Volume Güvenliği
- Bot verilerini düzenli yedekleyin
- Hassas session dosyalarını koruyun

## 📈 Performance Optimizasyonu

### 1️⃣ Memory Optimization
```yaml
deploy:
  resources:
    limits:
      memory: 256M  # RAM'i azalt
```

### 2️⃣ Multi-stage Build (Gelişmiş)
```dockerfile
# Build stage
FROM python:3.11-slim as builder
...

# Runtime stage
FROM python:3.11-slim
COPY --from=builder ...
```

## 🎯 Production Deployment

### 1️⃣ Docker Swarm
```bash
docker stack deploy -c docker-compose.yml telegram-bot
```

### 2️⃣ Kubernetes
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: telegram-bot
spec:
  replicas: 1
  selector:
    matchLabels:
      app: telegram-bot
  template:
    metadata:
      labels:
        app: telegram-bot
    spec:
      containers:
      - name: telegram-bot
        image: telegram-bot:latest
```

---
🎉 **Docker ile bot'unuz artık profesyonel şekilde çalışıyor!** 🐳
