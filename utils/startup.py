"""Windows 작업 스케줄러(schtasks) 기반 자동 시작 관리 모듈.

관리자 권한 프로그램이 부팅 시 UAC 차단 없이 최고 권한(/rl highest)으로
자동 실행될 수 있도록 schtasks 명령어를 사용하여 작업 스케줄러에 등록/해제합니다.
"""

import os
from pathlib import Path
import subprocess
import sys
from typing import Optional

# 독립 실행 지원을 위한 프로젝트 루트 sys.path 등록 (플랫 및 서브폴더 구조 지원)
_here = Path(__file__).resolve().parent
_PROJECT_ROOT = _here if (_here / "main.py").exists() else _here.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from utils.logger import get_logger
except ImportError:
    from logger import get_logger

_LOGGER = get_logger("StartupManager")

_TASK_NAME: str = "AutoMemoryGuard"


def get_target_command() -> str:
    """부팅 시 작업 스케줄러가 실행할 대상 커맨드라인 문자열을 반환합니다."""
    is_frozen = getattr(sys, "frozen", False)
    if is_frozen:
        # PyInstaller 빌드 단일 바이너리
        return f'"{sys.executable}" --minimized'
    else:
        # 일반 파이썬 실행 환경: 콘솔 없는 pythonw.exe 사용
        main_py = _PROJECT_ROOT / "main.py"
        python_exe = sys.executable

        if python_exe.lower().endswith("python.exe"):
            candidate = python_exe[:-10] + "pythonw.exe"
            if os.path.exists(candidate):
                python_exe = candidate

        return f'"{python_exe}" "{main_py}" --minimized'


def is_startup_enabled() -> bool:
    """AutoMemoryGuard가 Windows 작업 스케줄러에 등록되어 있는지 확인합니다.

    Returns:
        bool: 등록되어 있는 경우 True, 미등록 또는 쿼리 실패 시 False.
    """
    if os.name != "nt":
        return False

    cmd = ["schtasks", "/query", "/tn", _TASK_NAME]

    try:
        # 콘솔 창 없이 실행
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            text=True,
        )
        return result.returncode == 0
    except Exception as exc:
        _LOGGER.debug("작업 스케줄러 쿼리 중 예외: %s", exc)
        return False


def enable_startup(custom_cmd: Optional[str] = None) -> bool:
    """Windows 작업 스케줄러에 최고 권한 로그온 자동 실행 작업을 등록합니다.

    Args:
        custom_cmd: 실행할 커맨드 (생략 시 자동 산출).

    Returns:
        bool: 등록 성공 시 True, 실패 시 False.
    """
    if os.name != "nt":
        return False

    target_cmd = custom_cmd if custom_cmd is not None else get_target_command()

    # schtasks /create /tn "AutoMemoryGuard" /tr ... /sc onlogon /rl highest /f
    cmd = [
        "schtasks",
        "/create",
        "/tn",
        _TASK_NAME,
        "/tr",
        target_cmd,
        "/sc",
        "onlogon",
        "/rl",
        "highest",
        "/f",
    ]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            text=True,
        )

        if result.returncode == 0:
            _LOGGER.info("작업 스케줄러 자동 실행 등록 완료: %s -> %s", _TASK_NAME, target_cmd)
            return True
        else:
            _LOGGER.error("작업 스케줄러 등록 실패 (코드 %d): %s", result.returncode, result.stderr.strip())
            return False

    except Exception as exc:
        _LOGGER.error("작업 스케줄러 등록 실행 중 예외: %s", exc)
        return False


def disable_startup() -> bool:
    """Windows 작업 스케줄러에서 AutoMemoryGuard 작업을 안전하게 삭제합니다.

    Returns:
        bool: 삭제 성공 시 True, 미등록 상태이거나 실패 시 False.
    """
    if os.name != "nt":
        return False

    cmd = ["schtasks", "/delete", "/tn", _TASK_NAME, "/f"]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            text=True,
        )

        if result.returncode == 0:
            _LOGGER.info("작업 스케줄러 자동 실행 작업 삭제 완료: %s", _TASK_NAME)
            return True
        else:
            _LOGGER.debug("작업 스케줄러 삭제 결과 (코드 %d): %s", result.returncode, result.stderr.strip())
            return False

    except Exception as exc:
        _LOGGER.error("작업 스케줄러 삭제 실행 중 예외: %s", exc)
        return False


def toggle_startup() -> bool:
    """자동 실행 상태를 반전(토글)합니다.

    Returns:
        bool: 변경 후 최종 등록 여부.
    """
    if is_startup_enabled():
        disable_startup()
        return False
    else:
        enable_startup()
        return True


if __name__ == "__main__":
    print(f"현재 작업 스케줄러 등록 여부: {is_startup_enabled()}")
    print(f"등록 대상 커맨드: {get_target_command()}")
