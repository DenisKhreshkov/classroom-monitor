"""Create a source-only deployment ZIP from an explicit allowlist."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent
SOURCE_FILES = (
    ".env.example",
    ".gitignore",
    "README.md",
    "PROJECT_FILES.md",
    "VERIFICATION.md",
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "main.py",
    "config.py",
    "models.py",
    "errors.py",
    "classroom_client.py",
    "google_authentication.py",
    "authorize_google.py",
    "materials.py",
    "assignment_analyzer.py",
    "telegram_bot.py",
    "telegram_setup.py",
    "database.py",
    "state_store.py",
    "package_project.py",
    "credentials/.gitkeep",
    ".github/workflows/daily_monitor.yml",
    ".github/workflows/tests.yml",
    "tests/conftest.py",
    "tests/test_database.py",
    "tests/test_analyzer.py",
    "tests/test_notifications.py",
    "tests/test_config.py",
    "tests/test_monitor.py",
    "tests/test_classroom.py",
    "tests/test_materials.py",
    "tests/test_state_store.py",
    "tests/test_workflow.py",
    "tests/test_telegram_setup.py",
    "tests/test_packaging.py",
    "tests/test_google_authorization.py",
)


def main():
    missing = [name for name in SOURCE_FILES if not (ROOT / name).is_file()]
    if missing:
        raise SystemExit("Package source files missing: " + ", ".join(missing))
    target = ROOT / "dist" / "ClassroomMonitor.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(target, "w", ZIP_DEFLATED) as archive:
        for name in SOURCE_FILES:
            archive.write(ROOT / name, arcname=name)
    print(f"Packaged {len(SOURCE_FILES)} source files into dist/ClassroomMonitor.zip")


if __name__ == "__main__":
    main()
