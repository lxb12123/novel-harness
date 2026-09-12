import { useState } from "react";
import { useCreateChat, useDeleteChat } from "../api/hooks";
import type { ChatSessionView } from "../api/types";
import { refusalText } from "../chat";
import { useLanguage, type Language } from "../language";
import { shownTime } from "../time";
import { CloseIcon } from "./icons";

// 写作助手的**会话列表**。作者可以同时留着好几段对话，每一段各自 resume
// （ADR 0019：线性 loop 的执行态就是「一串 message + 哪几个查询还缺结果」，
// 接着往下跑不需要一个图运行时）。
//
// ── 为什么它是「摊开的一块」而不是一条真侧栏 ──────────────────────────────
//
// 中栏对半分之后，助手那一半的下限是 `MIN_CHAT`（`layout.ts`）。在那个宽度里再切一条
// 会话侧栏出来，剩给对话本身的就只有一百多像素——那是这个仓库反复在拦的
// 「拉过头就什么都看不见」的同一种废墟，只不过这次是设计造出来的。
// 所以它默认收着，点开时是**挂在「对话列表」那颗按钮下面的一张浮层**
// （2026-09-07 从「横贯整条面板的一块」改的，理由写在 `styles.css` 的
// `.chat-sessions` 那条上），挑完自己收起来。
//
// ── 这一列上不许出现的两样东西 ────────────────────────────────────────────
//
// 1. **会话的内部标识**（`chat_session:…`）。它是 `前缀:标识` 形状，
//    `src/test/screenGuard.ts` 的第三张网认的就是它。列表里认人靠标题和时间。
// 2. **`message_count`**。它数的是历史里的**全部**条数，含工具往返那些不上屏的——
//    摆一个「4 条」在只有两句话的对话旁边，是一个看起来很正常的假数字。

/** 还没说过话的那一段叫什么。**后端只在标题为空时拿作者第一句话去填**，
 *  所以这一档只可能出现在「刚开了一段还没说话」上。 */
const UNNAMED = (language: Language): string =>
  language === "zh" ? "新对话" : "New conversation";

/** 后端一句话都没写时才轮到的那一句。**删这条路由的 404 就是这一档**
 *  （`{"error":"chat_not_found","chat_id":…}`，一个 `message` 都没有）。
 *  不许编理由，也不许说「请稍后再试」——那是在暗示重试有用。 */
const DELETE_FAILED = (language: Language): string =>
  language === "zh"
    ? "删除对话失败，未返回原因。"
    : "Could not delete this conversation; no reason was returned.";

function SessionRow({
  session,
  current,
  onPick,
  onDelete,
  deleting,
}: {
  session: ChatSessionView;
  current: boolean;
  onPick: () => void;
  onDelete: () => void;
  deleting: boolean;
}) {
  const [confirming, setConfirming] = useState(false);
  const language = useLanguage((s) => s.language);
  const title = session.title.trim() || UNNAMED(language);
  const time = shownTime(session.updated_at, language);

  return (
    <li className={"chat-session" + (current ? " on" : "")}>
      <button className="chat-session-pick" onClick={onPick} aria-current={current || undefined}>
        <span className="chat-session-name">{title}</span>
        <span className="chat-session-meta">
          {time && <span>{time}</span>}
          {session.running && (
            <span className="chat-badge run">{language === "zh" ? "进行中" : "Running"}</span>
          )}
          {/* 断在半路的和跑完的**必须长得不一样**：两者的下一步动作不同，而后端
              专门为这一列算了这个数（列表那条路由的 docstring 写着理由）。

              ⚠️ **这句话 2026-08-15 改过，因为原来那句变成了假话。** 原文是
              「接着说会自动补上」，而它成立只因为屏幕上还有一颗「接着往下」
              （发空话 = resume）。那颗按钮当天撤了，于是唯一剩下的路是作者自己打一句——
              **而打一句恰好是让那几步永远补不上的那条路**：`Conversation.pending_calls`
              往回扫到 `USER` 就停（`agent/loop.py` 的 `LOST_RESULT` 写着「再也没有人
              会去补它」）。实测过：断在半路时它数出 1 个，`with_author("继续")` 之后是 0 个。 */}
          {session.pending_lookups > 0 && (
            <span className="chat-badge half">
              {language === "zh"
                ? "上一轮中断，未完成"
                : "Last round was cut off before finishing"}
            </span>
          )}
        </span>
      </button>
      {confirming ? (
        /* **确认时整行换成这一条**，不是在原来那一行右边再挤两颗按钮——
           那两颗方框按钮把标题挤没了，而且它俩长得和「开一段新的对话」一样重
           （作者：「太丑了」）。这一条：左边一句问话，右边一颗实心的小药丸 +
           一个纯文字的「算了」。**只有危险的那一个有颜色**，取消永远是最轻的那个。 */
        <span className="chat-session-confirm">
          <span className="chat-session-ask">
            {language === "zh" ? "删除此对话？" : "Delete this conversation?"}
          </span>
          <button className="chat-session-yes" disabled={deleting} onClick={onDelete}>
            {language === "zh"
              ? deleting
                ? "删除中…"
                : "删除"
              : deleting
                ? "Deleting…"
                : "Delete"}
          </button>
          <button className="chat-session-no" onClick={() => setConfirming(false)}>
            {language === "zh" ? "取消" : "Cancel"}
          </button>
        </span>
      ) : (
        <button
          className="chat-session-del"
          aria-label={
            language === "zh" ? `删除对话：${title}` : `Delete this conversation: ${title}`
          }
          onClick={() => setConfirming(true)}
        >
          <CloseIcon />
        </button>
      )}
    </li>
  );
}

export function ChatSessions({
  pid,
  sessions,
  failed,
  current,
  onPick,
  onPicked,
}: {
  pid: string;
  sessions: ChatSessionView[];
  /** 这一列**根本没读出来**时说给作者的那句话（`null` = 读到了）。
   *  它和「一段都没有」是两件事，而屏幕上分不开的后果是一句假话。 */
  failed: string | null;
  current: string | null;
  onPick: (id: string | null) => void;
  /** 挑完 / 建完之后收起这一列。 */
  onPicked: () => void;
}) {
  const language = useLanguage((s) => s.language);
  const create = useCreateChat(pid);
  const remove = useDeleteChat(pid);

  // 后端把「正在跑的那一段」的删除挡在 409（**带一句人话**），这里原样说给作者，
  // **不做静默重试**：那一轮还在花钱，替他再打一次没有让任何事情变好。
  // 但 404 那一档只有码没有话，`error.message` 会退回 `chat_not_found`——
  // 所以走 `refusalText`，不走 `.message`。
  const refused = refusalText(remove.error, DELETE_FAILED(language));

  return (
    <div className="chat-sessions">
      <button
        className="chat-new"
        disabled={create.isPending}
        onClick={() =>
          create.mutate(undefined, {
            onSuccess: (session) => {
              onPick(session.id);
              onPicked();
            },
          })
        }
      >
        {language === "zh" ? "＋ 新建对话" : "+ New conversation"}
      </button>

      {/* **读不出来 ≠ 一段都没有。** 前者的下一步是刷新，后者的下一步是开一段——
          而「还没有说过话」在读失败时是一句它不知道真假的话，作者三个月的对话可能都在。 */}
      {failed && <div className="err-box">{failed}</div>}

      {sessions.length === 0 ? (
        failed ? null : (
          <p className="empty chat-sessions-empty">
            {language === "zh" ? (
              /* 「问它这一章有什么不能说」2026-09-07 撤了：秘密那套 2026-08-24 整套
                 下线（ADR 0039），这句话在替一个不存在的能力招手。同 `ChatPanel.tsx`
                 空态那一句，两处是同一个错。 */
              <>尚无对话。新建对话后，可先要求写作助手梳理前情，或直接说明本章要写的内容。</>
            ) : (
              <>
                No conversations yet. Start one, then ask the writing assistant to review the
                story so far, or say what this chapter is about.
              </>
            )}
          </p>
        )
      ) : (
        <ul className="chat-session-list">
          {sessions.map((session) => (
            <SessionRow
              key={session.id}
              session={session}
              current={session.id === current}
              deleting={remove.isPending}
              onPick={() => {
                onPick(session.id);
                onPicked();
              }}
              onDelete={() =>
                remove.mutate(session.id, {
                  onSuccess: () => {
                    // 删掉的正好是摊开的那一段 → 放下坐标，面板会自己停到最近的一段上。
                    if (session.id === current) onPick(null);
                  },
                })
              }
            />
          ))}
        </ul>
      )}

      {refused && <div className="err-box">{refused}</div>}
    </div>
  );
}
