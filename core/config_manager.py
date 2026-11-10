"""설정 관리자 모듈.

시스템 메모리 감시 임계치, 쿨다운 시간, 화이트리스트 및 타깃 블로트웨어 목록을
포함하는 설정(settings.json)을 로드, 저장, 검증하고 안전하게 관리합니다.
"""

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from typing import Any, Dict, List, Optional, Set

# 독립 실행 시 상위 프로젝트 루트 경로를 sys.path에 등록 (플랫 및 서브폴더 구조 지원)
_here = Path(__file__).resolve().parent
_PROJECT_ROOT = _here if (_here / "main.py").exists() else _here.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from utils.logger import get_logger
except ImportError:
    from logger import get_logger

_LOGGER = get_logger("ConfigManager")


def _get_project_root() -> Path:
    """프로젝트 루트 디렉토리 경로를 반환합니다 (PyInstaller 패키징 및 플랫 배포 환경 완벽 지원)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    here = Path(__file__).resolve().parent
    if (here / "main.py").exists():
        return here
    return here.parent


# 기본 화이트리스트 및 타깃 프로세스 목록 정의 (소문자 기준)
DEFAULT_SYSTEM_WHITELIST: List[str] = [
    "system",
    "registry",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "services.exe",
    "lsass.exe",
    "svchost.exe",
    "fontdrvhost.exe",
    "winlogon.exe",
    "dwm.exe",
    "explorer.exe",
    "sihost.exe",
    "taskhostw.exe",
    "ctfmon.exe",
    "audiodg.exe",
    "spoolsv.exe",
    "searchindexer.exe",
    "securityhealthservice.exe",
    "wlanext.exe",
    "conhost.exe",
    "dllhost.exe",
    "runtimebroker.exe",
    "backgroundtaskhost.exe",
]

DEFAULT_USER_WHITELIST: List[str] = [
    "antigravity.exe",
    "code.exe",
    "cursor.exe",
    "devenv.exe",
    "pycharm64.exe",
    "python.exe",
    "pythonw.exe",
    "node.exe",
    "git.exe",
    "docker.exe",
    "wsl.exe",
    "wslservice.exe",
    "wslhost.exe",
    "wslrelay.exe",
    "vmmem",
    "vmmemwsl",
    "com.docker.backend.exe",
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "kakaotalk.exe",
    "discord.exe",
    "slack.exe",
]

DEFAULT_TARGET_BLOATWARE_LIST: List[str] = [
    "msedgewebview2.exe",
    "gamebar.exe",
    "gamebarftserver.exe",
    "yourphone.exe",
    "phoneexperiencehost.exe",
    "widgets.exe",
    "cortana.exe",
    "adobeipcbroker.exe",
    "ccxprocess.exe",
    "armsvc.exe",
    "onedrive.exe",
]


@dataclass
class GuardConfig:
    """AutoMemoryGuard 설정 데이터 모델."""

    polling_interval_sec: int = 5
    stage1_trim_threshold_percent: float = 82.0
    stage2_kill_threshold_percent: float = 88.0
    cooldown_sec: int = 120
    idle_process_min_mb: float = 150.0
    idle_cpu_max_percent: float = 0.5
    idle_duration_sec: int = 180
    enable_toast_notifications: bool = True
    enable_high_timer_resolution: bool = False
    gaming_mode: str = "auto"  # "auto", "always_on", "disabled"
    custom_games: List[str] = field(default_factory=list)
    system_whitelist: List[str] = field(default_factory=lambda: list(DEFAULT_SYSTEM_WHITELIST))
    user_whitelist: List[str] = field(default_factory=lambda: list(DEFAULT_USER_WHITELIST))
    target_bloatware_list: List[str] = field(default_factory=lambda: list(DEFAULT_TARGET_BLOATWARE_LIST))

    def validate(self) -> None:
        """설정값의 물리적/논리적 유효성을 검증하고 소문자 정규화를 수행합니다."""
        if self.polling_interval_sec < 1:
            raise ValueError(f"감시 주기(polling_interval_sec)는 최소 1초 이상이어야 합니다: {self.polling_interval_sec}")

        if not (0.0 < self.stage1_trim_threshold_percent < 100.0):
            raise ValueError(
                f"1단계 임계치(stage1_trim_threshold_percent)는 0 초과 100 미만이어야 합니다: {self.stage1_trim_threshold_percent}"
            )

        if not (0.0 < self.stage2_kill_threshold_percent <= 100.0):
            raise ValueError(
                f"2단계 임계치(stage2_kill_threshold_percent)는 0 초과 100 이하여야 합니다: {self.stage2_kill_threshold_percent}"
            )

        if self.stage1_trim_threshold_percent >= self.stage2_kill_threshold_percent:
            raise ValueError(
                f"1단계 임계치({self.stage1_trim_threshold_percent}%)는 2단계 임계치({self.stage2_kill_threshold_percent}%)보다 낮아야 합니다."
            )

        if self.cooldown_sec < 0:
            raise ValueError(f"쿨다운 시간(cooldown_sec)은 0초 이상이어야 합니다: {self.cooldown_sec}")

        if self.idle_process_min_mb < 0:
            raise ValueError(f"유휴 메모리 기준(idle_process_min_mb)은 0MB 이상이어야 합니다: {self.idle_process_min_mb}")

        if not (0.0 <= self.idle_cpu_max_percent <= 100.0):
            raise ValueError(f"유휴 CPU 기준(idle_cpu_max_percent)은 0-100% 범위여야 합니다: {self.idle_cpu_max_percent}")

        if self.idle_duration_sec < 0:
            raise ValueError(f"유휴 지속 시간(idle_duration_sec)은 0초 이상이어야 합니다: {self.idle_duration_sec}")

        # 대소문자 구분을 없애기 위해 리스트 내 문자열을 소문자로 일괄 정규화 및 중복 제거
        self.system_whitelist = sorted(list({item.strip().lower() for item in self.system_whitelist if item.strip()}))
        self.user_whitelist = sorted(list({item.strip().lower() for item in self.user_whitelist if item.strip()}))
        self.target_bloatware_list = sorted(list({item.strip().lower() for item in self.target_bloatware_list if item.strip()}))
        self.custom_games = sorted(list({item.strip().lower() for item in self.custom_games if item.strip()}))
        if self.gaming_mode not in ("auto", "always", "off"):
            self.gaming_mode = "auto"


class ConfigManager:
    """설정 파일(JSON) 입출력 및 조회 관리자."""

    def __init__(self, config_path: Optional[Path] = None) -> None:
        """ConfigManager를 초기화합니다.

        Args:
            config_path: 커스텀 설정 파일 경로. 지정하지 않을 경우 <프로젝트루트>/config/settings.json 사용.
        """
        self._lock: threading.RLock = threading.RLock()
        if config_path is None:
            self._config_path = _get_project_root() / "config" / "settings.json"
        else:
            self._config_path = Path(config_path)

        # O(1) 조회를 위한 Set 캐시 초기화
        self._cached_system_whitelist: Set[str] = set()
        self._cached_user_whitelist: Set[str] = set()
        self._cached_custom_games: Set[str] = set()
        self._cached_bloatware_set: Set[str] = set()

        self._config: GuardConfig = self.load_config()
        self._refresh_lookup_caches()

    @property
    def config_path(self) -> Path:
        """현재 설정 파일 경로를 반환합니다."""
        return self._config_path

    @property
    def config(self) -> GuardConfig:
        """현재 로드된 설정 객체를 반환합니다."""
        return self._config

    def _refresh_lookup_caches(self) -> None:
        """화이트리스트 및 타깃 목록 검색용 캐시 집합(Set)을 갱신합니다."""
        self._cached_system_whitelist = {name.lower() for name in self._config.system_whitelist}
        self._cached_user_whitelist = {name.lower() for name in self._config.user_whitelist}
        self._cached_custom_games = {name.lower() for name in self._config.custom_games}
        self._cached_bloatware_set = {name.lower() for name in self._config.target_bloatware_list}

    def load_config(self, force_reload: bool = False) -> GuardConfig:
        """설정 파일에서 구성을 로드합니다. 파일이 없으면 안전한 기본값으로 생성합니다.

        Args:
            force_reload: 디스크에서 강제로 다시 읽어올지 여부.

        Returns:
            GuardConfig: 유효성이 검증된 설정 객체.
        """
        with self._lock:
            if not self._config_path.exists():
                _LOGGER.info("설정 파일이 존재하지 않아 기본값으로 새로 생성합니다: %s", self._config_path)
                default_config = GuardConfig()
                default_config.validate()
                self.save_config(default_config)
                self._config = default_config
                self._refresh_lookup_caches()
                return self._config

            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if not isinstance(data, dict):
                    raise ValueError("설정 파일 최상위 구조는 JSON 객체(dict) 형태여야 합니다.")

                # 필드가 누락되었거나 버전이 다를 경우를 대비하여 기본값과 병합
                merged_data: Dict[str, Any] = asdict(GuardConfig())
                merged_data.update(data)

                loaded_config = GuardConfig(
                    polling_interval_sec=int(merged_data["polling_interval_sec"]),
                    stage1_trim_threshold_percent=float(merged_data["stage1_trim_threshold_percent"]),
                    stage2_kill_threshold_percent=float(merged_data["stage2_kill_threshold_percent"]),
                    cooldown_sec=int(merged_data["cooldown_sec"]),
                    idle_process_min_mb=float(merged_data["idle_process_min_mb"]),
                    idle_cpu_max_percent=float(merged_data["idle_cpu_max_percent"]),
                    idle_duration_sec=int(merged_data["idle_duration_sec"]),
                    enable_toast_notifications=bool(merged_data.get("enable_toast_notifications", True)),
                    enable_high_timer_resolution=bool(merged_data.get("enable_high_timer_resolution", False)),
                    gaming_mode=str(merged_data.get("gaming_mode", "auto")),
                    custom_games=list(merged_data.get("custom_games", [])),
                    system_whitelist=list(merged_data["system_whitelist"]),
                    user_whitelist=list(merged_data["user_whitelist"]),
                    target_bloatware_list=list(merged_data["target_bloatware_list"]),
                )
                loaded_config.validate()

                self._config = loaded_config
                self._refresh_lookup_caches()
                _LOGGER.info("설정 파일을 성공적으로 로드했습니다: %s", self._config_path)
                return self._config

            except Exception as exc:
                _LOGGER.error("설정 파일 로드 실패 (파일 손상 가능성: %s). 기본 설정으로 복구합니다.", exc)
                fallback_config = GuardConfig()
                fallback_config.validate()
                self._config = fallback_config
                self._refresh_lookup_caches()
                return self._config

    def save_config(self, config: Optional[GuardConfig] = None) -> None:
        """현재 설정을 JSON 파일로 디스크에 안전하게 기록(Atomic Write)합니다.

        Args:
            config: 저장할 설정 객체 (생략 시 현재 캐시된 설정 사용).
        """
        with self._lock:
            target_config = config if config is not None else self._config
            target_config.validate()

            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            data = asdict(target_config)

            # 쓰기 도중 충돌이나 프로세스 강제 종료로 인한 파일 손상을 방지하기 위한 임시 파일 쓰기 및 교체
            temp_file = self._config_path.with_suffix(f".{os.getpid()}_{threading.get_ident()}.tmp")
            try:
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())

                # atomic 교체
                temp_file.replace(self._config_path)
                self._config = target_config
                self._refresh_lookup_caches()
                _LOGGER.info("설정이 성공적으로 저장되었습니다: %s", self._config_path)
            except Exception as exc:
                _LOGGER.error("설정 파일 저장 중 오류 발생: %s", exc)
                if temp_file.exists():
                    try:
                        temp_file.unlink()
                    except OSError:
                        pass
                raise

    def is_system_whitelisted(self, process_name: str) -> bool:
        """해당 프로세스가 OS 필수 프로세스(시스템 화이트리스트)인지 확인합니다."""
        return process_name.strip().lower() in self._cached_system_whitelist

    def is_user_whitelisted(self, process_name: str) -> bool:
        """해당 프로세스가 사용자/개발 환경 보호 화이트리스트인지 확인합니다."""
        return process_name.strip().lower() in self._cached_user_whitelist

    def is_whitelisted(self, process_name: str) -> bool:
        """해당 프로세스가 시스템, 사용자 화이트리스트 또는 등록된 게임 목록에 포함되어 있는지 확인합니다."""
        name_lower = process_name.strip().lower()
        return (
            (name_lower in self._cached_system_whitelist)
            or (name_lower in self._cached_user_whitelist)
            or (name_lower in self._cached_custom_games)
        )

    def is_target_bloatware(self, process_name: str) -> bool:
        """해당 프로세스가 우선 종료 대상 블로트웨어 목록에 포함되어 있는지 확인합니다."""
        return process_name.strip().lower() in self._cached_bloatware_set

    def set_gaming_mode(self, mode: str) -> None:
        """게이밍 부스트 모드를 변경하고 저장합니다 ('auto', 'always', 'off')."""
        mode_cleaned = mode.strip().lower()
        if mode_cleaned not in ("auto", "always", "off"):
            mode_cleaned = "auto"
        with self._lock:
            self._config.gaming_mode = mode_cleaned
            self.save_config(self._config)
            _LOGGER.info("게이밍 모드 변경 저장: %s", mode_cleaned)

    def add_custom_game(self, game_name: str) -> bool:
        """사용자 정의 게임 목록에 프로세스 이름을 추가하고 설정을 저장합니다.

        Args:
            game_name: 추가할 게임 실행 파일 이름 (예: mygame.exe).

        Returns:
            bool: 새로 추가된 경우 True, 이미 존재하거나 비어있는 경우 False.
        """
        with self._lock:
            cleaned = game_name.strip().lower()
            if not cleaned or cleaned in self._cached_custom_games:
                return False

            self._config.custom_games.append(cleaned)
            self.save_config(self._config)
            _LOGGER.info("사용자 게임 프로세스 추가: %s", cleaned)
            return True

    def remove_custom_game(self, game_name: str) -> bool:
        """사용자 정의 게임 목록에서 프로세스를 제거하고 설정을 저장합니다."""
        with self._lock:
            cleaned = game_name.strip().lower()
            if cleaned not in self._cached_custom_games:
                return False

            self._config.custom_games = [g for g in self._config.custom_games if g != cleaned]
            self.save_config(self._config)
            _LOGGER.info("사용자 게임 프로세스 제거: %s", cleaned)
            return True

    def add_user_whitelist(self, process_name: str) -> bool:
        """사용자 보호 화이트리스트에 새 프로세스를 추가하고 설정을 저장합니다.

        Args:
            process_name: 추가할 프로세스 이름 (예: chrome.exe).

        Returns:
            bool: 새로 추가된 경우 True, 이미 존재하거나 비어있는 경우 False.
        """
        with self._lock:
            cleaned = process_name.strip().lower()
            if not cleaned or cleaned in self._cached_user_whitelist:
                return False

            self._config.user_whitelist.append(cleaned)
            self.save_config(self._config)
            _LOGGER.info("사용자 화이트리스트 프로세스 추가: %s", cleaned)
            return True

    def remove_user_whitelist(self, process_name: str) -> bool:
        """사용자 보호 화이트리스트에서 프로세스를 제거하고 설정을 저장합니다.

        Args:
            process_name: 제거할 프로세스 이름.

        Returns:
            bool: 제거 성공 시 True, 목록에 없었던 경우 False.
        """
        with self._lock:
            cleaned = process_name.strip().lower()
            if cleaned not in self._cached_user_whitelist:
                return False

            self._config.user_whitelist = [p for p in self._config.user_whitelist if p != cleaned]
            self.save_config(self._config)
            _LOGGER.info("사용자 화이트리스트 프로세스 제거: %s", cleaned)
            return True

    def update_thresholds(self, stage1_percent: float, stage2_percent: float) -> None:
        """1단계 및 2단계 자동 정리 임계치를 갱신하고 설정을 저장합니다.

        Args:
            stage1_percent: 1단계 Working Set 압축 임계치(%).
            stage2_percent: 2단계 프로세스 종료 임계치(%).
        """
        with self._lock:
            self._config.stage1_trim_threshold_percent = round(stage1_percent, 1)
            self._config.stage2_kill_threshold_percent = round(stage2_percent, 1)
            self.save_config(self._config)
            _LOGGER.info(
                "메모리 임계치 갱신 완료: 1단계=%.1f%%, 2단계=%.1f%%",
                self._config.stage1_trim_threshold_percent,
                self._config.stage2_kill_threshold_percent,
            )


if __name__ == "__main__":
    print("=== ConfigManager 독립 실행 검증 시작 ===")
    manager = ConfigManager()

    print(f"[1] 설정 파일 경로: {manager.config_path}")
    print(f"[2] 파일 존재 여부: {manager.config_path.exists()}")

    cfg = manager.config
    print("[3] 로드된 핵심 파라미터:")
    print(f"  - 감시 주기(polling_interval_sec): {cfg.polling_interval_sec}s")
    print(f"  - 1단계 임계치(stage1_trim_threshold_percent): {cfg.stage1_trim_threshold_percent}%")
    print(f"  - 2단계 임계치(stage2_kill_threshold_percent): {cfg.stage2_kill_threshold_percent}%")
    print(f"  - 쿨다운(cooldown_sec): {cfg.cooldown_sec}s")
    print(f"  - 유휴 메모리 기준(idle_process_min_mb): {cfg.idle_process_min_mb}MB")
    print(f"  - 유휴 CPU 기준(idle_cpu_max_percent): {cfg.idle_cpu_max_percent}%")
    print(f"  - 유휴 시간(idle_duration_sec): {cfg.idle_duration_sec}s")
    print(f"  - 시스템 화이트리스트 개수: {len(cfg.system_whitelist)}개")
    print(f"  - 사용자 화이트리스트 개수: {len(cfg.user_whitelist)}개")
    print(f"  - 타깃 블로트웨어 개수: {len(cfg.target_bloatware_list)}개")

    print("[4] 판별 로직 테스트:")
    test_cases = [
        ("explorer.exe", True, "시스템 화이트리스트"),
        ("code.exe", True, "사용자 화이트리스트"),
        ("GameBar.exe", False, "타깃 블로트웨어"),
        ("unknown_app.exe", False, "일반 프로세스"),
    ]

    for p_name, expected_white, desc in test_cases:
        is_white = manager.is_whitelisted(p_name)
        is_bloat = manager.is_target_bloatware(p_name)
        print(f"  - {p_name} ({desc}): 화이트리스트={is_white}, 블로트웨어={is_bloat}")

    print("=== ConfigManager 독립 실행 검증 완료 ===")
