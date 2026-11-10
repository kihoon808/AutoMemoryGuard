"""유휴 프로세스 추적 및 스마트 프로세스 선별 종료 모듈.

2단계 임계치 초과 시 GUI 가시 윈도우 및 시스템/사용자 화이트리스트를 철저히 보호하며,
IdleTracker를 기반으로 장시간 유휴 상태인 백그라운드 프로세스 및 블로트웨어를 안전하게 종료합니다.
"""

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

# 독립 실행 지원을 위한 프로젝트 루트 sys.path 등록 (플랫 및 서브폴더 구조 지원)
_here = Path(__file__).resolve().parent
_PROJECT_ROOT = _here if (_here / "main.py").exists() else _here.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import psutil
try:
    import win32con
    import win32gui
    import win32process
    import win32service
    _PYWIN32_AVAILABLE = True
except ImportError:
    _PYWIN32_AVAILABLE = False

try:
    from core.config_manager import ConfigManager
    from core.memory_trimmer import IdleTracker
    from utils.logger import get_logger
except ImportError:
    from config_manager import ConfigManager
    from memory_trimmer import IdleTracker
    from logger import get_logger

_LOGGER = get_logger("SmartProcessKiller")

# Win32 세션 식별 API 바인딩 (Session 0 서비스 보호용, 스레드 안전 use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
_kernel32.ProcessIdToSessionId.restype = wintypes.BOOL


def is_session_zero_process(pid: int) -> bool:
    """해당 PID가 Windows 서비스 전용 세션(Session 0)에서 실행 중인지 판별합니다.

    데이터베이스(MySQL, Postgres), VPN, 전력/팬 제어 데몬 등 백그라운드 시스템 서비스가
    오종료되는 것을 원천 차단하기 위해 사용합니다.

    Args:
        pid: 검사할 프로세스 ID.

    Returns:
        bool: Session 0 프로세스이면 True, 대화형 사용자 세션이면 False.
    """
    if os.name != "nt":
        return False
    session_id = wintypes.DWORD(0)
    if _kernel32.ProcessIdToSessionId(pid, ctypes.byref(session_id)):
        return session_id.value == 0
    return False


def get_protected_tree_pids(config_manager: Optional[ConfigManager] = None) -> Set[int]:
    """사용자가 상호작용 중인 가시 윈도우(GUI 창) 및 화이트리스트 프로세스, 그리고 그 하위 자식 프로세스 트리를 모두 보호 집합으로 수집합니다.

    Default 데스크톱 전수 조사, 포그라운드 최전면 창 보호, 화이트리스트 앱(트레이 최소화 포함)의
    하위 렌더러/워커 프로세스 연쇄 전파를 통해 IDE 언어 서버, 브라우저 탭, Docker/WSL2 가상화 엔진 오종료를 원천 방지합니다.

    Returns:
        Set[int]: 가시 창, 화이트리스트 프로세스 및 그 자식 워커를 포함한 전체 보호 PID 집합.
    """
    if os.name != "nt":
        return set()

    seed_pids: Set[int] = set()

    def _window_enum_callback(hwnd: int, _extra: Any) -> bool:
        try:
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title and title.strip():
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    if pid > 0:
                        seed_pids.add(pid)
        except Exception:
            pass
        return True

    if _PYWIN32_AVAILABLE:
        # 1. 현재 사용자가 타이핑/조작 중인 최전면 포그라운드 창 무조건 보호
        try:
            fg_hwnd = win32gui.GetForegroundWindow()
            if fg_hwnd:
                _, fg_pid = win32process.GetWindowThreadProcessId(fg_hwnd)
                if fg_pid > 0:
                    seed_pids.add(fg_pid)
        except Exception:
            pass

        # 2. Default 데스크톱 명시 순회 및 핸들 안전 해제
        hdesk = None
        try:
            hdesk = win32service.OpenDesktop("Default", 0, False, win32con.MAXIMUM_ALLOWED)
            if hdesk:
                win32gui.EnumDesktopWindows(hdesk, _window_enum_callback, None)
        except Exception as exc:
            _LOGGER.debug("EnumDesktopWindows 호출 중 예외: %s", exc)
        finally:
            if hdesk is not None:
                try:
                    hdesk.Close()
                except Exception:
                    pass

        # 3. 표준 EnumWindows 폴백/병합
        try:
            win32gui.EnumWindows(_window_enum_callback, None)
        except Exception as exc:
            _LOGGER.debug("EnumWindows 호출 중 예외: %s", exc)

    # 4. 부모-자식 트리 맵 구축 및 화이트리스트 프로세스(트레이 앱/가상화 엔진)도 보호 씨앗에 편입
    parent_map: Dict[int, int] = {}
    for proc in psutil.process_iter(["pid", "ppid", "name"]):
        try:
            p_id = proc.info["pid"]
            parent_map[p_id] = proc.info["ppid"]
            if config_manager is not None:
                p_name = (proc.info["name"] or "").strip().lower()
                if config_manager.is_whitelisted(p_name):
                    seed_pids.add(p_id)
        except Exception:
            continue

    # 5. 최대 6단계 깊이로 자식/손자 프로세스 연쇄 전파 보호
    expanded_pids = set(seed_pids)
    for pid in list(parent_map.keys()):
        curr = pid
        depth = 0
        while curr in parent_map and depth < 6:
            if curr in expanded_pids:
                expanded_pids.add(pid)
                break
            curr = parent_map[curr]
            depth += 1

    return expanded_pids


def get_visible_window_pids() -> Set[int]:
    """하위 호환성을 위한 래퍼 함수."""
    return get_protected_tree_pids()


@dataclass
class TerminationCandidate:
    """종료 검토 대상 프로세스 정보."""

    pid: int
    name: str
    rss_mb: float
    idle_duration_sec: float
    is_bloatware: bool
    priority_score: float  # 높을수록 우선 종료 대상


class SmartProcessKiller:
    """화이트리스트 및 사용자 인터랙션을 보호하며 안전하게 백그라운드 프로세스를 종료하는 스마트 킬러."""

    def __init__(self, config_manager: ConfigManager, idle_tracker: Optional[IdleTracker] = None) -> None:
        """SmartProcessKiller를 초기화합니다.

        Args:
            config_manager: 시스템 설정 관리자.
            idle_tracker: 프로세스 유휴 상태 추적기 (생략 시 기본 0.5% 임계치로 생성).
        """
        self.config_manager: ConfigManager = config_manager
        self.idle_tracker: IdleTracker = (
            idle_tracker if idle_tracker is not None else IdleTracker(config_manager.config.idle_cpu_max_percent)
        )

    def find_candidates(self) -> List[TerminationCandidate]:
        """현재 시스템에서 안전하게 종료 가능한 후보 프로세스 목록을 식별하고 우선순위별로 정렬합니다.

        Returns:
            List[TerminationCandidate]: 우선순위(priority_score) 내림차순으로 정렬된 후보 목록.
        """
        cfg = self.config_manager.config
        my_pid = os.getpid()

        # 1. 사용자가 보고 있는 GUI 활성 창 및 화이트리스트 하위 자식 프로세스 전체 보호 집합 획득
        protected_tree_pids = get_protected_tree_pids(self.config_manager)

        candidates: List[TerminationCandidate] = []

        for proc in psutil.process_iter(["pid", "name"]):
            try:
                pid = proc.info["pid"]
                name = (proc.info["name"] or "").strip().lower()

                # 커널 및 자체 프로세스 제외
                if pid <= 4 or pid == my_pid:
                    continue

                # 시스템 및 사용자 보호 화이트리스트 절대 보호
                if self.config_manager.is_whitelisted(name):
                    continue

                # Windows 서비스 전용 세션(Session 0) 프로세스 절대 보호
                if is_session_zero_process(pid):
                    continue

                # GUI 가시 창 또는 활성/화이트리스트 프로세스의 자식 워커는 절대 제외
                if pid in protected_tree_pids:
                    continue

                rss_mb = proc.memory_info().rss / (1024 * 1024)

                # 유휴 상태 정보 확인 (IdleTracker)
                record = self.idle_tracker.get_record(pid)
                idle_sec = record.idle_duration_sec if record else 0.0

                is_bloatware = self.config_manager.is_target_bloatware(name)

                # 종료 후보 조건:
                # 1) 타깃 블로트웨어: 창이 없고 메모리 점유 50MB 이상이며 최소 60초 이상 유휴일 때만 종료 (활성 WebView2/OneDrive 보호)
                # 2) 일반 프로세스: idle_process_min_mb 이상 AND IdleTracker 검증 기준 idle_duration_sec 이상 유휴
                should_terminate = False
                priority = 0.0

                if is_bloatware and rss_mb >= 50.0 and self.idle_tracker.is_process_idle(pid, 60.0):
                    should_terminate = True
                    # 블로트웨어 가중치 1000점 + 메모리 점유량 비례
                    priority = 1000.0 + rss_mb
                elif (rss_mb >= cfg.idle_process_min_mb) and self.idle_tracker.is_process_idle(pid, cfg.idle_duration_sec):
                    should_terminate = True
                    priority = rss_mb + (idle_sec / 60.0)

                if should_terminate:
                    candidates.append(
                        TerminationCandidate(
                            pid=pid,
                            name=name,
                            rss_mb=round(rss_mb, 1),
                            idle_duration_sec=round(idle_sec, 1),
                            is_bloatware=is_bloatware,
                            priority_score=round(priority, 1),
                        )
                    )

            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception:
                continue

        # 우선순위 내림차순 정렬
        candidates.sort(key=lambda c: c.priority_score, reverse=True)
        return candidates

    def terminate_process(self, pid: int, timeout_sec: float = 2.0) -> Tuple[bool, float, str]:
        """단일 프로세스를 부드럽게 종료(SIGTERM)하고, 불응 시 강제 종료(SIGKILL)합니다.

        Args:
            pid: 종료할 대상 PID.
            timeout_sec: 정상 종료 대기 시간(초).

        Returns:
            Tuple[bool, float, str]: (성공 여부, 회수된 RSS 메모리 MB, 프로세스 이름).
        """
        try:
            proc = psutil.Process(pid)
            name = proc.name()
            rss_mb = proc.memory_info().rss / (1024 * 1024)

            # 1단계: 정상 종료 요청 (SIGTERM)
            proc.terminate()

            try:
                proc.wait(timeout=timeout_sec)
                _LOGGER.info("프로세스 정상 종료 완료: %s (PID: %d, 회수: %.1f MB)", name, pid, rss_mb)
                return True, round(rss_mb, 1), name
            except psutil.TimeoutExpired:
                # 2단계: 시간 초과 시 강제 종료
                _LOGGER.warning("프로세스 종료 시간 초과로 강제 종료: %s (PID: %d)", name, pid)
                proc.kill()
                proc.wait(timeout=1.0)
                _LOGGER.info("프로세스 강제 종료 완료: %s (PID: %d, 회수: %.1f MB)", name, pid, rss_mb)
                return True, round(rss_mb, 1), name

        except psutil.NoSuchProcess:
            return True, 0.0, "unknown"
        except (psutil.AccessDenied, Exception) as exc:
            _LOGGER.error("프로세스 종료 실패 (PID: %d): %s", pid, exc)
            return False, 0.0, ""

    def terminate_candidates(self, max_kills: int = 5, dry_run: bool = False) -> Dict[str, Any]:
        """선별된 종료 후보 프로세스를 안전하게 종료하고 통계를 산출합니다.

        Args:
            max_kills: 1회 사이클당 최대 종료할 프로세스 개수 (안전장치). 기본값: 5.
            dry_run: True일 경우 실제 종료 없이 시뮬레이션만 수행.

        Returns:
            Dict[str, Any]: 종료 결과 및 회수 통계 딕셔너리.
        """
        candidates = self.find_candidates()
        selected = candidates[:max_kills]

        total_freed_mb = 0.0
        terminated_list: List[Dict[str, Any]] = []

        for cand in selected:
            if dry_run:
                total_freed_mb += cand.rss_mb
                terminated_list.append(
                    {
                        "pid": cand.pid,
                        "name": cand.name,
                        "freed_mb": cand.rss_mb,
                        "is_bloatware": cand.is_bloatware,
                        "dry_run": True,
                    }
                )
            else:
                success, freed_mb, name = self.terminate_process(cand.pid)
                if success:
                    total_freed_mb += freed_mb
                    terminated_list.append(
                        {
                            "pid": cand.pid,
                            "name": name,
                            "freed_mb": freed_mb,
                            "is_bloatware": cand.is_bloatware,
                            "dry_run": False,
                        }
                    )

        result = {
            "candidate_count": len(candidates),
            "killed_count": len(terminated_list),
            "total_freed_mb": round(total_freed_mb, 1),
            "terminated": terminated_list,
            "dry_run": dry_run,
        }

        if terminated_list:
            _LOGGER.info(
                "2단계 프로세스 종료 실행 완료 (Dry-run=%s): %d개 종료, 약 %.1f MB 회수",
                dry_run,
                len(terminated_list),
                total_freed_mb,
            )

        return result

    def scan_kill_candidates(self, dry_run: bool = True, max_kills: int = 20) -> Dict[str, Any]:
        """현재 종료 후보 백그라운드 프로세스를 정밀 스캔하여 반환합니다.

        UI 대시보드 탭 2 등에서 안전하게 후보 목록을 조회하기 위한 편의 메서드입니다.

        Args:
            dry_run: 프로세스를 종료하지 않고 정보만 스캔할지 여부 (기본값: True).
            max_kills: 최대 스캔/종료 후보 수.

        Returns:
            Dict[str, Any]: 후보 프로세스 정보 및 통계.
        """
        return self.terminate_candidates(max_kills=max_kills, dry_run=dry_run)


# 하위 호환성을 위한 클래스 별칭
ProcessKiller = SmartProcessKiller


if __name__ == "__main__":
    print("=== SmartProcessKiller 단독 검증 시작 ===")
    cfg_mgr = ConfigManager()
    killer = SmartProcessKiller(cfg_mgr)

    # 1. 2회 샘플링을 통한 IdleTracker 예열
    print("[1] IdleTracker 1차 샘플링...")
    killer.idle_tracker.update()
    time.sleep(1.0)
    print("[2] IdleTracker 2차 샘플링...")
    killer.idle_tracker.update()

    # 2. 가시 창 감지 테스트
    visible = get_visible_window_pids()
    print(f"[3] 식별된 가시 윈도우 보호 PID 수: {len(visible)}개")

    # 3. 종료 후보 탐색 (dry_run)
    res = killer.terminate_candidates(max_kills=5, dry_run=True)
    print(f"[4] 감지된 후보 프로세스 수: {res['candidate_count']}개")
    for item in res["terminated"]:
        label = "[타깃 블로트웨어]" if item["is_bloatware"] else "[유휴 프로세스]"
        print(f"  - PID {item['pid']:<6} | {item['name']:<25} | 점유 메모리: {item['freed_mb']:>6.1f} MB {label}")

    print(f"[5] 예상 회수 메모리 합계: {res['total_freed_mb']} MB")
    print("=== SmartProcessKiller 단독 검증 완료 ===")
