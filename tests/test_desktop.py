"""桌面壳（`novel_harness/desktop.py` + `desktop/NovelHarness.spec` + `scripts/build_dmg.sh`，ADR 0050）。

窗口本身（pywebview）在测试里开不了，这儿钉的是**能不开窗口就验的那几条**：东西放在哪、
没有终端时输出往哪儿去、打包清单有没有把「PyInstaller 自己看不见的东西」点名收进来。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from novel_harness import desktop

ROOT = Path(__file__).resolve().parents[1]


def test_the_three_locations_are_where_a_mac_app_keeps_them(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """库在 Application Support（不进 iCloud 同步的 Documents），稿子在 Documents（作者要用
    WPS 开它），日志在 Library/Logs。**这几个位置一旦发出去就很难改**（ADR 0007 的教训）。"""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    assert desktop.data_dir() == tmp_path / "Library" / "Application Support" / "Novel Harness"
    assert desktop.books_dir() == tmp_path / "Documents" / "Novel Harness"
    assert desktop.log_path() == tmp_path / "Library" / "Logs" / "Novel Harness" / "novel-harness.log"


def test_without_a_terminal_both_streams_land_in_the_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """窗口版的 stdout / stderr 是 `None`，库里任何一句 print 都会炸；壳第一件事是把它们接到日志上。"""
    log = tmp_path / "logs" / "novel-harness.log"
    saved = (sys.stdout, sys.stderr)
    try:
        monkeypatch.setattr(sys, "stdout", None)
        monkeypatch.setattr(sys, "stderr", None)
        desktop._redirect_output(log)
        print("一句到 stdout")
        print("一句到 stderr", file=sys.stderr)
        sys.stdout.flush()
    finally:
        sys.stdout, sys.stderr = saved
    assert log.read_text(encoding="utf-8") == "一句到 stdout\n一句到 stderr\n"


def test_the_bundle_recipe_collects_what_pyinstaller_cannot_see() -> None:
    """打包清单必须点名：包里的非 .py 文件（前端产物 / 迁移脚本 / 模型窗口表）、jieba 的词典、
    uvicorn 按字符串 import 的那几个模块。漏一样的症状都是「本机好好的、别人机器上一片白」。"""
    spec = (ROOT / "desktop" / "NovelHarness.spec").read_text(encoding="utf-8")
    assert 'collect_data_files("novel_harness")' in spec
    assert 'collect_data_files("jieba")' in spec
    assert 'collect_submodules("uvicorn")' in spec
    assert 'collect_submodules("novel_harness")' in spec
    assert "console=False" in spec, "窗口版不该带终端"
    # 版本号的真值在 pyproject：spec 里读它，不抄一份。
    assert "tomllib" in spec and f'"{_pyproject_version()}"' not in spec


def _pyproject_version() -> str:
    import tomllib

    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def test_the_package_reports_the_same_version_as_pyproject() -> None:
    """`novel_harness.__version__` 是第二份拷贝（provider 的 User-Agent 要在没有 dist-info 的
    PyInstaller 包里也读得到，所以没法只靠 importlib.metadata）。两份手改，这条钉它们一致——
    发版时改了 pyproject 忘了这儿，装出来的包会自报旧版本号。"""
    import novel_harness

    assert novel_harness.__version__ == _pyproject_version()


def test_the_build_script_signs_the_app_and_makes_a_dmg() -> None:
    script = (ROOT / "scripts" / "build_dmg.sh").read_text(encoding="utf-8")
    assert "npm run build" in script, "前端产物要先构建，否则包里是只读原型"
    assert "codesign --force --deep --sign -" in script
    assert "hdiutil create" in script
    assert "Applications" in script, "dmg 里要有那个拖进去的 Applications 快捷方式"


def test_the_window_opens_the_desktop_flavour_of_the_page() -> None:
    """壳开的地址带 `?desktop=1`——前端靠它给 `<html>` 挂 `desktop`，顶栏才知道顶上没有
    系统标题条、要给三颗窗口按钮让位（`frontend/src/desktop.ts`）。两头念的是同一个词。"""
    from novel_harness import desktop

    assert desktop.DESKTOP_QUERY == "desktop=1"
    ts = (ROOT / "frontend" / "src" / "desktop.ts").read_text(encoding="utf-8")
    assert 'DESKTOP_QUERY = "desktop"' in ts
    source = (ROOT / "src" / "novel_harness" / "desktop.py").read_text(encoding="utf-8")
    assert 'f"{started.url}/?{DESKTOP_QUERY}"' in source


def test_the_shell_api_only_drags_on_macos_and_only_with_a_window() -> None:
    """`drag()` / `zoom()` 没有窗口时是空操作（页面在壳还没把窗口交过来之前就可能叫它）。"""
    from novel_harness.desktop import _ShellApi

    api = _ShellApi()
    api.drag()  # 不抛
    api.zoom()  # 不抛
