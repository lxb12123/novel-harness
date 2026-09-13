"""PyInstaller 的入口（`desktop/NovelHarness.spec` 指着它）。壳本身在包里：`novel_harness.desktop`。"""

from novel_harness.desktop import main

raise SystemExit(main())
