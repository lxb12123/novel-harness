import { useState } from "react";
import {
  useAddCharacterAlias,
  useCharacterProfile,
  useRetractAlias,
} from "../api/hooks";
import type { StoredAlias } from "../api/types";
import { useLanguage } from "../language";
import { useCoords } from "../store";

// 人物基础信息（Task 15 / §4.5）：本名 + 一排别名 chip。
//
// **本名不是 chip、也不在这里改**——它由 `upsert_node` 独占（`canonical` 盾），
// 改本名请改人物本身。别名 chip：点「＋ 添加」加新称呼；点 chip 上的 × 撤回。
// 机器自动 alias（`source=extractor`）标一颗「自动」小标记，作者改它 = 新 author
// 派生行（`derived_from_alias_id` 指回原来的机器行），UI 上看起来就是改成新字。
//
// **一个 URL 里看见的别名字段，永远只有「本名 + 一排 chip」这一个形状。**
// 设置里那个多值文本框（逗号一次塞一堆）是 RosterDrawer 建人物时的入口，两码事。

export function CharacterBasicInfo({
  characterId,
  onClose,
}: {
  characterId: string;
  onClose?: () => void;
}) {
  const { projectId } = useCoords();
  const language = useLanguage((s) => s.language);
  const profile = useCharacterProfile(projectId, characterId);
  const add = useAddCharacterAlias(projectId ?? "", characterId);
  const retract = useRetractAlias(projectId ?? "");
  const [surface, setSurface] = useState("");

  const data = profile.data;
  const name = data?.character?.name ?? "……";

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
      <div className="cnrow">
        <strong>{name}</strong>
        {onClose && (
          <button className="link" onClick={onClose} title={language === "zh" ? "关掉" : "Close"}>
            ✕
          </button>
        )}
      </div>
      <div className="alias-chips">
        {(data?.aliases ?? []).map((alias) => (
          <AliasChip
            key={alias.id}
            alias={alias}
            onRetract={(id) => retract.mutate(id, {})}
          />
        ))}
      </div>
      <div className="alias-add">
        <input
          value={surface}
          onChange={(e) => setSurface(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") addAlias();
          }}
          placeholder={language === "zh" ? "加一个称呼…" : "Add a name…"}
        />
        <button className="link" onClick={addAlias} disabled={add.isPending || !surface.trim()}>
          ＋
        </button>
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
