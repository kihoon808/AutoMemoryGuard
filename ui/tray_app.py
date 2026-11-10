"""실시간 동적 시스템 트레이 모듈.

Pillow를 활용해 현재 RAM 점유율과 색상 인디케이터가 렌더링된 동적 트레이 아이콘을 생성하고,
pystray 기반의 백그라운드 트레이 스레드를 통해 메인 GUI 루프와 비동기 통신(Queue)을 수행합니다.
"""

from collections.abc import Callable
import functools
import os
from pathlib import Path
import queue
import sys
import threading
from typing import Any, Dict, Optional

# 독립 실행 지원을 위한 프로젝트 루트 sys.path 등록 (플랫 및 서브폴더 구조 지원)
_here = Path(__file__).resolve().parent
_PROJECT_ROOT = _here if (_here / "main.py").exists() else _here.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from PIL import Image, ImageDraw, ImageFont
import pystray

try:
    from utils.logger import get_logger
except ImportError:
    from logger import get_logger

_LOGGER = get_logger("TrayApp")


@functools.lru_cache(maxsize=8)
def _get_font(size: int = 24) -> ImageFont.ImageFont:
    """Windows 시스템에서 선명한 굵은 폰트를 메모리에 캐싱하여 로드합니다."""
    font_candidates = [
        "C:\\Windows\\Fonts\\segoeuib.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
        "C:\\Windows\\Fonts\\malgunbd.ttf",
        "segoeuib.ttf",
        "arialbd.ttf",
    ]
    for font_path in font_candidates:
        try:
            return ImageFont.truetype(font_path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def create_dynamic_tray_icon(ram_percent: float, size: int = 64) -> Image.Image:
    """RAM 점유율 수치와 상태별 배경색이 적용된 64x64 동적 트레이 아이콘을 생성합니다.

    Args:
        ram_percent: 현재 메모리 점유율 (%).
        size: 아이콘 픽셀 크기 (기본값: 64x64).

    Returns:
        Image.Image: 생성된 PIL Image 객체.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 1. 점유율별 배경색 분기 (75% 미만 에메랄드, 75-88% 앰버, 88% 이상 크림슨 레드)
    if ram_percent < 75.0:
        bg_color = (16, 185, 129, 255)  # #10B981
    elif ram_percent < 88.0:
        bg_color = (245, 158, 11, 255)  # #F59E0B
    else:
        bg_color = (239, 68, 68, 255)   # #EF4444

    # 2. 둥근 사각형 배경 그리기 (반경 14) 및 화이트 테두리
    padding = 2
    draw.rounded_rectangle(
        [padding, padding, size - padding, size - padding],
        radius=14,
        fill=bg_color,
        outline=(255, 255, 255, 220),
        width=2,
    )

    # 3. 중앙 텍스트 렌더링 (예: 91%)
    text = f"{int(round(ram_percent))}%"
    font = _get_font(22 if len(text) <= 3 else 18)

    # 중앙 정렬 (anchor="mm")
    center_x = size // 2
    center_y = size // 2

    # 약간의 텍스트 그림자 효과 후 본문 렌더링
    draw.text((center_x, center_y + 1), text, fill=(0, 0, 0, 160), font=font, anchor="mm")
    draw.text((center_x, center_y), text, fill=(255, 255, 255, 255), font=font, anchor="mm")

    return img


class TrayApp:
    """pystray 기반 백그라운드 시스템 트레이 제어기."""

    def __init__(
        self,
        command_queue: queue.Queue,
        is_auto_mode_fn: Callable[[], bool],
        is_startup_enabled_fn: Optional[Callable[[], bool]] = None,
        is_notify_enabled_fn: Optional[Callable[[], bool]] = None,
        is_high_timer_enabled_fn: Optional[Callable[[], bool]] = None,
        initial_percent: float = 50.0,
    ) -> None:
        """TrayApp을 초기화합니다.

        Args:
            command_queue: 메인 GUI 스레드로 커맨드를 전달할 스레드-세이프 큐.
            is_auto_mode_fn: 현재 자동 모드 상태를 반환하는 콜백.
            is_startup_enabled_fn: Windows 시작 프로그램 등록 상태를 반환하는 콜백.
            is_notify_enabled_fn: 알림 활성화 상태를 반환하는 콜백.
            is_high_timer_enabled_fn: 고정밀 게이밍 타이머 활성화 상태를 반환하는 콜백.
            initial_percent: 초기 아이콘 생성 시 표시할 메모리 점유율.
        """
        self.command_queue: queue.Queue = command_queue
        self.is_auto_mode_fn: Callable[[], bool] = is_auto_mode_fn
        self.is_startup_enabled_fn: Optional[Callable[[], bool]] = is_startup_enabled_fn
        self.is_notify_enabled_fn: Optional[Callable[[], bool]] = is_notify_enabled_fn
        self.is_high_timer_enabled_fn: Optional[Callable[[], bool]] = is_high_timer_enabled_fn

        self._icon: Optional[pystray.Icon] = None
        self._thread: Optional[threading.Thread] = None
        self._current_percent: float = initial_percent

    def _is_startup_enabled(self) -> bool:
        """시작 프로그램 등록 여부 안전 조회."""
        if self.is_startup_enabled_fn is not None:
            try:
                return bool(self.is_startup_enabled_fn())
            except Exception:
                return False
        return False

    def _is_notify_enabled(self) -> bool:
        """알림 활성화 여부 안전 조회."""
        if self.is_notify_enabled_fn is not None:
            try:
                return bool(self.is_notify_enabled_fn())
            except Exception:
                return False
        return False

    def _is_high_timer_enabled(self) -> bool:
        """고정밀 타이머 활성화 여부 안전 조회."""
        if self.is_high_timer_enabled_fn is not None:
            try:
                return bool(self.is_high_timer_enabled_fn())
            except Exception:
                return False
        return False

    def _build_menu(self) -> pystray.Menu:
        """트레이 컨텍스트 메뉴를 빌드합니다."""
        return pystray.Menu(
            pystray.MenuItem("대시보드 열기", self._on_menu_open_dashboard, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("즉시 메모리 압축 (안전 캐시 비우기)", self._on_menu_manual_trim),
            pystray.MenuItem("스마트 유휴 프로세스 정리", self._on_menu_manual_kill),
            pystray.MenuItem(
                "게이밍 초저지연 타이머 (0.5ms)",
                self._on_menu_toggle_timer,
                checked=lambda item: self._is_high_timer_enabled(),
            ),
            pystray.MenuItem(
                "실시간 자동 관리 활성화",
                self._on_menu_toggle_auto,
                checked=lambda item: self.is_auto_mode_fn(),
            ),
            pystray.MenuItem(
                "Windows 시작 시 자동 실행",
                self._on_menu_toggle_startup,
                checked=lambda item: self._is_startup_enabled(),
            ),
            pystray.MenuItem(
                "프로세스 종료 시 알림 표시",
                self._on_menu_toggle_notify,
                checked=lambda item: self._is_notify_enabled(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("종료", self._on_menu_exit),
        )

    def _on_menu_open_dashboard(self, _icon: Any = None, _item: Any = None) -> None:
        self.command_queue.put({"command": "OPEN_DASHBOARD"})

    def _on_menu_manual_trim(self, _icon: Any = None, _item: Any = None) -> None:
        self.command_queue.put({"command": "MANUAL_TRIM"})

    def _on_menu_manual_kill(self, _icon: Any = None, _item: Any = None) -> None:
        self.command_queue.put({"command": "MANUAL_KILL"})

    def _on_menu_toggle_timer(self, _icon: Any = None, _item: Any = None) -> None:
        self.command_queue.put({"command": "TOGGLE_HIGH_TIMER"})

    def _on_menu_toggle_auto(self, _icon: Any = None, _item: Any = None) -> None:
        current_val = self.is_auto_mode_fn()
        self.command_queue.put({"command": "SET_AUTO_MODE", "value": not current_val})

    def _on_menu_toggle_startup(self, _icon: Any = None, _item: Any = None) -> None:
        self.command_queue.put({"command": "TOGGLE_STARTUP"})

    def _on_menu_toggle_notify(self, _icon: Any = None, _item: Any = None) -> None:
        self.command_queue.put({"command": "TOGGLE_NOTIFY"})

    def _on_menu_exit(self, _icon: Any = None, _item: Any = None) -> None:
        self.stop()
        self.command_queue.put({"command": "EXIT_APP"})

    def notify(self, title: str, message: str) -> None:
        """Windows 시스템 트레이 풍선/토스트 알림을 표시합니다."""
        if self._icon is None:
            return
        try:
            if getattr(self._icon, "HAS_NOTIFICATION", True):
                self._icon.notify(message, title)
        except Exception as exc:
            _LOGGER.debug("트레이 알림 표시 중 예외 발생: %s", exc)

    def refresh_menu(self) -> None:
        """외부(Main 스레드)에서 상태 변경 완료 후 트레이 메뉴 체크 상태를 안전하게 갱신합니다."""
        if self._icon is not None:
            try:
                self._icon.update_menu()
            except Exception:
                pass

    def start(self) -> None:
        """pystray 비동기 모드(run_detached)로 트레이 아이콘을 시작합니다."""
        if self._icon is not None:
            return

        initial_img = create_dynamic_tray_icon(self._current_percent)
        menu = self._build_menu()

        self._icon = pystray.Icon(
            name="AutoMemoryGuard",
            icon=initial_img,
            title=f"AutoMemoryGuard - RAM: {self._current_percent:.1f}%",
            menu=menu,
        )

        try:
            _LOGGER.info("시스템 트레이 run_detached 구동 시작.")
            self._icon.run_detached()
        except Exception as exc:
            _LOGGER.error("트레이 아이콘 실행 중 오류: %s", exc)

    def update_metrics(self, ram_percent: float, used_gb: float, total_gb: float) -> None:
        """현재 RAM 지표를 수신하여 아이콘 이미지와 툴팁 텍스트를 동적으로 갱신합니다.

        Args:
            ram_percent: 현재 메모리 점유율 (%).
            used_gb: 사용 중인 물리 메모리 (GB).
            total_gb: 전체 물리 메모리 (GB).
        """
        if self._icon is None:
            return

        old_int = int(round(self._current_percent))
        new_int = int(round(ram_percent))
        self._current_percent = ram_percent
        tooltip = f"AutoMemoryGuard - RAM: {ram_percent:.1f}% ({used_gb:.1f} / {total_gb:.1f} GB)"

        try:
            self._icon.title = tooltip
            if old_int != new_int:
                new_img = create_dynamic_tray_icon(ram_percent)
                self._icon.icon = new_img
        except Exception:
            pass

    def stop(self) -> None:
        """트레이 아이콘을 안전하게 정리합니다."""
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
        _LOGGER.info("시스템 트레이가 안전하게 종료되었습니다.")


if __name__ == "__main__":
    print("=== TrayApp 아이콘 생성 단위 테스트 ===")
    test_q: queue.Queue = queue.Queue()
    app = TrayApp(test_q, lambda: True, initial_percent=82.5)

    img = create_dynamic_tray_icon(82.5)
    print(f"아이콘 생성 성공: 크기={img.size}, 포맷={img.format}, 모드={img.mode}")
    print("=== TrayApp 테스트 완료 ===")
