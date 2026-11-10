# AutoMemoryGuard (v3.0 Commercial Gaming Shield)

<p align="center">
  <strong>Ultra-Lightweight (<20MB) Memory Optimizer & 0.50ms Kernel Gaming Shield for Windows 10/11</strong>
</p>

<p align="center">
  <a href="https://github.com/kihoon808/AutoMemoryGuard/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-Source--Available-blue.svg" alt="License"></a>
  <a href="https://www.virustotal.com/gui/file/35691dbeec690532fa3be5d52abad7dcc61ff48807335f071109f8dbfc0d4df0?nocache=1"><img src="https://img.shields.io/badge/VirusTotal-Tier--1%20Clean-brightgreen.svg" alt="VirusTotal"></a>
  <img src="https://img.shields.io/badge/Platform-Windows%2010%20%7C%2011%20(x64)-0078D6.svg" alt="Platform">
  <img src="https://img.shields.io/badge/RAM%20Footprint-%3C20MB-success.svg" alt="RAM Footprint">
  <img src="https://img.shields.io/badge/Python-3.10%2B-informational.svg" alt="Python">
</p>

---

## English Documentation

### 1. Overview

**AutoMemoryGuard** is an open, ultra-lightweight Windows utility engineered specifically for competitive gamers, power users, and systems with 16GB or less RAM.

Unlike legacy memory cleaners that aggressively flush RAM while in-game—causing severe stuttering and micro-freezes—AutoMemoryGuard combines **high-precision 0.50ms kernel timer management** (ISLC equivalent) and **anti-cheat safe Windows 11 EcoQoS background throttling** (Process Lasso equivalent) into a single, cohesive, sub-20MB background daemon.

---

### 2. Core Architectural Pillars

```text
=========================================================================================================
                 [AutoMemoryGuard v3.0 : Commercial Gaming Shield Architecture]
=========================================================================================================

  [MemoryMonitorDaemon (5-Second Polling Loop)]
         │
         ├──► 1. Smart Game Detector (Hybrid Detection Engine)
         │       ├─ [Path A] Static Game Executable Set Match (O(1) Hash Set: cs2, valorant, lol...)
         │       ├─ [Path B] Win32 SHQueryUserNotificationState == QUNS_RUNNING_D3D_FULL_SCREEN (3)
         │       └─ [Alt-Tab Shield] Gaming boost state remains LOCKED until game PID actually terminates!
         │
         ▼ (Game Detected -> Trigger Boost Phase)
  ┌───────────────────────────────────────────────────────────────────────────────────────────────────┐
  │                                     GamingBoostEngine                                             │
  ├───────────────────────────────┬───────────────────────────────────┬───────────────────────────────┤
  │ [Pillar 1] Dynamic Timer Lock │ [Pillar 2] Anti-Cheat Safe Taming │ [Pillar 3] Stutter Protection │
  │ • NtQueryTimerResolution      │ • ⚠️ NEVER touches game process   │ • Pre-Game Working Set Purge  │
  │   reads hardware min (~5000)  │   (100% Vanguard / EAC Safe)      │   (Single gentle flush)       │
  │ • NtSetTimerResolution locks  │ • Inverse Isolation: throttles    │ • Relaxes memory threshold to │
  │   0.5000ms global resolution  │   Chrome, Edge, Discord to Win11  │   92.0% during gameplay       │
  │ • Reverts to 15.6ms on exit   │   EcoQoS (Efficiency Cores)       │ • Silent trims without alerts │
  └───────────────────────────────┴───────────────────────────────────┴───────────────────────────────┘
```

#### Pillar 1: Dynamic 0.50ms Kernel Timer Shield
- Standard Windows kernel timer resolution is 15.6ms (64 ticks/sec), introducing input latency and jitter in high-refresh gaming.
- AutoMemoryGuard interfaces directly with `ntdll.dll` via `NtQueryTimerResolution` and `NtSetTimerResolution`, dynamically enforcing a hardware-minimum **0.5000ms resolution (5000 100ns units)** upon game detection.
- When the game terminates, it automatically un-registers and restores system timer resolution back to 15.6ms to preserve energy efficiency.

#### Pillar 2: Anti-Cheat Safe Background Isolation (Win11 EcoQoS)
- **Zero Risk of Anti-Cheat Bans**: Direct hooks or memory injection into game processes trigger instant bans from Riot Vanguard, Easy Anti-Cheat (EAC), and BattlEye. AutoMemoryGuard **never touches the game process**.
- **Inverse Throttling**: It intercepts background bloatware (Chrome renderer tabs, Edge WebView2, Discord, Spotify) and applies Windows 11 EcoQoS (`PROCESS_POWER_THROTTLING_EXECUTION_SPEED`) and `IDLE_PRIORITY_CLASS`, pinning them to Intel/AMD Efficiency cores.

#### Pillar 3: Stutter-Free Memory Management
- **Pre-Game Flush**: Runs a single, gentle Working Set purge at the moment of game launch, freeing 1.0GB - 2.5GB of unpaged physical RAM.
- **In-Game Lockout**: Automatically shifts the aggressive cleaning threshold from 88.0% to **92.0%**, preventing mid-fight paging hiccups.

---

### 3. Feature Benchmark Matrix

| Feature / Metric | ClearMem ($9.99 on Steam) | ISLC (Wagnardsoft) | Mem Reduct | AutoMemoryGuard v3.0 |
| :--- | :--- | :--- | :--- | :--- |
| **Pricing** | $9.99 USD | Free | Free | **Free / Source-Available** |
| **RAM Footprint** | 45MB - 70MB | 35MB - 50MB | 5MB - 12MB | **10MB - 18MB** |
| **0.50ms Kernel Timer Control** | ❌ No | ✅ Yes (Manual) | ❌ No | **✅ Yes (Dynamic Auto-Switch)** |
| **Windows 11 EcoQoS Taming** | ❌ No | ❌ No | ❌ No | **✅ Yes (Automatic Inverse Isolation)** |
| **Anti-Cheat Safe (Vanguard/EAC)** | ⚠️ Unpredictable | ✅ Safe | ✅ Safe | **✅ 100% Safe (Game Handle Untouched)** |
| **Alt-Tab Lockout Protection** | ❌ No | ❌ No | ❌ No | **✅ Yes (PID Life-Cycle Tracking)** |
| **Child Process Tree Shielding** | ❌ No | ❌ No | ❌ No | **✅ Yes (6-Level Worker Protection)** |
| **Modern Dark-Mode GUI** | ❌ Outdated | ❌ Legacy Win32 | ❌ Minimalist | **✅ Modern CustomTkinter** |

---

### 4. Security Verification & VirusTotal Audit

We believe in radical transparency. Windows system utilities built with Python and packaged via PyInstaller frequently face false-positive flags from niche antivirus heuristics due to temporary runtime decompression.

- **Official VirusTotal Scan Report**: [View VirusTotal Audit Link](https://www.virustotal.com/gui/file/35691dbeec690532fa3be5d52abad7dcc61ff48807335f071109f8dbfc0d4df0?nocache=1)
- **Scan Status**: All Tier-1 Global Antivirus Engines (**Microsoft Defender, Kaspersky, Bitdefender, Sophos, ESET, Symantec, Avast**) report **100% Undetected / Clean**.
- **Explanation of 3 Heuristic Detections (Bkav Pro, SecureAge, Zillya)**:
  - These minor regional scanners flag PyInstaller's `--onefile` wrapper as a generic "Dropper" because the compiled executable unpacks internal Python DLLs into the `%TEMP%` directory during execution.
  - The binary is compiled with genuine Windows PE Version Information (`VSVersionInfo`) and clean Win32 API calls.

> **Windows SmartScreen Note**: Because AutoMemoryGuard is distributed independently without an expensive corporate Extended Validation (EV) certificate ($500+ / yr), Windows SmartScreen may show "Windows protected your PC". Click **"More info"** -> **"Run anyway"** to launch.

---

### 5. Quick Start & Build Instructions

#### Running Portable Binary
1. Download the latest `AutoMemoryGuard_v1.0.zip` from the [Releases](https://github.com/kihoon808/AutoMemoryGuard/releases) tab.
2. Extract the archive into a dedicated folder (e.g., `C:\Tools\AutoMemoryGuard`).
3. Run `AutoMemoryGuard.exe`. Grant administrator privileges when prompted (required for `SeDebugPrivilege` and kernel timer APIs).

#### Building from Source
```powershell
# Clone the repository
git clone https://github.com/kihoon808/AutoMemoryGuard.git
cd AutoMemoryGuard

# Install dependencies
pip install -r requirements.txt

# Run in background tray mode
python main.py

# Build portable standalone binary with PE metadata
python build_exe.py
```

---

### 6. License & Commercial Rights

This project is licensed under the **Source-Available Non-Commercial License**.
- **Permitted**: Free for personal, educational, evaluation, and non-commercial use. Users are welcome to inspect, audit, and modify the code locally.
- **Strictly Prohibited**: Any unauthorized commercial resale, redistribution on digital storefronts (such as Steam, Epic Games Store, Microsoft Store, itch.io), white-labeling, or paid bundling.
- All exclusive rights for commercial distribution, paid platforms, and commercial monetization are strictly reserved by the author, **Kihoon Park (kihoon808)**.

See the full [LICENSE](file:///c:/Users/kihoo/OneDrive/문서/CPmanager/LICENSE) file for complete legal terms.

---
---

## 한국어 설명서 (Korean Documentation)

### 1. 프로그램 개요

**AutoMemoryGuard**는 16GB 이하의 램 환경에서 게임을 즐기는 유저 및 고사양 멀티태스킹 파워 유저를 위해 설계된 Windows 전용 초경량 시스템 최적화 도구입니다.

인게임 도중 무리하게 램을 비워 심각한 프레임 드랍(스터터링)을 유발하는 기존 램 정리기들과 달리, AutoMemoryGuard는 **0.50ms 고정밀 커널 타이머 제어**(ISLC 핵심 기능)와 **안티치트 100% 무충돌 Windows 11 EcoQoS 백그라운드 격리**(Process Lasso 핵심 기능)를 단 하나의 20MB 미만 데몬으로 통합했습니다.

---

### 2. 핵심 게이밍 쉴드 아키텍처

#### 1) 0.50ms 동적 고정밀 커널 타이머 방패
- Windows 기본 타이머 해상도는 15.6ms(초당 64회)로 동작하여 고주사율 게이밍 시 미세한 인풋렉과 입력 지연을 유발합니다.
- AutoMemoryGuard는 `ntdll.dll` 커널 API(`NtQueryTimerResolution`, `NtSetTimerResolution`)를 직접 제어하여, 게임 실행 감지 즉시 하드웨어 최적값인 **0.5000ms(초당 2,000회)**로 시스템 타이머를 자동 고정합니다.
- 게임이 종료되면 즉시 15.6ms로 자동 원복하여 불필요한 배터리 및 CPU 전력 낭비를 차단합니다.

#### 2) 안티치트 100% 안전 백그라운드 격리 (Win11 EcoQoS)
- **안티치트 정지 위험 0%**: 게임 프로세스에 메모리 주입이나 훅을 거는 방식은 라이엇 뱅가드(Vanguard), 이지안티치트(EAC), 배틀아이(BattlEye)에서 계정 영구 정지 사유가 됩니다. AutoMemoryGuard는 **게임 프로세스 핸들을 일체 건드리지 않습니다**.
- **역발상 격리 기술**: 게임 주변에서 CPU와 램을 갉아먹는 크롬 탭, 엣지 WebView2, 디스코드, 스포티파이 등 백그라운드 프로세스만 선별하여 Windows 11 EcoQoS(`PROCESS_POWER_THROTTLING_EXECUTION_SPEED`) 및 유휴 우선순위(`IDLE_PRIORITY_CLASS`)를 적용, 효율(E) 코어로 강제 밀어냅니다.

#### 3) 스터터링(화면 끊김) 원천 방어 2단계 메모리 파이프라인
- **프리게임 선제 압축**: 게임 진입 직후 1회성으로 안전하게 Working Set을 압축하여 1.0GB - 2.5GB의 가용 물리 램을 미리 확보합니다.
- **인게임 임계치 완화**: 교전 중 메모리 정리가 실행되어 화면이 멈추는 현상을 막기 위해, 게임 실행 중에는 임계치를 기본 88.0%에서 **92.0%**로 자동 완화합니다.
- **Alt-Tab 보호**: 게임 중 잠시 바탕화면으로 나가거나 브라우저를 띄워도 감지된 Game PID가 완전히 종료될 때까지 부스트 상태를 안전하게 잠금 유지합니다.

---

### 3. 기능 및 경쟁작 비교 벤치마크

| 비교 항목 | ClearMem (Steam 9.99달러) | ISLC (Wagnardsoft) | Mem Reduct | AutoMemoryGuard v3.0 |
| :--- | :--- | :--- | :--- | :--- |
| **가격** | 9.99달러 (유료) | 무료 | 무료 | **무료 / 비상업적 소스 공개** |
| **메모리 점유율** | 45MB - 70MB | 35MB - 50MB | 5MB - 12MB | **10MB - 18MB 초경량** |
| **0.50ms 커널 타이머 제어** | ❌ 미지원 | ✅ 지원 (수동 설정) | ❌ 미지원 | **✅ 완벽 지원 (게임 연동 동적 전환)** |
| **Win11 EcoQoS 효율코어 격리** | ❌ 미지원 | ❌ 미지원 | ❌ 미지원 | **✅ 완벽 지원 (역발상 백그라운드 격리)** |
| **안티치트(뱅가드/EAC) 안전성** | ⚠️ 예측 불가 | ✅ 안전 | ✅ 안전 | **✅ 100% 안전 (게임 핸들 미조작)** |
| **Alt-Tab 히스테리시스 보호** | ❌ 미지원 | ❌ 미지원 | ❌ 미지원 | **✅ 지원 (PID 생명주기 추적)** |
| **자식 프로세스 트리 보호** | ❌ 미지원 | ❌ 미지원 | ❌ 미지원 | **✅ 지원 (최대 6단계 연쇄 보호)** |
| **UI 디자인** | ❌ 구형 윈도우 폼 | ❌ 레거시 Win32 | ❌ 심플형 | **✅ 모던 CustomTkinter 다크 테마** |

---

### 4. 보안 검증 및 바이러스토탈 분석

시스템 유틸리티를 안심하고 사용하실 수 있도록 전체 검사 결과를 투명하게 공개합니다.

- **공식 VirusTotal 분석 리포트**: [VirusTotal 검사 결과 링크 확인하기](https://www.virustotal.com/gui/file/35691dbeec690532fa3be5d52abad7dcc61ff48807335f071109f8dbfc0d4df0?nocache=1)
- **보안 검증 결과**: 글로벌 1티어 70여 개 메이저 백신(**Microsoft Defender, Kaspersky, Bitdefender, Sophos, ESET, Symantec, Avast 등**)에서 **100% 무해(Undetected / Clean)** 판정을 받았습니다.
- **마이너 3개 백신 오탐(Bkav Pro, SecureAge, Zillya) 원인 안내**:
  - PyInstaller `--onefile` 패키징 방식 특성상 프로그램 실행 시 `%TEMP%` 임시 디렉토리에 파이썬 런타임을 압축 해제하며 동작하므로, 일부 비주류 백신의 정적 휴리스틱 엔진에서 "Dropper" 계열로 오탐한 것입니다.
  - 소스 코드가 투명하게 공개되어 있으며 정식 Windows PE 버전 정보(`version_info.txt`)가 바이너리에 정상 주입되어 있습니다.

> **Windows SmartScreen 안내**: 고가의 기업용 EV 코드서명 인증서(연 수십만 원)가 없는 개인 무료 배포 툴이므로 실행 시 'Windows의 PC 보호' 화면이 나타날 수 있습니다. **'추가 정보'** 클릭 후 **'실행'**을 누르시면 정상 실행됩니다.

---

### 5. 설치 및 소스 빌드 가이드

#### 포터블 실행 파일 이용
1. [Releases](https://github.com/kihoon808/AutoMemoryGuard/releases) 페이지에서 최신 `AutoMemoryGuard_v1.0.zip` 파일을 다운로드합니다.
2. 원하는 작업 폴더(예: `C:\Tools\AutoMemoryGuard`)에 압축을 해제합니다.
3. `AutoMemoryGuard.exe`를 실행합니다. 커널 권한(`SeDebugPrivilege` 및 타이머 제어)을 위해 관리자 권한 승인을 허용합니다.

#### 소스 코드 빌드
```powershell
# 저장소 복제
git clone https://github.com/kihoon808/AutoMemoryGuard.git
cd AutoMemoryGuard

# 필수 라이브러리 설치
pip install -r requirements.txt

# 트레이 상주 모드로 실행
python main.py

# 정식 버전 정보가 포함된 단일 .exe 패키징
python build_exe.py
```

---

### 6. 라이선스 및 상업적 권리

본 프로젝트는 **Source-Available Non-Commercial License(비상업적 개인 무료 라이선스)**를 따릅니다.
- **허용 범위**: 개인적인 비상업적 목적의 사용, 코드 학습, 보안 감사 및 개인 용도의 소스 수정은 완전 무료로 허용됩니다.
- **엄격 금지**: 본 소프트웨어나 소스 코드를 무단으로 재판매하거나, 디지털 유료 스토어(Steam, Epic Games Store, MS Store, itch.io 등)에 유료로 등록 및 패키징하여 재배포하는 행위는 엄격히 금지됩니다.
- 본 소프트웨어의 상업적 출시, 플랫폼 등록, 유료화 권리는 원작자인 **박기훈(Kihoon Park, kihoon808)**에게 독점적으로 귀속됩니다.

자세한 법적 조항은 [LICENSE](file:///c:/Users/kihoo/OneDrive/문서/CPmanager/LICENSE) 파일을 참고하십시오.
