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
    assert schedule is None

def test_validate_schedule_entry():
    """스케줄 항목 검증."""
    from scheduler import validate_schedule_entry
    valid = {"id": "t", "type": "reminder", "time": "09:00", "repeat": "daily", "query": "test"}
    assert validate_schedule_entry(valid) is True
    invalid = {"id": "t", "type": "reminder"}
    assert validate_schedule_entry(invalid) is False
