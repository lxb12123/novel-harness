import { CanonEventCast } from "./CanonEventCast";
import { useCoords } from "../store";
import { useProjects } from "../api/hooks";

/** 右栏「事件」：这一本书里已经确认、写进正文的情节，在场/知情名单还能再改
 *  （ADR 0020 的「可改」——抽取干净就直接生效，作者第一次看见它时它已经生效了）。
 *
 *  **2026-08-31 之前这里还是「待确认」**：抽取产出的提案审阅（接受/驳回/改一改）
 *  和「从正文发现的情节」勾选确认都搬进了通知面板（`SystemNotifications.tsx`）——
 *  作者原话「待确认直接改成事件，然后将待确认搬到通知那边，这边仅仅有这个已经
 *  确认的全部事件」。这个组件从那时起只剩这一件事，没有再拆文件是因为剩下的
 *  就是 `<CanonEventCast>` 一行，另开一个文件只是换个名字包同一个东西。 */
export function ProposalReviewTab() {
  const { projectId } = useCoords();
  const projects = useProjects();
  const canonVersion = projects.data?.find((p) => p.id === projectId)?.canon_version ?? 0;
  return <CanonEventCast canonVersion={canonVersion} />;
}
