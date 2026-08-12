import { useEffect, useRef, useState } from "react";
import {
  useChatDetail,
  useChats,
  useCreateChat,
  useDrafts,
  useRunTurn,
  useStopChat,
} from "../api/hooks";
import type { ChatMessageView, TurnReceipt } from "../api/types";
import {
  elapsedText,
  emphasize,
  newRunId,
  receiptNotes,
  refusalText,
  stopFootnote,
  tailWindow,
  visibleMessages,
  SPEAKER_ZH,
} from "../chat";
import { useCoords } from "../store";
import { ChatSessions } from "./ChatSessions";
import { CompareLink, DraftCandidates } from "./DraftCandidates";

// 写作助手（模式二，[ADR 0019](docs/adr/0019-agent-loop-not-graph.md)）。
// 中栏对半分之后的右半边：左边正文、右边它。左栏书架和右栏面板一个像素不动。
//
// ── 三件这块屏幕必须自己做对的事 ──────────────────────────────────────────
//
// 1. **它不逐字往外冒，所以界面上不许装成在逐字往外冒。** 这一版 HTTP 不流式
//    （内部流式，打断才能中途生效——`api/chat.py` 的 3.4 余债），拿到的是一个跑完才
//    回来的响应。假一个打字机动画出来，作者会按着它的节奏判断「它是不是卡住了」，
//    而那个节奏是编的。诚实的形态只有两样：**一个还在跑的信号 + 一个真实的秒表**，
//    外加一句说清「跑完才会一次性出现」。
//
// 2. **查到了什么不上屏。** 后端出参已经是投影不是原文（工具返回和「只叫工具没说话」
//    的那几条根本没发出来），**前端也不许自己去别处把它们捞回来补上**——那里面是
//    `NodeRef` 的裸标识，一渲染就是屏幕上的研发术语。能说的只有一个数：查了几次。
//
// 3. **措辞的唯一出处在后端。** 停止原因是机器码（`reason`），一个字都不上屏；
//    说给作者的那一句是 `receipt.message`（`agent.loop.stop_wording()` 写好的）。
//    这里只补后端**说不出来**的那两句：作者按过停没有（`chat.ts::stopFootnote`），
//    以及这一轮裁掉了什么（`receiptNotes`）。

// 屏幕上只有两种说话人。那张表（连同「认不出的一律不上屏」这条）在 `chat.ts`：
// 它同时是措辞和白名单，**一份**（`visibleMessages` 的 docstring 写着为什么必须是一份）。

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

/** 还在跑的那一段屏幕。**秒表是真的，别的什么都不编。** */
function RunningStrip({ since, onStop, stopping }: {
  since: number;
  onStop: () => void;
  stopping: boolean;
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
        <span>
          它可能要来回查几次资料、想上几轮。
          <b>这一轮跑完才会一次性出现整段回话</b>
          ——所以这儿一直没动静不代表它停了。
        </span>
      </div>
      <button className="danger" disabled={stopping} onClick={onStop}>
        停
      </button>
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
  return (
    <div className="chat-receipt">
      <p className="chat-receipt-say">
        <Wording text={receipt.message} />
      </p>
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

  const [listOpen, setListOpen] = useState(false);
  const [said, setSaid] = useState("");
  /** 作者刚按下发送的那句话。跑完之前它在屏幕上占一格——**那不是假装**：
   *  后端做的第一件事就是把它落库（`run_chat` 的注释写着理由）。 */
  const [pendingSaid, setPendingSaid] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState(0);
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

  // 新消息到手就滚到底。jsdom 里 scrollHeight 恒为 0，这一句不会做任何事也不会炸。
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, running]);

  function runTurn(id: string, text: string) {
    stopLanded.current = false;
    setStopSaid(null);
    setReceipt(null);
    setPendingSaid(text || null);
    setRunFor(id);
    setStartedAt(Date.now());
    // **每一轮换一个新的**：上一轮那个还在的话，一次迟到的「停」就会认成这一轮。
    runId.current = newRunId();
    turn.mutate(
      { chatId: id, chapter, said: text, runId: runId.current },
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
  const canResume =
    !running &&
    !!chatId &&
    ((shownReceipt !== null && shownReceipt.reason !== "done") ||
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
        {window.shown.map((message) => (
          <Bubble key={message.seq} message={message} />
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
