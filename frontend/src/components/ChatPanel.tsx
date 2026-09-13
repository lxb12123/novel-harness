import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import {
  useAiSettings,
  useChatDetail,
  useChats,
  useCreateChat,
  useRunTurn,
  useSayMidTurn,
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
import { useLanguage, type Language } from "../language";
import { useCoords } from "../store";
import { useLiveDraft } from "../liveDraft";
import { ChatSessions } from "./ChatSessions";
import { BotIcon, ConversationsIcon, SendIcon, StopIcon } from "./icons";
import { DraftCandidates } from "./DraftCandidates";
import { ModelGuide } from "./ModelGuide";

// 写作助手（模式二，[ADR 0019](docs/adr/0019-agent-loop-not-graph.md)）。
// 中栏对半分之后的右半边：左边正文、右边它。左栏书架和右栏面板一个像素不动。
//
// ── 三件这块屏幕必须自己做对的事（2026-08-12 第一条重写，ADR 0024）──────────
//
// 1. **逐字看得见的只有稿子，回话那一档今天不逐字 —— 所以这儿不许装成逐字。**
//    这一轮走的是长连接了（`api/turnStream.ts`），中间过程真的一条条到手；
//    但两条流不是一回事：起草那次调用的片会递到这儿，**回话那次的不会**——
//    wire 上它 2026-09-12 起也是流式的了（回复要了 `interruptible`，为的是「停」落在
//    下一片之内），但装配层造模型端口时没接 `on_event`（`api/chat.py::build_agent_model`
//    那个注入点，见那儿的注释），一片都到不了界面。
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
const TURN_FAILED = (language: Language): string =>
  language === "zh"
    ? "本轮未完成，未返回原因。请刷新后查看对话。"
    : "This round did not complete and no reason was returned. Refresh to see the conversation.";
const CREATE_FAILED = (language: Language): string =>
  language === "zh"
    ? "新建对话失败，未返回原因。"
    : "Could not create a new conversation; no reason was returned.";
/** 中途那一句**没送出去**（网断了 / 这段对话不在了）。同 `STOP_FAILED`：一声不吭等于
 *  让他以为说出去了，而那句话其实哪儿都没到。 */
const SAY_FAILED = (language: Language): string =>
  language === "zh"
    ? "消息未发出，未返回原因。内容仍在输入框中，可在本轮结束后重新发送。"
    : "The message was not sent and no reason was returned. It is still in the input box; send it again after this round ends.";
const STOP_FAILED = (language: Language): string =>
  language === "zh"
    ? "「停」未送达，未返回原因。本轮可能仍在进行，请稍后重试。"
    : "Stop was not delivered and no reason was returned. This round may still be running; try again shortly.";
/** 列表读不出来。**不许让屏幕替它说「你还没说过话」**——那是一句它不知道真假的话，
 *  而作者三个月的对话可能都在里面（§10 约束 8）。 */
const LIST_FAILED = (language: Language): string =>
  language === "zh"
    ? "对话列表读取失败；下方为空不代表没有对话。请刷新后查看。"
    : "The conversation list could not be loaded; an empty list here does not mean there are none. Refresh to check.";

/** 离底不到这么多像素就算「贴着底」。8px 是给亚像素取整留的，不是给「差一点点」的：
 *  作者往上翻了哪怕一行，就是不想被拽回去。 */
const FOLLOW_SLACK = 8;

/** 让一个会滚的盒子**跟着它的底走**：内容变长时，贴着底就滚到底；作者往上翻了就不动，
 *  翻回底下又接着跟。**用法**：`ref` 和 `onScroll` 都挂在那个盒子上；作者自己发了一句
 *  就调 `pin()` 重新贴上，点开更早的话就调 `unpin()`；`pinOn` 一变（换了段对话）
 *  也重新贴上——那一段要从它的末尾看起，上一段里翻到哪儿跟它无关。
 *
 *  ── 为什么是「每次画完都看一眼」而不是列一份依赖表 ────────────────────────────
 *  这儿原来是 `useEffect(…, [messages.length, running])`：只有历史变长、或者一轮开始 /
 *  结束才滚一下。而一轮跑着的时候变长的东西全不在那两样里——进度行、它说的话、
 *  **逐字长的那一稿**、排着队的那句、回执——于是屏幕停在开跑那一刻不动，作者得自己
 *  往下滑（2026-09-12 报的原话：「没有跟紧他那个最新的输出，他只会停留在某一个时刻」）。
 *  会让对话变长的东西太多，列一份迟早漏一样，漏掉的那一样就是下一次「停在某一刻」。
 *  所以不列：每次画完（`useLayoutEffect`，画到屏幕之前）都看一眼，贴着底就滚到底——
 *  一次 `scrollHeight` 读取的代价，换「什么都不会漏」。
 *
 *  「贴没贴着底」记在 ref 里、由 `onScroll` 更新，**不是 state**：它每次滚动都在变，
 *  而它变了屏幕上什么都不用重画。程序自己滚到底那一下也会触发 `onScroll`，算出来
 *  正好是「贴着」，所以不用另外记。jsdom 里三个尺寸恒为 0，这一套在测试里就是
 *  「一直贴着」——要验它，测试得自己给盒子量尺寸。 */
function useFollowBottom<T extends HTMLElement>(pinOn?: unknown) {
  const ref = useRef<T>(null);
  const pinned = useRef(true);
  // **声明在「看一眼」那条前面**：同一次 commit 里的 layout effect 按声明顺序跑，
  // 换了段对话的那一次画面，先贴上、再看一眼，才会当场滚到那一段的末尾——
  // 反过来的话得等下一次画面，而缓存里有的那一段可能根本不再画第二次。
  useLayoutEffect(() => {
    pinned.current = true;
  }, [pinOn]);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  });
  return {
    ref,
    onScroll: () => {
      const el = ref.current;
      if (el) pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight <= FOLLOW_SLACK;
    },
    pin: () => {
      pinned.current = true;
    },
    unpin: () => {
      pinned.current = false;
    },
  };
}

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
  const language = useLanguage((s) => s.language);
  return (
    <div className={"chat-msg " + message.speaker}>
      <span className="chat-who-sr">{SPEAKER_ZH[message.speaker][language]}</span>
      <p className="chat-text">{message.text}</p>
    </div>
  );
}

/** 正在写（或者刚写完）的一稿——**一行字，不是一格稿子**。
 *
 *  **稿子的字一个都不在这儿画**（作者 2026-09-12，三次：「一定要在左边写」「为什么非要在
 *  右侧的那个稿子里面写东西，把这个东西去掉」）。正在写的那一稿在左边的编辑器里长
 *  （`liveDraft.ts`），写着的时候这儿连这一行都没有（「不用特地提醒在左边什么的」）；
 *  收场换成后端那句「第几稿完成 / 停在这儿了 / 没写成」。
 *  一批几稿同时在飞时第二条起的流不进编辑器，这儿就是一行「正在起草第 N 章…」——
 *  它的字在桌上，作者从右边那一行点「放入编辑器」才看。 */
function DraftingBox({ draft }: { draft: LiveDraft }) {
  const language = useLanguage((s) => s.language);
  const inEditor = useLiveDraft((s) => s.inEditor && s.draft?.stream === draft.stream);
  if (inEditor && !draft.done) return null;
  return (
    <span className="chat-step chat-drafting">
      {draft.done ||
        (draft.revising
          ? language === "zh"
            ? `正在修改第 ${draft.chapter} 章…`
            : `Revising chapter ${draft.chapter}…`
          : language === "zh"
            ? `正在起草第 ${draft.chapter} 章…`
            : `Drafting chapter ${draft.chapter}…`)}
    </span>
  );
}

/** 还在跑的那一段。**秒表是真的，中间那几行也是真的，别的什么都不编。**
 *
 *  ── 它排在对话里，不框在一张卡里（作者 2026-09-12）──────────────────────────
 *
 *  这儿原来是一张带边框、带底色的卡：抬头一行秒表，底下一句「下面是它这会儿在做的
 *  事」，再往下才是它说的话和做的事。作者的原话：「我不要用框框框住他的思考内容……
 *  直接放到那个上下文中」。所以现在：
 *
 *  - 它说的每一段话**长得和跑完之后那一条一模一样**（同 `Bubble` 的形）——
 *    回执落地、历史重取，那几段话原地不动，不会先缩在卡里再跳出来变大一号；
 *  - 它做的每一件事是一行灰的小字，夹在它说的话中间，**按到达顺序**
 *    （`chat.ts::ProgressLine`）；
 *  - 秒表收成末尾一行：对话的活尾巴，新东西长在它上面。**「停」不在这儿**
 *    （2026-09-12 挪去了发送那颗圆钮上：跑着而框里没字时它就是「停」）。
 *    那句「它可能要来回查几次资料…」撤了：每一轮都一样、说的又是屏幕上正在
 *    发生的事（同「说完了。」那一条的理由）。这儿 2026-08-13 还撤过一句讲流式
 *    语义的话，病根一样——**屏幕该用状态本身说话**：字在流就让他看见字
 *    （`DraftingBox`），不流就是秒表 + 一行行步骤，别靠一句文案说准。
 *
 *  ── 作者中途说的话（2026-09-12，后端 `Mailbox`）────────────────────────────
 *
 *  跑着的时候他还能说：那句话先以「排着队」的样子（`queued`，淡一档）贴在这一段的
 *  末尾，等后端在下一次模型调用之前把它并进对话、喊一声 `author_said`，它就变成
 *  一条正常的作者气泡、站在模型真的读到它的位置（`ProgressLine.author`）。
 *  底下那一句是后端的回执（「记下了，它下一步就会看到。」），有排着的才画。 */
function RunningStrip({ since, progress, queued, queuedNote, stopping }: {
  since: number;
  progress: TurnProgress;
  queued: string[];
  queuedNote: string | null;
  stopping: boolean;
}) {
  const language = useLanguage((s) => s.language);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="chat-running" role="status">
      {progress.lines.map((line, i) => {
        if (line.kind === "step") {
          return (
            <span key={i} className="chat-step">
              {line.text}
            </span>
          );
        }
        if (line.kind === "said" || line.kind === "author") {
          const speaker = line.kind === "said" ? "assistant" : "author";
          return (
            <div key={i} className={"chat-msg " + speaker}>
              <span className="chat-who-sr">{SPEAKER_ZH[speaker][language]}</span>
              <p className="chat-text">{line.text}</p>
            </div>
          );
        }
        const draft = progress.drafts.find((d) => d.stream === line.stream);
        return draft ? <DraftingBox key={i} draft={draft} /> : null;
      })}
      {queued.map((text, i) => (
        <div key={"queued-" + i} className="chat-msg author queued">
          <span className="chat-who-sr">{SPEAKER_ZH.author[language]}</span>
          <p className="chat-text">{text}</p>
        </div>
      ))}
      {queued.length > 0 && queuedNote && <p className="chat-receipt-note">{queuedNote}</p>}
      <div className="chat-running-tail">
        <span className="chat-running-dot" aria-hidden="true" />
        <span className="chat-running-clock">
          {/* 「停」送到之后这行换一句：工作已经停了，它正在问作者一句（后端 debrief）。
              秒表照走——那一句也是在花时间，而且再按一次「停」连它也停。 */}
          {stopping
            ? language === "zh" ? "已停止，写作助手正在提问 · " : "Stopped; the assistant is asking a question · "
            : language === "zh" ? "本轮进行中 · " : "Round in progress · "}
          {elapsedText(now - since, language)}
        </span>
      </div>
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
  const language = useLanguage((s) => s.language);
  const waitingLabel = language === "zh" ? "写作助手在等待回答" : "The assistant is waiting for your answer";
  return (
    <div className="chat-asked" role="group" aria-label={waitingLabel}>
      <span className="chat-asked-head">{waitingLabel}</span>
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
            {language === "zh"
              ? "点选一项即作为回答，也可在下方输入其他回答"
              : "Select one as your answer, or type a different answer below"}
          </span>
        </>
      ) : (
        <span className="chat-asked-note">
          {language === "zh" ? "请在下方输入回答" : "Type your answer below"}
        </span>
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
  const language = useLanguage((s) => s.language);
  const notes = receiptNotes(receipt, language);
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
      {/* 这一轮写出来的那几稿（ADR 0022 / 0048）：每稿一行，字在左边的编辑器里。 */}
      <DraftCandidates pid={pid} drafts={receipt.drafts} />
    </div>
  );
}

export function ChatPanel() {
  const { projectId, chapter, chatId, setChat } = useCoords();
  const language = useLanguage((s) => s.language);
  const pid = projectId ?? "";
  const ai = useAiSettings(); // 输入框上面那句「先连接模型」要知道配好了没有
  const sessions = useChats(projectId);
  const detail = useChatDetail(projectId, chatId);
  const create = useCreateChat(pid);
  const turn = useRunTurn(pid);
  const stop = useStopChat(pid);
  const sayMid = useSayMidTurn(pid);
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
  // 会话列表那一页开着时按 Esc 回到对话（同各处抽屉）。
  useEffect(() => {
    if (!listOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setListOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [listOpen]);
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
  /** 「停」送到了、这一轮还没收场的那几秒。**屏幕上要说出来**：信号一到后端，工作就
   *  停了（回复那一次调用走流式，下一片就断），接着它会问作者一句——这期间秒表那行
   *  要是还写着「正在跑这一轮」，作者看到的就是「按了没反应」（2026-09-12 报的原话）。
   *  只是一个显示态：它开着时那颗圆钮照旧是「停」，再按一次连那一句也停。 */
  const [stopping, setStopping] = useState(false);
  /** 按下「停」之后屏幕上多出来的那一句，以及它是不是一次失败。
   *
   *  **两档必须分开画**：`stopped=false`（那一刻本来就没在跑）是一句正常的话，
   *  画成红框就是把「不用停」说成「停失败了」；而**这一下真的没送出去**时
   *  一声不吭同样不行——一轮跑好几分钟，「停」是作者唯一能插手的地方，
   *  按下去屏幕纹丝不动读起来就是按钮坏了，而他只会再按一次、再等一次。 */
  const [stopSaid, setStopSaid] = useState<{ text: string; failed: boolean } | null>(null);
  /** 作者中途说的、**还排着队**的那几句（2026-09-12，后端 `Mailbox`）。
   *
   *  送出去的那一刻它进这个表；后端在下一次模型调用之前把它并进对话、喊一声
   *  `author_said`，它就从这儿出去、在 `progress.lines` 里以正常的作者气泡出现。
   *  **它不是对话的一部分**：只有 `author_said` 到手才算它进了对话，所以这儿的每一句
   *  都画淡一档。`queuedNote` 是后端那句回执（「记下了，它下一步就会看到。」）。 */
  const [queued, setQueued] = useState<string[]>([]);
  const [queuedNote, setQueuedNote] = useState<string | null>(null);
  /** 对话区跟着底走（新到的字、逐字长的那一稿、回执……）——贴着底才跟，见 `useFollowBottom`。 */
  const log = useFollowBottom<HTMLDivElement>(chatId);
  /** 输入区那一整块有多高。**对话区要正好在它底下多留这么多**，多一分少一分都看得出来：
   *  少了，最后一句话被玻璃压住读不全；多了，屏幕底下空出一条谁都不占的白带。
   *  它不是常数——那个框作者能自己拖高（`textarea` 的 `resize`），所以只能量。 */
  const sayRef = useRef<HTMLDivElement>(null);
  const [sayHeight, setSayHeight] = useState(0);

  const list = sessions.data ?? [];

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

  // 输入区一高一矮，底下那块留白跟着变。**量的是边框盒**（`getBoundingClientRect`），
  // 不是 `contentRect`：那个框自己画着 1px 描边和内边距，用内容盒会短一截。
  // jsdom 里没有 `ResizeObserver`，所以先判断一下再用——少了这一句整份组件测试全崩。
  useEffect(() => {
    const el = sayRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setSayHeight(el.getBoundingClientRect().height));
    // **`box: "border-box"` 不是可选项**：默认那个只在内容盒变了才响，而空态和常态
    // 之间变的是这一块的 `padding-top`——内容盒一个像素没动，于是它一声不吭，
    // 底下留白比实际矮 8px（2026-09-10 真踩了一次）。
    ro.observe(el, { box: "border-box" });
    return () => ro.disconnect();
  }, []);

  function runTurn(id: string, text: string) {
    stopLanded.current = false;
    setStopping(false);
    setStopSaid(null);
    setReceipt(null);
    setProgress(NO_PROGRESS);
    setQueued([]);
    setQueuedNote(null);
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
        onEvent: (event) => {
          setProgress((prev) => applyTurnEvent(prev, event, language));
          // 它读到了作者中途那句：从「排着队」那一格出去（归约那边把它排进对话里）。
          if (event.kind === "author_said") {
            setQueued((prev) => {
              const at = prev.indexOf(event.text);
              return at < 0 ? prev : [...prev.slice(0, at), ...prev.slice(at + 1)];
            });
          }
        },
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
        // 收场之后「排着队」那一格不该还有东西：后端收场时会把信箱清空并逐句喊
        // `author_said`（`Mailbox` 第一条规矩），流断在半路时那几句才会留在这儿——
        // 那时它们没进对话，还回输入框比让它们淡淡地挂着更诚实。
        onSettled: () => {
          setQueued((left) => {
            if (left.length) setSaid((prev) => (prev ? prev : left.join("\n")));
            return [];
          });
          setQueuedNote(null);
        },
      },
    );
  }

  /** 一轮跑着的时候再说一句（2026-09-12）。**不等、不打断**：后端把它放进正在跑的那一轮
   *  的信箱，下一次模型调用之前并进对话。`queued=false`（那一刻刚跑完 / 在跑的是另一轮）
   *  时这句话没排进去也没落库——**还回输入框**，后端那句话说清了为什么；作者再按一次
   *  就是正常的新一轮。 */
  function sayWhileRunning(id: string, text: string) {
    sayMid.mutate(
      { chatId: id, runId: runId.current, said: text },
      {
        onSuccess: (r) => {
          if (r.queued) {
            setQueued((prev) => [...prev, text]);
            setQueuedNote(r.message);
            return;
          }
          setSaid((prev) => (prev ? prev : text));
          setStopSaid({ text: r.message, failed: false });
        },
        onError: (e) => {
          setSaid((prev) => (prev ? prev : text));
          setStopSaid({
            text: refusalText(e, SAY_FAILED(language)) ?? SAY_FAILED(language),
            failed: true,
          });
        },
      },
    );
  }

  /** 按下「停」（发送那颗圆钮跑着、框里没字时的那一面）。 */
  function stopRunning() {
    if (!chatId) return;
    // **报的是这一轮的标识**，不是「停这段对话」：一次迟到的「停」
    // 到达时，正在跑的可能已经是作者刚发起的下一轮了
    // （`chat.ts::newRunId` 写着那个序列）。后端比对不上就忽略。
    stop.mutate({ chatId, runId: runId.current }, {
      onSuccess: (r) => {
        stopLanded.current = r.stopped;
        setStopping(r.stopped);
        // `stopped=false` 不是失败：那一刻它本来就没在跑。
        // 后端那句话原样说出来，这里不另写一句。
        setStopSaid(r.stopped ? null : { text: r.message, failed: false });
      },
      // 这一下**没送出去**（那段对话不在了 / 网断了）。原来这儿什么都没有，
      // 于是按下去屏幕上一个字都不变——见 `stopSaid` 那段注释。
      onError: (e) =>
        setStopSaid({
          text: refusalText(e, STOP_FAILED(language)) ?? STOP_FAILED(language),
          failed: true,
        }),
    });
  }

  function send() {
    const text = said.trim();
    if (!text || !projectId) return;
    // 自己发了一句就是要看它落在哪儿——哪怕刚才翻上去读旧话，这一下也重新贴回底下。
    log.pin();
    if (running) {
      // 跑着的是眼前这一段才能插话；别的段上输入框本来就是灰的。
      if (!runningHere || !chatId) return;
      setSaid("");
      return sayWhileRunning(chatId, text);
    }
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
  const failure = refusalText(turn.error, TURN_FAILED(language));
  const createFailure = refusalText(create.error, CREATE_FAILED(language));
  const listFailure = refusalText(sessions.error, LIST_FAILED(language));
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
    /* 一句话都还没有的时候整块屏幕换一种排法（`blank`，作者 2026-09-10）：
       那张脸和输入框**并成一组立在正中**，不再是脸悬在半空、输入框独自钉在底边。
       换的只有位置——输入框自己的长相（高度、圆角、那颗发送）一个像素不动。 */
    <section
      className={nothingSaidYet ? "pane chat blank" : "pane chat"}
      style={{ "--say-h": `${sayHeight}px` } as CSSProperties}
    >
      <div className="chat-head">
        {/* **这一格永远是「写作助手」**（作者 2026-09-10：「不要因为第一句话改变」）。
            原来它写的是那一段对话的标题，而标题是后端拿作者说的第一句话起的——
            于是打了声招呼，面板的名字就变成「你好」。**面板的名字是它自己的名字**，
            不是当前这一段的名字；哪一段在跟前由「对话列表」那一列说，那儿的标题一个
            字没动。 */}
        <span className="chat-head-title">
          {language === "zh" ? "写作助手" : "Writing assistant"}
        </span>
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
        {/* 「本章 N 稿 ↗」那条入口和它指向的并排页 2026-09-12 撤了（作者：「这块就不要了」）：
            稿子在左边的编辑器里，右边每稿一行；上一轮的稿子不再有第二个入口。 */}
        {/* 「这一章的规矩」那颗按钮原来在这儿。撤掉的理由写在上面 `listOpen` 那一段。 */}
        {/* 会话列表那颗开关：**只有图标**（作者 2026-09-12：「那个按钮换成图标」），
            名字由 `aria-label` 给、说明走 `data-tip`（同顶栏那几颗，`icons.tsx` 第 3 条）。
            开着时它亮着（`.on`），再点一次回到对话。 */}
        <button
          className={"icon-btn" + (listOpen ? " on" : "")}
          aria-label={language === "zh" ? "对话列表" : "Conversations"}
          aria-expanded={listOpen}
          data-tip={
            listOpen
              ? language === "zh" ? "返回对话" : "Back to the conversation"
              : language === "zh" ? "对话列表" : "Conversations"
          }
          onClick={() => setListOpen((v) => !v)}
        >
          <ConversationsIcon />
        </button>
      </div>

      {/* ── 会话列表是面板里的**一页**（2026-09-12）──────────────────────────
          它 2026-09-07 曾是挂在那颗按钮下面的一张浮层，作者又否了它（「太丑了……
          不是这种小窗口的方式」）。现在点开时**对话区和输入框整个换成它**：一列通栏的行，
          用满这一半的宽度；挑一段 / 开一段新的 / 再点一次那颗图标，都回到对话。
          对话那一侧不卸载（`hidden`）：一轮正跑着的秒表、翻到一半的位置都留着。 */}
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

      <div className="chat-log" ref={log.ref} onScroll={log.onScroll} hidden={listOpen}>
        {detail.isError && (
          <div className="err-box">
            {language === "zh" ? (
              <>此对话读取失败，可能已被删除。请从「对话列表」选择其他对话，或新建对话。</>
            ) : (
              <>
                This conversation could not be loaded; it may have been deleted. Choose another from
                “Conversations”, or start a new one.
              </>
            )}
          </div>
        )}
        {/* 列表读不出来这件事**只说一遍**：那一列摊开的时候由它自己说（它就长在这块
            屏幕正上方），收起来的时候由这儿说。两处同时画就是同一句话摆两遍。 */}
        {listFailure && !listOpen && <div className="err-box">{listFailure}</div>}
        {/* 一句话都还没有的那块屏幕。**判据是「这一段里没有话」，不是「还没挑一段」**——
            刚开的新对话 `chatId` 是有的、消息是空的，原来那条判据在这一档什么都不画，
            于是「＋ 开一段新的对话」按下去等于面对一片空白（作者 2026-08-15 的原话）。

            画的只有助手自己那张脸 + 一句招呼。**不摆按钮**：这块屏幕上唯一的
            下一步就在正下方那个输入框里，再放一颗按钮是同一个动作两个入口，而其中一个
            还得替作者想好第一句话该说什么。

            ⚠️ **底下那句「它能翻这本书的目录…」2026-09-10 撤了**（作者：「这句话去掉」）。
            它数的是助手有哪几把工具，而作者不是照着一张能力清单开口的——真要知道它会
            什么，问它就是了。留下的这一屏只说「从这儿开始」。 */}
        {nothingSaidYet && (
          <div className="chat-hello">
            <span className="chat-hello-mark" aria-hidden="true">
              <BotIcon />
            </span>
            <p className="chat-hello-title">
              {language === "zh" ? "无限创意，从此谱写" : "Endless ideas, written from here"}
            </p>
          </div>
        )}

        {window.hidden > 0 && (
          <button
            className="chat-earlier"
            // 点开更早的话是要往上读，这一下之后不许再把他拽回底下。
            onClick={() => {
              log.unpin();
              setExpanded(true);
            }}
          >
            {language === "zh"
              ? `查看更早的 ${window.hidden} 条`
              : `See ${window.hidden} earlier message${window.hidden === 1 ? "" : "s"}`}
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
            <span className="chat-who-sr">{SPEAKER_ZH.author[language]}</span>
            <p className="chat-text">{pendingSaid}</p>
          </div>
        )}

        {runningHere && (
          <RunningStrip
            since={startedAt}
            progress={progress}
            queued={queued}
            queuedNote={queuedNote}
            stopping={stopping}
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
            footnote={stopFootnote(stopLanded.current, shownReceipt, language)}
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

      <div className="chat-say" ref={sayRef} hidden={listOpen}>
        {/* 模型服务没配好：发出去只会换来一句「无法连接写作模型」。先在框上面把路画出来
            （作者 2026-09-13：「模式二发送一句话的情况下，都应该提醒用户配置」）。 */}
        {ai.data && !ai.data.model_configured && (
          <div className="chat-say-guide">
            <ModelGuide what="chat" />
          </div>
        )}
        {/* 输入框和那颗发送**是一个盒子**（`.chat-say-box`）：按钮吊在框内右下角，
            文字的右边和下边给它让出了位置（`.chat-say-box textarea` 的内边距）。
            它原来是框底下单独一行、写着「发送」两个字——作者要的是「放进框里、
            换成图标」。**框自己画边框，textarea 不画**，否则框里套一个框。 */}
        <div className="chat-say-box">
        <textarea
          aria-label={language === "zh" ? "输入消息" : "Message the writing assistant"}
          /* 占位符只说「这儿输入什么」。**键盘手势搬到框底下那行小字**（2026-09-07）：
             原来它写的是「开始写作…（Enter 发送，Shift + Enter 换行）」——那时上面
             空态的标题也是「开始写作」，同一句话摆两遍，后面还挂一份说明书，
             一个占位符干了三件事。 */
          placeholder={
            language === "zh" ? "输入消息" : "Message the writing assistant"
          }
          rows={3}
          value={said}
          /* 跑着的时候**还能说**（作者 2026-09-12：「像 codex 那样新的消息可以直接
             发出去」）——`send()` 会把那句话排进正在跑的这一轮（`sayWhileRunning`）。
             灰的只有一档：跑着的是**另一段**对话（下面那句 `chat-say-note` 说清了）。 */
          disabled={running && !runningHere}
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
              **这一颗有两面**（作者 2026-09-12：「停的按钮换到发送按钮那边」）：
              跑着而框里没字 = 「停」；框里有字 = 「发送」——跑着的时候发的是插话
              （`sayWhileRunning`），没跑就是新的一轮。**同一个动作只有一个入口**：
              跑动条上原来那颗「停」同日撤了。 */}
          {runningHere && !said.trim() ? (
            <button
              className="chat-send"
              aria-label={language === "zh" ? "停" : "Stop"}
              data-tip={language === "zh" ? "停" : "Stop"}
              disabled={stop.isPending}
              onClick={stopRunning}
            >
              <StopIcon />
            </button>
          ) : (
            <button
              className="chat-send"
              aria-label={language === "zh" ? "发送" : "Send"}
              data-tip={language === "zh" ? "发送" : "Send"}
              disabled={
                !said.trim() ||
                (running && !runningHere) ||
                sayMid.isPending ||
                create.isPending ||
                !projectId
              }
              onClick={send}
            >
              <SendIcon />
            </button>
          )}
        </div>
        {/* 键盘手势。**只在输入框有焦点时露面**（CSS 干的，见 `.chat-say-hint`）：
            它是给正要打字的人看的，不是常驻在屏幕上的装饰。位置写死了必须紧跟在
            `.chat-say-box` 后面——那条规则用的是相邻兄弟选择器。 */}
        <p className="chat-say-hint">
          {language === "zh"
            ? "Enter 发送，Shift + Enter 换行"
            : "Enter to send, Shift + Enter for a new line"}
        </p>
        {/* 输入框停用的理由必须写出来。**一轮跑好几分钟**，作者很可能在等的时候
            切去看另一段对话——那时这儿是一个没有任何解释的灰输入框，读起来像坏了。
            （一次只跑一轮是有意的：两轮同时飞，屏幕上就有两笔说不清是谁花的钱。） */}
        {running && !runningHere && (
          <p className="chat-say-note">
            {language === "zh"
              ? "另一段对话正在进行，结束后方可在此发送"
              : "Another conversation is in progress; sending here resumes when it finishes"}
          </p>
        )}
      </div>
    </section>
  );
}
