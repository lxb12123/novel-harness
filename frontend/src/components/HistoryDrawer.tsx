import { useState } from "react";
import { useHistory } from "../api/hooks";
import { lineDiff, diffStats } from "../diff";

// 版本对比（§2.2 SnapshotDiffView）：这一章内容去重后的历史版本 vs 当前，行级 diff。
// **快照按内容去重、非全量编辑史**——它的第一身份是证据的锚（后端 docstring）。

function when(iso: string): string {
  // "2026-07-19T08:12:34.567Z" → "07-19 08:12:34"（够作者认出「哪一版」）
  return iso.replace("T", " ").replace(/\.\d+Z?$/, "").slice(5);
}

export function HistoryDrawer({
  pid,
  chapter,
  onClose,
}: {
  pid: string;
  chapter: number;
  onClose: () => void;
}) {
  const { data, isFetching } = useHistory(pid, chapter, true);
  const snaps = data ?? [];
  const current = snaps.find((s) => s.is_current) ?? snaps[snaps.length - 1];
  // 默认对比：当前之前最近的一版（没有就当前自己）。
  const prior = [...snaps].reverse().find((s) => !s.is_current);
  const [selId, setSelId] = useState<string | null>(prior?.snapshot_id ?? null);
  const selected = snaps.find((s) => s.snapshot_id === selId) ?? prior ?? current;

  const lines = selected && current ? lineDiff(selected.text, current.text) : [];
  const stats = diffStats(lines);
  const isSameAsCurrent = selected?.snapshot_id === current?.snapshot_id;

  return (
    <>
      <div className="backdrop" onClick={onClose} />
      <div className="drawer wide">
        <h3>第 {chapter} 章 · 版本对比</h3>
        <div className="sub">
          这里保留已保存的正文版本。选择一个版本，查看它与当前正文的差异。
        </div>

        {isFetching && !data && <div className="empty">加载中…</div>}
        {!isFetching && snaps.length <= 1 && (
          <div className="empty">这一章只有一个版本，还没有可对比的历史。</div>
        )}

        {snaps.length > 1 && (
          <div className="hist">
            <div className="hist-list">
              {[...snaps].reverse().map((s) => (
                <button
                  key={s.snapshot_id}
                  className={"hist-item" + (s.snapshot_id === selId ? " on" : "")}
                  onClick={() => setSelId(s.snapshot_id)}
                >
                  {when(s.created_at)}
                  {s.is_current && <span className="cur">当前</span>}
                  <span className="len">{s.text.length} 字</span>
                </button>
              ))}
            </div>
            <div className="hist-diff">
              {isSameAsCurrent ? (
                <div className="empty">这是当前版本，无差异。选左边别的版本看改动。</div>
              ) : (
                <>
                  <div className="row" style={{ color: "var(--dim)", fontSize: 12, marginBottom: 6 }}>
                    从 {selected && when(selected.created_at)} 到当前：
                    <span style={{ color: "var(--k)" }}> +{stats.add}</span>
                    <span style={{ color: "var(--warn)" }}> −{stats.del}</span> 行
                  </div>
                  <pre className="diff">
                    {lines.map((l, i) => (
                      <div key={i} className={"dl " + l.t}>
                        <span className="sign">{l.t === "add" ? "+" : l.t === "del" ? "−" : " "}</span>
                        {l.s || " "}
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
