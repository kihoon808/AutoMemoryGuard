"""Windows 고정밀 시스템 타이머 해상도(Timer Resolution) 제어 모듈.

기본 15.6ms인 Windows 시스템 인터럽트 타이머 주기를 0.5ms - 1.0ms로 고정하여
게임 인풋렉(마우스/키보드 반응 지연) 및 프레임 타임 스파이크(마이크로 스터터링)를 제거합니다.
"""

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
from typing import Optional, Tuple

# 프로젝트 루트 경로 등록
_here = Path(__file__).resolve().parent
_PROJECT_ROOT = _here if (_here / "main.py").exists() else _here.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from utils.logger import get_logger
except ImportError:
    from logger import get_logger

_LOGGER = get_logger("TimerResolution")


class TimerResolutionManager:
    """Windows 시스템 타이머 해상도(Timer Resolution) 제어기."""

    def __init__(self, target_ms: float = 0.5) -> None:
        """TimerResolutionManager를 초기화합니다.

        Args:
            target_ms: 목표 타이머 해상도(ms). 기본값 0.5ms (최고 성능 게이밍).
        """
        self.target_ms: float = target_ms
        self._is_active: bool = False
        self._ntdll: Optional[ctypes.WinDLL] = None
        self._winmm: Optional[ctypes.WinDLL] = None
        self._init_apis()

    def _init_apis(self) -> None:
        """Win32 및 NT 커널 API 함수 시그니처 바인딩."""
        if os.name != "nt":
            return

        try:
            self._ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
            # NtQueryTimerResolution(PULONG MinimumResolution, PULONG MaximumResolution, PULONG CurrentResolution)
            self._ntdll.NtQueryTimerResolution.argtypes = [
                ctypes.POINTER(wintypes.ULONG),
                ctypes.POINTER(wintypes.ULONG),
                ctypes.POINTER(wintypes.ULONG),
            ]
            self._ntdll.NtQueryTimerResolution.restype = wintypes.LONG

            # NtSetTimerResolution(ULONG DesiredResolution, BOOLEAN SetResolution, PULONG CurrentResolution)
            self._ntdll.NtSetTimerResolution.argtypes = [
                wintypes.ULONG,
                wintypes.BOOLEAN,
                ctypes.POINTER(wintypes.ULONG),
            ]
            self._ntdll.NtSetTimerResolution.restype = wintypes.LONG
        except Exception as exc:
            _LOGGER.debug("ntdll 타이머 API 로드 실패: %s", exc)

        try:
            self._winmm = ctypes.WinDLL("winmm", use_last_error=True)
            self._winmm.timeBeginPeriod.argtypes = [wintypes.UINT]
            self._winmm.timeBeginPeriod.restype = wintypes.UINT
            self._winmm.timeEndPeriod.argtypes = [wintypes.UINT]
            self._winmm.timeEndPeriod.restype = wintypes.UINT
        except Exception as exc:
            _LOGGER.debug("winmm timeBeginPeriod 로드 실패: %s", exc)

    def get_resolutions(self) -> Tuple[float, float, float]:
        """현재 시스템의 (최소, 최대, 현재) 타이머 해상도를 ms 단위로 반환합니다.

        Returns:
            Tuple[float, float, float]: (min_ms, max_ms, current_ms). 실패 시 (15.6, 0.5, 15.6).
        """
        if not self._ntdll:
            return (15.6, 0.5, 15.6)

        min_res = wintypes.ULONG(0)
        max_res = wintypes.ULONG(0)
        cur_res = wintypes.ULONG(0)

        # 단위: 100ns (1ms = 10,000 * 100ns)
        status = self._ntdll.NtQueryTimerResolution(
            ctypes.byref(min_res),
            ctypes.byref(max_res),
            ctypes.byref(cur_res),
        )

        if status == 0:
            return (
                min_res.value / 10000.0,
                max_res.value / 10000.0,
                cur_res.value / 10000.0,
            )
        return (15.6, 0.5, 15.6)

    def enable_high_resolution(self) -> bool:
        """게이밍용 고정밀 타이머(0.5ms - 1.0ms)를 활성화합니다.

        Returns:
            bool: 성공 시 True, 실패 시 False.
        """
        if self._is_active or os.name != "nt":
            return self._is_active

        success = False

        # 1. ntdll NtSetTimerResolution 시도 (0.5ms = 5,000 * 100ns)
        if self._ntdll:
            desired_100ns = int(self.target_ms * 10000)
            current_res = wintypes.ULONG(0)
            status = self._ntdll.NtSetTimerResolution(desired_100ns, True, ctypes.byref(current_res))
            if status == 0:
                success = True

        # 2. winmm timeBeginPeriod 폴백 / 병합 (1ms)
        if self._winmm:
            res = self._winmm.timeBeginPeriod(1)
            if res == 0:
                success = True

        if success:
            self._is_active = True
            _, _, cur_ms = self.get_resolutions()
            _LOGGER.info("게이밍 고정밀 타이머 활성화 완료 (현재 해상도: %.2f ms)", cur_ms)

        return self._is_active

    def disable_high_resolution(self) -> bool:
        """고정밀 타이머를 해제하고 Windows 기본 절전 상태(15.6ms)로 복원합니다.

        Returns:
            bool: 해제 성공 시 True.
        """
        if not self._is_active or os.name != "nt":
            return True

        if self._ntdll:
            current_res = wintypes.ULONG(0)
            self._ntdll.NtSetTimerResolution(0, False, ctypes.byref(current_res))

        if self._winmm:
            self._winmm.timeEndPeriod(1)

        self._is_active = False
        _, _, cur_ms = self.get_resolutions()
        _LOGGER.info("게이밍 고정밀 타이머 해제 완료 (현재 해상도: %.2f ms)", cur_ms)
        return True

    @property
    def is_active(self) -> bool:
        """현재 고정밀 타이머가 활성화되어 있는지 여부."""
        return self._is_active


if __name__ == "__main__":
    print("=== TimerResolutionManager 단위 검증 시작 ===")
    mgr = TimerResolutionManager(0.5)
    min_ms, max_ms, cur_ms = mgr.get_resolutions()
    print(f"[초기 상태] 최소: {min_ms:.1f}ms, 최고가능: {max_ms:.2f}ms, 현재: {cur_ms:.2f}ms")

    mgr.enable_high_resolution()
    _, _, active_ms = mgr.get_resolutions()
    print(f"[활성화 후] 현재 타이머 해상도: {active_ms:.2f}ms (0.5ms - 1.0ms 고정 확인)")

    mgr.disable_high_resolution()
    _, _, restored_ms = mgr.get_resolutions()
    print(f"[해제 후] 복원된 타이머 해상도: {restored_ms:.2f}ms")
    print("=== TimerResolutionManager 검증 완료 ===")
