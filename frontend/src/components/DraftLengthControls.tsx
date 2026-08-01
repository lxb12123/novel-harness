import { useEffect, useState } from "react";
import type { DraftLanguage, DraftLengthSpec } from "../api/types";

export const DRAFT_LENGTH_STORAGE_KEY = "nh-draft-length:v1";

const DEFAULTS: Record<DraftLanguage, DraftLengthSpec> = {
  zh: { language: "zh", min_units: 2000, target_units: 2500, max_units: 3000 },
  en: { language: "en", min_units: 1200, target_units: 1500, max_units: 1800 },
};

const HARD_MAX: Record<DraftLanguage, number> = { zh: 20_000, en: 12_000 };

type EditableLengthSpec = {
  language: DraftLanguage;
  min_units: number | "";
  target_units: number | "";
  max_units: number | "";
};

type LengthField = "min_units" | "target_units" | "max_units";

function isLanguage(value: unknown): value is DraftLanguage {
  return value === "zh" || value === "en";
}

function asValidSpec(value: EditableLengthSpec): DraftLengthSpec | null {
  const { language, min_units, target_units, max_units } = value;
  if (
    !Number.isInteger(min_units) ||
    !Number.isInteger(target_units) ||
    !Number.isInteger(max_units) ||
    min_units === "" ||
    target_units === "" ||
    max_units === "" ||
    min_units < 1 ||
    target_units < 1 ||
    max_units < 1 ||
    min_units > target_units ||
    target_units > max_units ||
    max_units > HARD_MAX[language]
  ) {
    return null;
  }
  return { language, min_units, target_units, max_units };
}

function loadInitialSpec(): DraftLengthSpec {
  try {
    const stored = JSON.parse(
      window.localStorage.getItem(DRAFT_LENGTH_STORAGE_KEY) ?? "null",
    ) as Partial<DraftLengthSpec> | null;
    if (
      stored &&
      isLanguage(stored.language) &&
      typeof stored.min_units === "number" &&
      typeof stored.target_units === "number" &&
      typeof stored.max_units === "number"
    ) {
      const candidate: EditableLengthSpec = {
        language: stored.language,
        min_units: stored.min_units,
        target_units: stored.target_units,
        max_units: stored.max_units,
      };
      return asValidSpec(candidate) ?? DEFAULTS[stored.language];
    }
  } catch {
    // 浏览器里留下的旧值不是产品契约；安静回到冻结默认值。
  }
  return DEFAULTS.zh;
}

function validationMessage(value: EditableLengthSpec): string | null {
  const { language, min_units, target_units, max_units } = value;
  if (max_units !== "" && max_units > HARD_MAX[language]) {
    return language === "zh" ? "中文最多 20,000 字" : "English 最多 12,000 words";
  }
  if (
    min_units === "" ||
    target_units === "" ||
    max_units === "" ||
    !Number.isInteger(min_units) ||
    !Number.isInteger(target_units) ||
    !Number.isInteger(max_units) ||
    min_units < 1 ||
    target_units < 1 ||
    max_units < 1
  ) {
    return "最少、目标、最多都必须是大于 0 的整数";
  }
  if (min_units > target_units || target_units > max_units) {
    return "长度必须满足：最少 ≤ 目标 ≤ 最多";
  }
  return null;
}

export function DraftLengthControls() {
  const [length, setLength] = useState<EditableLengthSpec>(loadInitialSpec);
  const error = validationMessage(length);
  const unit = length.language === "zh" ? "字" : "words";
  const maximumTooHigh =
    length.max_units !== "" && length.max_units > HARD_MAX[length.language];

  useEffect(() => {
    const valid = asValidSpec(length);
    if (valid) {
      window.localStorage.setItem(DRAFT_LENGTH_STORAGE_KEY, JSON.stringify(valid));
    }
  }, [length]);

  const changeLanguage = (language: DraftLanguage) => setLength(DEFAULTS[language]);
  const changeField = (field: LengthField, raw: string) => {
    setLength((current) => ({
      ...current,
      [field]: raw === "" ? "" : Number(raw),
    }));
  };

  return (
    <section className="prep-card prep-wide draft-length-controls" aria-labelledby="draft-length-title">
      <div className="draft-length-head">
        <div>
          <h3 id="draft-length-title">AI 起草长度</h3>
          <div className="draft-length-note">不足时最多自动续写一次</div>
        </div>
        <label className="draft-language" htmlFor="draft-language">
          <span>写作语言</span>
          <select
            id="draft-language"
            value={length.language}
            onChange={(event) => changeLanguage(event.target.value as DraftLanguage)}
          >
            <option value="zh">中文</option>
            <option value="en">English</option>
          </select>
        </label>
      </div>

      <div className="draft-length-row">
        {(
          [
            ["min_units", "最少"],
            ["target_units", "目标"],
            ["max_units", "最多"],
          ] as const
        ).map(([field, label]) => (
          <label className="draft-length-field" key={field}>
            <span>{label}</span>
            <input
              type="number"
              min={1}
              max={HARD_MAX[length.language]}
              step={1}
              value={length[field]}
              aria-invalid={field === "max_units" && maximumTooHigh ? true : undefined}
              onChange={(event) => changeField(field, event.target.value)}
            />
          </label>
        ))}
        <span className="draft-length-unit">{unit}</span>
      </div>

      {error && (
        <div className="draft-length-error" role="alert">
          {error}
        </div>
      )}

      <div className="draft-length-lock">
        <button type="button" disabled>
          AI 起草（M2 必须 PASS）
        </button>
        <span>M2 必须 PASS 且 ADR 0009 完成后才会开放生成；当前只保存长度选择。</span>
      </div>
    </section>
  );
}
