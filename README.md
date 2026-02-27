# 📰 Gmail 뉴스레터 자동 요약 시스템

Gmail 받은편지함에서 뉴스레터를 자동으로 감지하고, Claude AI로 요약한 뒤 Daily Digest 이메일로 발송하는 자동화 시스템입니다.

---

## 주요 기능

| 기능 | 설명 |
|------|------|
| **뉴스레터 자동 감지** | `List-Unsubscribe` 헤더, 발신 도메인, 제목 패턴으로 뉴스레터 분류 |
| **AI 요약** | Claude API(claude-sonnet-4-6)로 각 뉴스레터를 한국어로 3~5줄 요약 |
| **중요도 평가** | 1~5점 중요도 점수 자동 산정 |
| **카테고리 분류** | AI / 개발 / 비즈니스 / 스타트업 / 마케팅 / 기타 자동 분류 |
| **Daily Digest 발송** | HTML 형식의 다이제스트 이메일을 지정 주소로 발송 (원문 링크 포함) |
| **Gmail 자동 정리** | 카테고리별 라벨 적용, 중요도 낮은 메일 자동 아카이브 |
| **중복 처리 방지** | SQLite DB로 이미 처리된 메일 추적 |
| **수동 발신자 추가** | CLI 명령으로 특정 이메일/도메인을 즉시 뉴스레터로 지정 |

---

## 동작 흐름

```
Gmail 수집 → 뉴스레터 필터링 → Claude AI 요약 → Gmail 라벨/아카이브 → DB 저장 → Digest 이메일 발송
```

**중요도 정책**
- **4~5점**: 받은편지함 유지 (직접 확인 필요)
- **1~3점**: 라벨 적용 + 읽음 처리 + 아카이브

---

## 기술 스택

- **Python** 3.12
- **Claude API** (Anthropic) — 뉴스레터 요약 및 중요도 평가
- **Gmail API** (Google OAuth2) — 메일 수집 및 정리
- **SQLAlchemy 2.0** + SQLite — 처리 이력 저장
- **Jinja2** — HTML 다이제스트 템플릿
- **Pydantic-settings** — 환경변수 관리
- **Click** — CLI 인터페이스
- **Docker** — 서버 배포

---

## 사전 준비

### 1. Google Cloud 설정

1. [GCP Console](https://console.cloud.google.com) → **APIs & Services** → **Enable APIs** → `Gmail API` 활성화
2. **Credentials** → **Create Credentials** → **OAuth 2.0 Client ID** (Desktop app) 생성
3. `credentials.json` 다운로드 → `data/credentials.json`에 저장
4. [Google Account](https://myaccount.google.com) → **Security** → **App Passwords** → Gmail 앱 비밀번호 생성

### 2. Anthropic API 키

[Anthropic Console](https://console.anthropic.com) → **API Keys** → 키 생성

---

## 설치 및 실행 (로컬)

```bash
# 1. 저장소 클론
git clone https://github.com/Gwanghun/mailing-summary.git
cd mailing-summary

# 2. 가상환경 생성 및 의존성 설치
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. 환경변수 설정
cp .env.example .env
# .env 파일을 열고 아래 항목 입력
```

`.env` 파일 설정:

```env
GMAIL_USER=your-email@gmail.com
GOOGLE_CREDENTIALS_PATH=data/credentials.json
GOOGLE_TOKEN_PATH=data/token.json
GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx   # Google 앱 비밀번호
DIGEST_RECIPIENT=your-email@gmail.com
ANTHROPIC_API_KEY=sk-ant-api03-...
CLAUDE_MODEL=claude-sonnet-4-6
LOOKBACK_HOURS=24
MAX_EMAILS_PER_RUN=50
MIN_IMPORTANCE_SCORE=2
```

```bash
# 4. Gmail OAuth 최초 인증 (브라우저 열림)
python main.py auth

# 5. 실행
python main.py run-digest
```

---

## CLI 명령어

### 핵심 명령어

```bash
# Gmail OAuth 인증 (최초 1회)
python main.py auth

# 전체 파이프라인 실행
python main.py run-digest

# 발송 없이 결과만 출력 (테스트용)
python main.py run-digest --dry-run

# 수집 시간 범위 변경 (기본: 24시간)
python main.py run-digest --lookback-hours 48
```

### 발신자 관리

```bash
# 특정 이메일을 뉴스레터로 수동 등록
python main.py add-sender --email newsletter@example.com

# 특정 도메인 전체를 뉴스레터로 등록
python main.py add-domain --domain example.co.kr

# 현재 등록된 발신자/도메인 목록 확인
python main.py list-senders
```

### 통계 확인

```bash
# 최근 7일 처리 통계
python main.py status

# 최근 30일 통계
python main.py status --days 30
```

---

## 뉴스레터 감지 방식

아래 세 가지 조건 중 하나라도 충족하면 뉴스레터로 분류합니다.

| 조건 | 예시 |
|------|------|
| `List-Unsubscribe` 헤더 존재 | 대부분의 뉴스레터 플랫폼 |
| 발신 도메인 매칭 | `substack.com`, `stibee.com`, `longblack.co` 등 |
| 제목 패턴 매칭 | `[뉴스레터]`, `모닝 브리핑`, `Weekly Digest` 등 |

지원 도메인 및 패턴은 `config/newsletter_sources.yaml`에서 수정할 수 있습니다.

---

## 서버 배포 (Docker + AWS Lightsail)

```bash
# 1. 서버에서 저장소 클론 및 Docker 설치
git clone https://github.com/Gwanghun/mailing-summary.git
cd mailing-summary

# 2. 민감 파일 복사 (로컬에서 실행)
scp data/credentials.json user@server:~/mailing-summary/data/
scp data/token.json       user@server:~/mailing-summary/data/
scp .env                  user@server:~/mailing-summary/

# 3. 컨테이너 실행
docker compose up -d --build

# 4. 동작 확인
docker compose logs -f
docker compose exec mailing-summary python main.py run-digest --dry-run
```

컨테이너는 매일 **22:00 UTC (07:00 KST)** 자동 실행됩니다.

### 코드 업데이트 시

```bash
# 로컬
git push origin main

# 서버
ssh user@server "cd ~/mailing-summary && git pull && docker compose up -d --build"
```

---

## macOS 로컬 스케줄러 (선택)

```bash
# launchd 등록 (매일 07:00 자동 실행)
cp scheduler/com.mailing-summary.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.mailing-summary.plist

# 즉시 실행
launchctl start com.hooeni.mailing-summary

# 등록 해제
launchctl unload ~/Library/LaunchAgents/com.mailing-summary.plist
```

---

## 프로젝트 구조

```
mailing-summary/
├── main.py                     # CLI 진입점
├── orchestrator.py             # 전체 파이프라인 조율
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
│
├── config/
│   ├── settings.py             # 환경변수 설정 (pydantic-settings)
│   ├── newsletter_sources.yaml # 뉴스레터 도메인 및 제목 패턴
│   └── allow_senders.yaml      # 수동 등록 발신자/도메인
│
├── gmail/
│   ├── auth.py                 # OAuth2 인증
│   ├── client.py               # Gmail API 래퍼
│   └── message_parser.py       # 이메일 파싱 (HTML→텍스트)
│
├── classifier/
│   └── newsletter_filter.py    # 뉴스레터 감지 및 카테고리 분류
│
├── summarizer/
│   ├── claude_client.py        # Claude API 요약 및 배치 처리
│   └── prompt_builder.py       # Claude 프롬프트 생성
│
├── organizer/
│   └── gmail_organizer.py      # Gmail 라벨/아카이브 자동 처리
│
├── digest/
│   ├── digest_builder.py       # 다이제스트 이메일 조립
│   ├── sender.py               # SMTP 이메일 발송
│   └── templates/
│       └── daily_digest.html   # HTML 이메일 템플릿
│
├── storage/
│   ├── models.py               # SQLAlchemy ORM 모델
│   └── database.py             # DB 초기화 및 쿼리
│
├── scheduler/
│   ├── run_digest.sh           # 실행 스크립트
│   └── com.mailing-summary.plist  # macOS launchd 설정
│
├── data/                       # 자격증명 및 DB (Git 제외)
│   ├── credentials.json        # GCP OAuth 자격증명
│   ├── token.json              # OAuth 토큰 (자동 갱신)
│   └── mailing_summary.db      # SQLite DB
│
└── logs/                       # 실행 로그 (Git 제외)
```

---

## 환경변수 전체 목록

| 변수 | 필수 | 기본값 | 설명 |
|------|------|--------|------|
| `GMAIL_USER` | ✅ | — | Gmail 주소 |
| `GOOGLE_CREDENTIALS_PATH` | ✅ | `data/credentials.json` | GCP OAuth 자격증명 경로 |
| `GOOGLE_TOKEN_PATH` | ✅ | `data/token.json` | OAuth 토큰 저장 경로 |
| `GMAIL_APP_PASSWORD` | ✅ | — | Gmail 앱 비밀번호 (SMTP용) |
| `DIGEST_RECIPIENT` | ✅ | — | 다이제스트 수신 이메일 |
| `ANTHROPIC_API_KEY` | ✅ | — | Anthropic API 키 |
| `CLAUDE_MODEL` | — | `claude-sonnet-4-6` | Claude 모델 ID |
| `LOOKBACK_HOURS` | — | `24` | 메일 수집 시간 범위 |
| `MAX_EMAILS_PER_RUN` | — | `50` | 1회 최대 처리 메일 수 |
| `MIN_IMPORTANCE_SCORE` | — | `2` | Digest 포함 최소 중요도 |
| `LOG_LEVEL` | — | `INFO` | 로그 레벨 |

---

## 라이선스

MIT License
