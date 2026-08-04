interface BlankBookFormProps {
  name: string;
  pending: boolean;
  error: string | null;
  onNameChange: (name: string) => void;
  onBack: () => void;
  onSubmit: () => void;
}

export function BlankBookForm({ name, pending, error, onNameChange, onBack, onSubmit }: BlankBookFormProps) {
  return (
    <>
      <p className="onboarding-eyebrow">新建小说</p>
      <h1 className="onboarding-title">给这本书起个名字</h1>
      <label className="onboarding-form onboarding-name-form">
        <span>书名</span>
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
        <button type="button" onClick={onBack} disabled={pending}>返回</button>
        <button className="primary" type="button" onClick={onSubmit} disabled={!name.trim() || pending}>
          {pending ? "创建中…" : "创建并进入工作台"}
        </button>
      </div>
    </>
  );
}
