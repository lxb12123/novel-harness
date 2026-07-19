import { useState } from "react";
import { useScenes, useWriteScene } from "../api/hooks";
import { useCoords } from "../store";
import { ApiError } from "../api/client";
import type { Scene } from "../api/types";

// 场景块条 —— 把中栏正文和右栏面板接起来的那一环。
// 点一场 = 用它的 cast 刷新右栏（矩阵/约束/state 的「在场是谁」不再靠顶栏手敲）。
// 改 = 无损回写 `<!-- nh: cast=… loc=… goal=… -->`（只动它拥有的字符，ADR 0007）。
// 注意：只能编辑**已存在**的场景块指令行——`## 场景 N` 标题本身要在正文里先写出来
// （write_scene_directive 要求那个场景号存在），那是编辑正文的事。
export function SceneBar() {
  const { projectId, chapter, cast, setCast } = useCoords();
  const { data: scenes } = useScenes(projectId, chapter);
  const write = useWriteScene(projectId ?? "", chapter);
  const [editing, setEditing] = useState<number | null>(null);

  if (!scenes || scenes.length === 0)
    return (
      <div className="scenebar empty">
        这一章没有场景块。在正文里写 <code>## 场景 1</code> +{" "}
        <code>&lt;!-- nh: cast=萧决,李管家 loc=北荒 --&gt;</code>，存盘后这里就能点选/编辑。
      </div>
    );

  return (
    <div className="scenebar">
      {scenes.map((s) =>
        editing === s.number ? (
          <SceneEditor
            key={s.number}
            scene={s}
            pending={write.isPending}
            error={write.error instanceof ApiError ? write.error : null}
            onSave={(body) =>
              write.mutate(body, {
                onSuccess: (updated) => {
                  setEditing(null);
                  // 存完顺手把面板切到这一场的在场
                  const me = updated.find((u) => u.number === s.number);
                  if (me) setCast(me.cast.join(","));
                },
              })
            }
            onCancel={() => setEditing(null)}
          />
        ) : (
          <button
            key={s.number}
            className={"scene-pill" + (cast === s.cast.join(",") && cast ? " on" : "")}
            title="用这一场的在场刷新右栏面板"
            onClick={() => setCast(s.cast.join(","))}
            onDoubleClick={() => setEditing(s.number)}
          >
            场景 {s.number}
            {s.cast.length > 0 && <span className="pc"> · {s.cast.join(",")}</span>}
            {s.loc && <span className="pl"> @{s.loc}</span>}
            <span className="pe" onClick={(e) => (e.stopPropagation(), setEditing(s.number))}>
              改
            </span>
          </button>
        ),
      )}
    </div>
  );
}

function SceneEditor({
  scene,
  pending,
  error,
  onSave,
  onCancel,
}: {
  scene: Scene;
  pending: boolean;
  error: ApiError | null;
  onSave: (b: { number: number; cast: string[]; loc: string | null; goal: string | null }) => void;
  onCancel: () => void;
}) {
  const [cast, setCast] = useState(scene.cast.join(","));
  const [loc, setLoc] = useState(scene.loc ?? "");
  const [goal, setGoal] = useState(scene.goal ?? "");
  return (
    <div className="scene-edit">
      <span className="lab">场景 {scene.number}</span>
      <input placeholder="在场：萧决,李管家" value={cast} onChange={(e) => setCast(e.target.value)} />
      <input placeholder="地点" value={loc} onChange={(e) => setLoc(e.target.value)} />
      <input placeholder="本场目标" value={goal} onChange={(e) => setGoal(e.target.value)} />
      <button
        disabled={pending}
        onClick={() =>
          onSave({
            number: scene.number,
            cast: cast
              .split(/[,，、]/)
              .map((s) => s.trim())
              .filter(Boolean),
            loc: loc.trim() || null,
            goal: goal.trim() || null,
          })
        }
      >
        {pending ? "写入…" : "保存"}
      </button>
      <button onClick={onCancel}>取消</button>
      {error && <span className="err">{error.message}</span>}
    </div>
  );
}
