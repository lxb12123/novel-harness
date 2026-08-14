import { useSyncManuscript } from "../api/hooks";
import { ApiError } from "../api/client";

// 「读回改动」 —— 作者在 WPS / VSCode / 手机上改完稿子回到工作台之后，把那些改动
// 读进库的那一下。
//
// ── 它补的是哪半条回路 ──────────────────────────────────────────────────────
//
// 正文他**看得见**（章目录和正文都直接扫磁盘，ADR 0007：磁盘是正文的真相源，
// 且 `useChapterText` 切回标签页就重取）。可**后台整理是按库里那份快照跑的**
// （`extraction_run.snapshot_id`，`api/autopilot.py` 自己不 sync），而快照只有这一下
// 落得下——不点它，他在 WPS 里新写的那一章会被**照着旧正文分析**，右栏「待确认」里
// 摆出来的是上一版的情节，而屏幕上没有任何东西说它读的是旧的。
//
// **2026-08-14 起这是它唯一的理由**：那天「记录这句」和整条选区工具条删了，
// 手工声明那条路（`locate` 搜快照）从浏览器里退场，只剩 `nh declare` / `nh locate`。
//
// ── 为什么是一颗按钮，不是 file-watch ──────────────────────────────────────
//
// 1. **它是一条写路径。** 每一次外部保存都落一条快照，而快照是证据的锚。
//    一个跟着磁盘自动写库的后台线程 = 作者按不停、也看不见的写入面；
//    而 ADR 0007 那句「磁盘先、DB 跟」里的「跟」是**他的动作**。
// 2. **它防的事一次点击就能补回来**，而它引入的事（后台线程 + 平台相关的监听依赖 +
//    编辑器的临时文件/原子改名噪声）修不回来。这个仓库的规矩是「系统不确定时闭嘴」。
// 3. 那条依赖会进 wheel，而这个产品的分发叙事是「一条命令，不装别的」。
//    要加它，先有触发条件（作者真的抱怨过「我改完还要点一下」≥ 几次），同 v1.1 那张表。
//
// **一个组件，两处挂载**（中栏工具条 + 声明抽屉的「找不到」那一档）：同一颗按钮、
// 同一句话、同一份回执。此前这个仓库为「同一颗按钮在两块屏幕上行为不同」栽过一次
// （那颗「看看最新的」靠调用方传 `onRefresh?.()`），修法就是把行为收进组件自己身上。
export function SyncButton({ pid }: { pid: string }) {
  const sync = useSyncManuscript(pid);
  const failed = sync.error instanceof ApiError ? sync.error : null;

  return (
    <span className="syncbox">
      <button
        type="button"
        disabled={sync.isPending}
        title="把你在 WPS、VSCode 等软件里改过的章节读进来。不读进来的话，系统整理这一章时看的还是旧正文。"
        onClick={() => sync.mutate()}
      >
        {sync.isPending ? "读取中…" : "读回改动"}
      </button>
      {/* 说清它在干什么。**跑完之后换成后端那句回执**——措辞的唯一出处在后端
          （`api/manuscript.py`），这一层一个字都不拼：「这次算干了活还是白跑一趟」
          是产品判断，两份判断迟早会互相说反话。 */}
      <span className={"syncsay" + (failed ? " err" : "")} role="status">
        {failed
          ? "没能读回来：" + failed.message
          : sync.data
            ? sync.data.headline
            : "在别的软件里改过这本书，就点它一下。"}
      </span>
      {sync.data?.notes.map((note) => (
        <span className="syncnote" key={note}>
          {note}
        </span>
      ))}
    </span>
  );
}
