"""AutoMemoryGuard 릴리즈 ZIP 패키징 자동화 스크립트.

dist/AutoMemoryGuard.exe, README_USER.txt, LICENSE.txt를 묶어
dist/AutoMemoryGuard_v1.0.0.zip으로 패키징합니다.
"""

from pathlib import Path
import sys
import zipfile


def create_release_zip() -> int:
    project_root = Path(__file__).resolve().parent
    dist_dir = project_root / "dist"
    exe_file = dist_dir / "AutoMemoryGuard.exe"
    readme_file = project_root / "README_USER.txt"
    license_file = project_root / "LICENSE.txt"

    if not exe_file.exists():
        print(f"오류: 바이너리 파일이 존재하지 않습니다: {exe_file}", file=sys.stderr)
        print("먼저 python build_exe.py를 실행하여 바이너리를 빌드하세요.", file=sys.stderr)
        return 1

    zip_path = dist_dir / "AutoMemoryGuard_v1.0.0.zip"
    print(f"릴리즈 ZIP 패키징 시작: {zip_path}")

    files_to_pack = [
        (exe_file, "AutoMemoryGuard.exe"),
        (readme_file, "README.txt"),
        (license_file, "LICENSE.txt"),
    ]

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for src_path, arc_name in files_to_pack:
            if src_path.exists():
                print(f"  + 압축 추가: {arc_name} ({src_path.stat().st_size:,} bytes)")
                zf.write(src_path, arc_name)
            else:
                print(f"  ! 경고: 파일 누락: {src_path}", file=sys.stderr)

    zip_size_mb = zip_path.stat().st_size / (1024 * 1024)
    print("==================================================")
    print(f"패키징 완료: {zip_path.name} ({zip_size_mb:.2f} MB)")
    print(f"전체 경로: {zip_path}")
    print("GitHub Releases 또는 배포 커뮤니티에 이 zip 파일을 업로드하세요.")
    print("==================================================")
    return 0


if __name__ == "__main__":
    sys.exit(create_release_zip())
