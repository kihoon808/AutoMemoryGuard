"""중앙 집중식 로깅 시스템 모듈.

콘솔 출력, 회전 파일 저장(RotatingFileHandler), 메모리 링 버퍼(collections.deque)를
통해 실시간 로깅 및 UI 대시보드 조회를 지원하는 싱글톤 로거를 제공합니다.
"""

from collections import deque
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import threading
from typing import Final, List, Optional

# 로그 버퍼 최대 보관 라인 수
_LOG_BUFFER_MAXLEN: Final[int] = 100
_LOG_BUFFER: deque[str] = deque(maxlen=_LOG_BUFFER_MAXLEN)
_BUFFER_LOCK: threading.Lock = threading.Lock()

_HANDLERS_INITIALIZED: bool = False
_SHARED_HANDLERS: List[logging.Handler] = []
_LOGGER_LOCK: threading.Lock = threading.Lock()


class DequeLogHandler(logging.Handler):
    """최근 로그를 메모리 내 deque 버퍼에 보관하는 핸들러."""

    def __init__(self, buffer: deque[str], lock: threading.Lock) -> None:
        super().__init__()
        self._buffer: deque[str] = buffer
        self._lock: threading.Lock = lock

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            with self._lock:
                self._buffer.append(msg)
        except Exception:
            self.handleError(record)


def _get_project_root() -> Path:
    """PyInstaller 환경, 서브폴더 구조, 단일 플랫 폴더 구조를 모두 지원하는 루트 경로 반환."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    here = Path(__file__).resolve().parent
    if (here / "main.py").exists():
        return here
    return here.parent


def get_logger(name: str = "AutoMemoryGuard", log_level: int = logging.INFO) -> logging.Logger:
    """모듈별 고유 이름을 유지하면서 파일·콘솔·링버퍼 핸들러를 공유하는 스레드 안전 로거를 반환합니다.

    Args:
        name: 로거 식별자 이름 (예: 'Main', 'MemoryTrimmer', 'ConfigManager').
        log_level: 기본 로깅 레벨 (기본값: logging.INFO).

    Returns:
        logging.Logger: 해당 모듈 전용 태그가 붙은 로거 객체.
    """
    global _HANDLERS_INITIALIZED

    with _LOGGER_LOCK:
        if not _HANDLERS_INITIALIZED:
            formatter = logging.Formatter(
                fmt="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )

            # 1. 콘솔 핸들러
            console_handler = logging.StreamHandler()
            console_handler.setLevel(log_level)
            console_handler.setFormatter(formatter)

            # 2. 파일 핸들러 (회전식: 5MB 단위, 최대 3개 백업)
            log_dir = _get_project_root() / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / "memory_guard.log"

            file_handler = RotatingFileHandler(
                filename=str(log_file),
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setLevel(log_level)
            file_handler.setFormatter(formatter)

            # 3. 메모리 링 버퍼 핸들러 (UI 모니터링 연동용)
            buffer_handler = DequeLogHandler(_LOG_BUFFER, _BUFFER_LOCK)
            buffer_handler.setLevel(log_level)
            buffer_handler.setFormatter(formatter)

            _SHARED_HANDLERS.extend([console_handler, file_handler, buffer_handler])
            _HANDLERS_INITIALIZED = True

        logger = logging.getLogger(name)
        logger.setLevel(log_level)
        logger.propagate = False

        if not logger.handlers:
            for h in _SHARED_HANDLERS:
                logger.addHandler(h)

        return logger


def get_recent_logs(count: int = 50) -> List[str]:
    """메모리 링 버퍼에서 최근 기록된 로그 라인을 반환합니다.

    Args:
        count: 가져올 최근 로그 개수 (기본값: 50, 최대: 100).

    Returns:
        List[str]: 최근 로그 문자열 리스트 (오래된 순 -> 최신 순).
    """
    count = max(1, min(count, _LOG_BUFFER_MAXLEN))
    with _BUFFER_LOCK:
        logs_snapshot = list(_LOG_BUFFER)

    return logs_snapshot[-count:]


if __name__ == "__main__":
    test_logger = get_logger("LoggerTest")
    test_logger.info("로거 초기화 테스트 완료.")
    for i in range(10):
        test_logger.debug(f"디버그 메시지 {i}")
        test_logger.info(f"정보 메시지 {i}")

    recent = get_recent_logs(5)
    print(f"--- 최근 로그 {len(recent)}건 확인 ---")
    for line in recent:
        print(line)
