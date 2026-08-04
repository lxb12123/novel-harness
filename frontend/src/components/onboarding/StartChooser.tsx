import type { ChangeEvent, RefObject } from "react";

interface StartChooserProps {
  fileInputRef: RefObject<HTMLInputElement>;
  onFile: (file: File) => void;
  onBlank: () => void;
}

export function StartChooser({ fileInputRef, onFile, onBlank }: StartChooserProps) {
  function onChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) onFile(file);
  }

  return (
    <>
      <p className="eyebrow">Novel workspace</p>
      <h1 className="title">从哪里开始？</h1>
      <p className="lead">打开已有手稿，或创建一本全新的小说。</p>
      <input
        ref={fileInputRef}
        className="setup-file-input"
        type="file"
        accept=".txt,text/plain"
        aria-label="选择 TXT"
        tabIndex={-1}
        onChange={onChange}
      />
      <div className="actions">
        <button className="action primary" type="button" onClick={() => fileInputRef.current?.click()}>
          <span className="action-icon" aria-hidden="true">↥</span>
          <span className="action-copy">
            <span className="action-name">导入现有小说</span>
            <span className="action-description">选择 TXT，自动识别书名并切分章节</span>
          </span>
        </button>
        <button className="action" type="button" onClick={onBlank}>
          <span className="action-icon" aria-hidden="true">＋</span>
          <span className="action-copy">
            <span className="action-name">新建空白小说</span>
            <span className="action-description">创建第一章，从零开始写</span>
          </span>
        </button>
      </div>
    </>
  );
}
