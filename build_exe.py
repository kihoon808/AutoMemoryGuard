"""AutoMemoryGuard PyInstaller 단일 바이너리(.exe) 빌드 자동화 스크립트.

customtkinter 테마 및 에셋 수집(--collect-all customtkinter),
콘솔 숨김(--noconsole), UAC 관리자 권한 매니페스트(--uac-admin),
단일 실행 파일(--onefile) 패키징을 수행합니다.
"""

from pathlib import Path
import subprocess
import sys


def build() -> int:
    """PyInstaller 빌드 파이프라인을 실행합니다."""
    project_root = Path(__file__).resolve().parent
    main_py = project_root / "main.py"
    dist_dir = project_root / "dist"
    build_dir = project_root / "build"
    config_dir = project_root / "config"

    print("==================================================")
    print("AutoMemoryGuard .exe 바이너리 빌드 시작")
    print(f"프로젝트 디렉토리: {project_root}")
    print("==================================================")

    # config 디렉토리가 없으면 자동 생성하여 PyInstaller 누락 오류 방지
    config_dir.mkdir(parents=True, exist_ok=True)

    # Windows PyInstaller 데이터 경로 구분자 ';'
    add_data_flag = f"{config_dir};config"
    assets_dir = project_root / "assets"
    add_assets_flag = f"{assets_dir};assets"
    icon_path = assets_dir / "icon.ico"
    version_file = project_root / "version_info.txt"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name=AutoMemoryGuard",
        "--noconsole",                  # 콘솔 창 없는 GUI/트레이 전용 실행
        "--onefile",                    # 단일 .exe 포터블 파일 생성
        "--clean",                      # 빌드 캐시 초기화
        "--uac-admin",                  # UAC 관리자 권한 자동 요청 매니페스트 주입
        "--collect-all", "customtkinter",  # customtkinter JSON 테마 및 폰트/에셋 전체 수집
        "--exclude-module", "numpy",
        "--exclude-module", "pandas",
        "--exclude-module", "torch",
        "--exclude-module", "scipy",
        "--exclude-module", "matplotlib",
        "--exclude-module", "playwright",
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc",
        "--exclude-module", "tkinter.test",
        f"--add-data={add_data_flag}",
        f"--add-data={add_assets_flag}",
        f"--distpath={dist_dir}",
        f"--workpath={build_dir}",
    ]

    # Windows PE 버전 정보 리소스 파일 연결
    if version_file.exists():
        cmd.append(f"--version-file={version_file}")

    # 아이콘 파일이 존재하면 바이너리 아이콘으로 지정
    if icon_path.exists():
        cmd.append(f"--icon={icon_path}")

    cmd.append(str(main_py))

    print("실행 명령어:")
    print(" ".join(cmd))
    print("--------------------------------------------------")

    result = subprocess.run(cmd, cwd=str(project_root))

    if result.returncode == 0:
        exe_path = dist_dir / "AutoMemoryGuard.exe"
        print("==================================================")
        print("빌드 성공 완료!")
        print(f"생성된 바이너리: {exe_path}")
        if exe_path.exists():
            size_mb = exe_path.stat().st_size / (1024 * 1024)
            print(f"바이너리 파일 크기: {size_mb:.2f} MB")
        print("==================================================")
    else:
        print(f"빌드 실패: 에러 코드 {result.returncode}", file=sys.stderr)

    return result.returncode


if __name__ == "__main__":
    sys.exit(build())
