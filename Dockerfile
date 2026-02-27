FROM python:3.12-slim

# 시스템 패키지 설치 (lxml 빌드용)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libxml2-dev \
    libxslt-dev \
    cron \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 의존성 먼저 설치 (레이어 캐시 활용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 코드 복사
COPY . .

# 디렉토리 생성
RUN mkdir -p /app/data /app/logs

# cron 스케줄 설정 (매일 07:00 KST = 22:00 UTC 전날)
# KST = UTC+9 이므로 07:00 KST = 22:00 UTC
RUN echo "0 22 * * * root cd /app && python main.py run-digest >> /app/logs/run.log 2>&1" \
    > /etc/cron.d/mailing-summary \
    && chmod 0644 /etc/cron.d/mailing-summary \
    && crontab /etc/cron.d/mailing-summary

# entrypoint 스크립트 복사 및 권한 부여
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
