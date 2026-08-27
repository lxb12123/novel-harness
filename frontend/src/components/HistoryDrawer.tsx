import { useState } from "react";
import { useDeleteSnapshot, useHistory, useRestoreSnapshot } from "../api/hooks";
import { ApiError } from "../api/client";
import { saidToTheAuthor } from "../correctionError";
import { lineDiff, diffStats } from "../diff";
import { useLanguage } from "../language";

// 历史版本（§2.2 SnapshotDiffView）：这一章内容去重后的历史版本 vs 当前，行级 diff，
// 外加还原 / 删除。
//
// **还原没有专门的端点**：它就是把那一版的正文按普通保存写回磁盘。后端按内容去重，
// 写回去正好命中已存在的那条，于是「当前」指回它、列表不会长出第三版。
// **删除有前提**：当前那一版删不掉（删了这一章就没有当前正文了），被证据引着的那一版
// 也删不掉（锚没了，那条依据就只是一句没有出处的话）。两条都由后端拒绝，这里只负责
// 把拒绝的理由说成人话。

function when(iso: string): string {
  // "2026-07-19T08:12:34.567Z" → "07-19 08:12:34"（够作者认出「哪一版」）
  return iso.replace("T", " ").replace(/\.\d+Z?$/, "").slice(5);
}

/** 后端拒绝删除时，把错误码翻成作者看得懂的一句话。
 *
 *  **`snapshot_is_current`/`snapshot_in_use` 故意不查 `saidToTheAuthor`。**
 *  这两档后端自己带的 `.message` 是 `str(exc)`（`graph/store.py::SnapshotInUse`），
 *  里头**直接嵌着裸快照 id**（`快照 snapshot:01J8… 还被引用着（证据 3 / 抽取 0 /
 *  提案 0）`）——那是 `screenGuard.ts` 第三张网（`RAW_ID`）要拦的形状，原样透出去
 *  就是给作者看一串他认不得的编号。国际化第四批推进这一批改造时在这儿也踩过一次
 *  （同 `RosterDrawer.tsx::Failure` 那次教训）：**统一接 `saidToTheAuthor` 之前，
 *  先确认某个码的后端消息真的对作者安全**，不能因为"看起来该统一"就无差别接。
 *  这两档继续用自己拼的句子（用 `err.body.usage.evidence` 这个数字，不用 id）。
 *  只有"认不出任何一档、也没有已知安全消息"的兜底才走 `saidToTheAuthor`。*/
function refusal(err: unknown): string {
  const language = useLanguage.getState().language;
  const generic = () =>
    language === "zh" ? "没能删掉这一版，请再试一次。" : "Couldn't delete this version — try again.";
  if (!(err instanceof ApiError)) return generic();
  if (err.code === "snapshot_is_current") {
    return language === "zh"
      ? "正文现在就是这一版，删不掉。可以先还原到别的版本，再回来删它。"
      : "This is the current version of the text, so it can't be deleted. Restore a different version first, then come back to delete it.";
  }
  if (err.code === "snapshot_in_use") {
    const used = err.body.usage as { evidence?: number } | undefined;
    const n = Number(used?.evidence ?? 0);
    if (language === "zh") {
      return n > 0
        ? `这一版被 ${n} 条原文依据引用着——删了它，那些依据就找不到出处了。`
        : "这一版还被别的记录引用着，删了会让它们找不到出处。";
    }
    return n > 0
      ? `This version is referenced by ${n} pieces of evidence — deleting it would leave them without a source.`
      : "This version is still referenced by other records — deleting it would leave them without a source.";
  }
  return saidToTheAuthor(err) ?? generic();
}

type Pending = { kind: "restore" | "delete"; id: string };

export function HistoryDrawer({
  pid,
  chapter,
  dirty = false,
  onRestored,
  onClose,
}: {
  pid: string;
  chapter: number;
  /** 编辑器里有没有还没保存的修改——有的话还原会覆盖掉它们，得先说一声。 */
  dirty?: boolean;
  onRestored?: () => void;
  onClose: () => void;
}) {
  const { data, isFetching } = useHistory(pid, chapter, true);
  const restore = useRestoreSnapshot(pid, chapter);
  const remove = useDeleteSnapshot(pid, chapter);
  const snaps = data ?? [];
  const current = snaps.find((s) => s.is_current) ?? snaps[snaps.length - 1];
  // 默认对比：当前之前最近的一版（没有就当前自己）。
  const prior = [...snaps].reverse().find((s) => !s.is_current);
  const [selId, setSelId] = useState<string | null>(prior?.snapshot_id ?? null);
  const [pending, setPending] = useState<Pending | null>(null);
  const selected = snaps.find((s) => s.snapshot_id === selId) ?? prior ?? current;

  const lines = selected && current ? lineDiff(selected.text, current.text) : [];
  const stats = diffStats(lines);
  const isSameAsCurrent = selected?.snapshot_id === current?.snapshot_id;
  const target = pending && snaps.find((s) => s.snapshot_id === pending.id);
  const busy = restore.isPending || remove.isPending;

  function confirm() {
    if (!pending || !target) return;
    if (pending.kind === "restore") {
      restore.mutate(target.text, {
        onSuccess: () => {
          setPending(null);
          onRestored?.();
          onClose();
        },
      });
    } else {
      remove.mutate(target.snapshot_id, {
        onSuccess: () => {
          if (selId === target.snapshot_id) setSelId(null);
          setPending(null);
        },
      });
    }
  }

  return (
    <>
      <div className="backdrop" onClick={onClose} />
      <div className="drawer wide">
        <h3>第 {chapter} 章 · 历史版本</h3>
        <div className="sub">
          每次保存都会留下一版。想回到哪一版，把鼠标移到它上面点「还原」——正文会变回那一版的
          样子，其他版本都还留着，随时能再换回来。
        </div>

        {isFetching && !data && <div className="empty">加载中…</div>}
        {!isFetching && snaps.length <= 1 && (
          <div className="empty">
            这一章目前只有一版，还没有别的版本可以还原。以后每保存一次改动，这里就会多一版。
          </div>
        )}

        {pending && target && (
          <div className={"hist-confirm" + (pending.kind === "delete" ? " danger" : "")}>
            <span className="q">
              {pending.kind === "restore"
                ? `把正文还原到 ${when(target.created_at)} 那一版？`
                : `删掉 ${when(target.created_at)} 这一版？删了就找不回来了。`}
            </span>
            {/* 「确认还原」而不是「还原」：确认条弹出来时，行里那个「还原」还在，
                两个按钮同名会让作者不确定自己按的是哪一个。 */}
            <button disabled={busy} onClick={confirm}>
              {busy ? "处理中…" : pending.kind === "restore" ? "确认还原" : "确认删除"}
            </button>
            <button disabled={busy} onClick={() => setPending(null)}>
              取消
            </button>
          </div>
        )}
        {pending?.kind === "restore" && dirty && (
          <div className="warn">编辑器里还有没保存的修改，还原会把它们覆盖掉。</div>
        )}
        {remove.error && <div className="err-box">{refusal(remove.error)}</div>}
        {restore.error && <div className="err-box">{refusal(restore.error)}</div>}

        {snaps.length > 1 && (
          <div className="hist">
            {/* list/listitem 不是装饰：一行里有三个可点的东西（选中、还原、删除），
                没有行这一层，读屏和键盘都只能听见一串孤立的按钮。 */}
            <div className="hist-list" role="list" aria-label="历史版本">
              {[...snaps].reverse().map((s) => (
                <div
                  key={s.snapshot_id}
                  role="listitem"
                  className={"hist-item" + (s.snapshot_id === selId ? " on" : "")}
                >
                  <button className="hist-pick" onClick={() => setSelId(s.snapshot_id)}>
                    {when(s.created_at)}
                    {s.is_current && <span className="cur">当前</span>}
                    <span className="len">{s.text.length} 字</span>
                  </button>
                  <span className="hist-actions">
                    {!s.is_current && (
                      <button
                        className="hist-act"
                        title="把正文换回这一版"
                        onClick={() => setPending({ kind: "restore", id: s.snapshot_id })}
                      >
                        还原
                      </button>
                    )}
                    <button
                      className="hist-act"
                      disabled={s.is_current}
                      title={s.is_current ? "正文现在就是这一版，删不掉" : "删掉这一版"}
                      onClick={() => setPending({ kind: "delete", id: s.snapshot_id })}
                    >
                      删除
                    </button>
                  </span>
                </div>
              ))}
            </div>
            <div className="hist-diff">
              {isSameAsCurrent ? (
                <div className="empty">
                  这就是正文现在的样子。选左边别的版本，能看到它和现在差在哪。
                </div>
              ) : (
                <>
                  <div className="row" style={{ color: "var(--dim)", fontSize: 12, marginBottom: 6 }}>
                    从 {selected && when(selected.created_at)} 到现在：
                    <span style={{ color: "var(--k)" }}> +{stats.add}</span>
                    <span style={{ color: "var(--warn)" }}> −{stats.del}</span> 行
                  </div>
                  <pre className="diff">
                    {lines.map((l, i) => (
                      <div key={i} className={"dl " + l.t}>
                        <span className="sign">{l.t === "add" ? "+" : l.t === "del" ? "−" : " "}</span>
                        {l.s || " "}
                      </div>
                    ))}
                  </pre>
                </>
              )}
            </div>
          </div>
        )}

        <div className="row" style={{ marginTop: 10 }}>
          <button onClick={onClose}>关闭</button>
        </div>
      </div>
    </>
  );
}
