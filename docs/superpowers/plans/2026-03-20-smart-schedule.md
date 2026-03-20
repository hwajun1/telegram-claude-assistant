# Smart Schedule Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 텔레그램-Claude 봇에 스마트 스케줄링 기능 추가 — 패턴 학습, 자동 알림, 리마인더, 수동 관리

**Architecture:** `scheduler.py` 모듈을 신규 생성하여 YAML 파일 관리, 스케줄 실행, 패턴 분석을 담당. `bot.py`에서 import하여 `asyncio.create_task()`로 백그라운드 실행. Claude 응답에서 `---SCHEDULE---` / `---DELETE_SCHEDULE---` 블록을 파싱하여 스케줄 등록/삭제.

**Tech Stack:** Python 3, PyYAML, python-telegram-bot, asyncio, Claude CLI

**Spec:** `docs/superpowers/specs/2026-03-20-smart-schedule-design.md`

**주의사항 (리뷰 반영):**
- `asyncio.Lock()`은 모듈 레벨이 아닌 `init_scheduler()` 함수에서 이벤트 루프 시작 후 생성
- `DEFAULT_DATA` 반환 시 매번 새 dict 리터럴 생성 (shallow copy 방지)
- `ApplicationBuilder.post_init()`을 사용하여 스케줄러 등록
- 삭제 요청 시 현재 스케줄 목록을 프롬프트에 포함하여 Claude가 정확한 id 참조 가능
- 모든 시간은 `Asia/Seoul` 타임존 사용 (`zoneinfo.ZoneInfo`)
- `[예약 알림]` 접두어는 분할 전송 시에도 첫 청크에 포함

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `scheduler.py` | Create | YAML 로드/저장/백업, 스케줄 매칭/실행, 패턴 로그 기록, 자정 패턴 분석 |
| `bot.py` | Modify | 응답 블록 파싱, pattern_log 기록 호출, /reload 핸들러, 스케줄러 백그라운드 시작 |
| `schedules.yaml` | Create | 스케줄 + 패턴 로그 데이터 파일 |
| `.gitignore` | Create | `schedules.yaml.bak`, `__pycache__/` 등 제외 |
| `tests/__init__.py` | Create | 테스트 패키지 초기화 |
| `tests/test_scheduler.py` | Create | scheduler.py 순수 함수 테스트 |
| `tests/test_parse_blocks.py` | Create | 응답 블록 파싱 테스트 |

---

### Task 1: PyYAML 의존성 확인

**Files:**
- Check: `bot.py` (기존 의존성)

- [ ] **Step 1: PyYAML 설치 확인**

Run: `python3 -c "import yaml; print(yaml.__version__)"`
Expected: 버전 출력. 없으면 `pip3 install pyyaml` 실행.

---

### Task 2: 프로젝트 기반 파일 생성

**Files:**
- Create: `schedules.yaml`
- Create: `.gitignore`
- Create: `tests/__init__.py`

- [ ] **Step 1: 빈 YAML 파일 생성**

```yaml
timezone: "Asia/Seoul"

schedules: []

pattern_log: []
```

- [ ] **Step 2: .gitignore 생성**

```
__pycache__/
*.pyc
schedules.yaml.bak
```

- [ ] **Step 3: tests 디렉토리 + __init__.py 생성**

```bash
mkdir -p tests
touch tests/__init__.py
```

- [ ] **Step 4: 커밋**

```bash
git add schedules.yaml .gitignore tests/__init__.py
git commit -m "chore: add schedules.yaml, .gitignore, tests dir"
```

---

### Task 3: scheduler.py — YAML 로드/저장/백업

`scheduler.py`의 기반 기능: 파일 읽기, 쓰기, 백업, Lock.

**Files:**
- Create: `scheduler.py`
- Create: `tests/test_scheduler.py`

- [ ] **Step 1: 테스트 작성 — load/save/backup**

```python
# tests/test_scheduler.py
import os
import yaml
import pytest
import tempfile

def test_load_schedules_empty_file(tmp_path):
    """빈 YAML 로드 시 기본 구조 반환."""
    from scheduler import load_schedules_from_path
    f = tmp_path / "schedules.yaml"
    f.write_text("timezone: Asia/Seoul\nschedules: []\npattern_log: []\n")
    data = load_schedules_from_path(str(f))
    assert data["schedules"] == []
    assert data["pattern_log"] == []

def test_load_schedules_missing_file(tmp_path):
    """파일 미존재 시 기본 구조 반환."""
    from scheduler import load_schedules_from_path
    f = tmp_path / "nonexistent.yaml"
    data = load_schedules_from_path(str(f))
    assert data["schedules"] == []
    assert data["pattern_log"] == []

def test_save_creates_backup(tmp_path):
    """저장 시 .bak 백업 생성."""
    from scheduler import save_schedules_to_path
    f = tmp_path / "schedules.yaml"
    f.write_text("timezone: Asia/Seoul\nschedules: []\npattern_log: []\n")
    data = {"timezone": "Asia/Seoul", "schedules": [{"id": "test"}], "pattern_log": []}
    save_schedules_to_path(str(f), data)
    assert (tmp_path / "schedules.yaml.bak").exists()
    reloaded = yaml.safe_load(f.read_text())
    assert len(reloaded["schedules"]) == 1

def test_load_corrupt_yaml_uses_backup(tmp_path):
    """YAML 파싱 실패 시 .bak에서 복구."""
    from scheduler import load_schedules_from_path
    f = tmp_path / "schedules.yaml"
    bak = tmp_path / "schedules.yaml.bak"
    bak.write_text("timezone: Asia/Seoul\nschedules:\n  - id: backup_entry\npattern_log: []\n")
    f.write_text("{{invalid yaml content!!")
    data = load_schedules_from_path(str(f))
    assert any(s["id"] == "backup_entry" for s in data["schedules"])
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd /Users/hwajunkoo/work/kakao/telegram-claude-bot && python3 -m pytest tests/test_scheduler.py -v`
Expected: FAIL (scheduler 모듈 없음)

- [ ] **Step 3: 구현 — load/save/backup**

```python
# scheduler.py
"""Smart schedule manager: YAML 기반 스케줄 관리, 실행, 패턴 분석."""

import os
import re
import asyncio
import logging
import shutil
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import yaml

logger = logging.getLogger("claude-bot")

# 프로젝트 루트 기준 YAML 경로
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEDULES_PATH = os.path.join(BASE_DIR, "schedules.yaml")

# 타임존
KST = ZoneInfo("Asia/Seoul")

# 파일 접근 동기화 — 이벤트 루프 시작 후 init_scheduler()에서 생성
_file_lock: asyncio.Lock | None = None

# 메모리 캐시
_cached_data: dict | None = None


def _default_data() -> dict:
    """매번 새 dict 리터럴 반환 (shallow copy 문제 방지)."""
    return {"timezone": "Asia/Seoul", "schedules": [], "pattern_log": []}


def init_scheduler():
    """이벤트 루프 시작 후 호출. asyncio.Lock 생성."""
    global _file_lock
    _file_lock = asyncio.Lock()


def load_schedules_from_path(path: str) -> dict:
    """YAML 파일 로드. 실패 시 .bak 복구 시도. 그래도 실패하면 기본 구조 반환."""
    for try_path in [path, path + ".bak"]:
        if os.path.exists(try_path):
            try:
                with open(try_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if isinstance(data, dict):
                    data.setdefault("timezone", "Asia/Seoul")
                    data.setdefault("schedules", [])
                    data.setdefault("pattern_log", [])
                    return data
            except Exception as e:
                logger.warning(f"YAML 로드 실패 ({try_path}): {e}")
    return _default_data()


def save_schedules_to_path(path: str, data: dict):
    """YAML 파일 저장. 기존 파일은 .bak으로 백업."""
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def load_schedules() -> dict:
    """기본 경로에서 로드."""
    global _cached_data
    _cached_data = load_schedules_from_path(SCHEDULES_PATH)
    return _cached_data


def save_schedules(data: dict):
    """기본 경로에 저장."""
    global _cached_data
    save_schedules_to_path(SCHEDULES_PATH, data)
    _cached_data = data


def get_cached_data() -> dict:
    """메모리 캐시 반환. 없으면 로드."""
    global _cached_data
    if _cached_data is None:
        load_schedules()
    return _cached_data
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

Run: `cd /Users/hwajunkoo/work/kakao/telegram-claude-bot && python3 -m pytest tests/test_scheduler.py -v`
Expected: 4 passed

- [ ] **Step 5: 커밋**

```bash
git add scheduler.py tests/test_scheduler.py
git commit -m "feat: add scheduler.py with YAML load/save/backup"
```

---

### Task 4: scheduler.py — 스케줄 추가/삭제/패턴 로그

**Files:**
- Modify: `scheduler.py`
- Modify: `tests/test_scheduler.py`

- [ ] **Step 1: 테스트 작성 — add/remove/log**

`tests/test_scheduler.py`에 추가:

```python
def test_add_schedule(tmp_path):
    """스케줄 추가."""
    from scheduler import load_schedules_from_path, save_schedules_to_path, add_schedule_to_data
    f = tmp_path / "schedules.yaml"
    f.write_text("timezone: Asia/Seoul\nschedules: []\npattern_log: []\n")
    data = load_schedules_from_path(str(f))
    entry = {"id": "test_1", "type": "reminder", "time": "09:00", "repeat": "daily", "query": "테스트", "chat_id": 123}
    data = add_schedule_to_data(data, entry)
    assert len(data["schedules"]) == 1
    assert data["schedules"][0]["id"] == "test_1"

def test_add_schedule_no_duplicate(tmp_path):
    """같은 id 중복 추가 방지."""
    from scheduler import load_schedules_from_path, add_schedule_to_data
    f = tmp_path / "schedules.yaml"
    f.write_text("timezone: Asia/Seoul\nschedules:\n  - id: dup\n    type: manual\n    time: '09:00'\n    repeat: daily\n    query: test\n    chat_id: 1\npattern_log: []\n")
    data = load_schedules_from_path(str(f))
    entry = {"id": "dup", "type": "manual", "time": "10:00", "repeat": "daily", "query": "new", "chat_id": 1}
    data = add_schedule_to_data(data, entry)
    assert len(data["schedules"]) == 1
    assert data["schedules"][0]["time"] == "10:00"  # 덮어쓰기

def test_remove_schedule():
    """스케줄 삭제."""
    from scheduler import remove_schedule_from_data
    data = {"schedules": [{"id": "a"}, {"id": "b"}], "pattern_log": []}
    data = remove_schedule_from_data(data, "a")
    assert len(data["schedules"]) == 1
    assert data["schedules"][0]["id"] == "b"

def test_log_pattern():
    """패턴 로그 추가."""
    from scheduler import add_pattern_log
    data = {"schedules": [], "pattern_log": []}
    data = add_pattern_log(data, 123, "오늘 날씨 알려줘")
    assert len(data["pattern_log"]) == 1
    assert data["pattern_log"][0]["chat_id"] == 123
    assert "date" in data["pattern_log"][0]
    assert "time" in data["pattern_log"][0]
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `python3 -m pytest tests/test_scheduler.py -v -k "add_schedule or remove_schedule or log_pattern"`
Expected: FAIL

- [ ] **Step 3: 구현**

`scheduler.py`에 추가:

```python
def add_schedule_to_data(data: dict, entry: dict) -> dict:
    """스케줄 추가. 같은 id 있으면 덮어쓰기."""
    data["schedules"] = [s for s in data["schedules"] if s.get("id") != entry["id"]]
    entry.setdefault("created_at", datetime.now().strftime("%Y-%m-%d"))
    data["schedules"].append(entry)
    return data


def remove_schedule_from_data(data: dict, schedule_id: str) -> dict:
    """id로 스케줄 삭제."""
    data["schedules"] = [s for s in data["schedules"] if s.get("id") != schedule_id]
    return data


def add_pattern_log(data: dict, chat_id: int, query: str) -> dict:
    """패턴 로그에 현재 시각으로 기록 추가."""
    now = datetime.now(KST)
    data.setdefault("pattern_log", [])
    data["pattern_log"].append({
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "chat_id": chat_id,
        "query": query,
    })
    return data


def cleanup_old_logs(data: dict, days: int = 30) -> dict:
    """N일 이상 된 pattern_log 항목 제거."""
    cutoff = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")
    data["pattern_log"] = [
        log for log in data.get("pattern_log", [])
        if log.get("date", "") >= cutoff
    ]
    return data
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

Run: `python3 -m pytest tests/test_scheduler.py -v`
Expected: ALL passed

- [ ] **Step 5: 커밋**

```bash
git add scheduler.py tests/test_scheduler.py
git commit -m "feat: add schedule add/remove/pattern-log functions"
```

---

### Task 5: scheduler.py — 스케줄 매칭 로직

**Files:**
- Modify: `scheduler.py`
- Modify: `tests/test_scheduler.py`

- [ ] **Step 1: 테스트 작성 — 매칭 로직**

`tests/test_scheduler.py`에 추가:

```python
from datetime import datetime

def test_match_daily():
    """daily 스케줄: 시간만 일치하면 매칭."""
    from scheduler import is_schedule_due
    schedule = {"time": "09:00", "repeat": "daily"}
    now = datetime(2026, 3, 20, 9, 0)  # 금요일
    assert is_schedule_due(schedule, now) is True
    assert is_schedule_due(schedule, datetime(2026, 3, 20, 9, 1)) is False

def test_match_once():
    """once 스케줄: 날짜+시간 일치해야 매칭."""
    from scheduler import is_schedule_due
    schedule = {"time": "2026-03-21 14:50", "repeat": "once"}
    assert is_schedule_due(schedule, datetime(2026, 3, 21, 14, 50)) is True
    assert is_schedule_due(schedule, datetime(2026, 3, 22, 14, 50)) is False

def test_match_weekdays():
    """weekdays: 월~금 + 시간 일치."""
    from scheduler import is_schedule_due
    schedule = {"time": "09:00", "repeat": "weekdays"}
    monday = datetime(2026, 3, 23, 9, 0)     # 월요일
    saturday = datetime(2026, 3, 21, 9, 0)   # 토요일
    assert is_schedule_due(schedule, monday) is True
    assert is_schedule_due(schedule, saturday) is False

def test_match_specific_days():
    """mon,wed,fri: 해당 요일 + 시간 일치."""
    from scheduler import is_schedule_due
    schedule = {"time": "09:00", "repeat": "mon,wed,fri"}
    monday = datetime(2026, 3, 23, 9, 0)     # 월요일
    tuesday = datetime(2026, 3, 24, 9, 0)    # 화요일
    wednesday = datetime(2026, 3, 25, 9, 0)  # 수요일
    assert is_schedule_due(schedule, monday) is True
    assert is_schedule_due(schedule, tuesday) is False
    assert is_schedule_due(schedule, wednesday) is True
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `python3 -m pytest tests/test_scheduler.py -v -k "match"`
Expected: FAIL

- [ ] **Step 3: 구현**

`scheduler.py`에 추가:

```python
DAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def is_schedule_due(schedule: dict, now: datetime) -> bool:
    """현재 시각이 스케줄 실행 시점과 일치하는지 판단."""
    time_str = schedule.get("time", "")
    repeat = schedule.get("repeat", "daily")
    now_hm = now.strftime("%H:%M")

    if repeat == "once":
        # "YYYY-MM-DD HH:MM" 형식
        return time_str == now.strftime("%Y-%m-%d %H:%M")

    # 시간(HH:MM)만 비교
    if time_str != now_hm:
        return False

    if repeat == "daily":
        return True

    if repeat == "weekdays":
        return now.weekday() < 5  # 월(0)~금(4)

    # "mon,wed,fri" 등 커스텀 요일
    days = [d.strip().lower() for d in repeat.split(",")]
    return any(DAY_MAP.get(d) == now.weekday() for d in days)
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

Run: `python3 -m pytest tests/test_scheduler.py -v`
Expected: ALL passed

- [ ] **Step 5: 커밋**

```bash
git add scheduler.py tests/test_scheduler.py
git commit -m "feat: add schedule matching logic (daily/once/weekdays/custom)"
```

---

### Task 6: 응답 블록 파싱 (SCHEDULE / DELETE_SCHEDULE)

bot.py에서 Claude 응답을 파싱하는 순수 함수. 별도 테스트 파일로 분리.

**Files:**
- Modify: `scheduler.py` (파싱 함수 추가)
- Create: `tests/test_parse_blocks.py`

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_parse_blocks.py

def test_parse_schedule_block():
    """응답에서 SCHEDULE 블록 파싱."""
    from scheduler import parse_schedule_block
    response = """알겠습니다. 내일 3시에 알려드리겠습니다.
---SCHEDULE---
id: meeting_0321
type: reminder
time: "2026-03-21 15:00"
repeat: once
query: "팀 회의 시작"
---END_SCHEDULE---"""
    clean, schedule = parse_schedule_block(response)
    assert "---SCHEDULE---" not in clean
    assert clean.strip() == "알겠습니다. 내일 3시에 알려드리겠습니다."
    assert schedule["id"] == "meeting_0321"
    assert schedule["type"] == "reminder"
    assert schedule["repeat"] == "once"

def test_parse_schedule_block_none():
    """일반 응답에는 블록 없음."""
    from scheduler import parse_schedule_block
    clean, schedule = parse_schedule_block("그냥 일반 대화입니다.")
    assert clean == "그냥 일반 대화입니다."
    assert schedule is None

def test_parse_delete_block():
    """응답에서 DELETE_SCHEDULE 블록 파싱."""
    from scheduler import parse_delete_block
    response = """날씨 알림을 삭제했습니다.
---DELETE_SCHEDULE---
id: weather_morning
---END_DELETE_SCHEDULE---"""
    clean, schedule_id = parse_delete_block(response)
    assert "---DELETE_SCHEDULE---" not in clean
    assert schedule_id == "weather_morning"

def test_parse_delete_block_none():
    """삭제 블록 없는 응답."""
    from scheduler import parse_delete_block
    clean, schedule_id = parse_delete_block("일반 대화")
    assert clean == "일반 대화"
    assert schedule_id is None

def test_parse_schedule_block_missing_fields():
    """필수 필드 누락 시 None 반환."""
    from scheduler import parse_schedule_block
    response = """테스트
---SCHEDULE---
id: incomplete
type: reminder
---END_SCHEDULE---"""
    clean, schedule = parse_schedule_block(response)
    assert schedule is None  # time, repeat, query 누락

def test_validate_schedule_entry():
    """스케줄 항목 검증."""
    from scheduler import validate_schedule_entry
    valid = {"id": "t", "type": "reminder", "time": "09:00", "repeat": "daily", "query": "test"}
    assert validate_schedule_entry(valid) is True
    invalid = {"id": "t", "type": "reminder"}  # 필수 필드 부족
    assert validate_schedule_entry(invalid) is False
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `python3 -m pytest tests/test_parse_blocks.py -v`
Expected: FAIL

- [ ] **Step 3: 구현**

`scheduler.py`에 추가:

```python
import re

REQUIRED_SCHEDULE_FIELDS = {"id", "type", "time", "repeat", "query"}


def validate_schedule_entry(entry: dict) -> bool:
    """스케줄 항목 필수 필드 검증."""
    return all(entry.get(f) for f in REQUIRED_SCHEDULE_FIELDS)


def parse_schedule_block(response: str) -> tuple[str, dict | None]:
    """응답에서 ---SCHEDULE--- 블록을 파싱. (정리된 응답, 스케줄 dict 또는 None) 반환."""
    match = re.search(r"---SCHEDULE---\s*\n(.+?)\n---END_SCHEDULE---", response, re.DOTALL)
    if not match:
        return response, None
    block_text = match.group(1)
    try:
        entry = yaml.safe_load(block_text)
        if not isinstance(entry, dict) or not validate_schedule_entry(entry):
            return response, None
    except yaml.YAMLError:
        return response, None
    clean = response[:match.start()].rstrip() + response[match.end():]
    return clean.strip(), entry


def parse_delete_block(response: str) -> tuple[str, str | None]:
    """응답에서 ---DELETE_SCHEDULE--- 블록을 파싱. (정리된 응답, schedule_id 또는 None) 반환."""
    match = re.search(r"---DELETE_SCHEDULE---\s*\n(.+?)\n---END_DELETE_SCHEDULE---", response, re.DOTALL)
    if not match:
        return response, None
    block_text = match.group(1)
    try:
        data = yaml.safe_load(block_text)
        schedule_id = data.get("id") if isinstance(data, dict) else None
    except yaml.YAMLError:
        return response, None
    if not schedule_id:
        return response, None
    clean = response[:match.start()].rstrip() + response[match.end():]
    return clean.strip(), schedule_id
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

Run: `python3 -m pytest tests/test_parse_blocks.py -v`
Expected: ALL passed

- [ ] **Step 5: 커밋**

```bash
git add scheduler.py tests/test_parse_blocks.py
git commit -m "feat: add SCHEDULE/DELETE_SCHEDULE response block parsers"
```

---

### Task 7: scheduler.py — 스케줄 실행 루프 + 자정 패턴 분석

비동기 루프 함수 구현. 테스트는 순수 함수로 검증 완료된 부분에 의존하므로, 이 태스크는 통합 코드 작성.

**Files:**
- Modify: `scheduler.py`

- [ ] **Step 1: 스케줄 실행 루프 구현**

`scheduler.py`에 추가:

```python
# 중복 실행 방지: {schedule_id: "YYYY-MM-DD HH:MM"}
_last_executed: dict[str, str] = {}


async def run_scheduler(bot, call_claude_fn):
    """매분 스케줄 체크 + 자정 패턴 분석. bot.py에서 asyncio.create_task()로 호출."""
    logger.info("[스케줄러] 시작")
    while True:
        try:
            now = datetime.now(KST)
            now_key = now.strftime("%Y-%m-%d %H:%M")

            # 매분: 스케줄 실행
            data = get_cached_data()
            executed_once_ids = []

            for schedule in data.get("schedules", []):
                sid = schedule.get("id", "")
                if _last_executed.get(sid) == now_key:
                    continue
                if is_schedule_due(schedule, now):
                    _last_executed[sid] = now_key
                    query = schedule.get("query", "")
                    chat_id = schedule.get("chat_id", "")
                    if not query or not chat_id:
                        continue
                    logger.info(f"[스케줄 실행] id={sid}, query={query[:50]}")
                    try:
                        response, stderr, timed_out = await call_claude_fn(query)
                        if not response and stderr:
                            response = f"(스케줄 오류: {stderr[:200]})"
                        elif not response:
                            response = "(응답 없음)"
                        # [예약 알림] 접두어 + 4000자 분할 전송
                        full_response = f"[예약 알림] {response}"
                        if len(full_response) > 4000:
                            for i in range(0, len(full_response), 4000):
                                await bot.send_message(chat_id=chat_id, text=full_response[i:i + 4000])
                        else:
                            await bot.send_message(chat_id=chat_id, text=full_response)
                    except Exception as e:
                        logger.error(f"[스케줄 실행 오류] id={sid}: {e}")

                    if schedule.get("repeat") == "once":
                        executed_once_ids.append(sid)

            # once 스케줄 정리
            if executed_once_ids:
                async with _file_lock:
                    data = load_schedules()
                    for sid in executed_once_ids:
                        data = remove_schedule_from_data(data, sid)
                    save_schedules(data)

            # 자정(00:00): 패턴 분석
            if now.hour == 0 and now.minute == 0:
                await run_pattern_analysis(call_claude_fn)

        except Exception as e:
            logger.error(f"[스케줄러 오류] {e}", exc_info=True)

        # 다음 분 0초까지 대기 (오버슈트 방지)
        now2 = datetime.now(KST)
        sleep_seconds = 60 - now2.second - now2.microsecond / 1_000_000
        if sleep_seconds <= 0:
            sleep_seconds = 60
        await asyncio.sleep(sleep_seconds)
```

- [ ] **Step 2: 자정 패턴 분석 구현**

`scheduler.py`에 추가:

```python
PATTERN_ANALYSIS_PROMPT = """아래는 사용자의 최근 7일간 대화 로그와 현재 등록된 스케줄이다.

[현재 스케줄]
{schedules_yaml}

[대화 로그]
{logs_yaml}

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
    chat_id: 대화id숫자
    learned_from: "패턴 설명"

패턴이 없으면 "none"이라고만 응답해."""


async def run_pattern_analysis(call_claude_fn):
    """패턴 분석: 최근 7일 로그를 Claude에 전달, 결과를 schedules에 반영."""
    logger.info("[패턴 분석] 시작")
    async with _file_lock:
        data = load_schedules()

    # 최근 7일 로그만 필터
    cutoff = (datetime.now(KST) - timedelta(days=7)).strftime("%Y-%m-%d")
    recent_logs = [log for log in data.get("pattern_log", []) if log.get("date", "") >= cutoff]

    if len(recent_logs) < 3:
        logger.info("[패턴 분석] 로그 3개 미만, 스킵")
        return

    prompt = PATTERN_ANALYSIS_PROMPT.format(
        schedules_yaml=yaml.dump(data.get("schedules", []), allow_unicode=True, default_flow_style=False),
        logs_yaml=yaml.dump(recent_logs, allow_unicode=True, default_flow_style=False),
    )

    try:
        response, stderr, timed_out = await call_claude_fn(prompt)
    except Exception as e:
        logger.error(f"[패턴 분석] Claude 호출 실패: {e}")
        return

    if not response or response.strip().lower() == "none":
        logger.info("[패턴 분석] 패턴 없음")
    else:
        try:
            result = yaml.safe_load(response)
            to_add = result.get("schedules_to_add", []) if isinstance(result, dict) else []
            if to_add:
                async with _file_lock:
                    data = load_schedules()
                    for entry in to_add:
                        if validate_schedule_entry(entry):
                            data = add_schedule_to_data(data, entry)
                            logger.info(f"[패턴 분석] 스케줄 추가: {entry.get('id')}")
                    save_schedules(data)
        except Exception as e:
            logger.error(f"[패턴 분석] 응답 파싱 실패: {e}")

    # 30일 이상 된 로그 정리
    async with _file_lock:
        data = load_schedules()
        data = cleanup_old_logs(data, days=30)
        save_schedules(data)

    logger.info("[패턴 분석] 완료")
```

- [ ] **Step 3: 커밋**

```bash
git add scheduler.py
git commit -m "feat: add scheduler run loop and midnight pattern analysis"
```

---

### Task 8: bot.py — ASSISTANT_PROMPT 업데이트 + 응답 파싱 통합

**Files:**
- Modify: `bot.py:110-115` (ASSISTANT_PROMPT)
- Modify: `bot.py:275-289` (send_response)
- Modify: `bot.py:350-397` (handle_message)

- [ ] **Step 1: ASSISTANT_PROMPT에 스케줄 감지 프롬프트 추가**

`bot.py:110-115`의 `ASSISTANT_PROMPT`를 교체:

```python
ASSISTANT_PROMPT = """너는 나의 개인 비서야. 코드 개발보다는 전반적인 업무를 도와줘.
- 일정 관리, 아이디어 정리, 문서 작성, 요약, 번역 등을 도와줘
- 맛집 확인이나 검색을 요청하면 웹 검색을 해서 응답을 요약해서 알려줘
- 질문에 친절하고 간결하게 답변해줘
- 한국어로 대화해줘

[스케줄 기능]
사용자가 알림, 리마인더, 스케줄 등록을 요청하는 경우, 응답 맨 끝에 아래 형식으로 추가해줘:
---SCHEDULE---
id: 고유id (영문_숫자, 내용을 반영)
type: reminder
time: "HH:MM" 또는 "YYYY-MM-DD HH:MM"
repeat: daily|weekdays|once|mon,wed,fri
query: "해당 시간에 실행할 질문"
---END_SCHEDULE---

사용자가 알림 삭제를 요청하면, 응답 맨 끝에 아래 형식으로 추가해줘:
---DELETE_SCHEDULE---
id: 삭제할_스케줄_id
---END_DELETE_SCHEDULE---

현재 등록된 스케줄 목록:
{schedule_list}

일반 대화에는 위 블록을 추가하지 마.
"""
```

**참고:** `{schedule_list}`는 `call_claude` 호출 전에 현재 스케줄 목록으로 치환한다. 이렇게 해야 삭제 요청 시 Claude가 정확한 id를 참조할 수 있다.

- [ ] **Step 2: handle_message에 pattern_log 기록 + 응답 블록 파싱 추가**

`bot.py` 상단 import 추가:

```python
from scheduler import (
    get_cached_data, load_schedules, save_schedules,
    add_pattern_log, add_schedule_to_data, remove_schedule_from_data,
    parse_schedule_block, parse_delete_block, _file_lock,
)
```

`call_claude` 호출 전에 현재 스케줄 목록을 ASSISTANT_PROMPT에 주입하는 헬퍼 추가:

```python
def build_schedule_prompt() -> str:
    """ASSISTANT_PROMPT에 현재 스케줄 목록을 주입."""
    data = get_cached_data()
    schedules = data.get("schedules", [])
    if not schedules:
        schedule_list = "(등록된 스케줄 없음)"
    else:
        lines = []
        for s in schedules:
            lines.append(f"- id: {s.get('id')}, time: {s.get('time')}, repeat: {s.get('repeat')}, query: {s.get('query', '')[:50]}")
        schedule_list = "\n".join(lines)
    return ASSISTANT_PROMPT.replace("{schedule_list}", schedule_list)
```

`handle_message` 함수(bot.py:350)에서 `call_claude` 호출 부분 수정 — `ASSISTANT_PROMPT` 대신 동적 프롬프트 사용. 이후 `send_response` 호출 전에 아래 로직 삽입:

```python
            # pattern_log 기록
            async with _file_lock:
                data = load_schedules()
                data = add_pattern_log(data, chat_id, user_message)
                save_schedules(data)

            # 응답에서 스케줄/삭제 블록 파싱
            response, new_schedule = parse_schedule_block(response)
            if new_schedule:
                new_schedule.setdefault("chat_id", chat_id)
                async with _file_lock:
                    data = load_schedules()
                    data = add_schedule_to_data(data, new_schedule)
                    save_schedules(data)
                logger.info(f"[스케줄 등록] id={new_schedule.get('id')}, time={new_schedule.get('time')}")

            response, delete_id = parse_delete_block(response)
            if delete_id:
                async with _file_lock:
                    data = load_schedules()
                    data = remove_schedule_from_data(data, delete_id)
                    save_schedules(data)
                logger.info(f"[스케줄 삭제] {delete_id}")
```

**참고:** `call_claude` 함수에서 `--append-system-prompt`에 전달하는 프롬프트를 `build_schedule_prompt()`로 교체해야 한다. 이를 위해 `call_claude`에 `system_prompt` 파라미터를 추가하거나, 전역 `ASSISTANT_PROMPT`를 동적으로 구성한다.

- [ ] **Step 3: 커밋**

```bash
git add bot.py
git commit -m "feat: integrate schedule detection and pattern logging into bot.py"
```

---

### Task 9: bot.py — /reload 핸들러 + 스케줄러 백그라운드 시작

**Files:**
- Modify: `bot.py:90-99` (/start 메시지 업데이트)
- Modify: `bot.py:427-439` (main 함수)

- [ ] **Step 1: /reload 핸들러 추가**

`bot.py`에 함수 추가 (new_session 뒤):

```python
async def reload_schedules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/reload 명령 처리: schedules.yaml 재로드."""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        await update.message.reply_text("권한이 없습니다.")
        return
    async with _file_lock:
        data = load_schedules()
    count = len(data.get("schedules", []))
    logger.info(f"[리로드] chat_id: {chat_id}, schedules: {count}개")
    await update.message.reply_text(f"schedules.yaml 리로드 완료. 등록된 스케줄: {count}개")
```

- [ ] **Step 2: /start 메시지에 /reload 추가**

`bot.py:94-99`의 reply_text 수정:

```python
    await update.message.reply_text(
        f"Claude CLI Bot입니다.\n"
        f"이 채팅의 ID: {chat_id}\n"
        f"메시지를 보내면 Claude가 응답합니다.\n"
        f"/new - 새 대화 시작\n"
        f"/reload - 스케줄 파일 리로드"
    )
```

- [ ] **Step 3: main() 수정 — 핸들러 등록 + 스케줄러 백그라운드 시작**

`bot.py`에 import 추가:

```python
from scheduler import run_scheduler, init_scheduler
```

`main()` 함수 수정:

```python
async def post_init(application):
    """폴링 시작 후 스케줄러를 백그라운드 태스크로 실행."""
    init_scheduler()  # asyncio.Lock 등 이벤트 루프 의존 객체 초기화
    application.create_task(run_scheduler(application.bot, call_claude))


def main():
    """봇 초기화 및 실행. CLAUDE.md 생성 → 핸들러 등록 → 스케줄러 시작 → 폴링 시작."""
    ensure_claude_md()
    load_schedules()  # 시작 시 YAML 로드 (동기, Lock 불필요)

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", new_session))
    app.add_handler(CommandHandler("reload", reload_schedules_cmd))
    app.add_handler(CallbackQueryHandler(handle_retry_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    mode = "dev" if DEV_MODE else "default"
    logger.info(f"Bot 시작... (mode: {mode}, cwd: {os.getcwd()})")
    app.run_polling()
```

- [ ] **Step 4: 커밋**

```bash
git add bot.py
git commit -m "feat: add /reload handler and start scheduler as background task"
```

---

### Task 10: 수동 테스트 — dev 모드로 전체 흐름 검증

**Files:**
- 없음 (실행 테스트)

- [ ] **Step 1: 유닛 테스트 전체 실행**

Run: `cd /Users/hwajunkoo/work/kakao/telegram-claude-bot && python3 -m pytest tests/ -v`
Expected: ALL passed

- [ ] **Step 2: dev 모드로 봇 실행**

Run: `python3 bot.py dev`
Expected: `Bot 시작... (mode: dev, ...)` + `[스케줄러] 시작` 로그 출력

- [ ] **Step 3: 텔레그램에서 테스트**

1. 일반 메시지 전송 → 정상 응답 확인 + `schedules.yaml`의 `pattern_log`에 기록 확인
2. "매일 아침 9시에 날씨 알려줘" 전송 → 응답에서 블록 파싱 → `schedules.yaml`에 스케줄 추가 확인
3. `/reload` → "리로드 완료" 응답 확인
4. "9시 날씨 알림 지워줘" → `schedules.yaml`에서 삭제 확인

- [ ] **Step 4: 최종 커밋 (필요한 경우만)**

남은 변경사항이 있으면:

```bash
git add bot.py scheduler.py schedules.yaml tests/
git commit -m "feat: smart schedule bot - pattern learning, auto alerts, reminders"
```
