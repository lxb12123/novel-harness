import { useIgnoreNotification, useNotifications } from "../api/hooks";
import { refusalText } from "../chat";
import type { SystemNotification } from "../api/types";
import { useCoords } from "../store";
import { useOpenChapter } from "../autopilot";

// 系统通知（Task 10 / 021，前端 Task 14）：右栏那一格。
//
// 三种语义：总结可能与正文不一致（只告警，不撤销/不用不了/不改 Canon）、
// 后台失败、正文验证阻断（保留了旧结果）。每一条是「要作者知道、也许要点一下
// 确认」的东西，不是错误——它永远不会是因为引擎坏了。
//
// **坐标和动作全由后端给**：`jump`（TextAnchor / 章号）定位，`actions` 是可点
// 的动作。前端不从那行字里认「去哪儿改」。

const KIND_TITLE: Record<SystemNotification["kind"], string> = {
  summary_mismatch: "总结与正文可能对不上",
  background_failure: "后台有一件事没办成",
  validation_blocked: "这一章的检查需要留意",
};

function NotificationRow({
  item,
  onIgnored,
}: {
  item: SystemNotification;
  onIgnored: (id: string) => void;
}) {
  const { projectId } = useCoords();
  const openChapter = useOpenChapter();
  const ignore = useIgnoreNotification(projectId ?? "");
  const failure = ignore.error ? refusalText(ignore.error, "没能忽略这条通知。") : null;

  const go = () => {
    if (item.chapter_number !== null) openChapter(item.chapter_number);
  };

  return (
    <div className="notice-card">
      <div className="notice-head">
        <span className="lab">{KIND_TITLE[item.kind] ?? item.kind}</span>
        {item.chapter_number !== null && (
          <span className="row dim">第 {item.chapter_number} 章</span>
        )}
      </div>
      <div className="row">{item.title}</div>
      <div className="actions">
        {item.chapter_number !== null && (
          <button className="link" onClick={go}>
            去这一章 →
          </button>
        )}
        {item.actions.includes("ignore") && (
          <button
            className="link"
            disabled={ignore.isPending}
            onClick={() =>
              ignore.mutate(item.id, { onSuccess: () => onIgnored(item.id) })
            }
          >
            {ignore.isPending ? "正在忽略…" : "不再提醒这一条"}
          </button>
        )}
      </div>
      {failure && <div className="err-box">{failure}</div>}
    </div>
  );
}

/** 右栏「通知」：OPEN 列表。空 = 一切正常的一道绿（作者不需要的点不做）。 */
export function SystemNotifications() {
  const { projectId } = useCoords();
  const notifications = useNotifications(projectId);
  const items = notifications.data ?? [];

  return (
    <div className="mnr">
      <div className="lab">系统通知</div>
      {notifications.isLoading && <span className="empty">读取中…</span>}
      {!notifications.isLoading && items.length === 0 && (
        <span className="empty">现在没有需要你注意的。写就是了。</span>
      )}
      {items.map((item) => (
        <NotificationRow
          key={item.id}
          item={item}
          onIgnored={() => {
            // 本地立刻从当前打开的通知里拿掉（后端已落 IGNORED）。
            notifications.refetch();
          }}
        />
      ))}
    </div>
  );
}
