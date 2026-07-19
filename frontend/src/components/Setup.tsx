import { useState } from "react";
import { useCreateProject, useImportBook } from "../api/hooks";
import { readTextFile, ApiError } from "../api/client";
import { useCoords } from "../store";
import type { ImportReport } from "../api/types";

// 上手 —— 非程序员的起步路径：建书 → 拖一个 TXT 进来，全程不碰命令行。
// 既是「库里没项目」的首屏，也是顶栏「＋新书」的弹窗（传 onClose 就变弹窗）。
export function Setup({ onClose }: { onClose?: () => void }) {
  const { projectId, setProject } = useCoords();
  const create = useCreateProject();
  const importBook = useImportBook(projectId ?? "");
  const [name, setName] = useState("");
  const [report, setReport] = useState<ImportReport | null>(null);
  const [reading, setReading] = useState(false);

  const createErr = create.error instanceof ApiError ? create.error : null;
  const importErr = importBook.error instanceof ApiError ? importBook.error : null;

  async function onPick(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !projectId) return;
    setReport(null);
    setReading(true);
    try {
      const text = await readTextFile(file);
      importBook.mutate(text, { onSuccess: setReport });
    } finally {
      setReading(false);
    }
  }

  const card = (
    <div className="setup-card">
      <h3>{onClose ? "新建 / 导入" : "开始一本书"}</h3>

      <div className="field">
        <span>① 书名</span>
        <div className="row">
          <input
            value={name}
            placeholder="青云记"
            onChange={(e) => setName(e.target.value)}
            style={{ flex: 1 }}
          />
          <button
            disabled={!name.trim() || create.isPending}
            onClick={() => create.mutate(name.trim(), { onSuccess: (p) => setProject(p.id) })}
          >
            {create.isPending ? "建中…" : "建书"}
          </button>
        </div>
        {createErr && <div className="err-box">{createErr.message}</div>}
        {create.isSuccess && projectId && (
          <div className="note" style={{ color: "var(--ok)" }}>
            ✓ 建好了。稿子目录由系统在服务器上开好（正文在磁盘，ADR 0007）。
          </div>
        )}
      </div>

      <div className="field">
        <span>② 导入 TXT（切章 → 落库）</span>
        <input type="file" accept=".txt,text/plain" disabled={!projectId || reading} onChange={onPick} />
        {!projectId && <div className="note">先建书或选一本，再导入。</div>}
        {reading && <div className="note">读取文件中…</div>}
        {importBook.isPending && <div className="note">切章落库中…</div>}
        {importErr && (
          <div className="err-box">
            {importErr.code === "import_refused"
              ? "切不出章：认的是行首的「第N章 / 第N节 / 第N回」。这本书的章标写法没被认出来，或这本书已导入过且内容不同。"
              : importErr.message}
          </div>
        )}
        {report && (
          <div className="receipt">
            <div className="vf">✓ 切出 {report.chapter_count} 章，已落库</div>
            <div className="note">
              新建 {report.written.length} 个章节文件 · 复用 {report.unchanged.length} 个
              {report.preamble_chars > 0 && ` · 卷首 ${report.preamble_chars} 字未计入任何章`}
            </div>
            <div className="note">拿这个章数跟你的目录对一下——数一样才算切对。</div>
          </div>
        )}
      </div>

      <div className="row" style={{ marginTop: 10 }}>
        {onClose && (
          <button onClick={onClose}>{report ? "进入工作台" : "关闭"}</button>
        )}
      </div>
    </div>
  );

  if (!onClose) return <div className="setup-full">{card}</div>;
  return (
    <>
      <div className="backdrop" onClick={onClose} />
      <div className="drawer">{card}</div>
    </>
  );
}
