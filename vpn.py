"""VPN detection helpers for bot pause-on-VPN behavior.

GlobalProtect 연결 시 macOS 라우팅 테이블의 IPv4 default route가
가상 인터페이스 (utunN)를 통하게 된다. 이 신호를 사용해 VPN 연결
여부를 판단한다.
"""

import logging
import subprocess
import time


def is_vpn_on() -> bool:
    """Return True if GlobalProtect VPN is connected.

    Detects connection by checking whether the IPv4 default route
    flows through a `utunN` interface (the GlobalProtect tunnel).
    Fail-open: any error returns False so the bot keeps running
    when VPN state cannot be determined.
    """
    try:
        result = subprocess.run(
            ["route", "-n", "get", "default"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("interface:"):
            iface = line.split(":", 1)[1].strip()
            return iface.startswith("utun")
    return False


def wait_for_vpn_off(check_interval: int, logger: logging.Logger) -> None:
    """Block until VPN is disconnected.

    If VPN is currently on, log entry once, sleep in
    `check_interval`-second intervals until VPN turns off, then log
    exit once. If VPN is already off, return immediately without
    logging anything.
    """
    if not is_vpn_on():
        return
    logger.info("[VPN] 연결 감지됨. 봇 대기 모드 진입.")
    while is_vpn_on():
        time.sleep(check_interval)
    logger.info("[VPN] 연결 해제됨. 봇 시작.")
