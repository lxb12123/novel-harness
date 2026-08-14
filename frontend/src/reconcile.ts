import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, proj } from "./api/client";
import type { ReconcileOutcome } from "./api/types";

// 把库和磁盘对一遍 —— **作者不按任何按钮，它自己发生**（三层方案第二层）。
//
// ── 它替掉的是哪颗按钮 ─────────────────────────────────────────────────────
//
// 「读回改动」要求作者先理解一件他不该知道的事：**屏幕上的正文来自磁盘，而库里那份
// 快照来自这颗按钮**。他不点，后台整理分析的就是旧正文，而屏幕上没有任何东西说得出来。
//
// ── 两个触发点，代价差两个数量级 ──────────────────────────────────────────
//
//     开书 / 换书      深对一次（全读 + 算哈希）    722 章实测 244ms，**一次**
//     切回这个标签页    快对（只 stat）             722 章实测 ~4ms，**零读盘**
//
// **为什么不用定时器**：定时器要么太密（白烧电）、要么太疏（留窗口），而
// 「作者切回来」是一个**精确、免费、语义正确**的信号——他不在这个页面的时候，
// 检测出来给谁看？（同 `useChapterText` 那条 `refetchOnWindowFocus`，同一个信号。）
//
// **为什么开书那一次要深对**：快路信的是 `(mtime, size)`，而 mtime 会撒谎
// （`rsync -t` / `cp -p` / 从备份恢复都可能保留原 mtime）。那一档只有全量哈希抓得到，
// 在此之前兜它的是作者手点那颗按钮。后端 `tests/test_reconcile.py` 有一条专门钉它。

/** 对完之后要失效的读端。**只在真的有章变了时才失效**——
 *  没变还整片失效等于每次 alt-tab 都把右栏全部重取一遍。 */
function invalidateAfterChange(qc: ReturnType<typeof useQueryClient>, pid: string): void {
  for (const key of ["chapters", "text", "history", "matrix", "state", "constraints", "check"]) {
    qc.invalidateQueries({ queryKey: [key, pid] });
  }
}

async function reconcile(pid: string, deep: boolean): Promise<ReconcileOutcome> {
  return api.post<ReconcileOutcome>(proj(pid, `/reconcile?deep=${deep}`), {});
}

/**
 * 开书时深对一次，之后每次切回标签页快对一次。
 *
 * **失败一律当无事发生**（同 `useRunAutopilot`）：这条路径作者没按过任何按钮，
 * 弹一个错等于每次 alt-tab 骂他一次。真出事的那一档（某一章章标写坏了）由
 * 后台整理的回执负责说——那一章他一打开就会撞上。
 */
export function useReconcileOnFocus(pid: string | null): void {
  const qc = useQueryClient();
  // 开过书的那一本记在这儿：**换书要重新深对一次**，而同一本书只深对一次。
  const deepDone = useRef<string | null>(null);
  // 上一次还没回来就别再发一条：alt-tab 连按几下会叠出好几个并发的写事务。
  const inFlight = useRef(false);

  useEffect(() => {
    if (!pid) return;

    const run = async (deep: boolean) => {
      if (inFlight.current) return;
      inFlight.current = true;
      try {
        const outcome = await reconcile(pid, deep);
        if (outcome.refreshed.length > 0) invalidateAfterChange(qc, pid);
      } catch {
        // 端点不在 / 网络断了 —— 下一次回焦再试。**不提示、不重试。**
      } finally {
        inFlight.current = false;
      }
    };

    if (deepDone.current !== pid) {
      deepDone.current = pid;
      void run(true);
    }

    const onVisible = () => {
      if (document.visibilityState === "visible") void run(false);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [pid, qc]);
}
