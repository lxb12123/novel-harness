interface ImportReviewProps {
  file: File | null;
  hasText: boolean;
  name: string;
  pending: boolean;
  reading: boolean;
  error: string | null;
  onNameChange: (name: string) => void;
  onBack: () => void;
  onSubmit: () => void;
}

function fileSize(bytes: number) {
  return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`;
}

export function ImportReview({
  file,
  hasText,
  name,
  pending,
  reading,
  error,
  onNameChange,
  onBack,
  onSubmit,
}: ImportReviewProps) {
  const disabled = !file || !hasText || !name.trim() || pending || reading;
  return (
    <>
      <p className="eyebrow">导入手稿</p>
      <h1 className="title">确认这本书</h1>
      <p className="lead">我们会在本地为它创建工作台。</p>
      {file && (
        <div className="file">
          <span className="file-name">{file.name}</span>
          <span className="file-meta">{fileSize(file.size)}</span>
        </div>
      )}
      <label className="form">
        <span>书名</span>
        <input value={name} onChange={(event) => onNameChange(event.target.value)} />
      </label>
      {reading && <p className="lead">正在读取 TXT…</p>}
      {error && <p className="error" role="alert" aria-live="polite">{error}</p>}
      <div className="form-actions">
        <button type="button" onClick={onBack} disabled={pending || reading}>返回</button>
        <button className="primary" type="button" onClick={onSubmit} disabled={disabled}>
          {pending ? "导入中…" : "导入并进入工作台"}
        </button>
      </div>
    </>
  );
}
