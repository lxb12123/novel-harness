import { useEffect, useRef, useState } from "react";
import {
  useChatDetail,
  useChatRules,
  useChats,
  useCreateChat,
  useDrafts,
  useRunTurn,
  useStopChat,
} from "../api/hooks";
import type {
  ChatAuthorQuestion,
  ChatMessageView,
  TurnReceipt,
} from "../api/types";
import {
  applyTurnEvent,
  elapsedText,
  emphasize,
  newRunId,
  receiptNotes,
  receiptSays,
  refusalText,
  stopFootnote,
  tailWindow,
  visibleMessages,
  NO_PROGRESS,
  SPEAKER_ZH,
  type LiveDraft,
  type TurnProgress,
} from "../chat";
import { useCoords } from "../store";
import { ChatRules } from "./ChatRules";
import { ChatSessions } from "./ChatSessions";
import { CompareLink, DraftCandidates } from "./DraftCandidates";

// 写作助手（模式二，[ADR 0019](docs/adr/0019-agent-loop-not-graph.md)）。
// 中栏对半分之后的右半边：左边正文、右边它。左栏书架和右栏面板一个像素不动。
//
// ── 三件这块屏幕必须自己做对的事（2026-08-12 第一条重写，ADR 0024）──────────
//
// 1. **逐字看得见的只有稿子，回话那一档今天不逐字 —— 所以这儿不许装成逐字。**
//    这一轮走的是长连接了（`api/turnStream.ts`），中间过程真的一条条到手；
//    但两条流不是一回事：起草那次调用是流式的（`interruptible`），
//    **回话那次不是**（输出预算 4,024 远在流式阈值之下 ⇒ `plan.stream is False`，
//    后端 `test_todays_agent_call_is_not_streaming_…` 钉着它）。
//    所以起草区**真的**一个字一个字长出来，回话区仍然是整段一次到位——
//    **给回话区做一个假的打字机 = 让作者按一个编出来的节奏判断它卡没卡住**，
//    那正是这块屏幕 2026-08-12 上午拒绝过一次的东西。
//
// 2. **工具在干什么可以显示，工具查到了什么不许显示。** 后端出参和事件流都已经是
//    投影不是原文（`TurnEvent` 上根本没有一个字段装得下工具返回），
//    **前端也不许自己去别处把它们捞回来补上**——那里面是 `NodeRef` 的裸标识，
//    一渲染就是屏幕上的研发术语。这一层读的只有 `said_to_author`（引擎写的中文）
//    和 `text`（模型自己的字）；`kind` / `tool` / `reason` 一个字都不上屏。
//
// 3. **措辞的唯一出处在后端。** 停止原因是机器码（`reason`），一个字都不上屏；
//    说给作者的那一句是 `receipt.message`（`agent.loop.stop_wording()` 写好的）。
//    这里只补后端**说不出来**的那两句：作者按过停没有（`chat.ts::stopFootnote`），
//    以及这一轮裁掉了什么（`receiptNotes`）。

// 屏幕上有三种说话人。那张表（连同「认不出的一律不上屏」这条）在 `chat.ts`：
// 它同时是措辞和白名单，**一份**（`visibleMessages` 的 docstring 写着为什么必须是一份）。
//
// 第三种「系统」是**一轮没跑成时留在对话里的那一行**（2026-08-13，后端迁移 012）。
// 它和下面那个红框（`failureHere`）分工不同，别合并：
//
// | | 什么时候 | 活多久 |
// |---|---|---|
// | 对话里那一行 | 这一轮**真的开始了**，然后什么都没跑出来 | 落盘，切走切回、三个月后都在 |
// | 红框 | 这一轮**根本没开始**（模型没配好 422 / 正在跑上一轮 409 / 网断了） | 这一次点击，作者的字还在输入框里 |
//
// 红框那一档不该落盘：它连作者那句话都没进历史，写一行进去就是凭空造出一轮
// 没发生过的对话（后端 `_notice_for` 的 docstring 里那张表写着每一档为什么）。

/** 后端一句话都没写时才轮到的那几句。**一个字都不解释「为什么」**——
 *  §10 约束 8：不知道就说不知道，编一个理由比不说更贵。 */
const TURN_FAILED = "这一轮没跑成，而系统没能说清是为什么。刷新一下看看这段对话现在是什么样。";
const CREATE_FAILED = "没能开一段新的对话，而系统没能说清是为什么。";
const STOP_FAILED = "这一下「停」没送出去，而系统没能说清是为什么。这一轮可能还在跑，过一会儿再按一次。";
/** 列表读不出来。**不许让屏幕替它说「你还没说过话」**——那是一句它不知道真假的话，
 *  而作者三个月的对话可能都在里面（§10 约束 8）。 */
const LIST_FAILED =
  "这本书有哪几段对话，这会儿没读出来。下面是空的不代表你没说过话——刷新一下再看。";

/** 后端那句话可能带 markdown 的重音（`stop_wording(CONTEXT_FULL)` 就带）。
 *  **不渲染就是两颗星号摆在作者脸上**，而这一层不许改那句话本身。 */
function Wording({ text }: { text: string }) {
  return (
    <>
      {emphasize(text).map((part, i) =>
        part.strong ? <strong key={i}>{part.text}</strong> : <span key={i}>{part.text}</span>,
      )}
    </>
  );
}

function Bubble({ message }: { message: ChatMessageView }) {
  return (
    <div className={"chat-msg " + message.speaker}>
      <span className="chat-who">{SPEAKER_ZH[message.speaker]}</span>
      <p className="chat-text">{message.text}</p>
    </div>
  );
}

/** 正在写（或者刚写完）的一稿。**这块是全屏幕唯一真的逐字长出来的东西。**
 *
 *  空的时候也要画出来：那一格里的「正在写第 N 章的一稿」本身就是信息——
 *  没有它，一批三稿同时在飞的时候屏幕上只有一坨交错的字，作者分不出哪段是哪稿。 */
function DraftingBox({ draft }: { draft: LiveDraft }) {
  return (
    <div className="chat-drafting">
      <span className="chat-drafting-head">
        {draft.done || `正在写第 ${draft.chapter} 章的一稿…`}
      </span>
      {draft.text ? (
        <p className="chat-drafting-text">{draft.text}</p>
      ) : (
        <p className="chat-drafting-wait">还没落下第一个字。</p>
      )}
    </div>
  );
}

/** 还在跑的那一段屏幕。**秒表是真的，中间那几行也是真的，别的什么都不编。** */
function RunningStrip({ since, onStop, stopping, progress }: {
  since: number;
  onStop: () => void;
  stopping: boolean;
  progress: TurnProgress;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="chat-running" role="status">
      <span className="chat-running-dot" aria-hidden="true" />
      <div className="chat-running-say">
        <b>正在跑这一轮 · {elapsedText(now - since)}</b>
        {/* ── 这儿曾经有一句讲内部流式语义的话，2026-08-13 删掉了 ──────────────
            原文：「回话是整段一次出现的，稿子才会一个字一个字长出来」。它是对上一句
            错话的修正，而上一句也是修正来的——每次发现文案不准就把文案改得更准，
            改了两轮，最后变成一段讲架构的免责声明，摆在一个写小说的人面前。

            **病根是把「文案要说准」当成了目标。** 屏幕该用**状态本身**说话：
            字在流就让他看见字（`DraftingBox`），不流就是秒表 + 步骤行。
            没有一个成熟工具会向用户解释自己的流式语义（对照过 Cursor / Codex）。

            而且那句话**没有任何条件**——端点退回一次性响应时它就是假的，
            那正是本仓反复栽的「屏幕在陈述一件不成立的事」。 */}
        <span>它可能要来回查几次资料、想上几轮。下面是它这会儿在做的事。</span>
        {progress.steps.map((line, i) => (
          <span key={i} className="chat-step">
            {line}
          </span>
        ))}
        {progress.said.map((text, i) => (
          <p key={i} className="chat-text chat-step-said">
            {text}
          </p>
        ))}
        {progress.drafts.map((draft) => (
          <DraftingBox key={draft.stream} draft={draft} />
        ))}
      </div>
      <button className="danger" disabled={stopping} onClick={onStop}>
        停
      </button>
    </div>
  );
}

/**
 * 它停下来问了作者一句（ADR 0024）。
 *
 * ── 为什么必须是一张卡，不能是一段话 ──────────────────────────────────────
 *
 * 一个问句混在散文里，作者会当陈述句翻过去——他在读的是「助手说了什么」，
 * 不是「助手在等我」。所以这一档在**结构上**和普通回话分开：一个框、一句
 * 「它在等你回一句」的抬头、几颗能直接点的按钮。
 *
 * ── 这里一个字都不许加 ────────────────────────────────────────────────────
 *
 * 问句和选项 100% 是模型自己的字（后端那条 handler 里连一个数据来源都没有）。
 * 所以不排序、不加「（推荐）」、不合并、不改写。选项为空时**不编两个出来**：
 * 那时能做的只有在下面的输入框里自己写一句，而这块屏幕会照实说。
 */
function AskedCard({ asked, onPick, busy }: {
  asked: ChatAuthorQuestion;
  onPick: (answer: string) => void;
  busy: boolean;
}) {
  return (
    <div className="chat-asked" role="group" aria-label="它在等你回一句">
      <span className="chat-asked-head">它在等你回一句</span>
      <p className="chat-asked-q">{asked.question}</p>
      {asked.options.length > 0 ? (
        <>
          <div className="chat-asked-opts">
            {asked.options.map((option, i) => (
              <button key={i} disabled={busy} onClick={() => onPick(option)}>
                {option}
              </button>
            ))}
          </div>
          <span className="chat-asked-note">
            点一个就当你这么答了；想说别的就在下面直接写。
          </span>
        </>
      ) : (
        <span className="chat-asked-note">在下面写一句回它。</span>
      )}
    </div>
  );
}

/** 一轮跑完之后那一条：后端那句话 + 这一轮实际发生了什么 + 这一轮写出来的那几稿。 */
function Receipt({
  receipt,
  footnote,
  pid,
}: {
  receipt: TurnReceipt;
  footnote: string | null;
  pid: string;
}) {
  const notes = receiptNotes(receipt);
  // 这一轮把「为什么」留在对话里了的话，这儿就不再说一遍（`chat.ts::receiptSays`）。
  const said = receiptSays(receipt);
  return (
    <div className="chat-receipt">
      {said && (
        <p className="chat-receipt-say">
          <Wording text={said} />
        </p>
      )}
      {footnote && <p className="chat-receipt-say">{footnote}</p>}
      {notes.map((note, i) => (
        <p key={i} className="chat-receipt-note">
          {note}
        </p>
      ))}
      {/* 这一轮写出来的那几稿（ADR 0022）。**它们既不在正文里也不在对话里**，
          所以这块屏幕是作者第一次、也是当下唯一一次看见它们的地方——
          关掉这条回执之后，靠的是上面那条「这一章还摆着几稿」。 */}
      <DraftCandidates pid={pid} drafts={receipt.drafts} />
    </div>
  );
}

export function ChatPanel() {
  const { projectId, chapter, chatId, setChat } = useCoords();
  const pid = projectId ?? "";
  const sessions = useChats(projectId);
  const detail = useChatDetail(projectId, chatId);
  const create = useCreateChat(pid);
  const turn = useRunTurn(pid);
  const stop = useStopChat(pid);
  /** 这一章**还摆在桌上**的那几稿（ADR 0022）。它们不在正文里也不在对话里，所以
   *  一旦那一轮的回执被下一轮顶掉，这条入口就是作者唯一找得回它们的地方。
   *  **零的时候一个字都不画**：没有稿子时摆一句「还摆着 0 稿」是噪音（同回执那一条）。 */
  const desk = useDrafts(projectId, chapter);
  /** 这一章此刻**生效着**的规矩（ADR 0023 决策二）。这儿只为了顶上那个按钮上的数
   *  ——面板本身由 `ChatRules` 自己取，两处走的是同一个 query key，react-query 只发一次。
   *
   *  **零的时候按钮照样在**（不同于「还摆着 N 稿」那个入口）：一块空面板要说得出
   *  自己是哪一种空，而作者也得有个地方确认「它没有背着我记下什么」。 */
  const rules = useChatRules(projectId, chatId, chapter);

  const [listOpen, setListOpen] = useState(false);
  const [rulesOpen, setRulesOpen] = useState(false);
  const [said, setSaid] = useState("");
  /** 作者刚按下发送的那句话。跑完之前它在屏幕上占一格——**那不是假装**：
   *  后端做的第一件事就是把它落库（`run_chat` 的注释写着理由）。 */
  const [pendingSaid, setPendingSaid] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState(0);
  /** 这一轮跑到这一刻做过什么（`chat.ts::applyTurnEvent`）。
   *
   *  **它是这一刀的产物**：以前这儿只有一个秒表，一轮三分钟的黑箱；现在长连接把
   *  每一步的边界都送过来了。**一轮跑完不清它**——清掉的话，回执落地那一瞬间
   *  屏幕会先空一下再长出结论，而那一闪读起来像出了什么事。下一轮开跑时才清。 */
  const [progress, setProgress] = useState<TurnProgress>(NO_PROGRESS);
  /** 这一轮跑的是**哪一段**对话。
   *
   *  **一轮要跑好几分钟，而作者可以同时留着好几段对话**——所以「跑到一半切过去看另一段」
   *  是常态不是边角。不记这个 id 的话，「正在跑」那一条和跑完的回执会画在他此刻看着的
   *  那一段上，而那一段什么都没发生：屏幕上是一句关于别处的话，却长得像这儿的事实。 */
  const [runFor, setRunFor] = useState<string | null>(null);
  /** 这一轮的标识（`chat.ts::newRunId`）。**「跑」和「停」报的必须是同一个**——
   *  用 ref 是因为它在一次点击里被写、在另一次点击的回调里被读，而它一个像素都不上屏。 */
  const runId = useRef("");
  /** 回执连同**它属于哪一段**一起存。同上：切走之后到手的那一份不许落到别人头上。 */
  const [receipt, setReceipt] = useState<{ chat: string; turn: TurnReceipt } | null>(null);
  const [expanded, setExpanded] = useState(false);
  /** 这一轮里作者按过停，而且那一次**真的送达了**（后端回 `stopped=true`）。
   *  用 ref 是因为它在另一个回调里被写、在回执到手时被读。 */
  const stopLanded = useRef(false);
  /** 按下「停」之后屏幕上多出来的那一句，以及它是不是一次失败。
   *
   *  **两档必须分开画**：`stopped=false`（那一刻本来就没在跑）是一句正常的话，
   *  画成红框就是把「不用停」说成「停失败了」；而**这一下真的没送出去**时
   *  一声不吭同样不行——一轮跑好几分钟，「停」是作者唯一能插手的地方，
   *  按下去屏幕纹丝不动读起来就是按钮坏了，而他只会再按一次、再等一次。 */
  const [stopSaid, setStopSaid] = useState<{ text: string; failed: boolean } | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  const list = sessions.data ?? [];
  const current = list.find((s) => s.id === chatId) ?? null;
  const onDesk = desk.data?.drafts.length ?? 0;
  /** 顶上那颗按钮上的数。**读不出来时它是 0，而那时按钮照样点得开**——
   *  空按钮点开是一句「没读出来」，比在按钮上编一个数字诚实。 */
  const onRules = rules.data?.rules.length ?? 0;

  // 没挑过就停在最近说过话的那一段（后端按这个顺序给），同 App 里「默认打开第一本书」。
  useEffect(() => {
    if (!chatId && list.length > 0) setChat(list[0].id);
  }, [chatId, list, setChat]);

  // 换一段对话 = 上一段的「看更早的」和那句「不用停」都过期了。
  // **回执不在这儿清**：它自己记着属于哪一段（`receipt.chat`），切回来还看得见
  // 上一轮的结论，切走也不会跟着跑到别人头上。
  //
  // 规矩那一块也收起来：它摊开时**盖住对话区**，换一段对话之后作者要看的是那一段说过
  // 什么，而不是继续盯着一块（属于新那一段的）规矩清单。
  useEffect(() => {
    setExpanded(false);
    setStopSaid(null);
    setRulesOpen(false);
  }, [chatId]);

  // **认不出的说话人一条都不画**（`chat.ts::visibleMessages`）。后端今天的投影是对的，
  // 这是第二道：ADR 0019 边界一那条错误不可回收，而屏幕是那条链的最后一厘米。
  const messages = visibleMessages(detail.data?.messages);
  const running = turn.isPending;
  /** 正在跑的**就是**眼前这一段。别的段上不画秒表、不画回执、不画那句待发的话。 */
  const runningHere = running && runFor === chatId;
  const shownReceipt = receipt && receipt.chat === chatId ? receipt.turn : null;
  const window = tailWindow(messages, expanded);
  /** 它问的那一句。**回执优先**：回执是持久的那一份（刷新页面、换台机器、三个月后
   *  回来，事件早就没了而这个字段还在）。事件流那一份只在**回执没到手**的时候顶上
   *  ——流断在了「问了」和「回执」之间，屏幕上不该因此少掉一个正在等他的问题。 */
  const asked =
    shownReceipt?.asked ?? (runFor === chatId ? progress.asked : null);

  // 新消息到手就滚到底。jsdom 里 scrollHeight 恒为 0，这一句不会做任何事也不会炸。
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, running]);

  function runTurn(id: string, text: string) {
    stopLanded.current = false;
    // 规矩那一块盖在对话区上，跑一轮的时候作者要看的是它在做什么。
    // （这一轮很可能又记下一条，而顶上那颗按钮上的数会跟着变——`useRunTurn` 失效它。）
    setRulesOpen(false);
    setStopSaid(null);
    setReceipt(null);
    setProgress(NO_PROGRESS);
    setPendingSaid(text || null);
    setRunFor(id);
    setStartedAt(Date.now());
    // **每一轮换一个新的**：上一轮那个还在的话，一次迟到的「停」就会认成这一轮。
    runId.current = newRunId();
    turn.mutate(
      {
        chatId: id,
        chapter,
        said: text,
        runId: runId.current,
        // 边跑边收。**归约在纯函数里**（`chat.ts`），这儿只负责把它挂上去——
        // 「一批三稿交错着到达」那种情形鼠标点不出来，只有单测点得出来。
        onEvent: (event) => setProgress((prev) => applyTurnEvent(prev, event)),
      },
      {
        onSuccess: (r) => {
          setPendingSaid(null);
          setReceipt({ chat: id, turn: r });
        },
        onError: () => {
          // **把他打的字还回去。** 这一轮最常见的两种失败（模型没配好 422 /
          // 这段对话正在跑 409）都发生在后端落库**之前**，所以还给他是准的。
          // 万一是落库之后才炸的那一种，他会同时看到对话里的那一句和输入框里的那一句
          // ——多一份好过丢一份，而丢掉的那一份没有任何地方找得回来。
          setPendingSaid(null);
          if (text) setSaid((prev) => (prev ? prev : text));
        },
      },
    );
  }

  function send() {
    const text = said.trim();
    if (!text || !projectId || running) return;
    setSaid("");
    if (chatId) return runTurn(chatId, text);
    // 还没有一段对话 —— 先开一段再说。**不预先开空会话**：那会在侧栏里堆出一排
    // 作者从没说过话的条目。
    create.mutate(undefined, {
      onSuccess: (session) => {
        setChat(session.id);
        runTurn(session.id, text);
      },
      onError: () => setSaid((prev) => (prev ? prev : text)),
    });
  }

  /** 接着往下跑：`said` 留空就是 resume（ADR 0019——看尾巴、补跑缺的、继续）。 */
  function resume() {
    if (chatId && !running) runTurn(chatId, "");
  }

  // **不读 `error.message`**：它在后端没写 `message` 时退回 `body.error`，而这几条路由的
  // 404 恰恰只有码没有话（`chat_not_found` / `project_not_found`，真 app 打过）。
  const failure = refusalText(turn.error, TURN_FAILED);
  const createFailure = refusalText(create.error, CREATE_FAILED);
  const listFailure = refusalText(sessions.error, LIST_FAILED);
  /** 那一轮的失败**属于它自己那一段**（同 `receipt.chat`，这儿原来漏了）。
   *  一轮跑好几分钟，「等的时候切过去看另一段」是常态：不钉住的话，另一段上会长出
   *  一个红框，而那一段什么都没发生——一句关于别处的话，长得像这儿的事实。 */
  const failureHere = runFor === chatId ? failure : null;
  // 上一轮没跑完（撞了闸 / 作者按了停 / 进程死在半路），接着往下才有意义。
  //
  // **「它问了你一句」不算没跑完**（ADR 0024）：那一轮是**说完了**的一种——
  // 在对话里，停下来问就等于这一轮说到这儿了，下一句归作者。这时摆一颗「接着往下」
  // 等于请他跳过那个问题往下跑，而它问的正是「不问就得猜、猜错了他看不出来」的事。
  const canResume =
    !running &&
    !!chatId &&
    ((shownReceipt !== null &&
      shownReceipt.reason !== "done" &&
      shownReceipt.reason !== "asked_author") ||
      (current?.pending_lookups ?? 0) > 0);

  return (
    <section className="pane chat">
      <div className="chat-head">
        <span className="chat-head-title">{current?.title.trim() || "写作助手"}</span>
        <span className="spacer" />
        {/* 章号不是作者填的，是他此刻在写的那一章（顶栏那个选择器）。
            **它必须显示出来**：助手回答「这儿能不能说破」时用的正是这个坐标，
            而作者看到的是一段没有坐标的对话。 */}
        <span className="chat-asof">按第 {chapter} 章回答</span>
        {onDesk > 0 && (
          <CompareLink pid={pid} chapter={chapter}>
            还摆着 {onDesk} 稿 ↗
          </CompareLink>
        )}
        {/* 它记下的规矩摆在这后面（ADR 0023 决策二）。**有几条就写几条，零不写数字**
            ——一个「0」在这儿是噪音，而「一条都没有」这件事本身要带着理由，
            那句理由在面板里（同 `/check` 的 `rules_run`：零必须说得出自己是哪一种零）。
            **没挑中任何一段对话时整颗按钮不画**：规矩的坐标是「这一段对话 × 这一章」，
            没有前一半时它连问题都不成立。
            **它和「对话列表」互斥**：两块都盖在对话区上，同时摊开就把对话挤没了——
            而这一半的下限本来就只有 `MIN_CHAT` 那么宽（`layout.ts`）。 */}
        {chatId && (
          <button
            aria-expanded={rulesOpen}
            onClick={() => {
              setRulesOpen((v) => !v);
              setListOpen(false);
            }}
          >
            这一章的规矩{onRules > 0 ? ` ${onRules}` : ""}
          </button>
        )}
        <button
          aria-expanded={listOpen}
          onClick={() => {
            setListOpen((v) => !v);
            setRulesOpen(false);
          }}
        >
          对话列表
        </button>
      </div>

      {listOpen && projectId && (
        <ChatSessions
          pid={pid}
          sessions={list}
          failed={listFailure}
          current={chatId}
          onPick={setChat}
          onPicked={() => setListOpen(false)}
        />
      )}

      {/* **`key` 是坐标，不是优化**：规矩的坐标是（这一段对话 × 这一章），换掉其中任何
          一半，这块面板就是另一块了。不换 `key` 的话它原地留着，而它手上攒着一条只对
          上一个坐标成立的东西——**上一次「这一条没能取消」那句红字**（mutation 的错误
          活到下一次 mutate 为止）。于是作者翻了一页，红字跟着翻过去，挂在一块什么都没
          发生的面板上。
          这块屏幕上有过一模一样的病，而且已经修过一次：`failureHere` 那一条注释写着
          「一句关于别处的话，长得像这儿的事实」。 */}
      {rulesOpen && projectId && chatId && (
        <ChatRules key={`${chatId}#${chapter}`} pid={pid} chatId={chatId} chapter={chapter} />
      )}

      <div className="chat-log" ref={logRef}>
        {detail.isError && (
          <div className="err-box">
            这段对话没读出来 —— 它可能已经被删掉了。从「对话列表」里挑一段，或者开一段新的。
          </div>
        )}
        {/* 列表读不出来这件事**只说一遍**：那一列摊开的时候由它自己说（它就长在这块
            屏幕正上方），收起来的时候由这儿说。两处同时画就是同一句话摆两遍。 */}
        {listFailure && !listOpen && <div className="err-box">{listFailure}</div>}
        {!chatId && !running && !listFailure && (
          <p className="empty">
            跟它说一句话就开始。它能去翻这本书的目录、某几章的正文和梗概，
            也能替你算这一章谁还不知道什么。
          </p>
        )}

        {window.hidden > 0 && (
          <button className="chat-earlier" onClick={() => setExpanded(true)}>
            看更早的 {window.hidden} 条
          </button>
        )}
        {/* **key 不再是 `seq`**（2026-08-13）：系统那一行不占历史下标，它带的那个数
            说的是「它前面有几条历史消息」——**和紧跟其后那一条撞号**，撞掉的那一条
            React 会当成同一个东西复用，于是屏幕上少一句话。这一列只增不改、也从不
            重排，所以位置就是稳定的身份。 */}
        {window.shown.map((message, i) => (
          <Bubble key={i} message={message} />
        ))}
        {runningHere && pendingSaid && (
          <div className="chat-msg author">
            <span className="chat-who">{SPEAKER_ZH.author}</span>
            <p className="chat-text">{pendingSaid}</p>
          </div>
        )}

        {runningHere && (
          <RunningStrip
            since={startedAt}
            progress={progress}
            stopping={stop.isPending}
            onStop={() => {
              if (!chatId) return;
              // **报的是这一轮的标识**，不是「停这段对话」：一次迟到的「停」
              // 到达时，正在跑的可能已经是作者刚发起的下一轮了
              // （`chat.ts::newRunId` 写着那个序列）。后端比对不上就忽略。
              stop.mutate({ chatId, runId: runId.current }, {
                onSuccess: (r) => {
                  stopLanded.current = r.stopped;
                  // `stopped=false` 不是失败：那一刻它本来就没在跑。
                  // 后端那句话原样说出来，这里不另写一句。
                  setStopSaid(r.stopped ? null : { text: r.message, failed: false });
                },
                // 这一下**没送出去**（那段对话不在了 / 网断了）。原来这儿什么都没有，
                // 于是按下去屏幕上一个字都不变——见 `stopSaid` 那段注释。
                onError: (e) =>
                  setStopSaid({ text: refusalText(e, STOP_FAILED) ?? STOP_FAILED, failed: true }),
              });
            }}
          />
        )}
        {stopSaid &&
          (stopSaid.failed ? (
            <div className="err-box">{stopSaid.text}</div>
          ) : (
            <p className="chat-receipt-note">{stopSaid.text}</p>
          ))}
        {shownReceipt && (
          <Receipt
            receipt={shownReceipt}
            footnote={stopFootnote(stopLanded.current, shownReceipt)}
            pid={pid}
          />
        )}
        {/* **排在回执后面**：它是这一轮的收场，而作者的下一个动作就在它上面。
            点一个选项 = 作者答了那一句，也就是开下一轮（`said` 就是那个选项原文）——
            **不是把选项抄进输入框让他再按一次发送**。 */}
        {asked && <AskedCard asked={asked} busy={running} onPick={(answer) => {
          if (chatId && !running) runTurn(chatId, answer);
        }} />}
        {(failureHere || createFailure) && (
          <div className="err-box">{failureHere ?? createFailure}</div>
        )}
      </div>

      <div className="chat-say">
        <textarea
          aria-label="跟写作助手说"
          placeholder="想问它什么？（Ctrl / ⌘ + Enter 发送）"
          rows={3}
          value={said}
          disabled={running}
          onChange={(e) => setSaid(e.target.value)}
          // 光按 Enter 不发：中文输入法里 Enter 是选字，那会把半句话发出去。
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              send();
            }
          }}
        />
        {/* 输入框停用的理由必须写出来。**一轮跑好几分钟**，作者很可能在等的时候
            切去看另一段对话——那时这儿是一个没有任何解释的灰输入框，读起来像坏了。
            （一次只跑一轮是有意的：两轮同时飞，屏幕上就有两笔说不清是谁花的钱。） */}
        {running && !runningHere && (
          <p className="chat-say-note">另一段对话正在跑，跑完才能在这儿说话。</p>
        )}
        <div className="chat-say-row">
          {canResume && (
            <button onClick={resume} title="上一轮没跑完，接着往下">
              接着往下
            </button>
          )}
          <span className="spacer" />
          <button
            className="primary"
            disabled={!said.trim() || running || create.isPending || !projectId}
            onClick={send}
          >
            {running ? "跑着呢…" : "发送"}
          </button>
        </div>
      </div>
    </section>
  );
}
