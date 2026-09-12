import { useState } from "react";
import {
  useAddCharacterAlias,
  useCharacterProfile,
  useRetractAlias,
} from "../api/hooks";
import type { StoredAlias } from "../api/types";
import { useLanguage } from "../language";
import { useCoords } from "../store";

// 人物的别名（Task 15 / §4.5）：一排 chip + 一个添加框。
//
// **本名不在这儿再印一遍**（2026-09-04 删，作者原话：「这上边已经有贾环了，为什么在
// 正文还要整一个」）——这张卡是长在他那一行**下面**的，那一行左边就写着他的名字，
// 卡片再印一份等于同一个字在同一屏上写两次。本名也不在这里改：它由 `upsert_node`
// 独占（`canonical` 盾），改本名走那一行的「⋯ → 改名」。
//
// chip：输入框回车、或点「＋」加一个；点 chip 上的 × 撤回。机器自动认出来的
// （`source=extractor`）标一颗「自动」小标记，作者改它 = 新 author 派生行
// （`derived_from_alias_id` 指回原来那条机器行），UI 上看起来就是改成新字。
//
// **一个 URL 里看见的别名字段，永远只有「一排 chip」这一个形状。** 设置里那个多值
// 文本框（逗号一次塞一堆）是 RosterDrawer 建人物时的入口，两码事。
//
// ── 2026-09-04 的三处措辞/间距，都是作者当场点的 ────────────────────────────
//
// 1. **标签叫「别名」**，不叫「称呼」。**全仓口径同日统一了**：抽屉里那个
//    「哪一类」三选（别名 / 小名 / 称号）整个删掉，作者裁定三者相等——「齐天大圣」
//    既是别名也是称号，这一格存在的意义只有「让系统认出这个说法指的也是这个人」。
//    引擎本来就是这么看的（`AliasKind` 只有 `CANONICAL` 有行为差别），见
//    `RosterDrawer.tsx` 顶上那段 ⚠️。
// 2. **「回车保存」写进输入框里当提示**（原来是下面一行常驻小字）。我提过
//    placeholder 一打字就消失，作者仍然要这个形状——按他定的做。
// 3. **没有 chip 时整个 chip 容器不渲染**：空的 `.alias-chips` 照样吃 flex 的
//    外边距，于是标签和输入框之间空出一截（作者：「这个称呼好像离下面那个输入框
//    隔的有点远」）。
//
// ⚠️ **这一格右上角原来有一颗 ✕（关掉卡片），2026-09-04 撤了**：那一行右端已经有
// 一颗「收起」（`RosterTab.tsx` 里展开态换掉「⋯」的那颗），同一件事两个入口隔着
// 30px 上下站着（作者：「这个右边的打叉把它去掉，因为那上面已经刚刚添加了一个收起
// 的那个符号」）。`onClose` 这个 prop 跟着一起删了——**没人传的参数就是负债**，
// 真要再开一个收起入口，从那颗按钮那边接。
//
// 基础资料（性别/性格/出身/备注）**2026-09-04 挪进了「状态」那一格**——作者裁定
// 「基础是状态里面的一个子集」，见 `CharacterStatus.tsx`。

export function CharacterBasicInfo({ characterId }: { characterId: string }) {
  const { projectId } = useCoords();
  const language = useLanguage((s) => s.language);
  const profile = useCharacterProfile(projectId, characterId);
  const add = useAddCharacterAlias(projectId ?? "", characterId);
  const retract = useRetractAlias(projectId ?? "");
  const [surface, setSurface] = useState("");

  const data = profile.data;
  const aliases = data?.aliases ?? [];

  const addAlias = () => {
    const text = surface.trim();
    if (!text || data?.canon_version === undefined) return;
    add.mutate(
      { surface: text, expected_canon_version: data.canon_version },
      {
        onSuccess: () => setSurface(""),
        onError: () => setSurface(""),
      },
    );
  };

  return (
    <div className="character-basic">
      <div className="grp">
        <div className="lab">{language === "zh" ? "别名" : "Aliases"}</div>
        {aliases.length > 0 && (
          <div className="alias-chips">
            {aliases.map((alias) => (
              <AliasChip
                key={alias.id}
                alias={alias}
                onRetract={(id) => retract.mutate(id, {})}
              />
            ))}
          </div>
        )}
        <div className="alias-add">
          <input
            value={surface}
            onChange={(e) => setSurface(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") addAlias();
            }}
            placeholder={
              language === "zh" ? "添加别名，回车保存…" : "Add an alias, press Enter to save…"
            }
          />
          <button className="link" onClick={addAlias} disabled={add.isPending || !surface.trim()}>
            ＋
          </button>
        </div>
      </div>

      {profile.isLoading && (
        <span className="dim">{language === "zh" ? "读取中…" : "Loading…"}</span>
      )}
    </div>
  );
}

function AliasChip({
  alias,
  onRetract,
}: {
  alias: StoredAlias;
  onRetract: (id: string) => void;
}) {
  const language = useLanguage((s) => s.language);
  const auto = language === "zh" ? "自动识别" : "Auto-recognized";
  return (
    <span
      className="alias-chip"
      title={alias.source === "extractor" ? auto : language === "zh" ? "作者确认" : "Confirmed by author"}
    >
      {alias.surface}
      {alias.source === "extractor" && (
        <i className="auto-mark" aria-label={auto}>
          {language === "zh" ? "自动" : "Auto"}
        </i>
      )}
      <button
        className="chip-x"
        aria-label={language === "zh" ? `撤回「${alias.surface}」` : `Retract "${alias.surface}"`}
        onClick={() => onRetract(alias.id)}
      >
        ×
      </button>
    </span>
  );
}
