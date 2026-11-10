"""AutoMemoryGuard v3 게이밍 특화 인풋렉 방패 & 백그라운드 자원 격리 엔진 (core/gaming_booster.py).

- 0.5ms 동적 고정밀 시스템 타이머 잠금/복원 (NtSetTimerResolution + timeBeginPeriod)
- Windows 11 자체 타이머 스로틀링 해제 및 백그라운드 프로세스 EcoQoS(E-코어 효율 모드) 격리
- Windows 전원 관리 옵션(고성능/최고성능) 자동 승격 및 원상복구
- Alt-Tab 히스테리시스 방어 및 안티치트(Vanguard/EAC/BattlEye) 무충돌 역발상 격리 아키텍처
"""

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Set
import uuid

import psutil

# 독립 실행 지원을 위한 프로젝트 루트 sys.path 등록
_here = Path(__file__).resolve().parent
_PROJECT_ROOT = _here if (_here / "main.py").exists() else _here.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    import win32gui
    import win32process
    _PYWIN32_AVAILABLE = True
except ImportError:
    _PYWIN32_AVAILABLE = False

try:
    from utils.logger import get_logger
except ImportError:
    from logger import get_logger

_LOGGER = get_logger("GamingBooster")

# Win32 프로세스 우선순위 및 정보 제어 상수
PROCESS_SET_INFORMATION: int = 0x0200
PROCESS_QUERY_LIMITED_INFORMATION: int = 0x1000

NORMAL_PRIORITY_CLASS: int = 0x00000020
BELOW_NORMAL_PRIORITY_CLASS: int = 0x00004000
IDLE_PRIORITY_CLASS: int = 0x00000040

# SetProcessInformation (ProcessPowerThrottling = 4)
ProcessPowerThrottling: int = 4
PROCESS_POWER_THROTTLING_CURRENT_VERSION: int = 1
PROCESS_POWER_THROTTLING_EXECUTION_SPEED: int = 0x1          # EcoQoS (E-코어 및 효율 클럭 격리)
PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION: int = 0x4  # 백그라운드 타이머 해상도 무시 정책

# SHQueryUserNotificationState 상태값
QUNS_NOT_PRESENT: int = 1
QUNS_BUSY: int = 2
QUNS_RUNNING_D3D_FULL_SCREEN: int = 3
QUNS_PRESENTATION_MODE: int = 4
QUNS_ACCEPTS_NOTIFICATIONS: int = 5
QUNS_QUIET_TIME: int = 6

# Windows 표준 전원 플랜 GUID 정의
GUID_BALANCED = uuid.UUID("381b4222-f694-41f0-9685-ff5bb260df2e")
GUID_HIGH_PERFORMANCE = uuid.UUID("8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c")
GUID_ULTIMATE_PERFORMANCE = uuid.UUID("e9a42b02-d5df-448d-aa00-03f14749eb61")

# 기본 등록 게임 프로세스 목록 (소문자)
DEFAULT_KNOWN_GAMES: Set[str] = {
    "league of legends.exe",
    "valorant-win64-shipping.exe",
    "cs2.exe",
    "overwatch.exe",
    "tslgame.exe",
    "eldenring.exe",
    "r5apex.exe",
    "battlegrounds.exe",
    "lostark.exe",
    "fc25.exe",
    "genshinimpact.exe",
    "starrail.exe",
    "pubg.exe",
    "dota2.exe",
    "rainbowsix.exe",
    "cyberpunk2077.exe",
    "blackmythwukong.exe",
    "destiny2.exe",
    "warframe.x64.exe",
    "helldivers2.exe",
}

# 게임 중 EcoQoS 및 우선순위 강등을 적용할 대표 백그라운드 멀티프로세스 타깃
BACKGROUND_TAME_TARGETS: Set[str] = {
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "whale.exe",
    "msedgewebview2.exe",
    "steamwebhelper.exe",
    "epicwebhelper.exe",
    "riotclientuxrender.exe",
    "onedrive.exe",
    "searchindexer.exe",
    "widgets.exe",
    "phoneexperiencehost.exe",
}

# 전체화면 동영상 시청 시 게임으로 오판하지 않기 위한 제외 목록
NON_GAME_FULLSCREEN_APPS: Set[str] = {
    "explorer.exe",
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "whale.exe",
    "potplayer64.exe",
    "potplayermini64.exe",
    "vlc.exe",
    "mpv.exe",
    "netflix.exe",
}


class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
    _fields_ = [
        ("Version", wintypes.ULONG),
        ("ControlMask", wintypes.ULONG),
        ("StateMask", wintypes.ULONG),
    ]


class WinGUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    ]

    @classmethod
    def from_uuid(cls, u: uuid.UUID) -> "WinGUID":
        data = u.bytes_le
        return cls.from_buffer_copy(data)

    def to_uuid(self) -> uuid.UUID:
        return uuid.UUID(bytes_le=bytes(self))


# 64비트 Windows Win32 API 함수 바인딩
if os.name == "nt":
    _ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _winmm = ctypes.WinDLL("winmm", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    _powrprof = ctypes.WinDLL("powrprof", use_last_error=True)

    _ntdll.NtQueryTimerResolution.argtypes = [
        ctypes.POINTER(wintypes.ULONG),
        ctypes.POINTER(wintypes.ULONG),
        ctypes.POINTER(wintypes.ULONG),
    ]
    _ntdll.NtQueryTimerResolution.restype = wintypes.LONG

    _ntdll.NtSetTimerResolution.argtypes = [
        wintypes.ULONG,
        wintypes.BOOLEAN,
        ctypes.POINTER(wintypes.ULONG),
    ]
    _ntdll.NtSetTimerResolution.restype = wintypes.LONG

    _kernel32.SetProcessInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _kernel32.SetProcessInformation.restype = wintypes.BOOL

    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL

    _powrprof.PowerGetActiveScheme.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(WinGUID))]
    _powrprof.PowerGetActiveScheme.restype = wintypes.DWORD

    _powrprof.PowerSetActiveScheme.argtypes = [ctypes.c_void_p, ctypes.POINTER(WinGUID)]
    _powrprof.PowerSetActiveScheme.restype = wintypes.DWORD

    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p


class GamingBoostEngine:
    """게이밍 프로세스 감지, 0.5ms 동적 타이머, 전원 플랜 승격, EcoQoS 백그라운드 격리를 총괄하는 엔진."""

    def __init__(self, custom_games: Optional[Set[str]] = None) -> None:
        """GamingBoostEngine을 초기화합니다."""
        self.known_games: Set[str] = set(DEFAULT_KNOWN_GAMES)
        if custom_games:
            self.known_games.update({name.strip().lower() for name in custom_games if name.strip()})

        self.is_boost_active: bool = False
        self.active_game_pid: Optional[int] = None
        self.active_game_name: str = ""
        self.current_timer_ms: float = 15.6

        self._saved_power_guid: Optional[uuid.UUID] = None
        self._tamed_pids: Dict[int, int] = {}  # {pid: original_priority}

        # 자체 프로세스가 Windows 11 백그라운드 타이머 스로틀링에 걸리지 않도록 초기화 시 즉시 해제
        self._disable_self_timer_throttling()

    def _disable_self_timer_throttling(self) -> None:
        """AutoMemoryGuard 자신의 타이머 해상도 요청이 Windows 11 커널에 의해 무시되지 않도록 설정합니다."""
        if os.name != "nt":
            return
        try:
            state = PROCESS_POWER_THROTTLING_STATE(
                Version=PROCESS_POWER_THROTTLING_CURRENT_VERSION,
                ControlMask=PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION,
                StateMask=0,  # 0 = IgnoreTimerResolution 비활성화 (타이머 요청 항상 반영)
            )
            curr_proc = _kernel32.GetCurrentProcess()
            _kernel32.SetProcessInformation(
                curr_proc,
                ProcessPowerThrottling,
                ctypes.byref(state),
                ctypes.sizeof(state),
            )
        except Exception as exc:
            _LOGGER.debug("자체 타이머 스로틀링 해제 실패: %s", exc)

    def detect_running_game(self) -> Optional[psutil.Process]:
        """현재 실행 중인 게임을 감지합니다 (기존 감지된 게임이 살아있으면 Alt-Tab 중이어도 유지)."""
        # 1. 이미 감지된 게임 PID가 살아있는지 먼저 확인 (Alt-Tab 히스테리시스 방어)
        if self.active_game_pid is not None:
            try:
                proc = psutil.Process(self.active_game_pid)
                if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                    return proc
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            self.active_game_pid = None
            self.active_game_name = ""

        # 2. 등록된 게임 프로세스 목록 스캔 (또는 steamapps 경로 매칭)
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = (proc.info["name"] or "").strip().lower()
                if name in self.known_games:
                    return proc

                # 스팀 게임 경로 휴리스틱 감지
                try:
                    exe_path = (proc.exe() or "").lower()
                    if "\\steamapps\\common\\" in exe_path and name not in NON_GAME_FULLSCREEN_APPS:
                        return proc
                except Exception:
                    pass
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # 3. Direct3D 전체화면 알림 상태 기반 감지
        if os.name == "nt" and _PYWIN32_AVAILABLE:
            try:
                q_state = ctypes.c_int(0)
                if _shell32.SHQueryUserNotificationState(ctypes.byref(q_state)) == 0:
                    if q_state.value == QUNS_RUNNING_D3D_FULL_SCREEN:
                        fg_hwnd = win32gui.GetForegroundWindow()
                        if fg_hwnd:
                            _, fg_pid = win32process.GetWindowThreadProcessId(fg_hwnd)
                            if fg_pid > 4:
                                fg_proc = psutil.Process(fg_pid)
                                fg_name = fg_proc.name().lower()
                                if fg_name not in NON_GAME_FULLSCREEN_APPS:
                                    return fg_proc
            except Exception:
                pass

        return None

    def lock_high_precision_timer(self) -> float:
        """시스템 타이머 해상도를 하드웨어 지원 최대치(약 0.5ms)로 고정합니다."""
        if os.name != "nt":
            return 15.6

        try:
            _winmm.timeBeginPeriod(1)
            min_res = wintypes.ULONG(0)
            max_res = wintypes.ULONG(0)
            curr_res = wintypes.ULONG(0)

            if _ntdll.NtQueryTimerResolution(ctypes.byref(min_res), ctypes.byref(max_res), ctypes.byref(curr_res)) == 0:
                target_res = max(5000, max_res.value) if max_res.value > 0 else 5000
                _ntdll.NtSetTimerResolution(target_res, wintypes.BOOLEAN(1), ctypes.byref(curr_res))
                self.current_timer_ms = round(curr_res.value / 10000.0, 2)
                _LOGGER.info("고정밀 타이머 잠금 완료: %.2f ms (요청값: %d)", self.current_timer_ms, target_res)
                return self.current_timer_ms
        except Exception as exc:
            _LOGGER.error("타이머 해상도 잠금 실패: %s", exc)

        return 1.0

    def unlock_high_precision_timer(self) -> None:
        """고정밀 타이머 잠금을 해제하여 Windows 기본 절전 주기(15.6ms)로 복원합니다."""
        if os.name != "nt":
            return
        try:
            curr_res = wintypes.ULONG(0)
            _ntdll.NtSetTimerResolution(0, wintypes.BOOLEAN(0), ctypes.byref(curr_res))
            _winmm.timeEndPeriod(1)
            self.current_timer_ms = round(curr_res.value / 10000.0, 2) if curr_res.value else 15.6
            _LOGGER.info("고정밀 타이머 잠금 해제 완료 (현재: %.2f ms)", self.current_timer_ms)
        except Exception as exc:
            _LOGGER.debug("타이머 해제 중 예외: %s", exc)

    def _set_process_ecoqos(self, pid: int, enable: bool) -> bool:
        """대상 백그라운드 프로세스에 Windows 11 EcoQoS(E-코어 효율 모드)를 적용하거나 해제합니다."""
        if os.name != "nt":
            return False
        handle = _kernel32.OpenProcess(PROCESS_SET_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            state = PROCESS_POWER_THROTTLING_STATE(
                Version=PROCESS_POWER_THROTTLING_CURRENT_VERSION,
                ControlMask=PROCESS_POWER_THROTTLING_EXECUTION_SPEED,
                StateMask=PROCESS_POWER_THROTTLING_EXECUTION_SPEED if enable else 0,
            )
            return bool(
                _kernel32.SetProcessInformation(
                    handle,
                    ProcessPowerThrottling,
                    ctypes.byref(state),
                    ctypes.sizeof(state),
                )
            )
        except Exception:
            return False
        finally:
            _kernel32.CloseHandle(handle)

    def tame_background_processes(self) -> int:
        """게임 도중 백그라운드 브라우저/웹뷰/런처 프로세스를 EcoQoS 및 BELOW_NORMAL로 격리합니다."""
        tamed_count = 0
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                pid = proc.info["pid"]
                if pid == self.active_game_pid or pid <= 4:
                    continue
                name = (proc.info["name"] or "").strip().lower()
                if name in BACKGROUND_TAME_TARGETS and pid not in self._tamed_pids:
                    try:
                        orig_prio = proc.nice()
                        proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
                        self._set_process_ecoqos(pid, enable=True)
                        self._tamed_pids[pid] = orig_prio
                        tamed_count += 1
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            except Exception:
                continue

        if tamed_count > 0:
            _LOGGER.info("백그라운드 프로세스 %d개 EcoQoS(E-코어) 및 우선순위 격리 완료", tamed_count)
        return tamed_count

    def restore_background_processes(self) -> None:
        """게임 종료 후 격리했던 백그라운드 프로세스들의 우선순위와 EcoQoS를 원상복구합니다."""
        restored_count = 0
        for pid, orig_prio in list(self._tamed_pids.items()):
            try:
                proc = psutil.Process(pid)
                proc.nice(orig_prio)
                self._set_process_ecoqos(pid, enable=False)
                restored_count += 1
            except Exception:
                pass
        self._tamed_pids.clear()
        if restored_count > 0:
            _LOGGER.info("격리되었던 백그라운드 프로세스 %d개 정상 복원 완료", restored_count)

    def elevate_power_scheme(self) -> bool:
        """Windows 전원 옵션을 고성능(또는 최고 성능)으로 일시 승격합니다."""
        if os.name != "nt":
            return False
        try:
            p_guid = ctypes.POINTER(WinGUID)()
            if _powrprof.PowerGetActiveScheme(None, ctypes.byref(p_guid)) == 0:
                self._saved_power_guid = p_guid.contents.to_uuid()
                _kernel32.LocalFree(p_guid)

            target_scheme = WinGUID.from_uuid(GUID_HIGH_PERFORMANCE)
            res = _powrprof.PowerSetActiveScheme(None, ctypes.byref(target_scheme))
            if res == 0:
                _LOGGER.info("Windows 전원 플랜 '고성능(High Performance)' 승격 완료")
                return True
        except Exception as exc:
            _LOGGER.debug("전원 플랜 승격 중 예외: %s", exc)
        return False

    def restore_power_scheme(self) -> bool:
        """이전 전원 옵션으로 복원합니다."""
        if os.name != "nt" or self._saved_power_guid is None:
            return False
        try:
            target_scheme = WinGUID.from_uuid(self._saved_power_guid)
            res = _powrprof.PowerSetActiveScheme(None, ctypes.byref(target_scheme))
            if res == 0:
                _LOGGER.info("Windows 전원 플랜 이전 상태(GUID: %s)로 복원 완료", self._saved_power_guid)
                self._saved_power_guid = None
                return True
        except Exception as exc:
            _LOGGER.debug("전원 플랜 복원 중 예외: %s", exc)
        return False

    def activate_boost(self, game_proc: psutil.Process) -> Dict[str, Any]:
        """게임 실행 감지 시 0.5ms 타이머 고정, 전원 승격, 백그라운드 격리를 동시 가동합니다."""
        self.active_game_pid = game_proc.pid
        self.active_game_name = game_proc.name()
        self.is_boost_active = True

        timer_ms = self.lock_high_precision_timer()
        power_ok = self.elevate_power_scheme()
        tamed = self.tame_background_processes()

        _LOGGER.info(
            "=== [Gaming Shield 가동] 게임: %s (PID: %d) | 타이머: %.2f ms | 격리: %d개 앱 ===",
            self.active_game_name,
            self.active_game_pid,
            timer_ms,
            tamed,
        )

        return {
            "game_pid": self.active_game_pid,
            "game_name": self.active_game_name,
            "timer_ms": timer_ms,
            "power_elevated": power_ok,
            "tamed_count": tamed,
        }

    def deactivate_boost(self) -> None:
        """게임 종료 시 모든 게이밍 가속 상태를 시스템 기본값으로 안전 복구합니다."""
        prev_name = self.active_game_name
        prev_pid = self.active_game_pid

        self.unlock_high_precision_timer()
        self.restore_background_processes()
        self.restore_power_scheme()

        self.is_boost_active = False
        self.active_game_pid = None
        self.active_game_name = ""

        _LOGGER.info("=== [Gaming Shield 해제] 게임 종료: %s (PID: %s) -> 시스템 복원 완료 ===", prev_name, prev_pid)


if __name__ == "__main__":
    print("=== GamingBoostEngine 모듈 단위 검증 시작 ===")
    engine = GamingBoostEngine()
    print(f"[1] 등록된 감지 게임 수: {len(engine.known_games)}개")

    t_ms = engine.lock_high_precision_timer()
    print(f"[2] 고정밀 타이머 잠금 결과: {t_ms:.2f} ms")

    engine.unlock_high_precision_timer()
    print(f"[3] 고정밀 타이머 복원 결과: {engine.current_timer_ms:.2f} ms")
    print("=== GamingBoostEngine 단위 검증 완료 ===")
