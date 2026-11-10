# AutoMemoryGuard (v1.0.0)

<p align="center">
  <strong>Source-Available (<35MB) System Memory Optimizer & 0.50ms Kernel Gaming Shield for Windows 10/11</strong>
</p>

<p align="center">
  <a href="https://github.com/kihoon808/AutoMemoryGuard/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-Source--Available-blue.svg" alt="License"></a>
  <a href="https://www.virustotal.com/gui/file/35691dbeec690532fa3be5d52abad7dcc61ff48807335f071109f8dbfc0d4df0?nocache=1"><img src="https://img.shields.io/badge/VirusTotal-Tier--1%20Clean-brightgreen.svg" alt="VirusTotal"></a>
  <img src="https://img.shields.io/badge/Platform-Windows%2010%20%7C%2011%20(x64)-0078D6.svg" alt="Platform">
  <img src="https://img.shields.io/badge/Measured%20RAM-25--35MB-success.svg" alt="RAM Footprint">
  <img src="https://img.shields.io/badge/Python-3.10%2B-informational.svg" alt="Python">
</p>

---

## English Documentation

### 1. Overview

**AutoMemoryGuard** is a source-available, lightweight Windows system optimization tool developed for multi-tasking users and gamers operating within 16GB RAM boundaries.

Instead of indiscriminate memory trimming during intense gameplay—which frequently leads to disk paging and micro-stuttering—AutoMemoryGuard focuses on:
1. **Dynamic 0.50ms Kernel Timer Resolution**: Enforcing high-precision timer ticks (`NtSetTimerResolution`) during gameplay and reverting to default 15.6ms on game exit.
2. **Anti-Cheat Collision Avoidance**: Permanently hard-guarding anti-cheat user-mode processes (Riot Vanguard, Easy Anti-Cheat, BattlEye) from handle manipulation or working set flushing, and strictly excluding the active game PID and all its child worker processes.
3. **Windows 11 EcoQoS Background Throttling**: Restricting idle background bloatware (browser renderer tabs, background updaters) to Efficiency (E) cores (`PROCESS_POWER_THROTTLING_EXECUTION_SPEED`).
4. **Stutter-Resistant 2-Stage Pipeline**: Performing a pre-game memory trim, lifting the trigger threshold to 92.0% during matches, and soft-locking stage-2 process termination during active gameplay.

---

### 2. Core Architectural Pillars

```text
=========================================================================================================
                      [AutoMemoryGuard : Gaming Shield Architecture]
=========================================================================================================

  [MemoryMonitorDaemon (5-Second Polling Loop)]
         │
         ├──► 1. Smart Game Detector (Hybrid Detection Engine)
         │       ├─ [Path A] Game Executable Hash-Set Match (cs2, valorant, lol...)
         │       ├─ [Path B] Win32 SHQueryUserNotificationState == QUNS_RUNNING_D3D_FULL_SCREEN (3)
         │       └─ [Alt-Tab Guard] Keeps boost state LOCKED until the detected game PID terminates
         │
         ▼ (Game Detected -> Boost Engagement)
  ┌───────────────────────────────────────────────────────────────────────────────────────────────────┐
  │                                     GamingBoostEngine                                             │
  ├───────────────────────────────┬───────────────────────────────────┬───────────────────────────────┤
  │ [Pillar 1] Dynamic Timer Lock │ [Pillar 2] Anti-Cheat Hard-Guard  │ [Pillar 3] Stutter Defense    │
  │ • NtQueryTimerResolution      │ • Hardcoded exclusion of vgc.exe, │ • Pre-Game Working Set Purge  │
  │   queries minimum hardware    │   EasyAntiCheat, BattlEye         │   (Single gentle flush)       │
  │ • NtSetTimerResolution locks  │ • Active Game PID + all recursive │ • Threshold relaxed to 92.0%  │
  │   0.5000ms global resolution  │   child processes never trimmed   │ • Stage 2 process termination │
  │ • Restores 15.6ms upon exit   │ • Throttles background bloat to   │   is SOFT-LOCKED during game  │
  │                               │   Win11 EcoQoS (Efficiency Cores) │   to protect user data        │
  └───────────────────────────────┴───────────────────────────────────┴───────────────────────────────┘
```

#### Pillar 1: Dynamic 0.50ms Kernel Timer Resolution
- Default Windows timer resolution is 15.6ms (64 ticks/second). High-tickrate applications benefit from lower timer intervals.
- AutoMemoryGuard interfaces with `ntdll.dll` (`NtQueryTimerResolution` and `NtSetTimerResolution`), dynamically locking a hardware-minimum **0.5000ms resolution** upon game detection.
- When the game terminates, it automatically un-registers the timer request and restores the system resolution to default 15.6ms to maintain energy efficiency.
- *Note: Subjective input responsiveness gains depend on hardware architecture, CPU thread scheduling, and engine polling rates.*

#### Pillar 2: Anti-Cheat Collision Avoidance & EcoQoS Throttling
- **Hard-Guarded Anti-Cheat Services**: `memory_trimmer` maintains an immutable protection list (`vgc.exe`, `vgk.sys`, `easyanticheat.exe`, `easyanticheat_eos.exe`, `battleye.exe`, `beservice.exe`) that bypasses handle opening and memory flushing.
- **Game PID & Child Tree Shield**: The detected game process and all its recursive child threads/processes are excluded from both memory trimming and candidate killing.
- **Inverse Throttling**: Rather than touching the game, AutoMemoryGuard intercepts idle background processes (Chrome tabs, Edge WebView2, Discord) and applies Windows 11 EcoQoS (`PROCESS_POWER_THROTTLING_EXECUTION_SPEED`) and `IDLE_PRIORITY_CLASS`, steering them toward efficiency cores.

#### Pillar 3: Stutter Defense & Data Safety
- **Pre-Game Gentle Purge**: At game launch, a single Working Set trim releases unpaged physical RAM from system background services.
- **In-Game Lockout**: During gameplay, the aggressive memory cleaning threshold is relaxed from 88.0% to **92.0%**. Furthermore, stage-2 process termination is completely soft-locked while gaming to prevent accidental loss of user work.

---

### 3. Feature & Scope Matrix

| Metric / Feature | ClearMem ($9.99 on Steam) | ISLC (Wagnardsoft) | Mem Reduct | AutoMemoryGuard v1.0.0 |
| :--- | :--- | :--- | :--- | :--- |
| **License / Cost** | Commercial ($9.99 USD) | Free Freeware | Free (GPLv3) | **Free Personal / Source-Available** |
| **Measured Memory Footprint** | ~45MB - 70MB | ~35MB - 50MB | ~5MB - 12MB | **~25MB - 35MB (Tray) / ~35-50MB (GUI)** |
| **0.50ms Dynamic Kernel Timer** | ❌ No | ✅ Yes (Manual toggle) | ❌ No | **✅ Yes (Game-aware auto-toggle)** |
| **Windows 11 EcoQoS Throttling** | ❌ No | ❌ No | ❌ No | **✅ Yes (Background bloat taming)** |
| **Anti-Cheat Process Hard-Guard** | ⚠️ Generic open | ✅ Safe | ✅ Safe | **✅ Dedicated hard-guard list** |
| **Game Child Process Shield** | ❌ No | ❌ No | ❌ No | **✅ Recursive child tree exclusion** |
| **In-Game Stage-2 Kill Lockout** | ❌ N/A | ❌ N/A | ❌ N/A | **✅ Yes (Safe-locked during gaming)** |
| **Modern Dark GUI & Tray Gauge** | ❌ Legacy Form | ❌ Win32 Legacy | ❌ Minimalist | **✅ Modern CustomTkinter** |

---

### 4. Security Verification & VirusTotal Audit

Windows utilities packed via PyInstaller frequently face static heuristic flags from smaller antivirus vendors due to temporary `%TEMP%` runtime unpacking.

- **Official VirusTotal Scan Report**: [View VirusTotal Scan Results](https://www.virustotal.com/gui/file/35691dbeec690532fa3be5d52abad7dcc61ff48807335f071109f8dbfc0d4df0?nocache=1)
- **Scan Status**: All Tier-1 Global Engines (**Microsoft Defender, Kaspersky, Bitdefender, Sophos, ESET, Symantec, Avast**) report **100% Undetected / Clean**.
- **Explanation of 3 Heuristic Detections (Bkav Pro, SecureAge, Zillya)**:
  - These minor scanners trigger a generic "Dropper" heuristic on PyInstaller's `--onefile` payload bootstrap.
  - The binary is packaged with official Windows PE Version Information (`VSVersionInfo`), and source code is fully inspectable.

> **Windows SmartScreen Notice**: Because AutoMemoryGuard is distributed independently without a commercial Extended Validation (EV) code-signing certificate, Windows SmartScreen may show "Windows protected your PC". Click **"More info"** -> **"Run anyway"** to run.

---

### 5. Quick Start & Build Instructions

#### Running Portable Binary
1. Download the latest `AutoMemoryGuard_v1.0.0.zip` from the [Releases](https://github.com/kihoon808/AutoMemoryGuard/releases) tab.
2. Extract the archive into a dedicated folder (e.g., `C:\Tools\AutoMemoryGuard`).
3. Run `AutoMemoryGuard.exe`. Grant administrator privileges when prompted (required for `SeDebugPrivilege` and timer resolution APIs).

#### Building from Source
```powershell
# Clone the repository
git clone https://github.com/kihoon808/AutoMemoryGuard.git
cd AutoMemoryGuard

# Install dependencies
pip install -r requirements.txt

# Run unit tests
pytest tests/

# Run in background tray mode
python main.py

# Package standalone portable binary with PE metadata
python build_exe.py
```

---

### 6. License & Commercial Rights

This project is released under the **Source-Available Non-Commercial License**.
- **Permitted**: Free for personal, non-commercial, educational, and evaluation purposes. You may freely inspect, audit, and modify the source code locally.
- **Strictly Prohibited**: Unauthorized commercial resale, repackaging, inclusion in commercial bundles, or distribution through commercial storefronts (such as Steam, Epic Games Store, Microsoft Store, itch.io).
- Commercial monetization and storefront distribution rights are strictly reserved by the author, **Kihyn Cho (kihoon808)**.

See the full [LICENSE](LICENSE) file for complete legal terms.

---
---

## 한국어 설명서 (Korean Documentation)

### 1. 프로그램 개요

**AutoMemoryGuard**는 16GB 이하 메모리 환경의 게이머 및 파워 유저를 위해 설계된 소스 공개형(Source-Available) 비상업적 Windows 시스템 최적화 도구입니다.

교전 도중 무리하게 램을 비워 심각한 디스크 페이징과 화면 끊김(스터터링)을 유발하는 기존 램 정리기와 달리, AutoMemoryGuard는 다음 4가지 핵심 축을 중심으로 안전하게 동작합니다.

1. **0.50ms 동적 고정밀 커널 타이머**: 게임 감지 시 `NtSetTimerResolution`을 통해 0.50ms로 고정하고, 게임 종료 시 15.6ms 기본값으로 안전하게 원복합니다.
2. **안티치트 충돌 회피 설계**: 라이엇 뱅가드(`vgc.exe`), 이지안티치트(`easyanticheat.exe`), 배틀아이(`battleye.exe`) 등 주요 안티치트 서비스를 하드코딩 보호 목록에 등록하여 핸들 접근 및 Working Set 조작을 원천 차단합니다. 또한 현재 감지된 게임 PID 및 그 직계 자식 프로세스 전체를 압축 및 종료 대상에서 영구 제외합니다.
3. **Windows 11 EcoQoS 백그라운드 격리**: 게임 본체를 건드리지 않고, 백그라운드에서 CPU/RAM을 점유하는 브라우저 렌더러와 유휴 블로트웨어만 Windows 11 EcoQoS(효율 코어)로 밀어냅니다.
4. **스터터링 방어 및 작업물 보호**: 게임 실행 직후 1회 선제적 캐시 정리만 수행하며, 인게임 중에는 임계치를 92.0%로 완화하고 **2단계 프로세스 강제 종료를 소프트 락(차단)**하여 사용자 작업물 손실을 원천 방지합니다.

---

### 2. 핵심 게이밍 쉴드 아키텍처

#### 1) 0.50ms 동적 고정밀 커널 타이머 제어
- Windows 기본 타이머 해상도는 15.6ms(초당 64회)로 동작합니다.
- AutoMemoryGuard는 `ntdll.dll` 커널 API(`NtQueryTimerResolution`, `NtSetTimerResolution`)를 직접 제어하여, 게임 실행 감지 즉시 하드웨어 최적값인 **0.5000ms(초당 2,000회)**로 시스템 타이머를 자동 고정합니다.
- 게임 종료 시 15.6ms로 자동 복원하여 전력 낭비를 방지합니다.
- *참고: 타이머 해상도 변경에 따른 인풋 반응 체감은 CPU 아키텍처, 스레드 스케줄러, 게임 엔진에 따라 상이할 수 있습니다.*

#### 2) 안티치트 프로세스 하드 가드 및 백그라운드 EcoQoS 격리
- **안티치트 영구 보호**: `memory_trimmer`에 `vgc.exe`, `easyanticheat.exe`, `battleye.exe` 등을 영구 화이트리스트(`TRIM_PROTECTED_NAMES`)로 등록하여 불필요한 프로세스 핸들 열람 및 조작을 방지합니다.
- **게임 자식 프로세스 연쇄 보호**: 감지된 게임 PID뿐만 아니라 해당 프로세스가 생성한 렌더러, 오버레이 등 모든 하위 자식 트리를 압축 대상에서 일괄 제외합니다.
- **역발상 백그라운드 격리**: 게임 프로세스는 일체 건드리지 않고, 주변 유휴 프로세스(크롬 탭, 엣지 WebView2, 디스코드)에 Windows 11 EcoQoS 및 `IDLE_PRIORITY_CLASS`를 적용하여 효율(E) 코어로 격리합니다.

#### 3) 스터터링 방어 및 2단계 자동 종료 세이프락
- **프리게임 1회 선제 압축**: 게임 진입 즉시 1회성으로 물리 RAM을 확보합니다.
- **인게임 2단계 강제 종료 차단**: 게임 구동 중에는 임계치를 92.0%로 완화할 뿐만 아니라, **2단계 유휴 프로세스 자동 종료 기능이 자동으로 차단(Soft-Lock)**되어 사용자가 작업 중이던 브라우저나 메신저가 종료되는 사고를 원천 방지합니다.

---

### 3. 기능 및 점유율 비교 표

| 비교 항목 | ClearMem (Steam 9.99달러) | ISLC (Wagnardsoft) | Mem Reduct | AutoMemoryGuard v1.0.0 |
| :--- | :--- | :--- | :--- | :--- |
| **라이선스 / 가격** | 상용 유료 ($9.99) | 완전 무료 프리웨어 | 오픈소스 (GPLv3) | **개인 비상업 무료 (Source-Available)** |
| **실측 메모리 점유율** | 약 45MB - 70MB | 약 35MB - 50MB | 약 5MB - 12MB | **실측 약 25MB - 35MB (트레이) / 약 35-50MB (GUI)** |
| **0.50ms 커널 타이머** | ❌ 미지원 | ✅ 지원 (수동 설정) | ❌ 미지원 | **✅ 지원 (게임 감지 시 동적 자동 전환)** |
| **Win11 EcoQoS 격리** | ❌ 미지원 | ❌ 미지원 | ❌ 미지원 | **✅ 지원 (주변 블로트웨어 효율코어 격리)** |
| **안티치트 하드 가드** | ⚠️ 단순 순회 | ✅ 안전 | ✅ 안전 | **✅ 뱅가드/EAC/배틀아이 영구 보호 명단 탑재** |
| **게임 자식 트리 연쇄 보호** | ❌ 미지원 | ❌ 미지원 | ❌ 미지원 | **✅ 지원 (하위 렌더러/오버레이 일괄 제외)** |
| **인게임 프로세스 종료 차단** | ❌ 해당 없음 | ❌ 해당 없음 | ❌ 해당 없음 | **✅ 지원 (게임 중 2단계 강제 종료 소프트 락)** |
| **모던 GUI 및 실시간 게이지** | ❌ 구형 폼 | ❌ 레거시 Win32 | ❌ 심플형 | **✅ 모던 CustomTkinter 다크 테마** |

---

### 4. 보안 검증 및 바이러스토탈 분석

- **공식 VirusTotal 분석 리포트**: [VirusTotal 검사 결과 링크 확인하기](https://www.virustotal.com/gui/file/35691dbeec690532fa3be5d52abad7dcc61ff48807335f071109f8dbfc0d4df0?nocache=1)
- **보안 검증 결과**: 글로벌 1티어 70여 개 메이저 백신(**Microsoft Defender, Kaspersky, Bitdefender, Sophos, ESET, Symantec, Avast 등**)에서 **100% 무해(Undetected / Clean)** 판정을 받았습니다.
- **마이너 3개 백신 오탐(Bkav Pro, SecureAge, Zillya) 안내**:
  - PyInstaller `--onefile` 패키징 특성상 실행 시 임시 폴더(`%TEMP%`)에 압축을 해제하며 구동하므로 일부 비주류 엔진에서 Dropper 휴리스틱으로 오탐한 것입니다.
  - 정식 Windows PE 버전 정보(`VSVersionInfo`)가 바이너리에 정상 주입되어 있습니다.

> **Windows SmartScreen 안내**: 유료 기업용 EV 인증서가 없는 개인 무료 배포 소프트웨어이므로 최초 실행 시 'Windows의 PC 보호' 화면이 나타날 수 있습니다. **'추가 정보'** 클릭 후 **'실행'**을 누르시면 정상 실행됩니다.

---

### 5. 설치 및 소스 빌드 가이드

#### 포터블 실행 파일 이용
1. [Releases](https://github.com/kihoon808/AutoMemoryGuard/releases) 페이지에서 최신 `AutoMemoryGuard_v1.0.0.zip` 파일을 다운로드합니다.
2. 원하는 폴더에 압축을 해제합니다.
3. `AutoMemoryGuard.exe`를 실행합니다 (커널 타이머 제어 및 메모리 접근을 위해 관리자 권한을 승인합니다).

#### 소스 코드 빌드 및 테스트
```powershell
# 저장소 복제
git clone https://github.com/kihoon808/AutoMemoryGuard.git
cd AutoMemoryGuard

# 필수 라이브러리 설치
pip install -r requirements.txt

# 단위 테스트 실행
pytest tests/

# 트레이 상주 모드로 실행
python main.py

# 단일 .exe 패키징
python build_exe.py
```

---

### 6. 라이선스 및 상업적 권리

본 프로젝트는 **Source-Available Non-Commercial License(비상업적 개인 무료 라이선스)**를 따릅니다.
- **허용 범위**: 개인적인 비상업적 이용, 소스 코드 열람, 코드 학습, 보안 감사 및 개인 용도의 소스 수정은 완전 무료로 허용됩니다.
- **엄격 금지**: 본 소프트웨어나 소스 코드를 무단으로 재판매하거나, 상용 스토어(Steam, Epic Games Store, MS Store, itch.io 등)에 유료로 등록 및 패키징하여 재배포하는 행위는 엄격히 금지됩니다.
- 본 소프트웨어의 상업적 출시, 플랫폼 등록, 유료화 권리는 원작자인 **조기훈(Kihyn Cho, kihoon808)**에게 독점적으로 귀속됩니다.

자세한 법적 조항은 [LICENSE](LICENSE) 파일을 참고하십시오.
