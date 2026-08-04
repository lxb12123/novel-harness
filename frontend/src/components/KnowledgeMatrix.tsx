import type { KnowledgeCell, KnowledgeMatrix, SceneConstraints } from "../api/types";

// 认知边界矩阵 —— 头牌（README 第一行）。三态必须长得完全不一样，这里就是那个区分点。
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

export function MatrixView({
  matrix,
  constraints,
}: {
  matrix?: KnowledgeMatrix;
  constraints?: SceneConstraints;
}) {
  if (!matrix) return <div className="empty">—</div>;
  const map = new Map<string, KnowledgeCell>();
  matrix.cells.forEach((c) => map.set(c.character_id + "|" + c.secret_id, c));

  const hasGrid = matrix.characters.length > 0 && matrix.secrets.length > 0;
  const mnr = constraints?.must_not_reveal ?? [];

  return (
    <div>
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
                {matrix.secrets.map((s) => (
                  <td className="cell" key={s.id}>
                    {cell(map.get(ch.id + "|" + s.id))}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="empty">当前没有可比较的人物和秘密。</div>
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
