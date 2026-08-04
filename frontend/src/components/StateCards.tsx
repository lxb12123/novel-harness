import type { StateSnapshot } from "../api/types";

/** 右栏「当前状态」：在场角色卡。纯展示，数据来自 /state 的收窄出参。 */
export function StateCards({ states }: { states?: StateSnapshot[] }) {
  if (!states || states.length === 0) return <div className="empty">无在场角色。</div>;
  return (
    <div>
      {states.map((s) => (
        <div className="statecard" key={s.node.id}>
          <div className="nm">
            {s.node.name}
            {s.is_dead && <span className="dead"> · 已亡</span>}
          </div>
          <div className="row">所在地：{s.location ? s.location.name : "未声明"}</div>
          {s.states.map((v, i) => (
            <div className="row" key={i}>
              {"name" in v.dim ? v.dim.name : ""}：{v.value || ""}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
