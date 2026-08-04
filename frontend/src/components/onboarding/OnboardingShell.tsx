import type { ReactNode } from "react";

export function OnboardingShell({ compact, children }: { compact: boolean; children: ReactNode }) {
  return (
    <main className={`onboarding-shell${compact ? " compact" : " full-bleed"}`}>
      {!compact && (
        <section className="onboarding-identity" aria-label="Novel Harness">
          <div className="onboarding-logo" aria-hidden="true">NH</div>
          <h1 className="onboarding-brand">Novel Harness</h1>
          <p className="onboarding-tagline">让长篇小说中的每个人，只知道此刻该知道的事。</p>
          <ul className="onboarding-promises">
            <li className="onboarding-promise"><span className="onboarding-promise-mark">✓</span>正文保存在本地磁盘</li>
            <li className="onboarding-promise"><span className="onboarding-promise-mark">✓</span>不配置 AI 也能完整使用</li>
            <li className="onboarding-promise"><span className="onboarding-promise-mark">✓</span>原始 TXT 不会被移动</li>
          </ul>
        </section>
      )}
      <section className="onboarding-flow">
        <div className="onboarding-panel">{children}</div>
        <footer className="onboarding-footer">
          你的稿子不会上传 · 外观跟随系统 · 导入会读取 TXT、按章标题切分，并在本地生成 Markdown。
        </footer>
      </section>
    </main>
  );
}
