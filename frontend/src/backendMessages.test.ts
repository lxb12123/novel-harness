import { describe, expect, it } from "vitest";
import {
  ACTOR_LABEL,
  CAPABILITY_LABEL,
  EDGE_LABEL,
  KIND_LABEL,
  NODE_LABEL,
  RUN_ERROR_LABEL,
  VERDICT_LABEL,
  allMessageCodes,
  messageForCode,
  rawTemplate,
} from "./backendMessages";

// 同 `tests/test_shared_prompt_terms.py` 那把尺：每个码两侧都要有值，英文侧
// 零中文字符。函数式模板（有条件分支的那三条）用空参数跑一次，够扫字符集。

const CJK = /[一-鿿　-〿＀-￯]/;

describe("每个码两侧都非空，英文侧零中文字符", () => {
  for (const code of allMessageCodes()) {
    it(code, () => {
      const zh = rawTemplate(code, "zh");
      const en = rawTemplate(code, "en");
      expect(zh.trim()).not.toBe("");
      expect(en.trim()).not.toBe("");
      expect(CJK.test(en)).toBe(false);
    });
  }
});

describe("认不出的码", () => {
  it("messageForCode 返回 undefined，不猜、不报错", () => {
    expect(messageForCode("no_such_code_at_all", "zh")).toBeUndefined();
    expect(messageForCode("no_such_code_at_all", "en")).toBeUndefined();
  });
});

describe("参数真的会被填进模板", () => {
  it("chapter_missing 的 {chapter} 换成真值", () => {
    expect(messageForCode("chapter_missing", "zh", { chapter: 5 })).toBe(
      "第 5 章已经不在了。刷新一下就对得上了。",
    );
    expect(messageForCode("chapter_missing", "en", { chapter: 5 })).toBe(
      "Chapter 5 is no longer there. Refresh and things will line up again.",
    );
  });

  it("validation_blocked_title：有 rule_title 和 rest 时都要出现", () => {
    const zh = messageForCode("validation_blocked_title", "zh", {
      paragraph: 3,
      rule_title: "设定提前出现",
      rest: 2,
      issue_message: "血脉秘密在第 2 章就出现了",
    });
    expect(zh).toContain("第 3 段·设定提前出现");
    expect(zh).toContain("血脉秘密在第 2 章就出现了");
    expect(zh).toContain("（另有 2 处）");
    // 那句真实的副作用不许被弄丢——它是这一档通知和别的通知的全部区别
    // （原来是后端测试 test_advisory_notifications.py 钉的，Phase B 之后
    // 整句在这儿拼，断言也搬过来）。
    expect(zh).toContain("新正文不会再自动生成总结与情节");
    // 规则编号不许原样出现——`rule_title` 应该是人话名字，不是 R2/R3。
    expect(zh).not.toContain("R2");
    expect(zh).not.toContain("R3");

    const en = messageForCode("validation_blocked_title", "en", {
      paragraph: 3,
      rule_title: "设定提前出现",
      rest: 2,
      issue_message: "血脉秘密在第 2 章就出现了",
    });
    expect(en).toContain("paragraph 3·设定提前出现");
    expect(en).toContain("(2 more)");
    expect(en).toContain("won't automatically generate summaries or events anymore");
    // checks/ 的原文（rule_title / issue_message）是不透明参数，原样透传，
    // 不指望它也是英文——那是另一批的范围。
  });

  it("extraction_yielded_nothing_title：unresolved 和 proposal_count 选中不同的分支", () => {
    const withProposals = messageForCode("extraction_yielded_nothing_title", "en", {
      lost: 12,
      unresolved: true,
      proposal_count: 3,
    });
    expect(withProposals).toContain("aren't recognized in the roster yet");
    expect(withProposals).toContain("raised 3 items");

    const noProposals = messageForCode("extraction_yielded_nothing_title", "zh", {
      lost: 12,
      unresolved: false,
      proposal_count: 0,
    });
    expect(noProposals).toContain("它们都没能落库");
    expect(noProposals).toContain("花名册里先得有人");
  });

  it("clash_title：conflict 枚举查表，不认识的枚举原样透传不崩", () => {
    expect(
      messageForCode("clash_title", "en", { sentence: 3, chapter: 64, conflict: "setting" }),
    ).toBe("Sentence 3 ↔ chapter 64: setting doesn't match.");
    expect(
      messageForCode("clash_title", "zh", { sentence: 3, chapter: 64, conflict: "unknown_kind" }),
    ).toBe("第 3 句 ↔ 第 64 章：unknown_kind。");
  });
});

// ── activity.py 的七张子表：每一行都要各自过一遍字符集检查 ────────────────────
//
// 上面那条"每个码两侧都非空、英文侧零中文"的通用扫描只拿**空参数**跑一次函数式
// 模板——`value_kind`/`value_verdict`/`value_actor`/`value_capability`/`run_error`
// 这几个码内部会查 `KIND_LABEL`/`VERDICT_LABEL`/`ACTOR_LABEL`/`CAPABILITY_LABEL`/
// `RUN_ERROR_LABEL`，但空参数只命中它们的**兜底**分支，表里那 51 行谁都没被
// 真的渲染过一次。这里逐行扫，判据和上面那条通用扫描同一把尺
// （非空 + 英文侧零中文），只是换成扫表本身而不是扫函数的空参数返回值。
describe("activity.py 的七张子表：每一行都非空，英文侧零中文字符", () => {
  const CJK = /[一-鿿　-〿＀-￯]/;
  const tables: Record<string, Record<string, { zh: string; en: string }>> = {
    CAPABILITY_LABEL,
    ACTOR_LABEL,
    KIND_LABEL,
    VERDICT_LABEL,
    EDGE_LABEL,
    NODE_LABEL,
    RUN_ERROR_LABEL,
  };
  for (const [tableName, table] of Object.entries(tables)) {
    for (const [key, { zh, en }] of Object.entries(table)) {
      it(`${tableName}.${key}`, () => {
        expect(zh.trim()).not.toBe("");
        expect(en.trim()).not.toBe("");
        expect(CJK.test(en)).toBe(false);
      });
    }
  }
});

// ── value_cache：六组合，每一组的措辞质量（不是后端参数）───────────────────
//
// 国际化第四批·笔二起，`tests/test_cache_metering.py`/`test_cache_usage.py`
// 里"三档/六档必须分得开、报了 0 不许摆裸 0、不许出现'缓存'这个机制词"这几条
// **措辞**层面的断言搬到了这里——那几份 Python 测试现在只钉后端参数对不对，
// 渲染出来的句子对不对由这份测试钉，同一份数据源（六组合 read/written）。
describe("value_cache：六档都各自一句话，且不许提机制的名字", () => {
  const CASES = [
    ["没报", null, null],
    ["报了 0", 0, null],
    ["报了数", 960, null],
    ["读写都有", 1_024, 176],
    ["写了 0", 1_024, 0],
    ["只有写", null, 176],
  ] as const;

  it("六档在 zh 下渲染出六句不同的话，且没有一句提「缓存」", () => {
    const said = CASES.map(([, read, written]) =>
      messageForCode("value_cache", "zh", { read, written }),
    );
    expect(new Set(said).size).toBe(CASES.length);
    for (const [i, [name]] of CASES.entries()) {
      expect(said[i], name).not.toContain("缓存");
      expect(said[i], name).not.toMatch(/[_]/); // 不许有裸的 snake_case 机制词
    }
  });

  it("「报了 0」不许把裸 0 摆上屏（零要带着理由）", () => {
    const zeroRead = messageForCode("value_cache", "zh", { read: 0, written: null });
    expect(zeroRead).not.toMatch(/\b0\b/);
  });

  it("「没报」是「未记录」，不是「这次没接上」", () => {
    expect(messageForCode("value_cache", "zh", { read: null, written: null })).toBe("未记录");
  });

  it("英文侧同样六句不同，且零中文字符", () => {
    const CJK = /[一-鿿]/;
    const said = CASES.map(([, read, written]) =>
      messageForCode("value_cache", "en", { read, written }),
    );
    expect(new Set(said).size).toBe(CASES.length);
    for (const s of said) expect(CJK.test(s!)).toBe(false);
  });
});
