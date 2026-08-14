"""把库和磁盘对一遍 —— **作者不按任何按钮，它自己发生**（三层方案第二层）。

## 它替掉的是哪颗按钮

2026-08-14 之前，「我在 WPS 里改了稿，让系统读回来」是一颗叫「读回改动」的按钮。
那颗按钮要求作者先理解一件他不该知道的事：**屏幕上的正文来自磁盘，而库里那份快照
来自这颗按钮**。他不点，后台整理分析的就是旧正文，而屏幕上没有任何东西说得出来。

这条端点让那件事自动发生。两个触发点，代价差两个数量级：

    开书 / 刷新页面   `deep=true`    全读 + 算 hash   722 章实测 244ms，**一次**
    切回这个标签页    `deep=false`   只 stat          722 章实测 ~4ms，**零读盘**

## 为什么快路那一档不能是唯一的一档

`deep=false` 走的是 Git 的 index 用了二十年的那一招：库里记着每个文件的
`(mtime, size)`，先 stat，一致就信缓存，只有对不上才读文件算 sha（迁移 015）。

**而 mtime 会撒谎**：`rsync -t` / `cp -p` / 从备份恢复 / 某些编辑器都可能保留原 mtime。
大小又恰好没变的话，快路这一关就漏了（Git 自己也有这个病，就是那个著名的
"racy git"）。在此之前兜这一档的是作者手点那颗按钮——它退休之后，兜底就是
**开书时那一次 deep**。

## 回执里为什么必须有 `refused`

一个章标写坏了的文件（作者在 WPS 里把「第二章 落幕」改成了「落幕」）会让那一章
从此既总结不了也抽不了。而这条路径**作者没按过任何按钮**——不把它带出来的话，
「什么都没发生」和「有一章一直读不回来」在屏幕上长得一模一样（约束 8）。

`importer.reconcile` 因此逐章收集而不是抛：第 500 章的章标写坏了，不该让
第 100 章的改动也读不回来。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from .. import importer
from ..graph.store import GraphStore
from .deps import get_store, load_project

router = APIRouter()


class ChapterRefusal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: int = Field(ge=1)
    message: str = Field(min_length=1)


class ReconcileOutcome(BaseModel):
    """出参是 Pydantic（铁律 4）。**措辞不在这一层**——它今天没有屏幕。

    这条路径是无人值守的，回执的读者是前端那个「有没有变、要不要失效缓存」的判断，
    不是作者的眼睛。哪天要把 `refused` 摆上屏，措辞归 `api/manuscript.py`
    （同 `SyncOutcome.headline`：措辞的唯一出处在后端，浏览器一个字都不拼）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    checked: int = Field(ge=0)
    reread: tuple[int, ...] = ()
    refreshed: tuple[int, ...] = ()
    refused: tuple[ChapterRefusal, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.refreshed)


@router.post(
    "/api/projects/{project_id}/reconcile",
    response_model=ReconcileOutcome,
)
def reconcile_manuscript(
    deep: bool = Query(
        default=False,
        description="忽略 stat 快路，每一章都重新读+算哈希（开书那一次用）。",
    ),
    proj: Any = Depends(load_project),
    graph: GraphStore = Depends(get_store),
) -> ReconcileOutcome:
    """把这本书和磁盘对一遍。**不花钱**（一次模型调用都没有）。

    **收的是读写交集，不是只读的 `StoryGraph`**：对上之后要把变了的章读进库
    （`put_chapter`）。写入面在签名上看得见，同 `api/autopilot.py` 那一处。
    """
    report = importer.reconcile(graph, proj.id, Path(proj.root_path), deep=deep)
    return ReconcileOutcome(
        checked=report.checked,
        reread=report.reread,
        refreshed=report.refreshed,
        refused=tuple(
            ChapterRefusal(chapter=number, message=message) for number, message in report.refused
        ),
    )
