import { useState } from "react";
import { useIgnoreNotification, useNotifications, useUndoTocSkip } from "../api/hooks";
import { messageForCode } from "../backendMessages";
import { refusalText } from "../chat";
import type { SystemNotification } from "../api/types";
import { useCoords } from "../store";
import { type Language, useLanguage } from "../language";
import { useOpenChapter } from "../chapterNavigation";

/** `title_code` 认得就整句渲染；认不出（还没配文案的码）或者 `null`
 *  （迁移 033 之前创建的旧通知，历史行不回填——同 `decision_log` 的既有纪律）
 *  就原样显示码本身，不编一句话糊弄过去（国际化第四批 Phase B）。**永远不返回
 *  空字符串**：一张没有正文的通知卡片比一句"码认不出来"更容易被当成 bug 忽略过去。 */
function noticeBody(item: SystemNotification, language: Language): string {
  if (!item.title_code) return "—";
  return messageForCode(item.title_code, language, item.title_params ?? {}) ?? item.title_code;
}

// 系统通知（Task 10 / 021，前端 Task 14）：右栏那一格。
//
// 六种语义：总结可能与正文不一致（只告警）、后台失败、正文验证阻断（保留了旧
// 结果）、保存后的语义核对（026，**只告警**）、这一章整理完但一件没留下（027）、
// 导入时跳过了目录页假章（032，带「撤销」）。每一条是「要作者知道、也许要点一下
// 确认」的东西，不是错误——它永远不会是因为引擎坏了。
//
// **坐标和动作全由后端给**：`jump`（TextAnchor / 章号）定位，`actions` 是可点
// 的动作。前端不从那行字里认「去哪儿改」。
//
// ⚠️ 这张表**必须罩住每一个 kind**（`Record<…["kind"], string>` 会在漏一个时让
// tsc 红）。漏了的话那一档会把 `text_advisory` 这种机器码原样摆到作者屏幕上——
// `screenGuard` 的 snake_case 那张网正是为这种兜底存在的。
const KIND_TITLE: Record<SystemNotification["kind"], { zh: string; en: string }> = {
  summary_mismatch: {
    zh: "总结与正文可能对不上",
    en: "The summary and the text may not match",
  },
  background_failure: {
    zh: "后台有一件事没办成",
    en: "Something didn't finish in the background",
  },
  validation_blocked: {
    zh: "这一章的检查需要留意",
    en: "This chapter's check needs attention",
  },
  // 措辞刻意和上一条分开：那一条**停掉了**这一章的自动整理，这一条没有。
  text_advisory: {
    zh: "这一段值得再看一眼",
    en: "This passage is worth another look",
  },
  // 措辞刻意不含「失败」二字：这一次没失败，只是一件都没留下（027）。
  extraction_yielded_nothing: {
    zh: "这一章什么都没整理出来",
    en: "Nothing came out of processing this chapter",
  },
  // 措辞刻意不说「切章器出错」——它没错，是这本书自带了一页跟正文长得一样的目录（032）。
  import_toc_skipped: {
    zh: "导入时跳过了几个空章",
    en: "A few empty chapters were skipped during import",
  },
};

function NotificationRow({
  item,
  onHandled,
}: {
  item: SystemNotification;
  onHandled: (id: string) => void;
}) {
  const { projectId, setHighlight } = useCoords();
  const openChapter = useOpenChapter();
  const ignore = useIgnoreNotification(projectId ?? "");
  const undoTocSkip = useUndoTocSkip(projectId ?? "");
  const language = useLanguage((s) => s.language);
  const failure =
    refusalText(
      ignore.error,
      language === "zh" ? "没能忽略这条通知。" : "Couldn't dismiss this notification.",
    ) ??
    refusalText(undoTocSkip.error, language === "zh" ? "没能撤销——" : "Couldn't undo —");

  const go = () => {
    if (item.chapter_number !== null) openChapter(item.chapter_number);
  };

  // 带锚的那些（规则报的问题、保存后核对出来的问题）能一路点到那一句上：换章 +
  // 设 highlight，编辑器按 quote 重寻并滚进视野（`anchor.ts::locate`，永不用 offset）。
  // **锚是后端给的**，前端不从标题里认位置。
  const goToQuote = () => {
    go();
    if (item.jump) setHighlight(item.jump);
  };

  return (
    <div className="notice-card">
      <div className="notice-head">
        <span className="lab">{KIND_TITLE[item.kind]?.[language] ?? item.kind}</span>
        {item.chapter_number !== null && (
          <span className="row dim">
            {language === "zh" ? `第 ${item.chapter_number} 章` : `Chapter ${item.chapter_number}`}
          </span>
        )}
      </div>
      <div className="row">{noticeBody(item, language)}</div>
      <div className="actions">
        {item.jump ? (
          <button className="link" onClick={goToQuote}>
            {language === "zh" ? "去这一句 →" : "Go to this sentence →"}
          </button>
        ) : (
          item.chapter_number !== null && (
            <button className="link" onClick={go}>
              {language === "zh" ? "去这一章 →" : "Go to this chapter →"}
            </button>
          )
        )}
        {item.actions.includes("ignore") && (
          <button
            className="link"
            disabled={ignore.isPending}
            onClick={() =>
              ignore.mutate(item.id, { onSuccess: () => onHandled(item.id) })
            }
          >
            {language === "zh"
              ? ignore.isPending ? "正在忽略…" : "不再提醒这一条"
              : ignore.isPending ? "Dismissing…" : "Don't remind me about this"}
          </button>
        )}
        {item.actions.includes("undo_toc_skip") && (
          <button
            className="link"
            disabled={undoTocSkip.isPending}
            onClick={() =>
              undoTocSkip.mutate(item.id, { onSuccess: () => onHandled(item.id) })
            }
          >
            {language === "zh"
              ? undoTocSkip.isPending ? "正在撤销…" : "撤销"
              : undoTocSkip.isPending ? "Undoing…" : "Undo"}
          </button>
        )}
      </div>
      {failure && <div className="err-box">{failure}</div>}
    </div>
  );
}

/** 同一档通知攒到这个数就折叠成一行。
 *
 *  ── 为什么需要这个数（2026-08-25）────────────────────────────────────
 *
 *  这一批之前，一本 158 章的老书只跑过 3 次抽取（只有保存过的章才排得上）。
 *  定期扫描从这一天起也管抽取了（`summary_schedule.ChapterSummaryState.needs_work`），
 *  于是**其余 155 章会第一次被整理**——其中每一章「一件都没留下」都落一条通知
 *  （引语对不上、称呼有歧义，都还会发生）。
 *
 *  一次冒出上百张卡片，每张都带「去这一章」和「不再提醒这一条」两颗按钮，
 *  等于把「要作者知道」变成「作者关掉这一格」。**这不是美化，是让它还读得下去。**
 *
 *  3 以下不折叠：一两条的时候摊开更好读，而且这样既有的形状一个字节没变。 */
const COLLAPSE_AT = 4;

/** 一档折叠起来的通知。**逐条的动作一个都没少**，只是默认收着。 */
function CollapsedKind({
  items,
  onHandled,
}: {
  items: SystemNotification[];
  onHandled: (id: string) => void;
}) {
  const language = useLanguage((s) => s.language);
  const [open, setOpen] = useState(false);
  const openChapter = useOpenChapter();
  // 章号按大小排，**不按通知落库的先后**：作者找的是「第几章」，不是「哪条先报的」。
  const chapters = [...new Set(items.map((i) => i.chapter_number))]
    .filter((n): n is number => n !== null)
    .sort((a, b) => a - b);

  return (
    <div className="notice-card">
      <div className="notice-head">
        <span className="lab">{KIND_TITLE[items[0].kind]?.[language] ?? items[0].kind}</span>
        <span className="row dim">
          {language === "zh"
            ? `${items.length} 条`
            : `${items.length} item${items.length === 1 ? "" : "s"}`}
        </span>
      </div>
      {chapters.length > 0 && (
        // 章号摆出来而不是只报一个总数：「12 条」不告诉作者该去看哪儿，
        // 「第 4、7、9… 章」他扫一眼就知道是不是同一段书。
        <div className="row dim">
          {language === "zh" ? (
            <>
              第 {chapters.slice(0, 12).join("、")} 章
              {chapters.length > 12 && ` 等 ${chapters.length} 章`}
            </>
          ) : (
            <>
              Chapter{chapters.length > 1 ? "s" : ""} {chapters.slice(0, 12).join(", ")}
              {chapters.length > 12 && ` among ${chapters.length} chapters total`}
            </>
          )}
        </div>
      )}
      <div className="actions">
        <button className="link" onClick={() => setOpen(!open)}>
          {language === "zh"
            ? open ? "收起" : "逐条看"
            : open ? "Collapse" : "See each one"}
        </button>
        {chapters.length > 0 && (
          <button className="link" onClick={() => openChapter(chapters[0])}>
            {language === "zh" ? `去第 ${chapters[0]} 章 →` : `Go to chapter ${chapters[0]} →`}
          </button>
        )}
      </div>
      {open &&
        items.map((item) => (
          <NotificationRow key={item.id} item={item} onHandled={onHandled} />
        ))}
    </div>
  );
}

/** 右栏「通知」：OPEN 列表。空 = 一切正常的一道绿（作者不需要的点不做）。 */
export function SystemNotifications() {
  const language = useLanguage((s) => s.language);
  const { projectId } = useCoords();
  const notifications = useNotifications(projectId);
  const items = notifications.data ?? [];

  // 按档分组，**组的先后 = 每一档第一条出现的先后**：后端已经排好序了，
  // 这一层不许自己发明第二个序。
  const groups: SystemNotification[][] = [];
  const index = new Map<string, number>();
  for (const item of items) {
    const at = index.get(item.kind);
    if (at === undefined) {
      index.set(item.kind, groups.length);
      groups.push([item]);
    } else {
      groups[at].push(item);
    }
  }

  const onHandled = () => {
    // 本地立刻从当前打开的通知里拿掉（后端已落 IGNORED 或 RESOLVED）。
    notifications.refetch();
  };

  return (
    <div className="mnr">
      <div className="lab">{language === "zh" ? "系统通知" : "Notifications"}</div>
      {notifications.isLoading && (
        <span className="empty">{language === "zh" ? "读取中…" : "Loading…"}</span>
      )}
      {!notifications.isLoading && items.length === 0 && (
        <span className="empty">
          {language === "zh"
            ? "现在没有需要你注意的。写就是了。"
            : "Nothing needs your attention right now. Just write."}
        </span>
      )}
      {groups.map((group) =>
        group.length >= COLLAPSE_AT ? (
          <CollapsedKind key={group[0].kind} items={group} onHandled={onHandled} />
        ) : (
          group.map((item) => (
            <NotificationRow key={item.id} item={item} onHandled={onHandled} />
          ))
        ),
      )}
    </div>
  );
}
