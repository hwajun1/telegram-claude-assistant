# Telegram Claude Assistant

Telegram bot powered by Claude CLI with smart scheduling and pattern learning.

## Quick Start

```bash
# 1. 의존성 설치
pip install python-telegram-bot pyyaml

# 2. Claude CLI 설치 (https://docs.anthropic.com/en/docs/claude-code)

# 3. 설정 파일 생성
cp config.yaml.example config.yaml
# config.yaml에 봇 토큰, 채팅 ID, Claude CLI 경로 입력

# 4. 실행
python3 bot.py          # 기본 모드
python3 bot.py dev      # 개발 모드 (별도 토큰/로그, 시스템 프롬프트 미적용)
```

## Commands

| 명령어 | 설명 |
|--------|------|
| `/start` | 봇 소개 |
| `/new` | 대화 초기화 |
| `/reload` | schedules.yaml 수동 리로드 |

---

# Detailed Documentation

## Architecture

```
telegram-claude-assistant/
├── bot.py              # 텔레그램 봇 메인 (메시지 핸들링, Claude CLI 호출)
├── scheduler.py        # 스케줄 관리, 실행, 패턴 분석
├── config.yaml         # 민감 정보 (git 미추적)
├── config.yaml.example # 설정 템플릿
├── schedules.yaml      # 스케줄 + 패턴 로그 (두뇌 파일)
├── CLAUDE.md           # Claude CLI 디렉토리 접근 제한 규칙
├── tests/              # 유닛 테스트 (18개)
├── logs/               # 일자별 로그 (git 미추적)
└── docs/               # 설계 스펙 + 구현 계획
```

## Features

### 1. Claude CLI Bridge
- 텔레그램 메시지를 Claude CLI에 전달하고 응답 반환
- 대화 내역 유지 (최근 100개, 매 요청 시 컨텍스트로 포함)
- 채팅별 Lock으로 동시 요청 방지
- 타임아웃 발생 시 인라인 버튼으로 2배 타임아웃 재시도 제안

### 2. Smart Scheduling
사용자의 대화 패턴을 학습하여 자동으로 알림을 등록합니다.

**자동 패턴 학습:**
- 매 메시지마다 `schedules.yaml`의 `pattern_log`에 기록
- 자정(00:00)에 Claude가 최근 7일 로그를 분석
- 3일 연속 ±30분 이내 유사 질문 → 자동 스케줄 등록
- 기존 스케줄과 중복 체크 후 신규만 추가

**수동 스케줄 등록:**
- "매일 9시에 날씨 알려줘" 같은 자연어 요청
- Claude가 응답에 `---SCHEDULE---` 블록을 포함하여 자동 파싱/등록

**스케줄 삭제:**
- "9시 날씨 알림 지워줘" 같은 자연어 요청
- Claude가 `---DELETE_SCHEDULE---` 블록으로 응답하여 자동 삭제

**스케줄 실행:**
- 매분 `schedules.yaml`을 체크하여 해당 시간 스케줄 실행
- `query`를 Claude에 전달하여 최신 답변 생성 후 텔레그램 전송
- `[예약 알림]` 접두어로 구분

### 3. Image Analysis
- 사진 전송 시 Claude CLI가 이미지를 직접 읽고 분석
- macOS에서는 Vision 프레임워크 OCR을 보너스 컨텍스트로 추가
- 다른 OS에서도 이미지 분석 가능 (OCR만 스킵)

### 4. Security
- `ALLOWED_USER_IDS`로 허용된 사용자만 접근 가능
- 민감 정보(토큰, Chat ID, CLI 경로)는 `config.yaml`에 분리
- `CLAUDE.md`로 Claude CLI의 디렉토리 접근 제한

## Configuration

### config.yaml

```yaml
telegram:
  tokens:
    default: "YOUR_BOT_TOKEN"        # 기본 모드 봇 토큰
    dev: "YOUR_DEV_BOT_TOKEN"        # 개발 모드 봇 토큰
  chat_id: "YOUR_CHAT_ID"           # 응답을 보낼 채팅 ID
  allowed_user_ids:                  # 허용할 사용자 ID 목록
    - 123456789

claude:
  cli_path: "/path/to/claude"       # Claude CLI 바이너리 경로
```

### schedules.yaml

```yaml
timezone: "Asia/Seoul"

schedules:
  - id: "weather_morning"
    type: learned                    # learned | reminder | manual
    time: "09:00"                    # HH:MM (반복) 또는 YYYY-MM-DD HH:MM (1회)
    repeat: daily                    # daily | weekdays | once | mon,wed,fri
    query: "오늘 날씨 알려줘"
    chat_id: 123456789
    learned_from: "3일 연속 09시에 날씨 질문"
    created_at: "2026-03-20"

pattern_log:
  - date: "2026-03-20"
    time: "09:05"
    chat_id: 123456789
    query: "오늘 비 와?"
```

**repeat 옵션:**

| 값 | 설명 | time 형식 |
|----|------|-----------|
| `daily` | 매일 | `HH:MM` |
| `weekdays` | 월~금 | `HH:MM` |
| `once` | 1회 실행 후 삭제 | `YYYY-MM-DD HH:MM` |
| `mon,wed,fri` | 특정 요일 | `HH:MM` |

## Running as a Service (macOS)

`launchd`를 사용하여 자동 시작/재시작 설정:

```xml
<!-- ~/Library/LaunchAgents/com.yourname.telegram-claude-bot.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.yourname.telegram-claude-bot</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/path/to/bot.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/telegram-claude-assistant</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/path/to/logs/launchd-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/path/to/logs/launchd-stderr.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.yourname.telegram-claude-bot.plist    # 시작
launchctl unload ~/Library/LaunchAgents/com.yourname.telegram-claude-bot.plist  # 중지
```

## Tests

```bash
python3 -m pytest tests/ -v
```

18개 테스트: YAML 로드/저장/백업, 스케줄 CRUD, 매칭 로직, 응답 블록 파싱

## Dev vs Default Mode

| | Default | Dev |
|---|---------|-----|
| 토큰 | `tokens.default` | `tokens.dev` |
| 로그 | `bot.log` | `bot-dev.log` |
| 시스템 프롬프트 | 적용 (스케줄 감지 포함) | 미적용 |
| 스케줄러 | 동작 | 동작 |
