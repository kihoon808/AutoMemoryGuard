"""Windows UAC(사용자 계정 컨트롤) 관리자 권한 확인 및 상승 모듈.

프로세스의 관리자 권한 보유 여부를 판별하고, 필요 시 UAC 프롬프트를 띄워
관리자 권한으로 프로세스를 재실행(Elevation)하는 기능을 제공합니다.
"""

import ctypes
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

_LOGGER = get_logger("AdminPrivilege")


def is_admin() -> bool:
    """현재 프로세스가 Windows 관리자(Administrator) 권한으로 실행 중인지 확인합니다.

    Returns:
        bool: 관리자 권한 보유 시 True, 일반 사용자 권한이거나 비-Windows 환경일 경우 False.
    """
    if os.name != "nt":
        _LOGGER.warning("Windows 운영체제가 아닙니다. (현재 OS: %s)", os.name)
        return False

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError) as exc:
        _LOGGER.error("관리자 권한 확인 API 호출 실패: %s", exc)
        return False


def elevate_privileges(terminate_current: bool = True) -> bool:
    """관리자 권한으로 현재 프로그램을 재실행(UAC 권한 상승)합니다.

    이미 관리자 권한인 경우 즉시 True를 반환합니다.
    권한 상승 프로세스 실행에 성공하면 기본적으로 현재 프로세스를 안전하게 종료합니다.

    Args:
        terminate_current: 권한 상승 프로세스 생성 성공 시 현재 프로세스를 종료할지 여부.

    Returns:
        bool: 권한 상승 프로세스 생성 성공 시 True, UAC 거부 또는 실패 시 False.
    """
    if is_admin():
        _LOGGER.debug("이미 관리자 권한으로 실행 중입니다.")
        return True

    if os.name != "nt":
        _LOGGER.error("Windows 이외의 환경에서는 UAC 권한 상승을 지원하지 않습니다.")
        return False

    # 실행 파일 및 매개변수 구성 (PyInstaller 패키징 환경 지원)
    is_frozen = getattr(sys, "frozen", False)
    if is_frozen:
        executable = sys.executable
        arguments = subprocess.list2cmdline(sys.argv[1:])
    else:
        executable = sys.executable
        # 스크립트 파일 경로 및 전달된 CLI 인자들을 윈도우 커맨드라인 규칙에 맞게 이스케이프
        arguments = subprocess.list2cmdline(sys.argv)

    current_dir: Optional[str] = os.getcwd()

    _LOGGER.info("관리자 권한 상승(UAC)을 요청합니다. 대상 실행 파일: %s", executable)

    try:
        # ShellExecuteW 반환값이 32를 초과하면 성공
        # SW_SHOWNORMAL = 1
        res = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            executable,
            arguments,
            current_dir,
            1,
        )

        if res > 32:
            _LOGGER.info("관리자 권한 상승 프로세스가 성공적으로 생성되었습니다 (Instance handle: %d).", res)
            if terminate_current:
                _LOGGER.info("권한 상승 완료에 따라 현재 비관리자 프로세스를 종료합니다.")
                sys.exit(0)
            return True
        else:
            _LOGGER.warning("사용자가 UAC 승인을 취소했거나 권한 상승에 실패했습니다 (반환 코드: %d).", res)
            return False

    except Exception as exc:
        _LOGGER.error("UAC 권한 상승 중 예외 발생: %s", exc)
        return False


if __name__ == "__main__":
    print(f"현재 관리자 권한 여부: {is_admin()}")
    # 단독 테스트 시에는 실제 권한 상승을 묻지 않고 상태만 확인
