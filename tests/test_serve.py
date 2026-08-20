"""`nh serve` 与前端产物的打包路径。

本文件守的是**一类特别难自查的故障**：分发路径坏掉，但什么都不报错。

`api/app.py` 里那个 `_DIST` 曾经指 `parents[3]/"frontend"/"dist"`——仓库根下面。
在源码树里跑得好好的；一旦 `pip install` 进 site-packages，`parents[3]` 指向的是
site-packages 的上一层，那儿没有 `frontend/`。于是装出来的包**静默**降级成
`api/static` 的只读原型：`/` 照样 200、没有异常、CI 全绿，只有作者看得见工作台不见了。
（§10 约束 8 说的就是这种：漂亮的空结果 + exit 0。）

所以这里有两条守卫是本文件存在的理由，别的都是附带：
- `test_webui_lives_inside_the_package` —— 产物必须在包内，否则 wheel 带不走它。
- `test_vite_outdir_and_dist_agree` —— **Vite 往哪儿写**和**FastAPI 从哪儿读**是两个
  分别住在 ts 和 py 里的字面量，中间没有任何东西把它们连起来。改一个忘一个，
  结果同样是「wheel 里没有前端」，而整套测试照绿（测试套件从不构建前端）。
"""

from __future__ import annotations

import re
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from novel_harness.api import app as app_mod
from novel_harness.api.launch import LaunchError, _bind

REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = Path(app_mod.__file__).resolve().parents[1]  # …/novel_harness


# ── 打包路径（本文件的头牌）─────────────────────────────────────────────────


def test_webui_lives_inside_the_package() -> None:
    """前端产物的目录必须在 `novel_harness/` 包内。

    判据是「在不在包内」而不是「路径长什么样」——因为唯一重要的事实是
    `uv_build` 只打包模块目录下的东西（实测：非 .py 也一并打进去）。产物落在包外，
    就必须另加一套 force-include 或 build hook，那是第二份要维护、且会被忘记的东西。
    """
    assert app_mod._DIST.is_relative_to(PKG_ROOT), (
        f"前端产物目录跑到包外面去了：{app_mod._DIST}\n"
        f"包内是 {PKG_ROOT}。落在包外 = `uv build` 带不走它 = 装出来的包没有工作台，"
        "而且不报错，只是静默降级成 api/static 的只读原型。"
    )


def test_vite_outdir_and_dist_agree() -> None:
    """Vite 写到哪儿 == FastAPI 从哪儿读。两个字面量，必须指同一个目录。

    这是本仓库里少数几处「两个文件必须同时改」的接缝之一，而且它坏掉的方式是无声的。
    """
    config = (REPO_ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    match = re.search(r"outDir:\s*[\"']([^\"']+)[\"']", config)
    assert match, "vite.config.ts 里找不到 build.outDir——它是打包路径的另一半"

    # outDir 是相对 frontend/ 的（Vite 的 root 就是那儿）
    vite_writes_to = (REPO_ROOT / "frontend" / match.group(1)).resolve()
    assert vite_writes_to == app_mod._DIST, (
        f"Vite 写到 {vite_writes_to}，而 FastAPI 从 {app_mod._DIST} 读。\n"
        "两边不一致 = `npm run build` 之后 wheel 里依然没有前端，且没有任何东西会红。"
    )


def test_webui_built_follows_the_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`webui_built()` 说的是实话——`nh serve` 那句黄色警告靠它。

    没构建时 `/` 仍然返回 200（只读原型），所以「有没有工作台」这件事必须由起服务的
    那一刻主动说出来，不能指望作者从一个 200 里看出来。
    """
    monkeypatch.setattr(app_mod, "_DIST", tmp_path / "webui")
    assert app_mod.webui_built() is False

    (tmp_path / "webui").mkdir()
    (tmp_path / "webui" / "index.html").write_text("<html></html>", encoding="utf-8")
    assert app_mod.webui_built() is True


# ── 端口 ────────────────────────────────────────────────────────────────────


def test_bind_takes_the_port_it_was_asked_for() -> None:
    """端口稳定是有价值的（书签不失效），所以空闲时必须真的用 `wanted`，不是随便挑。"""
    with socket.socket() as probe:  # 先问内核要一个此刻空闲的端口号
        probe.bind(("127.0.0.1", 0))
        wanted = probe.getsockname()[1]

    sock = _bind("127.0.0.1", wanted)
    try:
        assert sock.getsockname()[1] == wanted
    finally:
        sock.close()


def test_bind_falls_back_when_the_port_is_taken() -> None:
    """端口被占（多半是上一个 `nh serve` 还开着）→ 换一个，**不是**报错退出。

    作者要的是「打开工作台」。一个因为 8756 被占就拒绝启动的命令，会把「端口是什么」
    这个问题推给一个不该知道答案的人。
    """
    squatter = socket.socket()
    squatter.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    squatter.bind(("127.0.0.1", 0))
    squatter.listen(1)  # 必须真的 listen：只 bind 不 listen 时 SO_REUSEADDR 允许再绑
    taken = squatter.getsockname()[1]

    try:
        sock = _bind("127.0.0.1", taken)
        try:
            assert sock.getsockname()[1] != taken
            assert sock.getsockname()[1] > 0
        finally:
            sock.close()
    finally:
        squatter.close()


def test_bind_dies_loudly_on_an_unusable_host() -> None:
    """绑不上就得响亮地死，别退化成一个「起来了但连不上」的服务。

    现在 `_bind` 是 `api/launch.py` 的库函数（CLI 薄壳负责把 `LaunchError` 转成退出码 1，
    `demo.sh` 是 `set -e` 的，那个码就是心跳的布尔值）。
    """
    with pytest.raises(LaunchError):
        _bind("203.0.113.1", 0)  # TEST-NET-3，本机不可能有这个地址


# ── 端到端 ──────────────────────────────────────────────────────────────────


def _readline(stream: object, timeout: float) -> str:
    """带超时的 readline —— 服务起来后会永远阻塞，不能裸调。"""
    box: list[str] = []
    worker = threading.Thread(target=lambda: box.append(stream.readline()), daemon=True)  # type: ignore[attr-defined]
    worker.start()
    worker.join(timeout)
    return box[0] if box else ""


def test_serve_creates_the_db_and_prints_one_url(tmp_path: Path) -> None:
    """一条命令 = 建库 + 起服务 + 报出真实端口。子进程跑真服务，零 mock。

    三条断言各有出处：
    - **建库**：`api/deps.py` 的 `_db_path()` 拒绝连一个不存在的路径，所以库必须由
      这条命令先建出来，否则作者第一次跑就撞 500。
    - **stdout 只有一行 URL**：同 `nh init` 的纪律（stdout 是给机器吃的）。以后的桌面壳
      就是靠读这一行知道该把窗口指向哪儿——**所以它还必须是 flush 过的**，
      不能躺在块缓冲里等进程退出。这个测试正是在证明它没躺着。
    - **`--port 0`**：让内核挑端口，CI 上并发跑也不会撞车。
    """
    db = tmp_path / "serve.db"
    proc = subprocess.Popen(
        [sys.executable, "-m", "novel_harness.cli", "serve",
         "--db", str(db), "--port", "0", "--no-open"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        line = _readline(proc.stdout, timeout=30).strip()
        assert re.fullmatch(r"http://127\.0\.0\.1:\d+", line), (
            f"stdout 第一行该是一条 URL，实际是 {line!r}。"
            "（拿不到 = 要么进程没起来，要么那行还躺在块缓冲里没 flush。）"
        )
        assert db.exists(), "库没被建出来——作者第一次跑会撞 api/deps.py 的「库不存在」"

        port = int(line.rsplit(":", 1)[1])
        with socket.create_connection(("127.0.0.1", port), timeout=10):
            pass  # 连得上 = 那个端口号不是印着好看的
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=15)
