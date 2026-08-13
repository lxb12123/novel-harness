import { useState } from "react";
import { useChatRules, useRevokeRule } from "../api/hooks";
import type { AuthorRuleView } from "../api/types";
import { refusalText } from "../chat";

// **作者的规矩**摆出来这一块（[ADR 0023](docs/adr/0023-context-is-pruned-by-rebuildability.md) 决策二）。
//
// ── 它为什么必须存在 ──────────────────────────────────────────────────────
//
// 规矩不是作者填的表单，是**模型从他随口一句话里提炼出来**的（「这三版太煽情了」→
// 「别太煽情」）。ADR 0023 自己把「它会判错」写在代价里，而押的退路只有一句：
// **看得见 + 能取消，不是事前确认**。在这块面板之前，引擎侧四件事全齐了，
// 而作者看不见它、也点不掉它——那条退路只兑现了一半。
//
// ── 为什么它长在写作助手这一栏里，而不是右栏第九格 ────────────────────────
//
// 规矩的坐标是**（这一段对话 × 这一章）**。右栏那八格全是「这本书」的读端
// （花名册 / 认知 / 状态 / 关系 / 依据 / 提醒 / 检查 / 待确认），一格都不认识
// 「哪一段对话」；把一个会话作用域的东西摆进去，作者切一段对话右栏就得跟着变，
// 而那正是三栏布局里最不该动的一栏。它也不能常驻在对话上方——
// 一轮几分钟的对话本来就在跟屏幕抢竖直空间，而**绝大多数时候这里是零条**。
// 所以它和「对话列表」同一个形状：顶上一个按钮，点开盖住对话区，挑完自己收起来
// （`ChatSessions` 那段注释写着这个形状为什么在这个宽度里是唯一可行的）。
//
// ── 这块屏幕上不许出现的东西 ──────────────────────────────────────────────
//
// 1. **`seq`**：它是历史下标，不是给人看的编号（`src/test/screenGuard.ts` 第一张网
//    认的是 snake_case，认不出一个裸数字——所以这一条只能靠人守着）。
// 2. **「管到哪儿」那句话的第二个版本**：措辞的唯一出处在后端（`scope`）。
//    在这儿按某个 bool 自己造一句，就是「码 → 中文」那张被删过一次的表又长回来了。

/** 后端一句话都没写时才轮到的那几句。**一个字都不解释「为什么」**（§10 约束 8）。 */
const LIST_FAILED =
  "这一章有哪些规矩，这会儿没读出来。上面空着不代表没有规矩 —— 刷新一下再看。";
const REVOKE_FAILED = "这一条没能取消，而系统没能说清是为什么。过一会儿再试一次。";

/** 一条规矩 + 一颗 ×。**取消要二次确认。**
 *
 *  确认那一步不是礼貌，是因为**这个动作在库里是不可见的**：取消不是删掉一行，
 *  是往历史里追加一条指着它的记录（canonical 只增不改）。作者看到的却是「这一行没了」
 *  ——两者之间的差别他永远看不到，所以在按下去之前得先说清他能拿回什么。 */
function RuleRow({
  rule,
  onRevoke,
  busy,
}: {
  rule: AuthorRuleView;
  onRevoke: () => void;
  busy: boolean;
}) {
  const [confirming, setConfirming] = useState(false);

  return (
    <li className="chat-rule">
      <div className="chat-rule-body">
        <p className="chat-rule-text">{rule.text}</p>
        {/* 它管到哪儿、什么时候自己就没了。**后端写的那句话，照抄。** */}
        <span className="chat-rule-scope">{rule.scope}</span>
        {confirming && (
          <span className="chat-rule-warn">
            取消之后它就不再管着你的稿子了。你说过的话还留在这段对话里，想让它回来，再说一次就行。
          </span>
        )}
      </div>
      {confirming ? (
        <span className="chat-rule-confirm">
          <button className="danger" disabled={busy} onClick={onRevoke}>
            取消它
          </button>
          <button onClick={() => setConfirming(false)}>算了</button>
        </span>
      ) : (
        <button
          className="chat-rule-del"
          aria-label={`取消这条规矩：${rule.text}`}
          onClick={() => setConfirming(true)}
        >
          ×
        </button>
      )}
    </li>
  );
}

export function ChatRules({
  pid,
  chatId,
  chapter,
}: {
  pid: string;
  chatId: string;
  chapter: number;
}) {
  const listed = useChatRules(pid, chatId, chapter);
  const revoke = useRevokeRule(pid, chatId);

  // **读不出来 ≠ 一条都没有。** 前者的下一步是刷新，后者的下一步是接着写——
  // 而「这一章还没有规矩」在读失败时是一句它不知道真假的话。
  const failed = listed.isError ? (refusalText(listed.error, LIST_FAILED) ?? LIST_FAILED) : null;
  const refused = refusalText(revoke.error, REVOKE_FAILED);
  const rules = listed.data?.rules ?? [];
  const expired = listed.data?.expired ?? 0;

  return (
    <div className="chat-rules">
      {failed ? (
        <div className="err-box">{failed}</div>
      ) : listed.isPending ? (
        <p className="empty chat-rules-empty">正在看这一章有哪些规矩…</p>
      ) : rules.length === 0 ? (
        // **零带着理由**（§10 约束 8）。两种空的下一步动作不同，所以它们说两句话：
        // 定过的那一种要把「规矩只管一阵子」讲出来，否则规矩在作者眼里就是系统忘事
        // ——而他会去重复一件他以为已经交代过的事。
        //
        // ⚠️ **那句理由必须把两条放掉的路都说出来，不许挑一条。** 出参分不出这一档是
        // 哪一种过期：`{rules: [], expired: 1}` 既可能是他翻了页，也可能是他一页没翻、
        // 只是又说了一句话（`tests/test_rules_panel.py` 从真 app 钉住这两条来路的出参
        // 一个字节都不差）。而**「又说了一句」才是默认那一档**（ADR 0023「取窄」：
        // 说一遍的规矩活到他下一次开口为止），也就是他最常撞见的那一种。
        // 只说翻页的代价不是少说一句，是给他一个**恰好反着**的心智模型
        // ——「只要我不翻页，规矩就还在」——而下一次他就会跳过一件他以为交代过的事。
        <p className="empty chat-rules-empty">
          {expired > 0
            ? `你定过 ${expired} 条规矩，这会儿一条都不作数了 —— 规矩只管一阵子：有的管你说它时那一章，翻到下一章就放掉；有的只管眼下那一轮，你再说一句话就放掉。想让它接着管，跟写作助手再说一次。`
            : "这一章还没有规矩在管着。你在对话里说「这一章别写打斗」「冷一点」，它会记下来，记下的就摆在这儿，随时点掉。"}
        </p>
      ) : (
        <ul className="chat-rule-list">
          {rules.map((rule) => (
            <RuleRow
              key={rule.seq}
              rule={rule}
              busy={revoke.isPending}
              // **不在这儿把那一行抹掉。** 重取回来它还在不在，是「撤销按身份撤掉每一份」
              // 这件事唯一会现形的地方（`useRevokeRule` 那段注释写着为什么）。
              onRevoke={() => revoke.mutate(rule.seq)}
            />
          ))}
        </ul>
      )}

      {refused && <div className="err-box">{refused}</div>}
    </div>
  );
}
