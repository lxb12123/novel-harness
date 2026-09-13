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
    # 版本号只有 pyproject 一处：spec 里读它，不抄一份。
    assert 'tomllib' in spec and '"0.0.1"' not in spec


def test_the_build_script_signs_the_app_and_makes_a_dmg() -> None:
    script = (ROOT / "scripts" / "build_dmg.sh").read_text(encoding="utf-8")
    assert "npm run build" in script, "前端产物要先构建，否则包里是只读原型"
    assert "codesign --force --deep --sign -" in script
    assert "hdiutil create" in script
    assert "Applications" in script, "dmg 里要有那个拖进去的 Applications 快捷方式"
