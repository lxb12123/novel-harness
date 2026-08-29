import { useLanguage } from "../../language";

interface BlankBookFormProps {
  name: string;
  pending: boolean;
  error: string | null;
  onNameChange: (name: string) => void;
  onBack: () => void;
  onSubmit: () => void;
}

export function BlankBookForm({ name, pending, error, onNameChange, onBack, onSubmit }: BlankBookFormProps) {
  const language = useLanguage((s) => s.language);
  return (
    <>
      <p className="onboarding-eyebrow">{language === "zh" ? "新建小说" : "New novel"}</p>
      <h1 className="onboarding-title">
        {language === "zh" ? "给这本书起个名字" : "Give this book a name"}
      </h1>
      <label className="onboarding-form onboarding-name-form">
        <span>{language === "zh" ? "书名" : "Title"}</span>
        <input
          className="onboarding-name-input"
          autoFocus
          disabled={pending}
          value={name}
          onChange={(event) => onNameChange(event.target.value)}
        />
      </label>
      {error && <p className="onboarding-error" role="alert" aria-live="polite">{error}</p>}
      <div className="onboarding-form-actions">
        <button type="button" onClick={onBack} disabled={pending}>
          {language === "zh" ? "返回" : "Back"}
        </button>
        <button className="primary" type="button" onClick={onSubmit} disabled={!name.trim() || pending}>
          {language === "zh"
            ? pending ? "创建中…" : "创建并进入工作台"
            : pending ? "Creating…" : "Create and enter the workbench"}
        </button>
      </div>
    </>
  );
}
