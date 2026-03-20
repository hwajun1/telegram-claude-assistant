# Smart Schedule Bot Design

## Overview

기존 텔레그램-Claude 봇에 **스마트 스케줄링** 기능을 추가한다.
봇이 사용자의 대화 패턴을 학습하고, 학습된 패턴에 따라 자동으로 알림을 보내며, 사용자의 직접 요청도 처리한다.

## 핵심 파일 구조

```
telegram-claude-bot/
├── bot.py              # 기존 봇 (메시지 핸들링 + pattern_log 기록)
├── scheduler.py        # 신규: 스케줄 실행, 리로드, 패턴 분석 트리거
├── schedules.yaml      # 두뇌 파일: 스케줄 + 패턴 로그
└── logs/
```

## schedules.yaml 구조

```yaml
timezone: "Asia/Seoul"  # 모든 시간은 KST 기준

schedules:
  - id: "weather_morning"
    type: learned              # learned | reminder | manual
    time: "09:00"
    repeat: daily              # daily | weekdays | once | mon,wed,fri
    query: "오늘 날씨 알려줘"
    chat_id: 8240727660
    learned_from: "3일 연속 08:50~09:10에 날씨 관련 질문"
    created_at: "2026-03-20"

  - id: "meeting_0321"
    type: reminder
    time: "2026-03-21 14:50"
    repeat: once
    query: "팀 회의 시작 10분 전입니다"
    chat_id: 8240727660
    created_at: "2026-03-20"

pattern_log:
  - date: "2026-03-18"
    time: "09:05"
    chat_id: 8240727660
    query: "오늘 비 와?"
  - date: "2026-03-19"
    time: "08:52"
    chat_id: 8240727660
    query: "날씨 어때?"
  - date: "2026-03-20"
    time: "09:10"
    chat_id: 8240727660
    query: "오늘 날씨 좀 알려줘"
```

**시간 형식 규칙:**
- `repeat: once` → `time`에 전체 날짜시간: `"2026-03-21 14:50"`
- 그 외 (`daily`, `weekdays`, `mon,wed,fri`) → `time`에 시:분만: `"09:00"`
- 모든 시간은 KST (Asia/Seoul) 기준

## 동작 흐름

### 1. 메시지 수신 시

- 기존 `handle_message` 로직 그대로 수행
- 추가: `pattern_log`에 `{date, time, chat_id, query}` 기록
- **스케줄 요청 감지**: Claude 응답에 스케줄 요청 여부 판단을 포함시킴 (아래 프롬프트 참조)
- 스케줄 요청이면 → `schedules`에 즉시 추가 → 즉시 리로드 → 사용자에게 확인 메시지 전송

**스케줄 요청 감지 방식:**

Claude에게 메시지를 전달할 때, 시스템 프롬프트에 아래를 추가:

```
사용자가 알림, 리마인더, 스케줄 등록을 요청하는 경우, 응답 맨 끝에 아래 형식으로 추가해줘:
---SCHEDULE---
id: 고유id
type: reminder
time: "HH:MM" 또는 "YYYY-MM-DD HH:MM"
repeat: daily|weekdays|once|mon,wed,fri
query: "실행할 질문"
---END_SCHEDULE---
일반 대화에는 이 블록을 추가하지 마.
```

봇 코드는 응답에서 `---SCHEDULE---` 블록을 파싱하여 YAML에 추가하고, 해당 블록을 제거한 나머지를 사용자에게 전송한다.

### 2. 패턴 분석 (하루 1회, 자정 00:00)

- 최근 7일간의 `pattern_log`만 Claude에 전달 (비용/성능 최적화)
- 기존 `schedules` 목록도 함께 전달 (중복 방지)

**패턴 분석 프롬프트:**

```
아래는 사용자의 최근 7일간 대화 로그와 현재 등록된 스케줄이다.

[현재 스케줄]
{현재 schedules 목록 YAML}

[대화 로그]
{최근 7일 pattern_log YAML}

규칙:
- 3일 연속 ±30분 이내에 의미적으로 유사한 질문이 있으면 패턴으로 간주
- 이미 등록된 스케줄과 중복되면 추가하지 마
- 패턴을 발견하면 아래 YAML 형식으로만 응답 (설명 없이):

schedules_to_add:
  - id: "고유id"
    type: learned
    time: "HH:MM"
    repeat: daily
    query: "대표 질문"
    learned_from: "패턴 설명"

패턴이 없으면 "none"이라고만 응답해.
```

- Claude 응답이 "none"이면 스킵
- YAML 응답이면 파싱 → 검증 → `schedules`에 추가 → 즉시 리로드
- **검증**: id/type/time/repeat/query 필수 필드 존재 여부, time 형식 체크
- 30일 이상 된 `pattern_log` 항목 정리

### 3. 스케줄 실행

- `scheduler.py`가 매분 현재 시각(HH:MM)과 `schedules`를 비교
- **매칭 로직**: 현재 시각의 HH:MM이 스케줄의 time과 일치하면 실행
  - `repeat: once` → 날짜+시간 모두 일치 시 실행
  - `repeat: daily` → 시간만 일치
  - `repeat: weekdays` → 월~금 + 시간 일치
  - `repeat: mon,wed,fri` 등 → 해당 요일 + 시간 일치
- 매칭된 스케줄의 `query`를 `call_claude()`에 전달, 응답을 해당 `chat_id`로 전송
- `repeat: once` 항목은 실행 후 리스트에서 삭제 (순회 완료 후 일괄 삭제)
- **실행 기록**: 중복 실행 방지를 위해 마지막 실행 시각을 메모리에 보관

### 4. 삭제 요청

- 사용자가 텔레그램에서 "9시 날씨 알림 지워줘" 등 요청
- 스케줄 요청 감지와 동일 방식으로 Claude 응답에 삭제 블록 포함:

```
---DELETE_SCHEDULE---
id: weather_morning
---END_DELETE_SCHEDULE---
```

- 봇 코드가 블록을 파싱 → `schedules.yaml`에서 해당 id 제거 → 리로드

### 5. 수동 리로드

- `/reload` 명령 → `schedules.yaml`을 디스크에서 다시 읽어 메모리 갱신

## 패턴 학습 기준

- **시간 유사도**: ±30분 이내면 같은 시간대로 간주
- **질문 유사도**: Claude가 의미적으로 판단 ("오늘 비 와?" ≈ "날씨 어때?")
- **등록 조건**: 3일 연속 유사 패턴 감지 시 자동 등록
- **알림 없음**: 파일에만 조용히 추가 (사용자가 로컬에서 확인 가능)
- **중복 방지**: 기존 스케줄을 프롬프트에 포함하여 Claude가 중복 체크

## 아키텍처

### bot.py 변경사항

- `handle_message`에 `pattern_log` 기록 로직 추가
- Claude 응답에서 `---SCHEDULE---` / `---DELETE_SCHEDULE---` 블록 파싱
- `/reload` 커맨드 핸들러 추가
- `Application.post_init`에서 스케줄러를 `asyncio.create_task()`로 백그라운드 실행
- `ASSISTANT_PROMPT`에 스케줄 요청/삭제 감지용 프롬프트 추가

### scheduler.py (신규)

- **scheduler.py는 bot.py에서 import하는 모듈** (별도 프로세스 아님)
- `call_claude` 함수를 bot.py에서 import하여 사용

```python
# 주요 함수
load_schedules() -> dict          # YAML 파일 로드, 파싱 실패 시 기존 데이터 유지
reload_schedules()                # 디스크에서 다시 로드
run_scheduler(bot)                # 매분 실행 루프 (asyncio)
run_pattern_analysis()            # 자정 패턴 분석 트리거
add_schedule(entry: dict)         # 스케줄 추가 → 파일 저장 → 리로드
remove_schedule(schedule_id: str) # 스케줄 삭제 → 파일 저장 → 리로드
log_pattern(chat_id, query)       # pattern_log에 기록
```

### 파일 접근 동기화

- `schedules.yaml`에 대한 모든 읽기/쓰기는 `asyncio.Lock`으로 보호
- 쓰기 전 백업: `schedules.yaml.bak` 생성 후 쓰기
- YAML 파싱 실패 시 백업에서 복구

### 패턴 분석 방식 (Claude 프롬프트 위임)

패턴 분석을 별도 코드로 구현하지 않고, Claude CLI에 프롬프트로 위임한다.
자정에 최근 7일간의 `pattern_log`와 현재 `schedules`를 포함한 프롬프트를 Claude에 전달하고,
Claude가 YAML 형식으로 추가할 스케줄을 응답하면 검증 후 파일에 반영한다.

## 에러 처리

- **YAML 파싱 실패**: 기존 스케줄 유지 + `.bak`에서 복구 시도 + 로그 기록
- **Claude 호출 실패**: 해당 스케줄 스킵, 로그 기록
- **Claude 응답 파싱 실패** (패턴 분석/스케줄 블록): 무시하고 로그 기록
- **봇 재시작**: 시작 시 YAML 로드하여 정상 동작
- **schedules.yaml 미존재**: 빈 구조로 자동 생성

## 봇 명령어 (최종)

| 명령어 | 설명 |
|--------|------|
| `/start` | 봇 소개 (기존) |
| `/new` | 대화 초기화 (기존) |
| `/reload` | schedules.yaml 수동 리로드 (신규) |
