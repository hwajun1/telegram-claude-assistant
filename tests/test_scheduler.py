# tests/test_scheduler.py
import os
import yaml
import pytest

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

def test_add_schedule(tmp_path):
    """스케줄 추가."""
    from scheduler import load_schedules_from_path, add_schedule_to_data
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
    assert data["schedules"][0]["time"] == "10:00"

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

from datetime import datetime

def test_match_daily():
    """daily 스케줄: 시간만 일치하면 매칭."""
    from scheduler import is_schedule_due
    schedule = {"time": "09:00", "repeat": "daily"}
    now = datetime(2026, 3, 20, 9, 0)
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
