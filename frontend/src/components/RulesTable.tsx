import { useRecordedRules } from "../api/hooks";
import { useLanguage } from "../language";
import { useCoords } from "../store";

// 作者交代过的那些规矩，摆成一张回头能翻的表
// （[ADR 0028](docs/adr/0028-rules-expire-by-situation.md) + 迁移 016）。
//
// ── 它和 2026-08-14 撤掉的那块面板不是一回事，这一段是给下一个人看的 ──────────
//
// 撤掉的是写作助手顶上那颗「这一章的规矩」按钮 + 一块**带取消按钮**的面板，
// 回答的是「这一章此刻哪几条生效」。作者的原话：「这个原本就不需要展示给用户看……
// 而不是显示出来给用户选择」。
//
// 这一张是他第二天自己要的另一件事：「你在写某一章的时候用户讲过的规则，然后**你规定的
// 时效**什么的都可以记一下」。**「记一下」和「摆出来让他管」是两件事。**
//
// 所以这块屏幕上有两样东西**不许长出来**：
//
// 1. **一颗取消按钮。** 有了它，这张表就变回那块被撤掉的面板了。
// 2. **一列「还生不生效」。** 那个答案只有读到规矩的那个模型知道（有效期是情境的事，
//    引擎不判）——编一个出来，它在屏幕上看起来完全正常，而这个仓库反复栽的正是这种
//    「一句它不知道真假的话」。后端的出参里根本没有这一位（`RecordedRule`），
//    所以这儿也造不出来。
//
// ── 为什么它长在日志页上 ──────────────────────────────────────────────────
//
// 日志页回答的就是「回头看，系统和我各做过什么」。规矩是**他做过的事**（他随口说的
// 那一句），只是记下它的是模型。摆在这儿不用新开一页，也不占工作台任何一栏的常驻空间
// ——而它绝大多数时候是空的（`remember_rule` 在真书上一次都没开过火）。

export function RulesTable() {
  const language = useLanguage((s) => s.language);
  const { projectId } = useCoords();
  const recorded = useRecordedRules(projectId);

  if (recorded.isLoading)
    return <div className="empty">{language === "zh" ? "读取中…" : "Loading…"}</div>;
  if (recorded.isError) {
    // **读不出来 ≠ 一条都没有。** 前者的下一步是刷新，后者的下一步是接着写。
    return (
      <div className="err-box">
        {language === "zh"
          ? "写作要求读取失败，请刷新后重试"
          : "Recorded instructions could not be loaded; refresh to try again"}
      </div>
    );
  }

  const rules = recorded.data?.rules ?? [];
  const scanned = recorded.data?.scanned_chats ?? 0;

  if (rules.length === 0) {
    // **零带着成色**（§10 约束 8）：两种空的下一步动作不同。
    // 「还没跟它说过话」→ 去说；「说过但一条都没记下」→ 那是**默认**那一档，
    // 别让他以为功能坏了（`remember_rule` 在真书上一次都没开过火）。
    return (
      <p className="empty">
        {language === "zh" ? (
          scanned === 0 ? (
            <>尚无对话。对写作助手提出的写作要求（如「本章不写打斗」）会记录在此。</>
          ) : (
            <>对话中尚无被记录的写作要求。仅记录关于「怎么写」的要求。</>
          )
        ) : scanned === 0 ? (
          <>
            No conversations yet. Writing instructions given to the assistant (such as “no fight
            scenes in this chapter”) are recorded here.
          </>
        ) : (
          <>
            No writing instruction has been recorded from the conversations yet. Only instructions
            about how to write are recorded.
          </>
        )}
      </p>
    );
  }

  return (
    <table className="ruletable">
      <thead>
        <tr>
          <th>{language === "zh" ? "提出时所在章" : "Chapter when given"}</th>
          <th>{language === "zh" ? "写作要求" : "Instruction"}</th>
          <th>{language === "zh" ? "有效至" : "Applies through"}</th>
          <th>{language === "zh" ? "所在对话" : "Conversation"}</th>
        </tr>
      </thead>
      <tbody>
        {rules.map((rule, i) => (
          <tr key={`${rule.chat_id}#${rule.chapter}#${i}`}>
            <td className="rt-ch">
              {language === "zh" ? `第 ${rule.chapter} 章` : `Chapter ${rule.chapter}`}
            </td>
            <td className="rt-text">{rule.text}</td>
            {/* **空的时候照实说**（那是迁移 016 之前记下的，模型当时没有这一格）。
                替它编一句，读起来就像是它当时真的判断过。 */}
            <td className={rule.until ? "rt-until" : "rt-until dim"}>
              {rule.until || (language === "zh" ? "未记录" : "Not recorded")}
            </td>
            <td className="rt-chat">
              {rule.chat_title || (language === "zh" ? "未命名对话" : "Unnamed conversation")}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
