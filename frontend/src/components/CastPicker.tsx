import type { EventView, NodeRef } from "../api/types";

// 「谁在场、谁知道了」这两维名单的**唯一一份画法**。
//
// 它被两个地方用着，而那两个地方在产品上是同一件事的前后两步：
//
// | 谁 | 改的是 | 走哪条路由 |
// |---|---|---|
// | `ProposalReviewTab` 的「改一改再收下」 | 还没生效的那条（提案） | `POST …/proposals/{id}/edit` |
// | `CanonEventCast` 的「保存名单」 | **已经生效**的那条 | `POST …/canon/events/{id}/cast` |
//
// 抽出来不是为了省几行，是为了**措辞和语义只有一份**：两处各写一遍「知道这件事的人」，
// 迟早有一处会改成别的说法；两处各写一遍「勾上的就是改完之后的名单」，迟早有一处会
// 变成「＋加一个人」——而那两种控件对**绝对集合**语义的暗示是相反的
// （再点一次保存会不会把同一个人加两遍）。
//
// ── 名单是绝对集合，控件必须把这件事说出来 ────────────────────────────────
//
// 两条路由收的都是「改完之后是这些人」，`null` = 这一维不动。所以这里用勾选框而不是
// 「＋加一个人」：勾上的那些就是最终名单，重发一次是空操作。

/** 两维名单，措辞是作者的。**「知情」这一维是抽取里唯一靠推断得来的**（谁在场是文本里
 *  写着的，谁**因此知道了**是猜的），所以它也是最需要改的一维（ADR 0020 的代价那节）。 */
export const DIMENSION = {
  knowers: "知道这件事的人",
  participants: "在场的人",
} as const;

export type Dimension = keyof typeof DIMENSION;

/** 画的顺序：先「在场」后「知道」。**和读的顺序一致**（卡片上也是先在场后知情）。 */
export const DIMENSIONS: readonly Dimension[] = ["participants", "knowers"];

export const idsOf = (refs: NodeRef[]): string[] => refs.map((r) => r.id);

/** 两份名单是不是同一批人（顺序无关 —— 它是集合不是列表）。 */
export const same = (a: string[], b: string[]): boolean =>
  a.length === b.length && a.every((id) => b.includes(id));

/** 候选人 = 花名册里的人物 ∪ 这条情节上现有的名单。
 *
 *  并集那一半不是防御性编程：名单里出现一个花名册没有的人时，只按花名册画会让他
 *  **在界面上凭空消失**，而作者一按保存就把他从这条情节上删掉了——一次他没打算做的删除。
 *  （后台整理造出的新人物会在花名册那条缓存里缺席一拍，那一拍是常态不是边角。） */
export function candidates(roster: NodeRef[], view: EventView): NodeRef[] {
  const seen = new Map<string, NodeRef>();
  for (const n of roster) if (n.label === "Character") seen.set(n.id, n);
  for (const n of [...view.participants, ...view.knowers]) if (!seen.has(n.id)) seen.set(n.id, n);
  return [...seen.values()];
}

/** 两组勾选框 + 那句「绝对集合」的说明。**纯展示**：不发请求、不认路由。 */
export function CastPicker({
  people,
  picked,
  onToggle,
}: {
  people: NodeRef[];
  picked: Record<Dimension, string[]>;
  onToggle: (dim: Dimension, id: string) => void;
}) {
  return (
    <>
      {DIMENSIONS.map((dim) => (
        <fieldset className="cast-dim" key={dim}>
          <legend>{DIMENSION[dim]}</legend>
          {people.length === 0 ? (
            <span className="empty">花名册里还没有人物 —— 先去「花名册」那一格加人。</span>
          ) : (
            people.map((p) => (
              <label className="cast-pick" key={p.id}>
                <input
                  type="checkbox"
                  checked={picked[dim].includes(p.id)}
                  onChange={() => onToggle(dim, p.id)}
                />
                <span>{p.name}</span>
              </label>
            ))
          )}
        </fieldset>
      ))}
      <div className="row dim">
        勾上的就是改完之后的名单 —— 再保存一次不会把同一个人加两遍。
      </div>
    </>
  );
}
