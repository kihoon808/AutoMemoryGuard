"""CustomTkinter 기반 3탭 모던 다크모드 대시보드 모듈.

탭 1: 실시간 물리 RAM 게이지, 원클릭 메모리 압축/프로세스 정리, 1/2단계 임계치 슬라이더
탭 2: 스마트 프로세스 감시 후보 스캔 및 사용자 화이트리스트 실시간 추가/삭제
탭 3: 실시간 메모리 회수 및 이벤트 로깅 뷰어

창 종료(X) 시 위젯 메모리를 완전 반환(destroy)하고 gc.collect() 및
자기 자신의 Working Set을 비워 초경량 트레이 모드로 복귀합니다.
"""

import gc
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

# 독립 실행 지원을 위한 프로젝트 루트 sys.path 등록 (플랫 및 서브폴더 구조 지원)
_here = Path(__file__).resolve().parent
_PROJECT_ROOT = _here if (_here / "main.py").exists() else _here.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import customtkinter as ctk

try:
    from core.config_manager import ConfigManager
    from core.memory_trimmer import MemoryTrimmer
    from core.monitor_daemon import MemoryMonitorDaemon
    from utils.admin import is_admin
    from utils.logger import get_logger, get_recent_logs
except ImportError:
    from config_manager import ConfigManager
    from memory_trimmer import MemoryTrimmer
    from monitor_daemon import MemoryMonitorDaemon
    from admin import is_admin
    from logger import get_logger, get_recent_logs

_LOGGER = get_logger("DashboardUI")

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


class DashboardWindow(ctk.CTkToplevel):
    """3탭 구조의 초경량 고성능 메모리 관리 대시보드 윈도우."""

    def __init__(
        self,
        parent: ctk.CTk,
        daemon: MemoryMonitorDaemon,
        on_window_closed: Optional[Callable[[], None]] = None,
    ) -> None:
        """DashboardWindow를 초기화합니다.

        Args:
            parent: 메인 루트 윈도우.
            daemon: 구동 중인 메모리 모니터링 데몬 객체.
            on_window_closed: 창 종료 시 호출할 콜백.
        """
        super().__init__(parent)

        self.daemon: MemoryMonitorDaemon = daemon
        self.on_window_closed: Optional[Callable[[], None]] = on_window_closed
        self.trimmer: MemoryTrimmer = MemoryTrimmer()

        self.title("AutoMemoryGuard - System Dashboard")
        self.geometry("780x580")
        self.minsize(740, 540)

        # 윈도우 타이틀바 아이콘 적용 (PyInstaller _MEIPASS 완벽 지원)
        icon_path = Path(getattr(sys, "_MEIPASS", _PROJECT_ROOT)) / "assets" / "icon.ico"
        if icon_path.exists():
            try:
                self.iconbitmap(str(icon_path))
            except Exception:
                pass

        # 닫기(X) 이벤트 핸들러 바인딩
        self.protocol("WM_DELETE_WINDOW", self.close_and_trim_self)

        # 내부 상태 캐시
        self._last_log_hash: str = ""
        self._selected_candidate_name: Optional[str] = None
        self._selected_whitelist_name: Optional[str] = None
        self._save_timer_id: Optional[str] = None

        # UI 위젯 구축
        self._build_ui()

        # 실시간 상태 갱신 타이머 시작 (500ms 주기)
        self._is_active = True
        self._schedule_realtime_updates()

        # 창 표시 및 전면 포커스
        self.lift()
        self.focus_force()

    def _build_ui(self) -> None:
        """대시보드 메인 레이아웃 및 3개 탭뷰를 생성합니다."""
        # 1. 최상단 헤더
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(12, 6))

        title_lbl = ctk.CTkLabel(
            header,
            text="AutoMemoryGuard",
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        title_lbl.pack(side="left")

        ver_lbl = ctk.CTkLabel(
            header,
            text="v1.0 (16GB RAM Optimized)",
            font=ctk.CTkFont(size=12),
            text_color="#94a3b8",
        )
        ver_lbl.pack(side="left", padx=(8, 0), pady=(3, 0))

        # 관리자 권한 뱃지
        admin_ok = is_admin()
        badge_text = " Administrator " if admin_ok else " User Mode "
        badge_bg = "#15803d" if admin_ok else "#475569"
        badge_fg = "#bbf7d0" if admin_ok else "#e2e8f0"

        badge = ctk.CTkLabel(
            header,
            text=badge_text,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=badge_bg,
            text_color=badge_fg,
            corner_radius=6,
        )
        badge.pack(side="right")

        # 2. 3탭 탭뷰 컨테이너
        self.tabview = ctk.CTkTabview(self, corner_radius=10)
        self.tabview.pack(fill="both", expand=True, padx=20, pady=(0, 14))

        self.tab_overview = self.tabview.add("실시간 현황 & 제어")
        self.tab_processes = self.tabview.add("프로세스 감시 & 화이트리스트")
        self.tab_logs = self.tabview.add("실행 로그")

        self._build_tab_overview()
        self._build_tab_processes()
        self._build_tab_logs()

    # -------------------------------------------------------------------------
    # 탭 1: 실시간 현황 & 제어
    # -------------------------------------------------------------------------
    def _build_tab_overview(self) -> None:
        """탭 1 위젯 구성."""
        container = ctk.CTkFrame(self.tab_overview, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=12, pady=8)

        # 메모리 게이지 카드
        card = ctk.CTkFrame(container, corner_radius=10, fg_color="#1e293b")
        card.pack(fill="x", pady=(0, 10))

        top_row = ctk.CTkFrame(card, fg_color="transparent")
        top_row.pack(fill="x", padx=16, pady=(12, 4))

        self.ram_percent_lbl = ctk.CTkLabel(
            top_row,
            text="--.-%",
            font=ctk.CTkFont(size=32, weight="bold"),
            text_color="#38bdf8",
        )
        self.ram_percent_lbl.pack(side="left")

        self.ram_usage_lbl = ctk.CTkLabel(
            top_row,
            text="사용 중 0.0 GB / 0.0 GB",
            font=ctk.CTkFont(size=13),
            text_color="#94a3b8",
        )
        self.ram_usage_lbl.pack(side="right", pady=(10, 0))

        # 프로그레스 바
        self.progress_bar = ctk.CTkProgressBar(card, height=12, corner_radius=6)
        self.progress_bar.pack(fill="x", padx=16, pady=(0, 8))
        self.progress_bar.set(0.0)

        # 게이지 하단 메트릭
        bottom_row = ctk.CTkFrame(card, fg_color="transparent")
        bottom_row.pack(fill="x", padx=16, pady=(0, 12))

        self.cumulative_freed_lbl = ctk.CTkLabel(
            bottom_row,
            text="누적 확보량: 0.0 MB",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#34d399",
        )
        self.cumulative_freed_lbl.pack(side="left")

        self.daemon_status_lbl = ctk.CTkLabel(
            bottom_row,
            text="상태: 감시 활성",
            font=ctk.CTkFont(size=12),
            text_color="#94a3b8",
        )
        self.daemon_status_lbl.pack(side="right")

        # 원클릭 수동 액션 버튼 행
        action_row = ctk.CTkFrame(container, fg_color="transparent")
        action_row.pack(fill="x", pady=(0, 10))

        self.btn_trim = ctk.CTkButton(
            action_row,
            text="⚡ 즉시 메모리 압축 (Working Set)",
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#0284c7",
            hover_color="#0369a1",
            height=34,
            command=self._handle_manual_trim,
        )
        self.btn_trim.pack(side="left", expand=True, fill="x", padx=(0, 6))

        self.btn_kill = ctk.CTkButton(
            action_row,
            text="🧹 유휴 프로세스 정밀 스캔 & 정리",
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#475569",
            hover_color="#334155",
            height=34,
            command=self._handle_manual_kill,
        )
        self.btn_kill.pack(side="left", expand=True, fill="x", padx=(6, 0))

        # 게이밍 쉴드 (Gaming Shield : 0.5ms 타이머 + EcoQoS 격리) 카드
        game_card = ctk.CTkFrame(container, corner_radius=10, fg_color="#1e293b")
        game_card.pack(fill="x", pady=(0, 10))

        game_header = ctk.CTkFrame(game_card, fg_color="transparent")
        game_header.pack(fill="x", padx=16, pady=(10, 4))

        game_title = ctk.CTkLabel(
            game_header,
            text="🎮 게이밍 쉴드 (0.50ms 타이머 잠금 · 백그라운드 EcoQoS 격리)",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#f8fafc",
        )
        game_title.pack(side="left")

        current_mode = self.daemon.config_manager.config.gaming_mode
        mode_map = {"auto": "자동 감지", "always": "상시 켬", "off": "끔"}
        initial_val = mode_map.get(current_mode, "자동 감지")

        self.gaming_mode_seg = ctk.CTkSegmentedButton(
            game_header,
            values=["자동 감지", "상시 켬", "끔"],
            command=self._on_gaming_mode_changed,
            font=ctk.CTkFont(size=11),
            width=180,
            height=24,
        )
        self.gaming_mode_seg.set(initial_val)
        self.gaming_mode_seg.pack(side="right")

        game_info_row = ctk.CTkFrame(game_card, fg_color="transparent")
        game_info_row.pack(fill="x", padx=16, pady=(0, 10))

        self.gaming_status_lbl = ctk.CTkLabel(
            game_info_row,
            text="🎮 감지 대기 중 (게임 실행 시 0.50ms 자동 잠금 및 EcoQoS 격리)",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8",
        )
        self.gaming_status_lbl.pack(side="left")

        self.timer_res_lbl = ctk.CTkLabel(
            game_info_row,
            text="타이머: -- ms",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#38bdf8",
        )
        self.timer_res_lbl.pack(side="right")

        # 실시간 임계치 설정 카드
        slider_card = ctk.CTkFrame(container, corner_radius=10, fg_color="#1e293b")
        slider_card.pack(fill="x", pady=(0, 4))

        cfg = self.daemon.config_manager.config

        # 1단계 슬라이더 (70 - 95%)
        s1_row = ctk.CTkFrame(slider_card, fg_color="transparent")
        s1_row.pack(fill="x", padx=16, pady=(10, 2))

        self.s1_title_lbl = ctk.CTkLabel(
            s1_row,
            text=f"1단계 압축 임계치: {cfg.stage1_trim_threshold_percent:.1f}%",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.s1_title_lbl.pack(side="left")

        self.slider_stage1 = ctk.CTkSlider(
            slider_card,
            from_=70.0,
            to=95.0,
            number_of_steps=50,
            command=self._on_slider_stage1_change,
        )
        self.slider_stage1.set(cfg.stage1_trim_threshold_percent)
        self.slider_stage1.pack(fill="x", padx=16, pady=(0, 8))

        # 2단계 슬라이더 (80 - 98%)
        s2_row = ctk.CTkFrame(slider_card, fg_color="transparent")
        s2_row.pack(fill="x", padx=16, pady=(4, 2))

        self.s2_title_lbl = ctk.CTkLabel(
            s2_row,
            text=f"2단계 종료 임계치: {cfg.stage2_kill_threshold_percent:.1f}%",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.s2_title_lbl.pack(side="left")

        self.slider_stage2 = ctk.CTkSlider(
            slider_card,
            from_=80.0,
            to=98.0,
            number_of_steps=36,
            command=self._on_slider_stage2_change,
        )
        self.slider_stage2.set(cfg.stage2_kill_threshold_percent)
        self.slider_stage2.pack(fill="x", padx=16, pady=(0, 12))

    def _schedule_debounced_save(self) -> None:
        """슬라이더 조작 시 400ms 동안 추가 조작이 없으면 디스크에 최종 1회 저장합니다."""
        if getattr(self, "_save_timer_id", None) is not None:
            try:
                self.after_cancel(self._save_timer_id)
            except Exception:
                pass
        self._save_timer_id = self.after(
            400,
            lambda: self.daemon.config_manager.update_thresholds(
                self.daemon.config_manager.config.stage1_trim_threshold_percent,
                self.daemon.config_manager.config.stage2_kill_threshold_percent,
            ),
        )

    def _on_slider_stage1_change(self, val: float) -> None:
        """1단계 임계치 슬라이더 실시간 반응 (디바운싱 저장)."""
        s1 = round(float(val), 1)
        s2 = self.daemon.config_manager.config.stage2_kill_threshold_percent
        if s1 >= s2:
            s1 = s2 - 1.0
            self.slider_stage1.set(s1)

        self.s1_title_lbl.configure(text=f"1단계 압축 임계치: {s1:.1f}%")
        self.daemon.config_manager.config.stage1_trim_threshold_percent = s1
        self._schedule_debounced_save()

    def _on_slider_stage2_change(self, val: float) -> None:
        """2단계 임계치 슬라이더 실시간 반응 (디바운싱 저장)."""
        s2 = round(float(val), 1)
        s1 = self.daemon.config_manager.config.stage1_trim_threshold_percent
        if s2 <= s1:
            s2 = s1 + 1.0
            self.slider_stage2.set(s2)

        self.s2_title_lbl.configure(text=f"2단계 종료 임계치: {s2:.1f}%")
        self.daemon.config_manager.config.stage2_kill_threshold_percent = s2
        self._schedule_debounced_save()

    def _on_gaming_mode_changed(self, value: str) -> None:
        """게이밍 부스트 모드 변경 콜백."""
        rev_map = {"자동 감지": "auto", "상시 켬": "always", "끔": "off"}
        mode = rev_map.get(value, "auto")
        self.daemon.config_manager.set_gaming_mode(mode)
        if mode == "always":
            self.daemon.gaming_engine.activate_boost(None)
        elif mode == "off":
            self.daemon.gaming_engine.deactivate_boost()
        _LOGGER.info("UI에서 게이밍 모드를 '%s'(%s)로 변경했습니다.", value, mode)

    def _handle_manual_trim(self) -> None:
        """수동 1단계 압축 비동기 실행."""
        self.btn_trim.configure(state="disabled", text="압축 실행 중...")

        def _work() -> None:
            try:
                self.daemon.manual_trim()
            finally:
                if getattr(self, "_is_active", False):
                    try:
                        self.after(600, lambda: self.btn_trim.configure(state="normal", text="⚡ 즉시 메모리 압축 (Working Set)"))
                    except Exception:
                        pass

        threading.Thread(target=_work, daemon=True).start()

    def _handle_manual_kill(self) -> None:
        """수동 2단계 스마트 종료 비동기 실행."""
        self.btn_kill.configure(state="disabled", text="정리 실행 중...")

        def _work() -> None:
            try:
                self.daemon.manual_smart_kill()
            finally:
                if getattr(self, "_is_active", False):
                    try:
                        self.after(600, lambda: self.btn_kill.configure(state="normal", text="🧹 유휴 프로세스 정밀 스캔 & 정리"))
                    except Exception:
                        pass

        threading.Thread(target=_work, daemon=True).start()

    # -------------------------------------------------------------------------
    # 탭 2: 프로세스 감시 & 화이트리스트
    # -------------------------------------------------------------------------
    def _build_tab_processes(self) -> None:
        """탭 2 위젯 구성."""
        container = ctk.CTkFrame(self.tab_processes, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=10, pady=8)

        # 좌측: 종료 후보 백그라운드 프로세스 목록
        left_col = ctk.CTkFrame(container, fg_color="#1e293b", corner_radius=10)
        left_col.pack(side="left", fill="both", expand=True, padx=(0, 6), pady=0)

        cand_header = ctk.CTkFrame(left_col, fg_color="transparent")
        cand_header.pack(fill="x", padx=12, pady=(10, 4))

        ctk.CTkLabel(
            cand_header,
            text="종료 후보 백그라운드 프로세스",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(side="left")

        btn_rescan = ctk.CTkButton(
            cand_header,
            text="새로고침",
            width=64,
            height=24,
            font=ctk.CTkFont(size=11),
            command=self._refresh_candidates_list,
        )
        btn_rescan.pack(side="right")

        self.candidate_listbox = ctk.CTkScrollableFrame(left_col, fg_color="#0f172a", corner_radius=6)
        self.candidate_listbox.pack(fill="both", expand=True, padx=12, pady=6)

        self.btn_protect_selected = ctk.CTkButton(
            left_col,
            text="🛡️ 선택 프로세스 보호 목록 추가",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#059669",
            hover_color="#047857",
            height=30,
            command=self._add_selected_candidate_to_whitelist,
        )
        self.btn_protect_selected.pack(fill="x", padx=12, pady=(4, 10))

        # 우측: 등록된 사용자 화이트리스트 관리
        right_col = ctk.CTkFrame(container, fg_color="#1e293b", corner_radius=10)
        right_col.pack(side="right", fill="both", expand=True, padx=(6, 0), pady=0)

        white_header = ctk.CTkFrame(right_col, fg_color="transparent")
        white_header.pack(fill="x", padx=12, pady=(10, 4))

        ctk.CTkLabel(
            white_header,
            text="보호 화이트리스트 (user_whitelist)",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(side="left")

        # 입력창 및 추가 버튼
        input_row = ctk.CTkFrame(right_col, fg_color="transparent")
        input_row.pack(fill="x", padx=12, pady=(0, 6))

        self.entry_whitelist = ctk.CTkEntry(
            input_row,
            placeholder_text="예: notepad.exe",
            height=28,
            font=ctk.CTkFont(size=12),
        )
        self.entry_whitelist.pack(side="left", fill="x", expand=True, padx=(0, 6))

        btn_add = ctk.CTkButton(
            input_row,
            text="추가",
            width=50,
            height=28,
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._handle_add_custom_whitelist,
        )
        btn_add.pack(side="right")

        self.whitelist_scroll = ctk.CTkScrollableFrame(right_col, fg_color="#0f172a", corner_radius=6)
        self.whitelist_scroll.pack(fill="both", expand=True, padx=12, pady=4)

        self.btn_remove_whitelist = ctk.CTkButton(
            right_col,
            text="🗑️ 선택 항목 보호 해제",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#dc2626",
            hover_color="#b91c1c",
            height=30,
            command=self._handle_remove_selected_whitelist,
        )
        self.btn_remove_whitelist.pack(fill="x", padx=12, pady=(4, 10))

        # 초기 목록 채우기
        self._refresh_whitelist_ui()
        self._refresh_candidates_list()

    def _refresh_whitelist_ui(self) -> None:
        """등록된 화이트리스트 항목 UI 리스트 재구성."""
        for widget in self.whitelist_scroll.winfo_children():
            widget.destroy()

        whitelist = sorted(self.daemon.config_manager.config.user_whitelist)
        self._whitelist_radio_var = ctk.StringVar(value="")

        if not whitelist:
            ctk.CTkLabel(
                self.whitelist_scroll,
                text="등록된 사용자 보호 항목이 없습니다.",
                text_color="#64748b",
                font=ctk.CTkFont(size=11),
            ).pack(pady=10)
            return

        for p_name in whitelist:
            rb = ctk.CTkRadioButton(
                self.whitelist_scroll,
                text=p_name,
                value=p_name,
                variable=self._whitelist_radio_var,
                font=ctk.CTkFont(size=11),
                command=self._on_select_whitelist_item,
            )
            rb.pack(anchor="w", padx=6, pady=2)

    def _on_select_whitelist_item(self) -> None:
        self._selected_whitelist_name = self._whitelist_radio_var.get()

    def _handle_add_custom_whitelist(self) -> None:
        """입력창에서 사용자 화이트리스트 추가."""
        text = self.entry_whitelist.get().strip()
        if text:
            if not text.endswith(".exe") and "." not in text:
                text += ".exe"
            self.daemon.config_manager.add_user_whitelist(text)
            self.entry_whitelist.delete(0, "end")
            self._refresh_whitelist_ui()
            self._refresh_candidates_list()

    def _handle_remove_selected_whitelist(self) -> None:
        """선택된 화이트리스트 항목 제거."""
        if self._selected_whitelist_name:
            self.daemon.config_manager.remove_user_whitelist(self._selected_whitelist_name)
            self._selected_whitelist_name = None
            self._refresh_whitelist_ui()
            self._refresh_candidates_list()

    def _refresh_candidates_list(self) -> None:
        """종료 후보 백그라운드 프로세스 비동기 스캔 후 목록 갱신."""
        def _worker() -> None:
            try:
                res = self.daemon.killer.scan_kill_candidates(dry_run=True, max_kills=20)
                candidates = res.get("terminated", [])
                self.after(0, lambda: self._populate_candidates(candidates))
            except Exception as exc:
                _LOGGER.debug("후보 스캔 중 예외: %s", exc)

        threading.Thread(target=_worker, daemon=True).start()

    def _populate_candidates(self, candidates: List[Dict[str, Any]]) -> None:
        """스캔 결과를 좌측 리스트에 렌더링."""
        for widget in self.candidate_listbox.winfo_children():
            widget.destroy()

        self._candidate_radio_var = ctk.StringVar(value="")

        if not candidates:
            ctk.CTkLabel(
                self.candidate_listbox,
                text="현재 종료 대상 백그라운드 프로세스가 없습니다.\n(시스템 상태 양호)",
                text_color="#64748b",
                font=ctk.CTkFont(size=11),
            ).pack(pady=20)
            return

        for item in candidates:
            p_name = item.get("name", "unknown")
            rss_mb = item.get("freed_mb", 0.0)
            is_bloat = item.get("is_bloatware", False)
            tag = " [타깃]" if is_bloat else " [유휴]"

            rb = ctk.CTkRadioButton(
                self.candidate_listbox,
                text=f"{p_name:<20} ({rss_mb:.1f} MB){tag}",
                value=p_name,
                variable=self._candidate_radio_var,
                font=ctk.CTkFont(family="Consolas", size=11),
                command=self._on_select_candidate_item,
            )
            rb.pack(anchor="w", padx=6, pady=3)

    def _on_select_candidate_item(self) -> None:
        self._selected_candidate_name = self._candidate_radio_var.get()

    def _add_selected_candidate_to_whitelist(self) -> None:
        """선택된 종료 후보 프로세스를 화이트리스트에 즉시 추가."""
        if self._selected_candidate_name:
            self.daemon.config_manager.add_user_whitelist(self._selected_candidate_name)
            self._selected_candidate_name = None
            self._refresh_whitelist_ui()
            self._refresh_candidates_list()

    # -------------------------------------------------------------------------
    # 탭 3: 실행 로그
    # -------------------------------------------------------------------------
    def _build_tab_logs(self) -> None:
        """탭 3 위젯 구성."""
        container = ctk.CTkFrame(self.tab_logs, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=10, pady=8)

        log_top = ctk.CTkFrame(container, fg_color="transparent")
        log_top.pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(
            log_top,
            text="실시간 인메모리 시스템 및 정리 로그 (최근 100건)",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(side="left")

        self.log_box = ctk.CTkTextbox(
            container,
            corner_radius=8,
            fg_color="#0f172a",
            text_color="#e2e8f0",
            font=ctk.CTkFont(family="Consolas", size=11),
        )
        self.log_box.pack(fill="both", expand=True, pady=(0, 4))
        self.log_box.configure(state="disabled")

    # -------------------------------------------------------------------------
    # 실시간 모니터링 동기화 및 메모리 절약 창 닫기 처리
    # -------------------------------------------------------------------------
    def _schedule_realtime_updates(self) -> None:
        """500ms 주기로 RAM 상태 및 로그 화면을 최신화합니다."""
        if not self._is_active:
            return

        try:
            # 1. RAM 상태 수치 반영
            import psutil
            mem = psutil.virtual_memory()

            pct = round(mem.percent, 1)
            used_gb = round((mem.total - mem.available) / (1024**3), 2)
            total_gb = round(mem.total / (1024**3), 2)

            self.ram_percent_lbl.configure(text=f"{pct:.1f}%")
            self.ram_usage_lbl.configure(text=f"사용 중 {used_gb:.1f} GB / {total_gb:.1f} GB ({pct:.1f}%)")

            self.progress_bar.set(max(0.0, min(1.0, pct / 100.0)))

            if pct >= 88.0:
                self.progress_bar.configure(progress_color="#ef4444")
                self.ram_percent_lbl.configure(text_color="#f87171")
            elif pct >= 82.0:
                self.progress_bar.configure(progress_color="#f59e0b")
                self.ram_percent_lbl.configure(text_color="#fbbf24")
            else:
                self.progress_bar.configure(progress_color="#10b981")
                self.ram_percent_lbl.configure(text_color="#38bdf8")

            # 누적 확보량 갱신 (데몬 인스턴스에서 영속 유지)
            self.cumulative_freed_lbl.configure(
                text=f"누적 확보량: {self.daemon.cumulative_freed_mb:.1f} MB (최근: {self.daemon.last_freed_mb:.1f} MB)"
            )

            # 데몬 쿨다운 상태 표시
            if not self.daemon.auto_mode_enabled:
                self.daemon_status_lbl.configure(text="상태: 자동 감시 일시정지", text_color="#94a3b8")
            elif self.daemon.is_cooling_down:
                rem = self.daemon.get_cooldown_remaining_sec()
                self.daemon_status_lbl.configure(text=f"상태: 쿨다운 중 ({rem:.0f}초 대기)", text_color="#f59e0b")
            else:
                self.daemon_status_lbl.configure(text="상태: 실시간 감시 활성", text_color="#10b981")

            # 게이밍 쉴드 상태 반영
            mode_map = {"auto": "자동 감지", "always": "상시 켬", "off": "끔"}
            target_seg = mode_map.get(self.daemon.config_manager.config.gaming_mode, "자동 감지")
            if hasattr(self, "gaming_mode_seg") and self.gaming_mode_seg.get() != target_seg:
                self.gaming_mode_seg.set(target_seg)

            gaming_active = self.daemon.gaming_engine.is_active
            res_ms = self.daemon.gaming_engine.timer_manager.current_resolution_ms
            self.timer_res_lbl.configure(text=f"타이머: {res_ms:.2f} ms")

            if gaming_active:
                game_name = self.daemon.gaming_engine.active_game_name or "수동 상시 가동"
                self.gaming_status_lbl.configure(
                    text=f"🔥 게이밍 쉴드 가동 중: {game_name} (0.50ms 고정 · 백그라운드 EcoQoS 밀어내기)",
                    text_color="#34d399",
                )
                self.timer_res_lbl.configure(text_color="#34d399")
            else:
                self.gaming_status_lbl.configure(
                    text="🎮 감지 대기 중 (게임 실행 시 0.50ms 자동 잠금 및 EcoQoS 격리)",
                    text_color="#94a3b8",
                )
                self.timer_res_lbl.configure(text_color="#38bdf8")

            # 2. 로그 뷰어 갱신
            recent = get_recent_logs(80)
            log_str = "\n".join(recent)
            if log_str != self._last_log_hash:
                self._last_log_hash = log_str
                self.log_box.configure(state="normal")
                self.log_box.delete("1.0", "end")
                self.log_box.insert("end", log_str)
                self.log_box.see("end")
                self.log_box.configure(state="disabled")

        except Exception as exc:
            _LOGGER.debug("대시보드 실시간 갱신 중 예외: %s", exc)

        if self._is_active:
            self.after(500, self._schedule_realtime_updates)

    def close_and_trim_self(self) -> None:
        """대시보드 창을 완전히 destroy하고, Python GC 및 자체 Working Set을 비워 초경량 복귀를 수행합니다."""
        _LOGGER.info("대시보드 닫기 요청: GUI 위젯 완전 파괴 및 Working Set 압축 시작")
        self._is_active = False

        if getattr(self, "_save_timer_id", None) is not None:
            try:
                self.after_cancel(self._save_timer_id)
            except Exception:
                pass
            self._save_timer_id = None

        # 1. Toplevel 윈도우 파괴
        try:
            self.destroy()
        except Exception:
            pass

        # 2. Python 가비지 컬렉터 강제 구동
        gc.collect()

        # 3. 본인 프로세스 물리 RAM Working Set 비우기
        my_pid = os.getpid()
        self.trimmer.trim_process_working_set(my_pid)
        _LOGGER.info("자체 프로세스(PID: %d) Working Set 플러시 완료 (초경량 트레이 상태 복귀)", my_pid)

        # 4. 상위 제어기에 종료 통보
        if self.on_window_closed is not None:
            self.on_window_closed()


if __name__ == "__main__":
    print("=== DashboardWindow 단독 테스트 시작 ===")
    root = ctk.CTk()
    root.withdraw()

    cfg_m = ConfigManager()
    d = MemoryMonitorDaemon(cfg_m)
    d.start()

    def _closed() -> None:
        print("대시보드 창이 닫혔습니다. 루트를 종료합니다.")
        d.stop()
        root.quit()

    dash = DashboardWindow(root, d, on_window_closed=_closed)
    root.mainloop()
    d.join(timeout=2.0)
    print("=== DashboardWindow 단독 테스트 종료 ===")
