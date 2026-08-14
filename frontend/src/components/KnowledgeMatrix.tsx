import { useState } from "react";
import { useAddKnowledge, useCorrectKnowledge, useRefreshPanels } from "../api/hooks";
import { readCorrectionError } from "../correctionError";
import type {
  KnowledgeCell,
  KnowledgeEdgeType,
  KnowledgeMatrix,
  NodeRef,
  SceneConstraints,
} from "../api/types";
import { useCoords } from "../store";

// 认知边界矩阵 —— 头牌（README 第一行）。三态必须长得完全不一样，这里就是那个区分点。
//
// 2026-08-11 起这张表**可以动手改**：点一格 → 把「知道」改成「以为」，或者反过来
// （`POST /canon/knowledge`，业务在 `corrections.py`）。这是 ADR 0020 押的那条退路
// ——抽取结果直接进 CANON，作者第一次看见它时它已经生效了，所以退路必须落在 CANON 上。
//
// 三条纪律：
//
// 1. **「不知道」的格子也动得了，但走的是另一条路。** 改正层（`POST /canon/knowledge`）
//    改的是**已经存在的那条边**，空格子它一律 404；补一条走
//    `POST /chapters/{n}/canon/knowledge`（`corrections.add_knowledge`）。
//    **两条路不合并**：一个能力两个入口，作者学会的会是错的那一个；而且「补」比
//    「改」多一件事要交代 —— 它背后没有引语，生效章只能是**作者正看着的这一章**。
//    那个数由 `matrix.chapter` 带来（矩阵本来就是 AS OF 那一章渲染的），进的是 URL，
//    **不是请求体、更不是一个输入框**（约束 10：作者永不填章号）。
//    这一格已经有内容时后端答 409，让作者先去看一眼 —— 这一条也不许在前端悄悄兼做「改」。
// 2. **版本跟着数据走。** `expected_canon_version` 取自 `matrix.version.canon_version`
//    ——作者看到的那一版。撞上 409 就重新取一次**让他再看一眼**，绝不静默重试。
// 3. **屏幕上不出现引擎的词。** 这一格上的两种事实在界面里叫「知道」和「以为」，
//    后端的拒绝也过一遍同一张词表（`correctionError.ts`）。

/** 这一格上的两种事实，作者的说法。**这张表是界面措辞的唯一出处**（矩阵、编辑器、
 *  拒绝提示共用），别在别处再写一份「KNOWS 就是知道」。 */
const STATE_ZH: Record<KnowledgeEdgeType, string> = {
  KNOWS: "知道",
  BELIEVES: "以为",
};

const EDITABLE = new Set(["KNOWS", "BELIEVES"]);

function cell(c: KnowledgeCell | undefined) {
  if (!c) return <span className="u">·</span>;
  if (c.state === "KNOWS")
    return (
      <>
        <span className="k">✓ 知道</span>
        <small>第 {c.since_chapter} 章起</small>
      </>
    );
  if (c.state === "BELIEVES")
    return (
      <>
        <span className="b">⚠ {c.believed_value || "错误认知"}</span>
        <small>第 {c.since_chapter} 章起</small>
      </>
    );
  return <span className="u">✗ 不知道</span>;
}

/** 改这一格：从「知道」换成「以为（内容是 …）」，或者反过来。
 *
 *  能按下去之前就不让作者犯的三件事（后端也会拒，但那时他已经点过一次了）：
 *  改成它现在已经是的那一种、「以为」不填内容、「知道」还带着内容。 */
function CellEditor({
  character,
  secret,
  current,
  canonVersion,
  onClose,
  onStale,
}: {
  character: NodeRef;
  secret: NodeRef;
  current: KnowledgeCell;
  canonVersion: number;
  onClose: () => void;
  onStale: () => void;
}) {
  const { projectId } = useCoords();
  const correct = useCorrectKnowledge(projectId!);
  // 「看看最新的」由**编辑器自己**重取，不指望挂载它的那一页传个回调进来：
  // 这张表挂在两个地方，而一个可选 prop 决定这条退路死不死的话，漏掉的那一块屏幕上
  // 作者会一直撞同一个 409（见 `useRefreshPanels` 的说明）。
  const refresh = useRefreshPanels(projectId);
  const from = current.state as KnowledgeEdgeType;
  // 只有另一种可选：改成它已经是的那个类型后端会拒（422），所以这儿根本不给这个选项。
  const to: KnowledgeEdgeType = from === "KNOWS" ? "BELIEVES" : "KNOWS";
  // 换一格就重来（调用方给了 `key`，这个组件会整个重挂）：上一格填了一半的
  // 「他以为的是什么」跟着跑到下一格去，是会真的写错一条 CANON 事实的那种脏。
  const [believed, setBelieved] = useState("");
  const failure = correct.error ? readCorrectionError(correct.error) : null;

  const needsValue = to === "BELIEVES";
  const ready = !needsValue || believed.trim().length > 0;

  const submit = () => {
    if (!projectId || !ready) return;
    correct.mutate(
      {
        character_id: character.id,
        secret_id: secret.id,
        to_type: to,
        // 「知道」这一边**一个字都不许带**：他知道的就是真的那一版（后端也会拒）。
        ...(needsValue ? { believed_value: believed.trim() } : {}),
        expected_canon_version: canonVersion,
      },
      { onSuccess: onClose },
    );
  };

  return (
    <div className="cell-editor">
      <div className="lab">
        {character.name} 对「{secret.name}」
      </div>
      <div className="row dim">
        现在是「{STATE_ZH[from]}」
        {current.since_chapter !== null && <> · 第 {current.since_chapter} 章起</>}
        {/* 章号是**系统从引语算出来的**，改这条事实不会动它——说出来，作者才不会去找输入框。 */}
        <span className="dim"> · 改这一处不会动它是从第几章开始的</span>
      </div>

      {/* 「以为」独有的那一格内容。改成「知道」时这里什么都不画——他知道的就是真的
          那一版，多一个输入框就是邀请作者填一个后端必然拒掉的东西。 */}
      {needsValue && (
        <label className="cell-believed">
          <span>他以为的是</span>
          <input
            value={believed}
            placeholder="例如：以为那只是个传闻"
            onChange={(e) => setBelieved(e.target.value)}
          />
        </label>
      )}

      {failure && (
        <div className="err-box">
          <div>{failure.message}</div>
          {failure.kind === "stale" && (
            <button
              className="link"
              onClick={() => {
                refresh();
                // 调用方仍然收得到这一声（右栏用它顺手 refetch）——同一个 queryKey，
                // react-query 会合成一次在途请求，不会打两遍。
                onStale();
              }}
            >
              看看最新的
            </button>
          )}
        </div>
      )}

      <div className="actions">
        <button disabled={!ready || correct.isPending} onClick={submit}>
          {correct.isPending ? "保存中…" : `改成「${STATE_ZH[to]}」`}
        </button>
        <button className="link" onClick={onClose}>
          取消
        </button>
      </div>
      {needsValue && !ready && (
        <div className="row dim">
          写一句他以为的版本再保存 —— 空着的话，这一格上只会显示一片空白。
        </div>
      )}
    </div>
  );
}

/** 补这一格：这一格现在是「不知道」，作者要手工添一条「他知道」或「他以为（内容是 …）」。
 *
 *  和上面那个编辑器的两处不同，都不是随手定的：
 *
 *  1. **两种都给**（编辑器只给「另一种」）。这一格上还没有事实，没有哪一种是多余的。
 *  2. **说清楚从第几章起算，同时不给他填的机会**。那个数是他正看着的这一章，
 *     由这张表自己带来（`matrix.chapter`）——说出来他才不会去找输入框。 */
function CellAdder({
  character,
  secret,
  chapter,
  canonVersion,
  onClose,
  onStale,
}: {
  character: NodeRef;
  secret: NodeRef;
  /** 这张表画的是第几章。**这就是这条边的生效章**，不是作者的输入。 */
  chapter: number;
  canonVersion: number;
  onClose: () => void;
  onStale: () => void;
}) {
  const { projectId } = useCoords();
  const add = useAddKnowledge(projectId!, chapter);
  // 重取归编辑器自己管（理由同 `CellEditor`：这张表挂在两个地方）。
  const refresh = useRefreshPanels(projectId);
  // 换一格就重来（调用方给了 `key`）：上一格选了一半的「以为 + 内容」跟着跑到下一格，
  // 写出来的是一条**凭空多出来**的 CANON 事实，比改错一条更难发现。
  const [type, setType] = useState<KnowledgeEdgeType | null>(null);
  const [believed, setBelieved] = useState("");
  const failure = add.error ? readCorrectionError(add.error) : null;

  const needsValue = type === "BELIEVES";
  const ready = type !== null && (!needsValue || believed.trim().length > 0);

  const submit = () => {
    if (!projectId || !ready || type === null) return;
    add.mutate(
      {
        character_id: character.id,
        secret_id: secret.id,
        type,
        // 「知道」这一边**一个字都不许带**：他知道的就是真的那一版（后端也会拒）。
        ...(needsValue ? { believed_value: believed.trim() } : {}),
        expected_canon_version: canonVersion,
      },
      { onSuccess: onClose },
    );
  };

  return (
    <div className="cell-editor">
      <div className="lab">
        {character.name} 对「{secret.name}」
      </div>
      <div className="row dim">
        现在是「不知道」
        {/* 章号是**这块屏幕正开着的那一章**，不是作者敲的——说出来，他才不会去找输入框。 */}
        <span className="dim"> · 补上的这一条从第 {chapter} 章起算</span>
      </div>

      <div className="row">
        {(["KNOWS", "BELIEVES"] as const).map((k) => (
          <button
            key={k}
            className={type === k ? "" : "link"}
            aria-pressed={type === k}
            onClick={() => setType(k)}
          >
            他{STATE_ZH[k]}
          </button>
        ))}
      </div>

      {/* 「以为」独有的那一格内容（同 `CellEditor`）。 */}
      {needsValue && (
        <label className="cell-believed">
          <span>他以为的是</span>
          <input
            value={believed}
            placeholder="例如：以为那只是个传闻"
            onChange={(e) => setBelieved(e.target.value)}
          />
        </label>
      )}

      {failure && (
        <div className="err-box">
          <div>{failure.message}</div>
          {failure.kind === "stale" && (
            <button
              className="link"
              onClick={() => {
                refresh();
                onStale();
              }}
            >
              看看最新的
            </button>
          )}
        </div>
      )}

      <div className="actions">
        <button disabled={!ready || add.isPending} onClick={submit}>
          {add.isPending ? "保存中…" : type === null ? "补上这一条" : `补上「${STATE_ZH[type]}」`}
        </button>
        <button className="link" onClick={onClose}>
          取消
        </button>
      </div>
      {type === null && <div className="row dim">先选一种：他知道了，还是他以为的是别的。</div>}
      {needsValue && !ready && (
        <div className="row dim">
          写一句他以为的版本再保存 —— 空着的话，这一格上只会显示一片空白。
        </div>
      )}
    </div>
  );
}

export function MatrixView({
  matrix,
  constraints,
  onRefresh,
}: {
  matrix?: KnowledgeMatrix;
  constraints?: SceneConstraints;
  /** 撞上「别处刚改过」时重新取一次这张表。不给也能用（作者切一下章同样会重取）。 */
  onRefresh?: () => void;
}) {
  // 从活动记录跳过来时要高亮的那一格（两个 id 都是后端 `jump` 给的坐标）。
  // 没人跳过来时它是 null，这张表和以前逐字节一样。
  const focus = useCoords((s) => s.focusCell);
  const [editing, setEditing] = useState<{ character_id: string; secret_id: string } | null>(null);
  if (!matrix) return <div className="empty">—</div>;
  const map = new Map<string, KnowledgeCell>();
  matrix.cells.forEach((c) => map.set(c.character_id + "|" + c.secret_id, c));

  const hasGrid = matrix.characters.length > 0 && matrix.secrets.length > 0;
  const mnr = constraints?.must_not_reveal ?? [];

  const open = editing && map.get(editing.character_id + "|" + editing.secret_id);
  const editingCharacter = matrix.characters.find((c) => c.id === editing?.character_id);
  const editingSecret = matrix.secrets.find((s) => s.id === editing?.secret_id);

  // 跳过来了，但这一章的表上**没有那一格**。
  //
  // 这不是异常，是两条 ADR 交叉出来的常态：`valid_from` 只由引语决定（ADR 0006），
  // 而这张表的行由本章正文推（ADR 0018）——作者用一句满是代词的话声明认知，跳转坐标
  // 就会指向一章「他一次都没被点名」的正文，于是这一行根本不在表上。
  //
  // 那时高亮和编辑入口一起落空，而屏幕上是一张**看起来完全正常的表**。§10 约束 8：
  // 静默的零和真的零不许长得一样。已确认情节那一侧早就把这句话说出来了
  //（「没有在这一章找到刚才那条情节」），这一侧不该是另一套规矩。
  //
  // **2026-08-11：常见的那一半补上了，这句话仍然要留着。** 后端现在在 `jump.cast` 里
  // 多给一个坐标（那个人的称呼），前端原样进 `?include=`（**不是 `?cast=`**：那是过滤，
  // 用它会把推导出来的其余几行一起挤掉，而右栏的写作提醒吃的是同一份在场——
  // 少一个人 = 少一批禁令，ADR 0018 §3），于是那一行被**加**回表上，别的行一行不少。
  // 但它**只在称呼能唯一指回那个人时才有值**——歧义的时候后端什么都不给
  //（绝不替作者挑，ADR 0004），那时照旧落到这里；那一章一个人都没数出来时也一样
  //（`_effective_cast` 不许坐标把「不知道谁在场 ⇒ 全禁」撬开）。
  // **前端仍然不许自己补**：拿屏幕上的人名去反查再塞进去就是「从标题反推」，
  // 而「师兄」在一章里可能指 8 个人，猜错的产物是作者改了另一个人的那一格。
  const lost = !!focus && !map.has(focus.character_id + "|" + focus.secret_id);

  return (
    <div>
      {lost && (
        <div className="warn">
          没有在这一章找到刚才那一格 —— 这张表画的是本章正文里出现过的人。
          那条事实还在，到他出场的那一章再点一次就看得到。
        </div>
      )}
      {hasGrid ? (
        <table className="mx">
          <thead>
            <tr>
              <th />
              {matrix.secrets.map((s) => (
                <th key={s.id}>{s.name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.characters.map((ch) => (
              <tr key={ch.id}>
                <th>{ch.name}</th>
                {matrix.secrets.map((s) => {
                  const focused = focus?.character_id === ch.id && focus?.secret_id === s.id;
                  const here = map.get(ch.id + "|" + s.id);
                  // 「不知道」那一格上没有可**改**的事实（改正层只在两种已存在的边之间
                  // 换），但 2026-08-14 起它有可**补**的：两条路由不同，措辞也不同，
                  // 所以按钮的名字必须分得开（「改「…」」 / 「补上「…」」）——
                  // 光看一格是不是按钮，作者分不出自己按下去会发生什么。
                  const editable = !!here && EDITABLE.has(here.state);
                  const addable = !!here && !editable;
                  const isOpen =
                    editing?.character_id === ch.id && editing?.secret_id === s.id;
                  return (
                    <td
                      className={
                        "cell" +
                        (focused ? " focus" : "") +
                        (editable || addable ? " editable" : "") +
                        (isOpen ? " editing" : "")
                      }
                      key={s.id}
                      // 跳过来的那一格：给它一个能被读屏和测试认出来的名字，
                      // 光靠一圈边框的话，读屏的人根本不知道自己被送到了哪儿。
                      aria-label={focused ? `${ch.name} 对 ${s.name}（刚跳转到这一格）` : undefined}
                    >
                      {editable || addable ? (
                        <button
                          className="cell-open"
                          aria-label={
                            editable
                              ? `改「${ch.name} 对 ${s.name}」`
                              : `补上「${ch.name} 对 ${s.name}」`
                          }
                          onClick={() =>
                            setEditing(
                              isOpen ? null : { character_id: ch.id, secret_id: s.id },
                            )
                          }
                        >
                          {cell(here)}
                        </button>
                      ) : (
                        cell(here)
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="empty">当前没有可比较的人物和秘密。</div>
      )}

      {open && editingCharacter && editingSecret && EDITABLE.has(open.state) && (
        <CellEditor
          key={editingCharacter.id + "|" + editingSecret.id}
          character={editingCharacter}
          secret={editingSecret}
          current={open}
          canonVersion={matrix.version.canon_version}
          onClose={() => setEditing(null)}
          onStale={() => {
            setEditing(null);
            onRefresh?.();
          }}
        />
      )}

      {open && editingCharacter && editingSecret && !EDITABLE.has(open.state) && (
        <CellAdder
          key={editingCharacter.id + "|" + editingSecret.id}
          character={editingCharacter}
          secret={editingSecret}
          // ★ 章号从**数据**来（这张表画的就是这一章），不从作者来（约束 10）。
          chapter={matrix.chapter}
          canonVersion={matrix.version.canon_version}
          onClose={() => setEditing(null)}
          onStale={() => {
            setEditing(null);
            onRefresh?.();
          }}
        />
      )}

      {matrix.unresolved_cast.length > 0 && (
        <div className="warn">
          这些称呼未在花名册中找到：{matrix.unresolved_cast.join("、")}。请检查名称或补充称呼。
        </div>
      )}

      <div className="mnr">
        <div className="lab">本场不能说破</div>
        {mnr.length ? (
          mnr.map((n) => (
            <span className="tag" key={n.id}>
              {n.name}
            </span>
          ))
        ) : (
          <span className="empty">（无）</span>
        )}
      </div>
    </div>
  );
}
