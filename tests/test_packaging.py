from zipfile import ZipFile

import package_project


def test_archive_only_contains_allowlisted_source(tmp_path, monkeypatch):
    for name in package_project.SOURCE_FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test source", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=private", encoding="utf-8")
    (tmp_path / "credentials/token.json").write_text("SECRET", encoding="utf-8")
    monkeypatch.setattr(package_project, "ROOT", tmp_path)
    package_project.main()
    with ZipFile(tmp_path / "dist/ClassroomMonitor.zip") as archive:
        assert set(archive.namelist()) == set(package_project.SOURCE_FILES)
        assert ".env" not in archive.namelist()
        assert "credentials/token.json" not in archive.namelist()
