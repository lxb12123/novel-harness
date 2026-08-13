import { emphasize } from "../../chat";
import type { ImportSummary } from "../../api/types";

// 导入回执 —— **导完一本 300 章的书之后，屏幕上不再是一个数字都没有。**
//
// 在这一屏出现之前，`Setup` 拿到 `BootstrapResult` 只读了 `project.id` 和
// `initial_chapter`，整份 `import_report` 被丢在地上。而里面躺着**唯一一个全书性的
// 信号**：`preamble_chars`（第一个章标之前那堆字）异常地长，往往意味着第一章的章标
// 没被切章器认出来——那时全书的顺序位置集体少 1，而顺序位置就是每一条 `valid_from`
// 的最终来源。症状是沉默的：切章成功、面板画得出来、规则不报错，只是他在第 24 章
// 记下的事被系统记成第 23 章。
//
// **这一屏一个字都不是这里写的。** `headline` / `lines` / `warning` 全来自后端
// （`api/manuscript.py`），门槛也在那儿——「多长算不对劲」是产品判断，
// 在浏览器里再写一份判断，两份迟早互相说反话，而没有任何东西会红。
export function ImportReceipt({
  summary,
  onEnter,
}: {
  summary: ImportSummary;
  onEnter: () => void;
}) {
  return (
    <>
      <p className="onboarding-eyebrow">导入完成</p>
      <h1 className="onboarding-title">{summary.headline}</h1>
      <ul className="import-lines">
        {summary.lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      {summary.warning && (
        // `role="alert"`：这一段是「你整本书的章号可能都错一位」，它值得被读屏念出来。
        // `**…**` 是后端写的重音，用和写作助手同一个渲染器画出来——**不许在这儿
        // 换一句自己的话**，也不许让作者看见两颗星号。
        <div className="import-warning" role="alert">
          {summary.warning.split("\n").map((para) => (
            <p key={para}>
              {emphasize(para).map((part, i) =>
                part.strong ? <strong key={i}>{part.text}</strong> : <span key={i}>{part.text}</span>,
              )}
            </p>
          ))}
        </div>
      )}
      <div className="onboarding-form-actions">
        <span />
        <button className="primary" type="button" onClick={onEnter}>
          进入工作台
        </button>
      </div>
    </>
  );
}
