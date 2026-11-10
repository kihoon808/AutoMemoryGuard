"""Windows 물리 RAM(Working Set) 압축 및 프로세스 유휴 상태 추적 모듈.

Win32 커널 API(EmptyWorkingSet)와 권한 조정 토큰(SeDebugPrivilege, SeProfileSingleProcessPrivilege)을
활용하여 프로세스를 강제 종료하지 않고 물리 메모리를 즉각 비워내며,
psutil 첫 호출 0.0% 오탐지를 방지하는 2회 이상 폴링 기반 IdleTracker를 제공합니다.
"""

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
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

_LOGGER = get_logger("MemoryTrimmer")

# Working Set 압축 시 프레임 드랍, 오디오 노이즈, 안티치트 충돌을 방지하기 위한 영구 보호 프로세스
TRIM_PROTECTED_NAMES: Set[str] = {
    # Windows 필수 디스플레이/오디오/보안 커널 서비스
    "audiodg.exe",
    "dwm.exe",
    "memory compression",
    "csrss.exe",
    "smss.exe",
    "wininit.exe",
    "services.exe",
    "lsass.exe",
    # 메이저 안티치트 서비스 (핸들 조작 및 Working Set 플러시 시 오탐 방지)
    "vgc.exe",
    "vgk.sys",
    "easyanticheat.exe",
    "easyanticheat_eos.exe",
    "battleye.exe",
    "beservice.exe",
    # 게임 플랫폼 런처 및 오버레이
    "steam.exe",
    "steamwebhelper.exe",
    "riotclientux.exe",
    "riotclientservices.exe",
    "epicgameslauncher.exe",
}

# Win32 프로세스 접근 권한 마스크 상수 (Windows 11 제한된 정보 쿼리 우선)
PROCESS_QUERY_LIMITED_INFORMATION: int = 0x1000
PROCESS_QUERY_INFORMATION: int = 0x0400
PROCESS_SET_QUOTA: int = 0x0100
PROCESS_ACCESS_FLAGS: int = PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SET_QUOTA

# Win32 보안 토큰 상수
TOKEN_ADJUST_PRIVILEGES: int = 0x0020
TOKEN_QUERY: int = 0x0008
SE_PRIVILEGE_ENABLED: int = 0x00000002
SE_DEBUG_NAME: str = "SeDebugPrivilege"
SE_PROFILE_SINGLE_PROCESS_NAME: str = "SeProfileSingleProcessPrivilege"


# Win32 C 구조체 정의 (64비트 Windows 정렬 준수)
class LUID(ctypes.Structure):
    _fields_ = [
        ("LowPart", wintypes.DWORD),
        ("HighPart", wintypes.LONG),
    ]


class LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Luid", LUID),
        ("Attributes", wintypes.DWORD),
    ]


class TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [
        ("PrivilegeCount", wintypes.DWORD),
        ("Privileges", LUID_AND_ATTRIBUTES * 1),
    ]


# 64비트 Windows Win32 API 함수 시그니처 엄격 바인딩 (스레드 안전 use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

_kernel32.GetCurrentProcess.argtypes = []
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE

_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE

_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL

# EmptyWorkingSet: K32EmptyWorkingSet 우선 바인딩, 레거시 psapi fallback
try:
    _empty_working_set_func = _kernel32.K32EmptyWorkingSet
except AttributeError:
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)
    _empty_working_set_func = _psapi.EmptyWorkingSet

_empty_working_set_func.argtypes = [wintypes.HANDLE]
_empty_working_set_func.restype = wintypes.BOOL

_advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
_advapi32.OpenProcessToken.restype = wintypes.BOOL

_advapi32.LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(LUID)]
_advapi32.LookupPrivilegeValueW.restype = wintypes.BOOL

_advapi32.AdjustTokenPrivileges.argtypes = [
    wintypes.HANDLE,
    wintypes.BOOL,
    ctypes.POINTER(TOKEN_PRIVILEGES),
    wintypes.DWORD,
    ctypes.c_void_p,
    ctypes.c_void_p,
]
_advapi32.AdjustTokenPrivileges.restype = wintypes.BOOL


def enable_privilege(privilege_name: str) -> bool:
    """현재 프로세스의 액세스 토큰에 지정된 Windows 보안 권한을 활성화합니다.

    Args:
        privilege_name: 활성화할 권한 이름 (예: SeDebugPrivilege, SeProfileSingleProcessPrivilege).

    Returns:
        bool: 권한 활성화 성공 시 True, 비관리자 권한 등으로 실패 시 False.
    """
    if os.name != "nt":
        return False

    token_handle = wintypes.HANDLE()
    current_process = _kernel32.GetCurrentProcess()

    if not _advapi32.OpenProcessToken(current_process, TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, ctypes.byref(token_handle)):
        _LOGGER.debug("OpenProcessToken 호출 실패 (LastError: %d)", ctypes.get_last_error())
        return False

    try:
        luid = LUID()
        if not _advapi32.LookupPrivilegeValueW(None, privilege_name, ctypes.byref(luid)):
            _LOGGER.debug("LookupPrivilegeValueW 실패 (%s): %d", privilege_name, ctypes.get_last_error())
            return False

        tp = TOKEN_PRIVILEGES()
        tp.PrivilegeCount = 1
        tp.Privileges[0].Luid = luid
        tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED

        if not _advapi32.AdjustTokenPrivileges(token_handle, False, ctypes.byref(tp), ctypes.sizeof(tp), None, None):
            _LOGGER.debug("AdjustTokenPrivileges 실패 (%s): %d", privilege_name, ctypes.get_last_error())
            return False

        # AdjustTokenPrivileges 성공이어도 ERROR_NOT_ALL_ASSIGNED(1300)일 수 있음
        last_error = ctypes.get_last_error()
        if last_error == 1300:
            _LOGGER.debug("권한이 사용자 계정에 할당되어 있지 않습니다 (1300 - %s)", privilege_name)
            return False

        _LOGGER.debug("Windows 권한 토큰 활성화 완료: %s", privilege_name)
        return True

    finally:
        _kernel32.CloseHandle(token_handle)


class MemoryTrimmer:
    """Windows 프로세스별 Working Set 물리 메모리를 정리하는 엔진 클래스."""

    def __init__(self) -> None:
        """MemoryTrimmer 인스턴스를 초기화하고 시스템 권한 획득을 시도합니다."""
        self._init_privileges()

    def _init_privileges(self) -> None:
        """타 프로세스 메모리 조작에 필요한 특권 토큰 활성화를 시도합니다."""
        debug_ok = enable_privilege(SE_DEBUG_NAME)
        profile_ok = enable_privilege(SE_PROFILE_SINGLE_PROCESS_NAME)
        _LOGGER.debug(
            "시스템 권한 토큰 초기화 결과: SeDebugPrivilege=%s, SeProfileSingleProcessPrivilege=%s",
            debug_ok,
            profile_ok,
        )

    def trim_process_working_set(self, pid: int) -> bool:
        """지정된 PID 프로세스의 Working Set을 비웁니다.

        Args:
            pid: 대상 프로세스 ID.

        Returns:
            bool: 비우기 성공 시 True, 권한 부족 또는 핸들 획득 실패 시 False.
        """
        if os.name != "nt" or pid <= 4:
            return False

        handle = _kernel32.OpenProcess(PROCESS_ACCESS_FLAGS, False, pid)
        if not handle:
            # PROCESS_QUERY_INFORMATION 폴백 시도
            handle = _kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_SET_QUOTA, False, pid)
            if not handle:
                return False

        try:
            return bool(_empty_working_set_func(handle))
        except Exception:
            return False
        finally:
            _kernel32.CloseHandle(handle)

    def trim_all_processes(self, exclude_pids: Optional[Set[int]] = None) -> Dict[str, Any]:
        """접근 가능한 모든 프로세스의 Working Set을 비워 물리 RAM을 확보합니다.

        Args:
            exclude_pids: 정리를 건너뛸 예외 PID 집합 (기본적으로 본인 및 커널 PID 제외).

        Returns:
            Dict[str, Any]: 정리 전/후 메모리 점유율, 절감 용량, 처리 통계.
        """
        current_pid = os.getpid()
        ignored_pids: Set[int] = {0, 4, current_pid}
        if exclude_pids:
            ignored_pids.update(exclude_pids)
            # 활성 게임 프로세스의 모든 하위 자식(렌더러, 오버레이, 안티치트 래퍼)까지 연쇄 보호
            for epid in list(exclude_pids):
                try:
                    p = psutil.Process(epid)
                    for child in p.children(recursive=True):
                        ignored_pids.add(child.pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

        # 포그라운드 활성 윈도우 및 직계 자식(GPU/렌더러) 프로세스 압축 제외 (사용자 입력 지연 제로화)
        fg_pid = 0
        if _PYWIN32_AVAILABLE:
            try:
                fg_hwnd = win32gui.GetForegroundWindow()
                if fg_hwnd:
                    _, fg_pid = win32process.GetWindowThreadProcessId(fg_hwnd)
                    if fg_pid > 0:
                        ignored_pids.add(fg_pid)
            except Exception:
                pass

        # 1. 정리 전 메모리 상태 측정
        vm_before = psutil.virtual_memory()
        before_percent = round(vm_before.percent, 1)
        before_used_mb = round((vm_before.total - vm_before.available) / (1024 * 1024), 1)

        success_count = 0
        failed_count = 0

        # 2. 시스템 프로세스 순회 및 Working Set 비우기 (15MB 미만 소형 프로세스 및 포그라운드 자식 스킵)
        for proc in psutil.process_iter(["pid", "ppid", "name", "memory_info"]):
            try:
                info = proc.info
                pid = info["pid"]
                if pid in ignored_pids:
                    continue

                if fg_pid > 0 and info.get("ppid") == fg_pid:
                    continue

                name = (info["name"] or "").lower()
                if name in TRIM_PROTECTED_NAMES:
                    continue

                mem_info = info.get("memory_info")
                if mem_info is not None and (mem_info.rss / (1024 * 1024)) < 15.0:
                    continue

                if self.trim_process_working_set(pid):
                    success_count += 1
                else:
                    failed_count += 1

            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                failed_count += 1
                continue
            except Exception:
                failed_count += 1
                continue

        # 커널 페이징 I/O 및 스탠바이 리스트 정리를 위한 안정화 대기
        time.sleep(0.3)

        # 3. 정리 후 메모리 상태 측정
        vm_after = psutil.virtual_memory()
        after_percent = round(vm_after.percent, 1)
        after_used_mb = round((vm_after.total - vm_after.available) / (1024 * 1024), 1)
        freed_mb = max(0.0, round(before_used_mb - after_used_mb, 1))

        result = {
            "before_percent": before_percent,
            "after_percent": after_percent,
            "before_used_mb": before_used_mb,
            "after_used_mb": after_used_mb,
            "freed_mb": freed_mb,
            "trimmed_count": success_count,
            "failed_count": failed_count,
        }

        _LOGGER.info(
            "Working Set 정리 완료: %s%% -> %s%% (확보: %s MB, 성공: %d개, 제외/실패: %d개)",
            before_percent,
            after_percent,
            freed_mb,
            success_count,
            failed_count,
        )

        return result


@dataclass
class ProcessMetricRecord:
    """단일 프로세스의 CPU 및 유휴 관측 상태 레코드."""

    pid: int
    name: str
    create_time: float = 0.0
    sample_count: int = 0
    idle_duration_sec: float = 0.0
    last_cpu_percent: float = 0.0
    last_rss_mb: float = 0.0
    first_seen_timestamp: float = field(default_factory=time.time)
    last_seen_timestamp: float = field(default_factory=time.time)


class IdleTracker:
    """psutil 첫 호출 0.0% 오탐지 방지 및 PID 재사용 방어 메커니즘이 내장된 스레드 안전 유휴 추적기."""

    def __init__(self, idle_cpu_max_percent: float = 0.5) -> None:
        """IdleTracker를 초기화합니다.

        Args:
            idle_cpu_max_percent: 유휴 상태로 간주할 최대 CPU 사용률(%). 기본값 0.5%.
        """
        self.idle_cpu_max_percent: float = idle_cpu_max_percent
        self._records: Dict[int, ProcessMetricRecord] = {}
        self._proc_cache: Dict[int, psutil.Process] = {}
        self._last_poll_time: float = time.time()
        self._lock: threading.Lock = threading.Lock()

    def update(self) -> None:
        """현재 실행 중인 프로세스의 CPU 사용량과 유휴 시간을 스레드 안전하게 샘플링합니다."""
        now = time.time()
        delta_sec = max(0.1, now - self._last_poll_time)
        self._last_poll_time = now

        current_active_pids: Set[int] = set()

        for proc in psutil.process_iter(["pid", "name"]):
            try:
                pid = proc.info["pid"]
                if pid <= 4:
                    continue

                current_active_pids.add(pid)
                name = (proc.info["name"] or "").lower()
                create_time = proc.create_time()

                # 프로세스 핸들 캐시 유지 (PID 재사용 시 캐시 객체 교체 보장)
                with self._lock:
                    existing = self._records.get(pid)
                    is_reused = existing is not None and abs(existing.create_time - create_time) > 1.0
                    proc_obj = self._proc_cache.get(pid)
                    if proc_obj is None or is_reused:
                        proc_obj = proc
                        self._proc_cache[pid] = proc_obj

                # interval=None 비동기 CPU 측정
                cpu = proc_obj.cpu_percent(interval=None)
                rss_mb = proc_obj.memory_info().rss / (1024 * 1024)

                with self._lock:
                    existing = self._records.get(pid)

                    # 신규 프로세스이거나 동일 PID가 재사용된 경우 레코드 재초기화
                    if existing is None or abs(existing.create_time - create_time) > 1.0:
                        self._records[pid] = ProcessMetricRecord(
                            pid=pid,
                            name=name,
                            create_time=create_time,
                            sample_count=1,
                            idle_duration_sec=0.0,
                            last_cpu_percent=cpu,
                            last_rss_mb=rss_mb,
                            first_seen_timestamp=now,
                            last_seen_timestamp=now,
                        )
                    else:
                        existing.sample_count += 1
                        existing.name = name
                        existing.last_cpu_percent = cpu
                        existing.last_rss_mb = rss_mb
                        existing.last_seen_timestamp = now

                        # 최소 2회 이상 샘플링된 유효 구간에서만 유휴 시간 산출
                        if existing.sample_count >= 2:
                            if cpu <= self.idle_cpu_max_percent:
                                existing.idle_duration_sec += delta_sec
                            else:
                                existing.idle_duration_sec = 0.0

            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception:
                continue

        # 종료된 프로세스 레코드 정리 (메모리 누수 방지)
        with self._lock:
            dead_pids = set(self._records.keys()) - current_active_pids
            for dead_pid in dead_pids:
                self._records.pop(dead_pid, None)
                self._proc_cache.pop(dead_pid, None)

    def is_process_idle(self, pid: int, min_idle_sec: float) -> bool:
        """해당 프로세스가 오탐지 방지 조건을 통과하고 지정된 시간 이상 유휴 상태인지 검증합니다.

        Args:
            pid: 대상 PID.
            min_idle_sec: 충족해야 하는 최소 유휴 지속 시간(초).

        Returns:
            bool: 최소 2회 이상 관측되었고 유휴 지속 시간이 min_idle_sec 이상이면 True.
        """
        with self._lock:
            rec = self._records.get(pid)
            if rec is None:
                return False

            # 최소 2회 이상 샘플링되지 않은 신규 프로세스는 유휴 판정에서 원천 제외
            if rec.sample_count < 2:
                return False

            return rec.idle_duration_sec >= min_idle_sec

    def get_record(self, pid: int) -> Optional[ProcessMetricRecord]:
        """지정된 PID의 상태 레코드를 스레드 안전하게 반환합니다."""
        with self._lock:
            return self._records.get(pid)


if __name__ == "__main__":
    print("=== MemoryTrimmer 단독 실행 검증 시작 ===")
    trimmer = MemoryTrimmer()
    stats = trimmer.trim_all_processes()

    print(
        f"정리 전: {stats['before_percent']}% -> "
        f"정리 후: {stats['after_percent']}% "
        f"(총 {stats['freed_mb']} MB 확보)"
    )
    print("=== MemoryTrimmer 단독 실행 검증 완료 ===")
