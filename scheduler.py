# scheduler.py
"""Smart schedule manager: YAML 기반 스케줄 관리, 실행, 패턴 분석."""

from __future__ import annotations

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

# 파일 접근 동기화 — get_file_lock()으로 lazy 초기화
_file_lock: asyncio.Lock | None = None


def get_file_lock() -> asyncio.Lock:
    """Lock을 lazy 초기화하여 반환."""
    global _file_lock
    if _file_lock is None:
        _file_lock = asyncio.Lock()
    return _file_lock

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


def add_schedule_to_data(data: dict, entry: dict) -> dict:
    """스케줄 추가. 같은 id 있으면 덮어쓰기."""
    data["schedules"] = [s for s in data["schedules"] if s.get("id") != entry["id"]]
    entry.setdefault("created_at", datetime.now(KST).strftime("%Y-%m-%d"))
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


DAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def is_schedule_due(schedule: dict, now: datetime) -> bool:
    """현재 시각이 스케줄 실행 시점과 일치하는지 판단."""
    time_str = schedule.get("time", "")
    repeat = schedule.get("repeat", "daily")
    now_hm = now.strftime("%H:%M")

    if repeat == "once":
        return time_str == now.strftime("%Y-%m-%d %H:%M")

    if time_str != now_hm:
        return False

    if repeat == "daily":
        return True

    if repeat == "weekdays":
        return now.weekday() < 5

    # "mon,wed,fri" 등 커스텀 요일
    days = [d.strip().lower() for d in repeat.split(",")]
    return any(DAY_MAP.get(d) == now.weekday() for d in days)


REQUIRED_SCHEDULE_FIELDS = {"id", "type", "time", "repeat", "query"}


def validate_schedule_entry(entry: dict) -> bool:
    """스케줄 항목 필수 필드 검증."""
    return all(entry.get(f) for f in REQUIRED_SCHEDULE_FIELDS)


def parse_schedule_block(response: str) -> tuple:
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


def parse_delete_block(response: str) -> tuple:
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


# 중복 실행 방지: {schedule_id: "YYYY-MM-DD HH:MM"}
_last_executed: dict = {}


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
                        logger.info(f"[스케줄 응답] id={sid}, timed_out={timed_out}, response_len={len(response)}, stderr_len={len(stderr)}")
                        if timed_out:
                            logger.warning(f"[스케줄 타임아웃] id={sid}, partial_len={len(response)}")
                        if stderr:
                            logger.warning(f"[스케줄 stderr] id={sid}: {stderr[:300]}")
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
                        logger.info(f"[스케줄 전송 완료] id={sid}")
                    except Exception as e:
                        logger.error(f"[스케줄 실행 오류] id={sid}: {e}", exc_info=True)

                    if schedule.get("repeat") == "once":
                        executed_once_ids.append(sid)

            # once 스케줄 정리
            if executed_once_ids:
                async with get_file_lock():
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
    async with get_file_lock():
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
                async with get_file_lock():
                    data = load_schedules()
                    for entry in to_add:
                        if validate_schedule_entry(entry):
                            data = add_schedule_to_data(data, entry)
                            logger.info(f"[패턴 분석] 스케줄 추가: {entry.get('id')}")
                    save_schedules(data)
        except Exception as e:
            logger.error(f"[패턴 분석] 응답 파싱 실패: {e}")

    # 30일 이상 된 로그 정리
    async with get_file_lock():
        data = load_schedules()
        data = cleanup_old_logs(data, days=30)
        save_schedules(data)

    logger.info("[패턴 분석] 완료")
