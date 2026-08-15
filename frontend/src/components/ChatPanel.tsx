import { useEffect, useRef, useState } from "react";
import {
  useChatDetail,
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
import { ChatSessions } from "./ChatSessions";
import { BotIcon, SendIcon } from "./icons";
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
// **它今天的第一身份是白名单**，措辞只剩读屏在用（2026-08-15 起名字不画在屏幕上，
// 见 `Bubble`）——但仍然**一份**（`visibleMessages` 的 docstring 写着为什么必须是一份）。
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

/** 一句话。**说话人不写在屏幕上**（作者 2026-08-15：「不需要系统和你这种文字区分」）。
 *
 *  分得开靠的是形：**你说的话有一块底**，它说的话没有，系统那一行是灰的窄字加一道竖线。
 *  这三样都不是文字，所以不占地方，也不会在一屏五句话时重复五遍「你」。
 *
 *  **`SPEAKER_ZH` 一个字都不许删**：它同时是那张白名单（`chat.ts::visibleMessages`
 *  靠「认不认得这个说话人」把工具返回挡在屏幕外，而那里面是节点标识和作者写的
 *  秘密）。名字改成读屏专用（`.chat-who` 那一版是画在屏幕上的），读屏还念得出
 *  这句是谁说的——去掉它，那块屏幕对读屏用户就变成一串没有归属的段落。 */
function Bubble({ message }: { message: ChatMessageView }) {
  return (
    <div className={"chat-msg " + message.speaker}>
      <span className="chat-who-sr">{SPEAKER_ZH[message.speaker]}</span>
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
  // ⚠️ **「这一章的规矩」那颗按钮和它那块面板 2026-08-14 整个撤了**（ADR 0023 决策二的
  // 界面那一半）。作者的原话：
  //
  //   「这个原本就不需要展示给用户看，并且这个规则是有时效性的。比如用户说男主不准
  //     杀人在这个沙地，那男主出了沙地这个规则就失效了，这个要由 llm 智能判断，
  //     而不是显示出来给用户选择。」
  //
  // 他说的是**机制**，不是显示：一条规矩的有效期是**情境**的（「在这片沙地时」），
  // 而引擎今天把它算成**章号**的——`agent/rules.py` 只有两档「这一轮」/「这一章」，
  // 靠 `!=` 在切章时整批放掉。那块面板把这个粗糙度原样摆到了作者面前，还要他去管。
  //
  // **提炼那一步本来就是模型干的**（`agent/rules.py` 顶上那句「第五件不在这儿」），
  // 作者一个表单都没填过——所以撤掉的只是「让他去点掉」这一半。
  //
  // ⚠️ **它换走了 ADR 0023 押的那条退路**（「看得见 + 能取消」）。今天的兜底是
  // 规矩最多祸害一章（切章自动失效）；**一旦有效期改成模型判**，那条兜底就没了，
  // 留痕必须先落到日志（「这一轮用了你说过的哪几句」）**再**改机制，顺序不能反。
  //
  // 后端 `GET/DELETE …/rules` 因此**变成零调用方**——这个仓库为「零调用方端点」
  // 栽过一次（ARCHITECTURE「工作台的已知洞」第 4 条：滚动总结一直在花作者的钱，
  // 而他看不见），所以这句话写在这儿而不是靠人记着。

  const [listOpen, setListOpen] = useState(false);
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

  // 没挑过就停在最近说过话的那一段（后端按这个顺序给），同 App 里「默认打开第一本书」。
  useEffect(() => {
    if (!chatId && list.length > 0) setChat(list[0].id);
  }, [chatId, list, setChat]);

  // 换一段对话 = 上一段的「看更早的」和那句「不用停」都过期了。
  // **回执不在这儿清**：它自己记着属于哪一段（`receipt.chat`），切回来还看得见
  // 上一轮的结论，切走也不会跟着跑到别人头上。
  useEffect(() => {
    setExpanded(false);
    setStopSaid(null);
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

  // **不读 `error.message`**：它在后端没写 `message` 时退回 `body.error`，而这几条路由的
  // 404 恰恰只有码没有话（`chat_not_found` / `project_not_found`，真 app 打过）。
  const failure = refusalText(turn.error, TURN_FAILED);
  const createFailure = refusalText(create.error, CREATE_FAILED);
  const listFailure = refusalText(sessions.error, LIST_FAILED);
  /** 那一轮的失败**属于它自己那一段**（同 `receipt.chat`，这儿原来漏了）。
   *  一轮跑好几分钟，「等的时候切过去看另一段」是常态：不钉住的话，另一段上会长出
   *  一个红框，而那一段什么都没发生——一句关于别处的话，长得像这儿的事实。 */
  const failureHere = runFor === chatId ? failure : null;
  /** 这块屏幕上一句话都还没有。**含「刚开的新对话」那一档**（`chatId` 有了、消息是空的）——
   *  原来那条判据是 `!chatId`，于是新开一段之后屏幕上什么都没有。
   *  读不出来（`detail.isError` / `listFailure`）时不画：那时候「还没说话」是一句
   *  它不知道真假的话，而作者三个月的对话可能都在里面。 */
  const nothingSaidYet =
    messages.length === 0 && !running && !listFailure && !detail.isError && !detail.isLoading;

  // ⚠️ **「接着往下」那颗按钮 2026-08-15 撤了**（作者：「就不能用户用打字的形式说继续吗」）。
  // 它发的是一句空话（`said=""` 即 resume，ADR 0019：看尾巴、补跑缺的、继续）。
  // **后端那条路一个字没动**，撤掉的只是屏幕上那颗按钮。
  //
  // 诚实说明：作者打「继续」和那颗按钮**不是同一件事**——前者是新的一轮、带着他那两个字，
  // 后者是接着上一轮的尾巴往下跑。今天两者的结果多半一样（模型看得见整段历史），
  // 但「上次断在半路」那一档（`pending_lookups > 0`）走打字这条路，补查是模型自己
  // 决定要不要做的，不再是系统替他补。会话列表那一行的提示（「接着说会自动补上」）
  // 说的就是这条路。

  return (
    <section className="pane chat">
      <div className="chat-head">
        <span className="chat-head-title">{current?.title.trim() || "写作助手"}</span>
        <span className="spacer" />
        {/* 这儿曾经有一句「按第 N 章回答」。**2026-08-14 撤掉**，作者的原话是
            「写这一章也可能引用其他章的内容，不一定要强制地把这个显示在这」——
            而他是对的，那句话有两处不对：

            1. **它读起来像一条限制，而那条限制不存在。** 助手能去翻目录、别的章的正文
               和梗概（正下方那句空态自己就写着）。章号管的只有「这儿能不能说破」
               这一件事，不是「只准用这一章的材料」。
            2. **那个坐标屏幕上本来就有**：顶栏的章节选择器和中栏的正文都停在同一章。
               在对话头上再抄一份，抄的还是一句会误导人的措辞。

            它原来的理由是「作者看到的是一段没有坐标的对话」。那件事仍然真——
            同一段对话在第 700 章和第 722 章会给出不一样的答案——**但那属于助手
            开口时该说的话**（「第 722 章这儿还不能说破他的身世」），不属于常驻在
            标题栏的一行字。想补的时候补在那句话里，别把它加回这儿。 */}
        {onDesk > 0 && (
          <CompareLink pid={pid} chapter={chapter}>
            还摆着 {onDesk} 稿 ↗
          </CompareLink>
        )}
        {/* 「这一章的规矩」那颗按钮原来在这儿。撤掉的理由写在上面 `listOpen` 那一段。 */}
        <button aria-expanded={listOpen} onClick={() => setListOpen((v) => !v)}>
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

      <div className="chat-log" ref={logRef}>
        {detail.isError && (
          <div className="err-box">
            这段对话没读出来 —— 它可能已经被删掉了。从「对话列表」里挑一段，或者开一段新的。
          </div>
        )}
        {/* 列表读不出来这件事**只说一遍**：那一列摊开的时候由它自己说（它就长在这块
            屏幕正上方），收起来的时候由这儿说。两处同时画就是同一句话摆两遍。 */}
        {listFailure && !listOpen && <div className="err-box">{listFailure}</div>}
        {/* 一句话都还没有的那块屏幕。**判据是「这一段里没有话」，不是「还没挑一段」**——
            刚开的新对话 `chatId` 是有的、消息是空的，原来那条判据在这一档什么都不画，
            于是「＋ 开一段新的对话」按下去等于面对一片空白（作者 2026-08-15 的原话）。

            画的是助手自己那张脸 + 一句「开始写作」+ 它能干什么。**不摆按钮**：
            这块屏幕上唯一的下一步就在正下方那个输入框里，再放一颗按钮是同一个动作
            两个入口，而其中一个还得替作者想好第一句话该说什么。 */}
        {nothingSaidYet && (
          <div className="chat-hello">
            <span className="chat-hello-mark" aria-hidden="true">
              <BotIcon open={false} />
            </span>
            <p className="chat-hello-title">开始写作</p>
            <p className="chat-hello-sub">
              说一句就行。它能翻这本书的目录、某几章的正文和梗概，
              也能替你算这一章谁还不知道什么。
            </p>
          </div>
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
            <span className="chat-who-sr">{SPEAKER_ZH.author}</span>
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
        {/* 输入框和那颗发送**是一个盒子**（`.chat-say-box`）：按钮吊在框内右下角，
            文字的右边和下边给它让出了位置（`.chat-say-box textarea` 的内边距）。
            它原来是框底下单独一行、写着「发送」两个字——作者要的是「放进框里、
            换成图标」。**框自己画边框，textarea 不画**，否则框里套一个框。 */}
        <div className="chat-say-box">
        <textarea
          aria-label="跟写作助手说"
          placeholder="开始写作…（Enter 发送，Shift + Enter 换行）"
          rows={3}
          value={said}
          disabled={running}
          onChange={(e) => setSaid(e.target.value)}
          /* **Enter 直接发，Shift + Enter 换行**（2026-08-15 作者定的）。
           *
           * 这儿原先是「必须 Ctrl/⌘ + Enter 才发」，理由写的是「中文输入法里 Enter
           * 是选字，光按 Enter 会把半句话发出去」——**那个顾虑是真的，但躲开它的
           * 办法不是让作者多按一个键**，是问浏览器「这一下 Enter 是不是在选字」：
           *
           *   `isComposing` = 输入法的候选框正开着。中文敲「你好」时按下的那个
           *   Enter 属于输入法，不属于这个输入框——放过它，`onChange` 会照常把
           *   确认好的字填进来。**这一条错了的症状正是原来那句注释描述的东西**，
           *   而这个产品的作者每一句话都是中文敲的。
           *
           *   `keyCode === 229` 是同一件事的老写法。两个都判：Safari 和一部分
           *   输入法（搜狗、微信输入法）在某些版本上只给得出其中一个。
           *
           * **屏幕上只说这一套手势**（作者 2026-08-15：老那套不保留）。
           * Ctrl/⌘ + Enter 落在「Enter 且没按 Shift」里，所以它照样把话发出去——
           * 那不是留着的第二套手势，是**没有为它单开一条分支**：为了让它彻底没反应
           * 而多写一个 `if`，等于亲手做出一颗按下去什么都不发生的键。 */
          onKeyDown={(e) => {
            if (e.key !== "Enter") return;
            if (e.nativeEvent.isComposing || e.keyCode === 229) return;
            if (e.shiftKey) return; // 换行：交给浏览器自己插那个 \n
            e.preventDefault();
            send();
          }}
        />
          {/* 名字由 `aria-label` 给（图标按钮的规矩，同顶栏那几颗）。
              跑着的时候只是灰掉——**「停」在上面那条跑动条上**，这儿再放一颗
              就是同一个动作两个入口。 */}
          <button
            className="chat-send"
            aria-label="发送"
            data-tip="发送"
            disabled={!said.trim() || running || create.isPending || !projectId}
            onClick={send}
          >
            <SendIcon />
          </button>
        </div>
        {/* 输入框停用的理由必须写出来。**一轮跑好几分钟**，作者很可能在等的时候
            切去看另一段对话——那时这儿是一个没有任何解释的灰输入框，读起来像坏了。
            （一次只跑一轮是有意的：两轮同时飞，屏幕上就有两笔说不清是谁花的钱。） */}
        {running && !runningHere && (
          <p className="chat-say-note">另一段对话正在跑，跑完才能在这儿说话。</p>
        )}
      </div>
    </section>
  );
}
