import { useLanguage } from "../../language";

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
  const language = useLanguage((s) => s.language);
  const disabled = !file || !hasText || !name.trim() || pending || reading;
  return (
    <>
      <p className="onboarding-eyebrow">{language === "zh" ? "导入手稿" : "Import a manuscript"}</p>
      <h1 className="onboarding-title">{language === "zh" ? "确认这本书" : "Confirm this book"}</h1>
      <p className="onboarding-lead">
        {language === "zh"
          ? "我们会在本地为它创建工作台。"
          : "We'll set up a workbench for it locally."}
      </p>
      {file && (
        <div className="onboarding-file">
          <span className="onboarding-file-name">{file.name}</span>
          <span className="onboarding-file-meta">{fileSize(file.size)}</span>
        </div>
      )}
      <label className="onboarding-form">
        <span>{language === "zh" ? "书名" : "Title"}</span>
        <input disabled={pending} value={name} onChange={(event) => onNameChange(event.target.value)} />
      </label>
      {reading && (
        <p className="onboarding-lead">
          {language === "zh" ? "正在读取 TXT…" : "Reading the TXT file…"}
        </p>
      )}
      {error && <p className="onboarding-error" role="alert" aria-live="polite">{error}</p>}
      <div className="onboarding-form-actions">
        <button type="button" onClick={onBack} disabled={pending}>
          {language === "zh" ? "返回" : "Back"}
        </button>
        <button className="primary" type="button" onClick={onSubmit} disabled={disabled}>
          {language === "zh"
            ? pending ? "导入中…" : "导入并进入工作台"
            : pending ? "Importing…" : "Import and enter the workbench"}
        </button>
      </div>
    </>
  );
}
