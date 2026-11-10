"""AutoMemoryGuard 안전성 및 핵심 비즈니스 로직 단위 테스트.

- 안티치트 및 시스템 핵심 프로세스 하드 가드 검증
- ConfigManager 유효성 검증 및 소문자 정규화 검증
- ProcessKiller 화이트리스트 및 안전 제약 검증
- GamingBoostEngine 타이머/전원 설정 멱등성 및 락 검증
"""

import os
from pathlib import Path
import sys
import unittest

# 프로젝트 루트 sys.path 등록
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.config_manager import GuardConfig, ConfigManager, DEFAULT_SYSTEM_WHITELIST
from core.memory_trimmer import TRIM_PROTECTED_NAMES
from core.monitor_daemon import GamingBoostEngine
from core.process_killer import ProcessKiller


class TestAntiCheatAndSafetyProtection(unittest.TestCase):
    """안티치트 충돌 회피 및 시스템 안전 보호 검증."""

    def test_trim_protected_anti_cheat_names(self):
        """주요 안티치트 프로세스가 Working Set 트리밍 보호 목록에 반드시 포함되어야 함."""
        required_anti_cheat = {
            "vgc.exe",
            "vgk.sys",
            "easyanticheat.exe",
            "easyanticheat_eos.exe",
            "battleye.exe",
            "beservice.exe",
        }
        for proc in required_anti_cheat:
            self.assertIn(
                proc,
                TRIM_PROTECTED_NAMES,
                f"안티치트 프로세스 {proc}가 TRIM_PROTECTED_NAMES에 누락되었습니다.",
            )

    def test_trim_protected_core_windows_names(self):
        """Windows 핵심 디스플레이 및 커널 프로세스가 트리밍 보호 목록에 포함되어야 함."""
        required_core = {
            "audiodg.exe",
            "dwm.exe",
            "memory compression",
            "csrss.exe",
        }
        for proc in required_core:
            self.assertIn(
                proc,
                TRIM_PROTECTED_NAMES,
                f"핵심 커널 프로세스 {proc}가 TRIM_PROTECTED_NAMES에 누락되었습니다.",
            )


class TestConfigValidation(unittest.TestCase):
    """설정 유효성 및 데이터 정규화 검증."""

    def test_stage1_must_be_lower_than_stage2(self):
        """1단계 임계치가 2단계 임계치보다 크거나 같으면 ValueError를 발생시켜야 함."""
        with self.assertRaises(ValueError):
            GuardConfig(
                stage1_trim_threshold_percent=90.0,
                stage2_kill_threshold_percent=85.0,
            )

        with self.assertRaises(ValueError):
            GuardConfig(
                stage1_trim_threshold_percent=88.0,
                stage2_kill_threshold_percent=88.0,
            )

    def test_whitelist_normalization(self):
        """화이트리스트 목록은 대소문자 중복 없이 소문자로 정규화되어야 함."""
        cfg = GuardConfig(
            user_whitelist=["Discord.exe", "DISCORD.EXE", " Chrome.exe "],
            custom_games=["CS2.EXE", "cs2.exe", " Valorant.exe "],
        )
        self.assertIn("discord.exe", cfg.user_whitelist)
        self.assertIn("chrome.exe", cfg.user_whitelist)
        self.assertEqual(len(cfg.user_whitelist), 2)
        self.assertIn("cs2.exe", cfg.custom_games)
        self.assertIn("valorant.exe", cfg.custom_games)
        self.assertEqual(len(cfg.custom_games), 2)


class TestProcessKillerSafety(unittest.TestCase):
    """프로세스 킬러의 안전 장치 및 화이트리스트 보호 검증."""

    def setUp(self):
        self.cfg_mgr = ConfigManager()
        self.killer = ProcessKiller(self.cfg_mgr)

    def test_system_whitelist_always_protected(self):
        """OS 필수 프로세스는 화이트리스트에 항상 포함되어야 함."""
        essential = {"system", "registry", "smss.exe", "csrss.exe", "services.exe", "lsass.exe"}
        for proc in essential:
            self.assertIn(
                proc,
                DEFAULT_SYSTEM_WHITELIST,
                f"OS 필수 프로세스 {proc}가 DEFAULT_SYSTEM_WHITELIST에 누락되었습니다.",
            )

    def test_dry_run_never_terminates(self):
        """dry_run=True 호출 시 실제 프로세스를 종료하지 않고 시뮬레이션 결과만 반환해야 함."""
        result = self.killer.terminate_candidates(max_kills=3, dry_run=True)
        self.assertIn("killed_count", result)
        self.assertIn("candidate_count", result)
        self.assertEqual(result["killed_count"], 0)


class TestGamingBoostIdempotency(unittest.TestCase):
    """게이밍 부스트 엔진의 수명주기 및 멱등성 검증."""

    def setUp(self):
        self.engine = GamingBoostEngine({"cs2.exe", "valorant.exe"})

    def test_deactivate_when_not_active_is_safe(self):
        """활성화되지 않은 상태에서 deactive_boost()를 여러 번 호출해도 예외 없이 안전해야 함."""
        self.assertFalse(self.engine.is_active)
        self.engine.deactivate_boost()
        self.engine.deactivate_boost()
        self.assertFalse(self.engine.is_active)

    def test_unlock_timer_is_idempotent(self):
        """unlock_high_precision_timer()를 다중 호출해도 예외 없이 안전해야 함."""
        self.engine.unlock_high_precision_timer()
        self.engine.unlock_high_precision_timer()
        self.assertFalse(self.engine.is_active)


if __name__ == "__main__":
    unittest.main()
