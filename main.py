"""AutoMemoryGuard 메인 진입점 모듈.

중복 실행 방지(Global Mutex), UAC 관리자 권한 자동 상승,
시스템 트레이(pystray) 및 실시간 메모리 감시 데몬(MemoryMonitorDaemon),
CustomTkinter 대시보드를 오케스트레이션합니다.
"""

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import queue
import sys
import threading
from typing import Any, Dict, Optional

# 프로젝트 루트 경로 등록 (플랫 및 서브폴더 구조 지원)
_here = Path(__file__).resolve().parent
_PROJECT_ROOT = _here if (_here / "main.py").exists() else _here.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import customtkinter as ctk

try:
    from core.config_manager import ConfigManager
    from core.memory_trimmer import MemoryTrimmer
    from core.monitor_daemon import MemoryMonitorDaemon
    from ui.dashboard import DashboardWindow
    from ui.tray_app import TrayApp
    from utils.admin import elevate_privileges, is_admin
    from utils.logger import get_logger
    from utils.startup import is_startup_enabled, toggle_startup
except ImportError:
    from config_manager import ConfigManager
    from memory_trimmer import MemoryTrimmer
    from monitor_daemon import MemoryMonitorDaemon
    from dashboard import DashboardWindow
    from tray_app import TrayApp
    from admin import elevate_privileges, is_admin
    from logger import get_logger
    from startup import is_startup_enabled, toggle_startup

_LOGGER = get_logger("Main")

# Win32 뮤텍스 상수
ERROR_ALREADY_EXISTS: int = 183
SINGLETON_MUTEX_NAME: str = r"Global\AutoMemoryGuard_Singleton_Mutex"

# 뮤텍스 핸들 보관 (가비지 컬렉션에 의해 핸들이 닫히는 현상 방지)
_MUTEX_HANDLE: Optional[int] = None

# 64비트 Windows 엄격 Win32 API 바인딩
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL


def check_and_acquire_singleton_mutex() -> bool:
    """중복 실행을 방지하기 위한 시스템 전역 네임스페이스 뮤텍스를 획득합니다.

    Returns:
        bool: 첫 번째 인스턴스이면 True, 이미 다른 인스턴스가 실행 중이면 False.
    """
    global _MUTEX_HANDLE

    if os.name != "nt":
        return True

    # 고해상도(QHD/4K) 모니터 DPI 인식 활성화
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    handle = _kernel32.CreateMutexW(None, False, SINGLETON_MUTEX_NAME)
    last_error = ctypes.get_last_error()

    if last_error == ERROR_ALREADY_EXISTS:
        _LOGGER.warning("AutoMemoryGuard 인스턴스가 이미 실행 중입니다. 중복 실행을 차단합니다.")
        if handle:
            _kernel32.CloseHandle(handle)

        user32.MessageBoxW(
            None,
            "AutoMemoryGuard가 이미 백그라운드에서 실행 중입니다.\n작업 표시줄 트레이 아이콘을 확인하세요.",
            "AutoMemoryGuard 알림",
            0x40 | 0x1000,
        )
        return False

    _MUTEX_HANDLE = handle
    return True


class AutoMemoryGuardApp:
    """AutoMemoryGuard 메인 애플리케이션 라이프사이클 관리자."""

    def __init__(self) -> None:
        """애플리케이션 환경 및 핵심 컴포넌트를 초기화합니다."""
        _LOGGER.info("=== AutoMemoryGuard 프로그램을 초기화합니다 ===")

        # 1. 설정 및 명령 큐 초기화
        self.config_manager = ConfigManager()
        self.command_queue: queue.Queue = queue.Queue()

        # 2. 백그라운드 모니터링 데몬 생성
        self.daemon = MemoryMonitorDaemon(
            config_manager=self.config_manager,
            on_status_update=self._handle_status_update,
            on_cleanup_executed=self._handle_cleanup_executed,
        )

        # 3. 메인 GUI 이벤트 루프용 숨겨진 루트 윈도우 생성
        self.root = ctk.CTk()
        self.root.withdraw()  # 화면에 띄우지 않고 큐 폴링 엔진으로만 활용

        # 4. 대시보드 인스턴스 참조
        self.dashboard_window: Optional[DashboardWindow] = None

        # 5. 게이밍 부스트 및 고정밀 타이머(0.5ms) 엔진 바인딩
        self.timer_manager = self.daemon.gaming_engine.timer_manager
        if self.config_manager.config.gaming_mode == "always":
            self.daemon.gaming_engine.activate_boost(None)

        # 6. 시스템 트레이 초기화 (시작프로그램, 알림, 게이밍 부스트 콜백 포함)
        self.tray = TrayApp(
            command_queue=self.command_queue,
            is_auto_mode_fn=lambda: self.daemon.auto_mode_enabled,
            is_startup_enabled_fn=is_startup_enabled,
            is_notify_enabled_fn=lambda: self.config_manager.config.enable_toast_notifications,
            is_high_timer_enabled_fn=lambda: self.daemon.gaming_engine.is_active,
            initial_percent=50.0,
        )

        # 7. 프로그램 시작 즉시 자체 프로세스 메모리 최소화 (Working Set 1회 비우기)
        my_pid = os.getpid()
        MemoryTrimmer().trim_process_working_set(my_pid)
        _LOGGER.debug("초기 구동 메모리 압축 완료 (PID: %d)", my_pid)

    def _handle_status_update(self, status: Dict[str, Any]) -> None:
        """데몬의 실시간 상태를 트레이 아이콘 메트릭 갱신에 동기화합니다."""
        ram_percent = status.get("memory_percent", 0.0)
        total_gb = status.get("total_mb", 0.0) / 1024.0
        used_gb = status.get("used_mb", 0.0) / 1024.0
        self.tray.update_metrics(ram_percent, used_gb, total_gb)

    def _handle_cleanup_executed(self, action: str, result: Dict[str, Any]) -> None:
        """메모리 정리 실행 완료 시 트레이 윈도우 알림을 처리합니다."""
        if not self.config_manager.config.enable_toast_notifications:
            return

        # 1단계 메모리 압축(Working Set 비우기)은 상시 무음(Silent) 처리하여 사용자 작업 방해를 차단
        if "TRIM" in action:
            return

        freed_mb = result.get("freed_mb", result.get("total_freed_mb", 0.0))
        if freed_mb <= 0.0:
            return

        # 2단계 유휴 프로세스 종료 등 프로세스 생명주기 변경 중요 이벤트만 알림
        if "KILL" in action:
            title = "AutoMemoryGuard - 2단계 스마트 종료"
            killed = result.get("killed_count", 0)
            msg = f"유휴 프로세스 {killed}개를 정리하여 {freed_mb:.1f} MB를 회수했습니다."
            self.tray.notify(title, msg)
        else:
            title = "AutoMemoryGuard 알림"
            msg = f"{freed_mb:.1f} MB의 물리 메모리를 확보했습니다."
            self.tray.notify(title, msg)

    def _on_dashboard_closed(self) -> None:
        """대시보드 창 파괴 시 참조를 비워 가비지 컬렉션을 지원합니다."""
        self.dashboard_window = None
        _LOGGER.debug("대시보드 인스턴스 참조 정리 완료.")

    def _poll_commands(self) -> None:
        """트레이 및 백그라운드 스레드로부터 전달된 명령 큐를 메인 GUI 루프에서 안전하게 처리합니다."""
        while not self.command_queue.empty():
            try:
                cmd_item = self.command_queue.get_nowait()
                cmd = cmd_item.get("command")

                if cmd == "OPEN_DASHBOARD":
                    self._show_dashboard()
                elif cmd == "MANUAL_TRIM":
                    threading.Thread(target=self.daemon.manual_trim, daemon=True).start()
                elif cmd == "MANUAL_KILL":
                    threading.Thread(target=self.daemon.manual_smart_kill, daemon=True).start()
                elif cmd == "SET_AUTO_MODE":
                    val = cmd_item.get("value", True)
                    self.daemon.set_auto_mode(val)
                    self.tray.refresh_menu()
                elif cmd == "TOGGLE_STARTUP":
                    def _toggle_worker() -> None:
                        try:
                            toggle_startup()
                            _LOGGER.info("작업 스케줄러 자동 실행 상태 토글: %s", is_startup_enabled())
                            self.tray.refresh_menu()
                        except Exception as exc:
                            _LOGGER.error("시작프로그램 토글 워커 오류: %s", exc)

                    threading.Thread(target=_toggle_worker, daemon=True).start()
                elif cmd == "TOGGLE_NOTIFY":
                    curr = self.config_manager.config.enable_toast_notifications
                    self.config_manager.config.enable_toast_notifications = not curr
                    self.config_manager.save_config()
                    _LOGGER.info("토스트 알림 활성화 상태 토글: %s", self.config_manager.config.enable_toast_notifications)
                    self.tray.refresh_menu()
                elif cmd == "TOGGLE_HIGH_TIMER":
                    if self.daemon.gaming_engine.is_active:
                        self.config_manager.set_gaming_mode("off")
                        self.daemon.gaming_engine.deactivate_boost()
                    else:
                        self.config_manager.set_gaming_mode("always")
                        self.daemon.gaming_engine.activate_boost(None)
                    _LOGGER.info("게이밍 부스트 상태 토글: %s", self.daemon.gaming_engine.is_active)
                    self.tray.refresh_menu()
                elif cmd == "EXIT_APP":
                    self.shutdown()
                    return

            except queue.Empty:
                break
            except Exception as exc:
                _LOGGER.error("명령 큐 처리 중 오류 발생: %s", exc)

        # 100ms 간격으로 계속 큐를 감시
        self.root.after(100, self._poll_commands)

    def _show_dashboard(self) -> None:
        """대시보드 창을 생성하거나 전면으로 가져옵니다."""
        if self.dashboard_window is None:
            _LOGGER.info("대시보드 창을 신규 인스턴스화합니다.")
            self.dashboard_window = DashboardWindow(
                parent=self.root,
                daemon=self.daemon,
                on_window_closed=self._on_dashboard_closed,
            )
        else:
            self.dashboard_window.lift()
            self.dashboard_window.focus_force()

    def run(self, open_dashboard_on_start: bool = False) -> None:
        """애플리케이션 메인 루프를 시작합니다."""
        # 데몬 및 트레이 시작
        self.daemon.start()
        self.tray.start()

        if open_dashboard_on_start:
            self._show_dashboard()

        # 큐 폴링 시작
        self.root.after(100, self._poll_commands)

        _LOGGER.info("AutoMemoryGuard 메인 이벤트 루프 구동 시작.")
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self.shutdown()

    def shutdown(self) -> None:
        """모든 서브시스템을 안전하게 종료하고 프로세스를 마칩니다."""
        _LOGGER.info("AutoMemoryGuard 완전 종료 시퀀스를 실행합니다.")

        # 트레이 및 데몬 정지 (데몬 stop 시 gaming_engine.deactivate_boost()가 자동 호출됨)
        self.tray.stop()
        self.daemon.stop()
        try:
            self.daemon.join(timeout=2.0)
        except Exception:
            pass

        if self.dashboard_window is not None:
            try:
                self.dashboard_window.destroy()
            except Exception:
                pass
            self.dashboard_window = None

        try:
            self.root.destroy()
        except Exception:
            pass

        # 싱글톤 뮤텍스 해제
        global _MUTEX_HANDLE
        if _MUTEX_HANDLE:
            try:
                _kernel32.CloseHandle(_MUTEX_HANDLE)
            except Exception:
                pass
            _MUTEX_HANDLE = None

        _LOGGER.info("=== AutoMemoryGuard가 안전하게 종료되었습니다 ===")
        sys.exit(0)


def main() -> None:
    """메인 진입 함수."""
    # 1. UAC 관리자 권한 확인 및 권한 상승 (뮤텍스 획득 전 우선 처리)
    allow_non_admin = "--no-admin" in sys.argv or "--no-elevate" in sys.argv
    if not is_admin() and not allow_non_admin:
        _LOGGER.info("전체 시스템 프로세스 메모리 회수를 위해 관리자 권한 상승을 시도합니다.")
        # 권한 상승 성공 시 현재 비관리자 프로세스는 종료됨
        # 만약 사용자가 UAC를 거부(아니오 클릭)하면 terminate_current=False이므로 일반 모드로 진행
        elevate_privileges(terminate_current=True)

    # 2. 중복 실행 방지 (실제 실행 프로세스가 뮤텍스 선점)
    if not check_and_acquire_singleton_mutex():
        sys.exit(0)

    app = AutoMemoryGuardApp()

    # 사용자가 프로그램을 처음 켰을 때 정상 실행 여부를 즉시 확인할 수 있도록 기본적으로 대시보드를 표시
    # 백그라운드 전용 실행 인자(--silent, --minimized)가 전달된 경우에만 창을 숨김
    start_minimized = "--silent" in sys.argv or "--minimized" in sys.argv
    app.run(open_dashboard_on_start=not start_minimized)


if __name__ == "__main__":
    main()
