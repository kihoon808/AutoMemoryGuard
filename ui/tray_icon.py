"""Windows 시스템 트레이 아이콘 관리 모듈.

Pystray 및 Pillow를 기반으로 시스템 작업 표시줄 트레이 영역에 상주하며,
현재 RAM 상태에 따른 동적 색상 인디케이터와 빠른 수동 제어 메뉴를 제공합니다.
"""

from collections.abc import Callable
import os
from pathlib import Path
import sys
import threading
from typing import Any, Dict, Optional

# 독립 실행 지원을 위한 프로젝트 루트 sys.path 등록
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from PIL import Image, ImageDraw
import pystray
from utils.logger import get_logger

_LOGGER = get_logger("TrayIcon")


def create_tray_image(color: str = "#22c55e", size: int = 64) -> Image.Image:
    """트레이 아이콘에 표시할 동적 원형 이미지를 생성합니다.

    Args:
        color: 원형 인디케이터 색상 코드 (기본값: 초록색).
        size: 이미지 픽셀 크기 (기본값: 64x64).

    Returns:
        Image.Image: 생성된 PIL Image 객체.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 외곽 원형 그림자/테두리
    padding = 6
    draw.ellipse(
        [padding - 2, padding - 2, size - padding + 2, size - padding + 2],
        fill=(30, 41, 59, 200),
    )
    # 중앙 상태 색상 원
    draw.ellipse(
        [padding, padding, size - padding, size - padding],
        fill=color,
    )
    return img


class TrayManager:
    """시스템 트레이 아이콘 및 컨텍스트 메뉴 총괄 관리자."""

    def __init__(
        self,
        on_show_window: Callable[[], None],
        on_manual_trim: Callable[[], None],
        on_manual_kill: Callable[[], None],
        on_toggle_auto: Callable[[bool], None],
        on_exit_app: Callable[[], None],
        is_auto_mode: Callable[[], bool],
    ) -> None:
        """TrayManager를 초기화합니다.

        Args:
            on_show_window: '대시보드 열기' 클릭 시 호출할 콜백.
            on_manual_trim: '즉시 1단계 압축' 클릭 시 호출할 콜백.
            on_manual_kill: '즉시 2단계 스마트 종료' 클릭 시 호출할 콜백.
            on_toggle_auto: '자동 감시 모드' 체크박스 토글 시 호출할 콜백.
            on_exit_app: '프로그램 종료' 클릭 시 호출할 콜백.
            is_auto_mode: 현재 자동 감시 모드 상태를 반환하는 콜백.
        """
        self._on_show_window = on_show_window
        self._on_manual_trim = on_manual_trim
        self._on_manual_kill = on_manual_kill
        self._on_toggle_auto = on_toggle_auto
        self._on_exit_app = on_exit_app
        self._is_auto_mode = is_auto_mode

        self._icon: Optional[pystray.Icon] = None
        self._thread: Optional[threading.Thread] = None

    def _build_menu(self) -> pystray.Menu:
        """트레이 우클릭 컨텍스트 메뉴를 구성합니다."""
        return pystray.Menu(
            pystray.MenuItem("대시보드 열기", self._handle_show_window, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("즉시 1단계 압축 (Working Set)", self._handle_manual_trim),
            pystray.MenuItem("즉시 2단계 스마트 종료 (유휴 프로세스)", self._handle_manual_kill),
            pystray.MenuItem(
                "자동 감시 활성화",
                self._handle_toggle_auto,
                checked=lambda item: self._is_auto_mode(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("프로그램 종료", self._handle_exit),
        )

    def _handle_show_window(self, icon: Any = None, item: Any = None) -> None:
        self._on_show_window()

    def _handle_manual_trim(self, icon: Any = None, item: Any = None) -> None:
        self._on_manual_trim()

    def _handle_manual_kill(self, icon: Any = None, item: Any = None) -> None:
        self._on_manual_kill()

    def _handle_toggle_auto(self, icon: Any = None, item: Any = None) -> None:
        current_state = self._is_auto_mode()
        self._on_toggle_auto(not current_state)
        if self._icon is not None:
            self._icon.update_menu()

    def _handle_exit(self, icon: Any = None, item: Any = None) -> None:
        self.stop()
        self._on_exit_app()

    def start(self) -> None:
        """백그라운드 스레드에서 트레이 아이콘을 시작합니다."""
        if self._icon is not None:
            return

        initial_image = create_tray_image("#22c55e")
        menu = self._build_menu()

        self._icon = pystray.Icon(
            name="AutoMemoryGuard",
            icon=initial_image,
            title="AutoMemoryGuard (모니터링 대기 중)",
            menu=menu,
        )

        def _run_icon() -> None:
            _LOGGER.info("시스템 트레이 루프가 시작되었습니다.")
            try:
                if self._icon is not None:
                    self._icon.run()
            except Exception as exc:
                _LOGGER.error("트레이 아이콘 구동 중 오류: %s", exc)

        self._thread = threading.Thread(target=_run_icon, name="SystemTrayThread", daemon=True)
        self._thread.start()

    def update_status(self, ram_percent: float, is_cooling: bool) -> None:
        """RAM 점유율 및 상태에 따라 트레이 아이콘 색상 및 툴팁을 갱신합니다.

        Args:
            ram_percent: 현재 메모리 점유율(%).
            is_cooling: 현재 쿨다운 진행 여부.
        """
        if self._icon is None:
            return

        # 점유율별 색상 구분
        if ram_percent >= 88.0:
            color = "#ef4444"  # 빨강 (위험/2단계)
            state_text = "위험 (2단계 임계)"
        elif ram_percent >= 82.0:
            color = "#f59e0b"  # 주황 (경고/1단계)
            state_text = "주의 (1단계 임계)"
        else:
            color = "#22c55e"  # 초록 (정상)
            state_text = "정상"

        if is_cooling:
            state_text += " [쿨다운 중]"

        tooltip = f"AutoMemoryGuard\nRAM 점유율: {ram_percent:.1f}% ({state_text})"

        try:
            self._icon.icon = create_tray_image(color)
            self._icon.title = tooltip
        except Exception:
            pass

    def notify(self, title: str, message: str) -> None:
        """Windows 알림 메시지(토스트)를 트레이 아이콘을 통해 팝업합니다."""
        if self._icon is not None:
            try:
                self._icon.notify(message, title)
            except Exception as exc:
                _LOGGER.debug("트레이 알림 전송 실패: %s", exc)

    def stop(self) -> None:
        """트레이 아이콘을 안전하게 파괴하고 스레드를 중지합니다."""
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
        _LOGGER.info("시스템 트레이 아이콘이 안전하게 종료되었습니다.")
