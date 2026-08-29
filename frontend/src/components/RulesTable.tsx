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
          ? "你交代过的规矩这会儿没读出来。刷新一下再看。"
          : "Couldn't load the rules you've told it — refresh and check again."}
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
            <>你还没跟写作助手说过话。等你随口交代一句「这一章别写打斗」，它会记在这儿。</>
          ) : (
            <>你跟它说过话，但还没有哪一句被当成交代记下来。它只记「怎么写」那一类的话。</>
          )
        ) : scanned === 0 ? (
          <>
            You haven’t talked to the writing assistant yet. Once you mention something in
            passing, like “no fight scenes in this chapter”, it’ll show up here.
          </>
        ) : (
          <>
            You’ve talked to it, but nothing you said has been recorded as an instruction yet. It
            only records the “how to write this” kind of thing.
          </>
        )}
      </p>
    );
  }

  return (
    <table className="ruletable">
      <thead>
        <tr>
          <th>{language === "zh" ? "写到第几章时说的" : "Said while writing which chapter"}</th>
          <th>{language === "zh" ? "你交代的" : "What you told it"}</th>
          <th>{language === "zh" ? "它当时判定管到" : "It judged this applies through"}</th>
          <th>{language === "zh" ? "哪一段对话" : "Which conversation"}</th>
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
              {rule.until || (language === "zh" ? "没记下" : "Not recorded")}
            </td>
            <td className="rt-chat">
              {rule.chat_title || (language === "zh" ? "没起名字的一段" : "An unnamed conversation")}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
