"""Windows Win32 저수준 API 바인딩 모듈.

Working Set 비우기(EmptyWorkingSet), 가시 윈도우(GUI Window) 소유 여부 판별,
프로세스 핸들 쿼리 등 OS 레벨의 시스템 호출을 래핑하여 제공합니다.
"""

import ctypes
from ctypes import wintypes
import os
from typing import Set

# Windows 프로세스 접근 권한 마스크 상수
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_SET_QUOTA = 0x0100

# 프로세스 열기 권한 조합: 메모리 Working Set 비우기용
PROCESS_ACCESS_TRIM = PROCESS_QUERY_INFORMATION | PROCESS_SET_QUOTA
PROCESS_ACCESS_TRIM_FALLBACK = PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SET_QUOTA

# Win32 C types 함수 시그니처 정의
_kernel32 = ctypes.windll.kernel32
_user32 = ctypes.windll.user32
_psapi = ctypes.windll.psapi

# OpenProcess
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE

# CloseHandle
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL

# EmptyWorkingSet (K32EmptyWorkingSet 우선, 미지원 시 psapi)
try:
    _empty_working_set_func = _kernel32.K32EmptyWorkingSet
    _empty_working_set_func.argtypes = [wintypes.HANDLE]
    _empty_working_set_func.restype = wintypes.BOOL
except AttributeError:
    _empty_working_set_func = _psapi.EmptyWorkingSet
    _empty_working_set_func.argtypes = [wintypes.HANDLE]
    _empty_working_set_func.restype = wintypes.BOOL

# EnumWindows 및 콜백
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
_user32.EnumWindows.restype = wintypes.BOOL

_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL

_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextLengthW.restype = ctypes.c_int

_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD


def trim_process_working_set(pid: int) -> bool:
    """지정된 PID 프로세스의 Working Set(물리 RAM 점유 페이징 영역)을 비웁니다.

    Windows 커널에 해당 프로세스의 미사용 메모리 페이지를 스왑/스탠바이 리스트로
    즉각 플러시하도록 명령합니다.

    Args:
        pid: 대상 프로세스 ID.

    Returns:
        bool: 플러시 성공 시 True, 권한 부족 또는 핸들 획득 실패 시 False.
    """
    if os.name != "nt" or pid <= 4:
        # PID 0(Idle), PID 4(System) 등 커널 프로세스는 제외
        return False

    handle = _kernel32.OpenProcess(PROCESS_ACCESS_TRIM, False, pid)
    if not handle:
        handle = _kernel32.OpenProcess(PROCESS_ACCESS_TRIM_FALLBACK, False, pid)

    if not handle:
        return False

    try:
        success = bool(_empty_working_set_func(handle))
        return success
    finally:
        _kernel32.CloseHandle(handle)


def get_pids_with_visible_windows() -> Set[int]:
    """현재 화면에 표시되는 가시적(Visible) 윈도우를 소유한 프로세스 PID 목록을 반환합니다.

    사용자가 직접 상호작용 중인 브라우저 창, 편집기 창 등을 판별하여
    백그라운드 유휴 프로세스 종료 대상에서 안전하게 제외하기 위해 사용합니다.

    Returns:
        Set[int]: 가시 윈도우를 보유한 고유 PID 집합.
    """
    if os.name != "nt":
        return set()

    visible_pids: Set[int] = set()

    def enum_windows_callback(hwnd: wintypes.HWND, lparam: wintypes.LPARAM) -> bool:
        if _user32.IsWindowVisible(hwnd):
            text_len = _user32.GetWindowTextLengthW(hwnd)
            # 윈도우 타이틀이 존재하는 실제 창을 가진 경우만 필터링
            if text_len > 0:
                pid = wintypes.DWORD()
                _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value > 0:
                    visible_pids.add(pid.value)
        return True

    callback = WNDENUMPROC(enum_windows_callback)
    _user32.EnumWindows(callback, 0)
    return visible_pids


if __name__ == "__main__":
    import os

    current_pid = os.getpid()
    trim_success = trim_process_working_set(current_pid)
    print(f"현재 프로세스(PID: {current_pid}) Working Set 정리 결과: {trim_success}")

    gui_pids = get_pids_with_visible_windows()
    print(f"가시 윈도우 보유 PID 개수: {len(gui_pids)}개")
    print(f"샘플 가시 PID 5개: {list(gui_pids)[:5]}")
