import { useLanguage } from "../../language";
import type { ReactNode } from "react";

export function OnboardingShell({ compact, children }: { compact: boolean; children: ReactNode }) {
  const language = useLanguage((s) => s.language);
  return (
    <main className={`onboarding-shell${compact ? " compact" : " full-bleed"}`}>
      {!compact && (
        <section className="onboarding-identity" aria-label="Novel Harness">
          <div className="onboarding-logo" aria-hidden="true">NH</div>
          <h1 className="onboarding-brand">Novel Harness</h1>
          <p className="onboarding-tagline">
            {language === "zh"
              ? "让长篇小说中的每个人，只知道此刻该知道的事。"
              : "Everyone in a long novel knows only what they should know by this point."}
          </p>
          <ul className="onboarding-promises">
            <li className="onboarding-promise">
              <span className="onboarding-promise-mark">✓</span>
              {language === "zh" ? "正文保存在本地磁盘" : "Your text is saved on local disk"}
            </li>
            <li className="onboarding-promise">
              <span className="onboarding-promise-mark">✓</span>
              {language === "zh"
                ? "不配置 AI 也能完整使用"
                : "Fully usable without configuring AI"}
            </li>
            <li className="onboarding-promise">
              <span className="onboarding-promise-mark">✓</span>
              {language === "zh" ? "原始 TXT 不会被移动" : "The original TXT file is never moved"}
            </li>
          </ul>
        </section>
      )}
      <section className="onboarding-flow">
        <div className="onboarding-panel">{children}</div>
        <footer className="onboarding-footer">
          {language === "zh" ? (
            <>你的稿子不会上传 · 外观跟随系统 · 导入会读取 TXT、按章标题切分，并在本地生成 Markdown。</>
          ) : (
            <>
              Your manuscript is never uploaded · appearance follows your system · importing reads
              the TXT file, splits it by chapter title, and generates Markdown locally.
            </>
          )}
        </footer>
      </section>
    </main>
  );
}
