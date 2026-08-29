import type { ChangeEvent, RefObject } from "react";
import { useLanguage } from "../../language";

interface StartChooserProps {
  fileInputRef: RefObject<HTMLInputElement>;
  onFile: (file: File) => void;
  onBlank: () => void;
}

export function StartChooser({ fileInputRef, onFile, onBlank }: StartChooserProps) {
  const language = useLanguage((s) => s.language);
  function onChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) onFile(file);
  }

  return (
    <>
      <p className="onboarding-eyebrow">Novel workspace</p>
      <h1 className="onboarding-title">
        {language === "zh" ? "从哪里开始？" : "Where do you want to start?"}
      </h1>
      <p className="onboarding-lead">
        {language === "zh"
          ? "打开已有手稿，或创建一本全新的小说。"
          : "Open an existing manuscript, or create a brand-new novel."}
      </p>
      <input
        ref={fileInputRef}
        className="setup-file-input"
        type="file"
        accept=".txt,text/plain"
        aria-label={language === "zh" ? "选择 TXT" : "Choose a TXT file"}
        tabIndex={-1}
        onChange={onChange}
      />
      <div className="onboarding-actions">
        <button className="onboarding-action primary" type="button" onClick={() => fileInputRef.current?.click()}>
          <span className="onboarding-action-icon" aria-hidden="true">↥</span>
          <span className="onboarding-action-copy">
            <span className="onboarding-action-name">
              {language === "zh" ? "导入现有小说" : "Import an existing novel"}
            </span>
            <span className="onboarding-action-description">
              {language === "zh"
                ? "选择 TXT，自动识别书名并切分章节"
                : "Choose a TXT file — the title is detected and chapters are split automatically"}
            </span>
          </span>
        </button>
        <button className="onboarding-action" type="button" onClick={onBlank}>
          <span className="onboarding-action-icon" aria-hidden="true">＋</span>
          <span className="onboarding-action-copy">
            <span className="onboarding-action-name">
              {language === "zh" ? "新建空白小说" : "Start a blank novel"}
            </span>
            <span className="onboarding-action-description">
              {language === "zh" ? "创建第一章，从零开始写" : "Create chapter one and start from scratch"}
            </span>
          </span>
        </button>
      </div>
    </>
  );
}
