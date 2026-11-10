"""실시간 시스템 메모리 모니터링 및 2단계 자동 관리 백그라운드 데몬 모듈.

메모리 점유율을 실시간으로 감시하며 임계치 도달 시
[1단계: Working Set 메모리 압축] -> [2단계: 유휴 프로세스 안전 종료]를
순차적으로 자동 발동하고, 쿨다운 타이머 및 UI 상태 콜백을 총괄 관리합니다.
"""

from collections.abc import Callable
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, Dict, Optional, Set

# 독립 실행 지원을 위한 프로젝트 루트 sys.path 등록 (플랫 및 서브폴더 구조 지원)
_here = Path(__file__).resolve().parent
_PROJECT_ROOT = _here if (_here / "main.py").exists() else _here.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import psutil
import ctypes
from ctypes import wintypes
import uuid

try:
    import win32gui
    import win32process
    _PYWIN32_AVAILABLE = True
except ImportError:
    _PYWIN32_AVAILABLE = False

try:
    from core.config_manager import ConfigManager
    from core.memory_trimmer import IdleTracker, MemoryTrimmer
    from core.process_killer import SmartProcessKiller
    from utils.logger import get_logger
except ImportError:
    from config_manager import ConfigManager
    from memory_trimmer import IdleTracker, MemoryTrimmer
    from process_killer import SmartProcessKiller
    from logger import get_logger

_LOGGER = get_logger("MonitorDaemon")

StatusCallback = Callable[[Dict[str, Any]], None]
CleanupCallback = Callable[[str, Dict[str, Any]], None]


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
    """게이밍 프로세스 감지, 0.5ms 동적 타이머, 전원 플랜 승격, EcoQoS 백그라운드 격리를 총괄하는 스레드 안전 엔진."""

    def __init__(self, custom_games: Optional[Set[str]] = None) -> None:
        """GamingBoostEngine을 초기화합니다."""
        self._lock: threading.RLock = threading.RLock()
        self.known_games: Set[str] = set(DEFAULT_KNOWN_GAMES)
        if custom_games:
            self.known_games.update({name.strip().lower() for name in custom_games if name.strip()})

        self.is_boost_active: bool = False
        self._timer_locked: bool = False
        self.active_game_pid: Optional[int] = None
        self.active_game_name: str = ""
        self.current_timer_ms: float = 15.6

        self._saved_power_guid: Optional[uuid.UUID] = None
        # {pid: (original_priority, create_time)} 형태로 저장하여 PID 재사용 오복원 차단
        self._tamed_pids: Dict[int, tuple[int, float]] = {}

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
                StateMask=0,
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
        with self._lock:
            # 1. 이미 감지된 실제 게임 PID가 살아있는지 먼저 확인 (Alt-Tab 히스테리시스 방어)
            if self.active_game_pid is not None:
                try:
                    proc = psutil.Process(self.active_game_pid)
                    if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                        return proc
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                self.active_game_pid = None
                self.active_game_name = ""

        # 2. 등록된 게임 프로세스 목록 및 steamapps 경로 배치 스캔 (proc.exe() 시스템콜 최소화)
        for proc in psutil.process_iter(["pid", "name", "exe"]):
            try:
                info = proc.info
                name = (info.get("name") or "").strip().lower()
                if not name:
                    continue
                if name in self.known_games:
                    return proc

                exe_path = (info.get("exe") or "").lower()
                if "\\steamapps\\common\\" in exe_path and name not in NON_GAME_FULLSCREEN_APPS:
                    return proc
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception:
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
        """시스템 타이머 해상도를 하드웨어 지원 최대치(약 0.5ms)로 고정합니다 (중복 호출 멱등성 보장)."""
        if os.name != "nt":
            return 15.6

        with self._lock:
            if self._timer_locked:
                return self.current_timer_ms

            try:
                _winmm.timeBeginPeriod(1)
                self._timer_locked = True
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
        """고정밀 타이머 잠금을 해제하여 Windows 기본 절전 주기(15.6ms)로 복원합니다 (중복 해제 방지)."""
        if os.name != "nt":
            return
        with self._lock:
            if not self._timer_locked:
                return
            try:
                curr_res = wintypes.ULONG(0)
                _ntdll.NtSetTimerResolution(0, wintypes.BOOLEAN(0), ctypes.byref(curr_res))
                _winmm.timeEndPeriod(1)
                self._timer_locked = False
                self.current_timer_ms = round(curr_res.value / 10000.0, 2) if curr_res.value else 15.6
                _LOGGER.info("고정밀 타이머 잠금 해제 완료 (현재: %.2f ms)", self.current_timer_ms)
            except Exception as exc:
                self._timer_locked = False
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
        with self._lock:
            for proc in psutil.process_iter(["pid", "name"]):
                try:
                    pid = proc.info["pid"]
                    if pid == self.active_game_pid or pid <= 4 or pid in self._tamed_pids:
                        continue
                    name = (proc.info["name"] or "").strip().lower()
                    if name in BACKGROUND_TAME_TARGETS:
                        orig_prio = proc.nice()
                        ctime = proc.create_time()
                        proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
                        self._set_process_ecoqos(pid, enable=True)
                        self._tamed_pids[pid] = (orig_prio, ctime)
                        tamed_count += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
                except Exception:
                    continue

        if tamed_count > 0:
            _LOGGER.info("백그라운드 프로세스 %d개 EcoQoS(E-코어) 및 우선순위 격리 완료", tamed_count)
        return tamed_count

    def restore_background_processes(self) -> None:
        """게임 종료 후 격리했던 백그라운드 프로세스들의 우선순위와 EcoQoS를 PID 재사용 검증 후 원상복구합니다."""
        restored_count = 0
        with self._lock:
            for pid, (orig_prio, saved_ctime) in list(self._tamed_pids.items()):
                try:
                    proc = psutil.Process(pid)
                    if abs(proc.create_time() - saved_ctime) <= 1.0:
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
        with self._lock:
            try:
                if self._saved_power_guid is None:
                    p_guid = ctypes.POINTER(WinGUID)()
                    if _powrprof.PowerGetActiveScheme(None, ctypes.byref(p_guid)) == 0 and p_guid:
                        try:
                            self._saved_power_guid = p_guid.contents.to_uuid()
                        finally:
                            _kernel32.LocalFree(p_guid)

                if self._saved_power_guid == GUID_HIGH_PERFORMANCE:
                    return True

                target_scheme = WinGUID.from_uuid(GUID_HIGH_PERFORMANCE)
                res = _powrprof.PowerSetActiveScheme(None, ctypes.byref(target_scheme))
                if res == 0:
                    _LOGGER.info("Windows 전원 플랜 '고성능(High Performance)' 승격 완료")
                    return True
                else:
                    self._saved_power_guid = None
            except Exception as exc:
                _LOGGER.debug("전원 플랜 승격 중 예외: %s", exc)
            return False

    def restore_power_scheme(self) -> bool:
        """이전 전원 옵션으로 복원합니다."""
        if os.name != "nt":
            return False
        with self._lock:
            if self._saved_power_guid is None:
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
            self._saved_power_guid = None
            return False

    def activate_boost(self, game_proc: Optional[psutil.Process] = None) -> Dict[str, Any]:
        """게임 실행 감지 또는 수동 상시 가동 시 0.5ms 타이머 고정, 전원 승격, 백그라운드 격리를 가동합니다."""
        with self._lock:
            if game_proc is not None:
                try:
                    self.active_game_pid = game_proc.pid
                    self.active_game_name = game_proc.name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    self.active_game_pid = None
                    self.active_game_name = "수동 상시 가동"
            else:
                self.active_game_pid = None
                self.active_game_name = "수동 상시 가동"

            self.is_boost_active = True

            timer_ms = self.lock_high_precision_timer()
            power_ok = self.elevate_power_scheme()
            tamed = self.tame_background_processes()

            _LOGGER.info(
                "=== [Gaming Shield 가동] 대상: %s (PID: %s) | 타이머: %.2f ms | 격리: %d개 앱 ===",
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
        with self._lock:
            if not self.is_boost_active and not self._timer_locked:
                return

            prev_name = self.active_game_name or "수동 상시 가동"
            prev_pid = self.active_game_pid

            self.unlock_high_precision_timer()
            self.restore_background_processes()
            self.restore_power_scheme()

            self.is_boost_active = False
            self.active_game_pid = None
            self.active_game_name = ""

            _LOGGER.info("=== [Gaming Shield 해제] 종료 대상: %s (PID: %s) -> 시스템 복원 완료 ===", prev_name, prev_pid)

    @property
    def is_active(self) -> bool:
        """게이밍 부스트 활성화 여부."""
        return self.is_boost_active

    @property
    def timer_manager(self) -> "GamingBoostEngine":
        """TimerResolutionManager 하위 호환 바인딩."""
        return self

    @property
    def current_resolution_ms(self) -> float:
        """현재 시스템 타이머 해상도 (ms)."""
        return self.current_timer_ms

    def enable_high_resolution(self) -> float:
        """타이머 고정밀 활성화 하위 호환."""
        return self.lock_high_precision_timer()

    def disable_high_resolution(self) -> None:
        """타이머 해상도 원복 하위 호환."""
        self.unlock_high_precision_timer()



class MemoryMonitorDaemon(threading.Thread):
    """실시간 메모리 모니터링 및 2단계 하이브리드 자동 정리를 수행하는 데몬 스레드."""

    def __init__(
        self,
        config_manager: Optional[ConfigManager] = None,
        on_status_update: Optional[StatusCallback] = None,
        on_cleanup_executed: Optional[CleanupCallback] = None,
    ) -> None:
        """MemoryMonitorDaemon을 초기화합니다.

        Args:
            config_manager: 설정 관리자 인스턴스 (생략 시 기본 경로에서 로드).
            on_status_update: 주기적 상태 갱신 시 호출할 콜백 (UI 및 트레이 갱신용).
            on_cleanup_executed: 1/2단계 정리 실행 시 호출할 콜백 (이벤트 알림용).
        """
        super().__init__(name="MemoryMonitorDaemonThread", daemon=True)

        self.config_manager: ConfigManager = config_manager if config_manager is not None else ConfigManager()
        self.trimmer: MemoryTrimmer = MemoryTrimmer()
        self.killer: SmartProcessKiller = SmartProcessKiller(
            self.config_manager,
            idle_tracker=IdleTracker(self.config_manager.config.idle_cpu_max_percent),
        )
        self.gaming_engine: GamingBoostEngine = GamingBoostEngine(
            custom_games=set(self.config_manager.config.custom_games)
        )

        self.on_status_update: Optional[StatusCallback] = on_status_update
        self.on_cleanup_executed: Optional[CleanupCallback] = on_cleanup_executed

        self.stop_event: threading.Event = threading.Event()
        self._action_lock: threading.Lock = threading.Lock()

        # 런타임 제어 및 상태 플래그
        self.auto_mode_enabled: bool = True
        self.last_cleanup_timestamp: float = 0.0
        self.last_action_name: str = "INITIALIZED"
        self.last_freed_mb: float = 0.0
        self.cumulative_freed_mb: float = 0.0

    @property
    def is_cooling_down(self) -> bool:
        """현재 쿨다운 대기 상태인지 여부를 반환합니다."""
        return self.get_cooldown_remaining_sec() > 0.0

    def get_cooldown_remaining_sec(self) -> float:
        """쿨다운 만료까지 남은 잔여 시간(초)을 반환합니다."""
        cooldown_sec = self.config_manager.config.cooldown_sec
        elapsed = time.time() - self.last_cleanup_timestamp
        remaining = cooldown_sec - elapsed
        return max(0.0, round(remaining, 1))

    def set_auto_mode(self, enabled: bool) -> None:
        """자동 관리 모드 활성화/비활성화 상태를 변경합니다.

        Args:
            enabled: True 시 자동 정리 활성화, False 시 수동 정리만 허용.
        """
        self.auto_mode_enabled = enabled
        _LOGGER.info("실시간 자동 관리 모드 변경: %s", "활성화" if enabled else "비활성화")

    def stop(self) -> None:
        """데몬 스레드에 안전한 종료 신호를 보내고 게이밍 최적화를 원복합니다."""
        _LOGGER.info("메모리 감시 데몬 종료 신호를 전달합니다.")
        self.stop_event.set()
        try:
            self.gaming_engine.deactivate_boost()
        except Exception as exc:
            _LOGGER.debug("게이밍 부스트 해제 중 오류: %s", exc)

    def run(self) -> None:
        """데몬 메인 실행 루프."""
        _LOGGER.info("MemoryMonitorDaemon 감시 루프가 시작되었습니다.")

        try:
            while not self.stop_event.is_set():
                try:
                    self._monitor_tick()
                except Exception as exc:
                    _LOGGER.error("감시 루프 실행 중 예기치 않은 오류 발생: %s", exc, exc_info=True)

                # polling_interval_sec 동안 대기하되, stop_event 신호 수신 시 즉각 반응
                interval = max(1, self.config_manager.config.polling_interval_sec)
                self.stop_event.wait(timeout=interval)
        finally:
            try:
                self.gaming_engine.deactivate_boost()
            except Exception:
                pass
            _LOGGER.info("MemoryMonitorDaemon 감시 루프가 안전하게 종료되었습니다.")

    def _monitor_tick(self) -> None:
        """1회 모니터링 주기 동안 메트릭을 갱신하고 필요 시 2단계 자동 관리를 발동합니다."""
        # 0. 게이밍 모드 판별 및 0.5ms 동적 타이머/EcoQoS 제어
        gaming_mode = self.config_manager.config.gaming_mode
        if gaming_mode == "auto":
            detected = self.gaming_engine.detect_running_game()
            if detected is not None:
                if not self.gaming_engine.is_active or self.gaming_engine.active_game_pid != detected.pid:
                    try:
                        g_name = detected.name()
                    except Exception:
                        g_name = "UnknownGame"
                    _LOGGER.info(
                        "게임 실행 감지됨: %s (PID: %d). 선제적 1단계 메모리 압축 및 0.5ms 게이밍 쉴드 발동",
                        g_name,
                        detected.pid,
                    )
                    # 프리게임(Pre-game) 1회성 선제 압축: 게임 프로세스를 제외한 백그라운드 Working Set 즉각 정리
                    self.trimmer.trim_all_processes(exclude_pids={detected.pid})
                    self.gaming_engine.activate_boost(detected)
                else:
                    # 게임 플레이 도중 새로 생성된 브라우저 탭/백그라운드 헬퍼 추가 격리
                    self.gaming_engine.tame_background_processes()
            else:
                if self.gaming_engine.is_active:
                    _LOGGER.info("실행 중이던 게임 종료 감지됨. 게이밍 쉴드 해제 및 사후 메모리 정리.")
                    self.gaming_engine.deactivate_boost()
                    # 게임 종료 후 1회 사후 정리
                    self.trimmer.trim_all_processes()
        elif gaming_mode == "always":
            if not self.gaming_engine.is_active:
                self.gaming_engine.activate_boost(None)
            else:
                self.gaming_engine.tame_background_processes()
        elif gaming_mode == "off":
            if self.gaming_engine.is_active:
                self.gaming_engine.deactivate_boost()

        # 1. 프로세스 CPU/유휴 추적기 상태 갱신
        self.killer.idle_tracker.update()

        # 2. 현재 가상 메모리 측정
        vm = psutil.virtual_memory()
        current_percent = round(vm.percent, 1)
        total_mb = round(vm.total / (1024 * 1024), 1)
        used_mb = round((vm.total - vm.available) / (1024 * 1024), 1)
        available_mb = round(vm.available / (1024 * 1024), 1)

        cfg = self.config_manager.config
        stage1_threshold = cfg.stage1_trim_threshold_percent
        stage2_threshold = cfg.stage2_kill_threshold_percent

        # 게이밍 모드 활성 중에는 인게임 교전 중 끊김(Micro-stutter)을 방지하기 위해 1단계 임계치를 92.0%로 완화
        if self.gaming_engine.is_active:
            stage1_threshold = max(stage1_threshold, 92.0)
            stage2_threshold = max(stage2_threshold, 96.0)

        # 3. 자동 정리 조건 판별
        if self.auto_mode_enabled and not self.is_cooling_down:
            if current_percent >= stage1_threshold:
                _LOGGER.warning(
                    "메모리 점유율(%.1f%%)이 1단계 임계치(%.1f%%)를 초과하여 자동 정리를 시작합니다.",
                    current_percent,
                    stage1_threshold,
                )
                self._execute_hybrid_auto_cleanup(current_percent, stage2_threshold)

        # 4. 실시간 상태 콜백 전달 (UI 및 트레이 갱신용)
        status_payload = {
            "memory_percent": current_percent,
            "total_mb": total_mb,
            "used_mb": used_mb,
            "available_mb": available_mb,
            "auto_mode": self.auto_mode_enabled,
            "is_cooling_down": self.is_cooling_down,
            "cooldown_remaining_sec": self.get_cooldown_remaining_sec(),
            "last_action": self.last_action_name,
            "last_freed_mb": self.last_freed_mb,
            "cumulative_freed_mb": round(self.cumulative_freed_mb, 1),
            "last_cleanup_timestamp": self.last_cleanup_timestamp,
            "gaming_mode": gaming_mode,
            "is_gaming_active": self.gaming_engine.is_active,
            "active_game_name": self.gaming_engine.active_game_name,
            "active_game_pid": self.gaming_engine.active_game_pid,
            "timer_resolution_ms": self.gaming_engine.timer_manager.current_resolution_ms,
        }

        if self.on_status_update is not None:
            try:
                self.on_status_update(status_payload)
            except Exception as cb_exc:
                _LOGGER.debug("상태 콜백 실행 중 오류: %s", cb_exc)

    def _execute_hybrid_auto_cleanup(self, initial_percent: float, stage2_threshold: float) -> None:
        """1단계 Working Set 압축 및 필요 시 2단계 프로세스 종료를 순차 실행합니다."""
        with self._action_lock:
            # 쿨다운 중복 실행 방지
            if self.is_cooling_down:
                return

            total_freed_mb = 0.0
            exclude_pids: Optional[Set[int]] = None
            if self.gaming_engine.is_active and self.gaming_engine.active_game_pid:
                exclude_pids = {self.gaming_engine.active_game_pid}

            # --- [1단계 자동 대응]: Working Set 비우기 ---
            _LOGGER.info("[자동 1단계] Working Set 메모리 압축 실행 (예외 PID: %s)", exclude_pids)
            trim_result = self.trimmer.trim_all_processes(exclude_pids=exclude_pids)
            total_freed_mb += trim_result.get("freed_mb", 0.0)

            # 커널 페이징 안정화 및 2단계 판단을 위한 2초 대기
            self.stop_event.wait(timeout=2.0)
            if self.stop_event.is_set():
                return

            # 압축 후 메모리 재측정
            vm_recheck = psutil.virtual_memory()
            post_trim_percent = round(vm_recheck.percent, 1)

            action_type = "STAGE1_AUTO_TRIM"
            killed_count = 0

            # --- [2단계 자동 대응]: 1단계 후에도 2단계 임계치 초과 시 ---
            if post_trim_percent >= stage2_threshold:
                # 게이밍 모드 활성 중에는 사용자 작업물 및 백그라운드 무단 종료를 방지하기 위해 2단계 강제 종료 차단
                if self.gaming_engine.is_active:
                    _LOGGER.info(
                        "[게이밍 쉴드] 게임 구동 중 사용자 작업물 보호를 위해 2단계 강제 종료를 차단하고 1단계 압축 상태를 유지합니다."
                    )
                else:
                    _LOGGER.warning(
                        "[자동 2단계] 1단계 압축 후에도 메모리(%.1f%%)가 2단계 임계치(%.1f%%)를 초과하여 유휴 프로세스 종료를 발동합니다.",
                        post_trim_percent,
                        stage2_threshold,
                    )
                    action_type = "STAGE2_AUTO_KILL"

                    # 유휴 백그라운드 프로세스 안전 종료
                    kill_result = self.killer.terminate_candidates(max_kills=5, dry_run=False)
                    killed_count = kill_result.get("killed_count", 0)
                    total_freed_mb += kill_result.get("total_freed_mb", 0.0)

            # 정리 완료 상태 기록 및 쿨다운 타이머 시작
            self.last_cleanup_timestamp = time.time()
            self.last_action_name = action_type
            self.last_freed_mb = round(total_freed_mb, 1)
            self.cumulative_freed_mb += self.last_freed_mb

            _LOGGER.info(
                "하이브리드 자동 정리 완료 (%s): 총 %.1f MB 확보. %d초 쿨다운을 시작합니다.",
                action_type,
                self.last_freed_mb,
                self.config_manager.config.cooldown_sec,
            )

            # 이벤트 콜백 통보
            if self.on_cleanup_executed is not None:
                try:
                    self.on_cleanup_executed(
                        action_type,
                        {
                            "initial_percent": initial_percent,
                            "final_percent": round(psutil.virtual_memory().percent, 1),
                            "freed_mb": self.last_freed_mb,
                            "killed_count": killed_count,
                            "timestamp": self.last_cleanup_timestamp,
                        },
                    )
                except Exception as cb_exc:
                    _LOGGER.debug("정리 완료 콜백 실행 오류: %s", cb_exc)

    def manual_trim(self) -> Dict[str, Any]:
        """사용자 요청에 의해 즉시 1단계 메모리 Working Set 비우기를 실행합니다.

        쿨다운 상태와 무관하게 즉시 발동됩니다.

        Returns:
            Dict[str, Any]: 메모리 압축 결과 딕셔너리.
        """
        with self._action_lock:
            _LOGGER.info("[수동 실행] 사용자가 1단계 메모리 압축을 요청했습니다.")
            exclude_pids = (
                {self.gaming_engine.active_game_pid}
                if (self.gaming_engine.is_active and self.gaming_engine.active_game_pid)
                else None
            )
            result = self.trimmer.trim_all_processes(exclude_pids=exclude_pids)

            self.last_cleanup_timestamp = time.time()
            self.last_action_name = "MANUAL_TRIM"
            self.last_freed_mb = result.get("freed_mb", 0.0)
            self.cumulative_freed_mb += self.last_freed_mb

            if self.on_cleanup_executed is not None:
                try:
                    self.on_cleanup_executed("MANUAL_TRIM", result)
                except Exception as exc:
                    _LOGGER.debug("수동 정리 콜백 오류: %s", exc)

            return result

    def manual_smart_kill(self) -> Dict[str, Any]:
        """사용자 요청에 의해 즉시 2단계 스마트 프로세스 종료 및 캐시 정리를 실행합니다.

        쿨다운 상태와 무관하게 즉시 발동됩니다.

        Returns:
            Dict[str, Any]: 프로세스 종료 및 메모리 회수 결과 딕셔너리.
        """
        with self._action_lock:
            _LOGGER.info("[수동 실행] 사용자가 2단계 스마트 프로세스 종료를 요청했습니다.")

            # 유휴 프로세스 종료
            kill_result = self.killer.terminate_candidates(max_kills=5, dry_run=False)
            freed_from_kill = kill_result.get("total_freed_mb", 0.0)

            # 종료 후 잔여 Working Set 압축
            exclude_pids = (
                {self.gaming_engine.active_game_pid}
                if (self.gaming_engine.is_active and self.gaming_engine.active_game_pid)
                else None
            )
            trim_result = self.trimmer.trim_all_processes(exclude_pids=exclude_pids)
            freed_from_trim = trim_result.get("freed_mb", 0.0)

            total_freed = round(freed_from_kill + freed_from_trim, 1)

            combined_result = {
                "killed_count": kill_result.get("killed_count", 0),
                "freed_from_kill_mb": freed_from_kill,
                "freed_from_trim_mb": freed_from_trim,
                "total_freed_mb": total_freed,
                "terminated": kill_result.get("terminated", []),
            }

            self.last_cleanup_timestamp = time.time()
            self.last_action_name = "MANUAL_KILL"
            self.last_freed_mb = total_freed
            self.cumulative_freed_mb += self.last_freed_mb

            if self.on_cleanup_executed is not None:
                try:
                    self.on_cleanup_executed("MANUAL_KILL", combined_result)
                except Exception as exc:
                    _LOGGER.debug("수동 프로세스 종료 콜백 오류: %s", exc)

            return combined_result


if __name__ == "__main__":
    print("=== MemoryMonitorDaemon 단독 검증 시작 ===")

    # 콜백 동작 테스트용 함수 정의
    def dummy_status_cb(st: Dict[str, Any]) -> None:
        print(
            f"[상태 수신] RAM: {st['memory_percent']}% "
            f"({st['used_mb']}/{st['total_mb']} MB) | "
            f"자동모드: {st['auto_mode']} | "
            f"쿨다운 잔여: {st['cooldown_remaining_sec']}s"
        )

    def dummy_cleanup_cb(action: str, res: Dict[str, Any]) -> None:
        print(f"[이벤트 수신] 정리 실행됨: {action} -> 결과: {res}")

    daemon = MemoryMonitorDaemon(
        on_status_update=dummy_status_cb,
        on_cleanup_executed=dummy_cleanup_cb,
    )

    print("[1] 데몬 스레드 시작")
    daemon.start()

    # 3초간 감시 상태 수신 대기
    time.sleep(3.0)

    print("[2] 수동 1단계 Working Set 압축 호출 테스트")
    manual_trim_res = daemon.manual_trim()
    print(f"  -> 수동 압축 결과: {manual_trim_res['before_percent']}% -> {manual_trim_res['after_percent']}% (확보: {manual_trim_res['freed_mb']} MB)")

    # 3초간 쿨다운 타이머 반영 확인
    time.sleep(3.0)

    print("[3] 데몬 스레드 안전 종료 요청")
    daemon.stop()
    daemon.join(timeout=3.0)
    print("=== MemoryMonitorDaemon 단독 검증 완료 ===")
