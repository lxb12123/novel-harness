import type { Language } from "./language";

// 后端码 → 界面语言那句完整的话（国际化第四批：界面语言独立于书的语言之后，
// 作者读的东西——通知标题、错误框——不能再由后端算好最终句子，因为后端不知道
// 读它的人用什么界面语言，`draft/product_draft.py` 那批"后端答什么语言"问的是
// 另一件事：模型写正文用什么语言）。
//
// ── 这不是那张被删掉的「引擎的词 → 作者的词」映射表 ──────────────────────
//
// `tests/test_canon_edit_boundary.py::test_the_frontend_keeps_no_second_glossary`
// 钉的是「前端**改写**后端已经写好的一句话」（`message.replace("BELIEVES", "以为")`
// 那种），不是「后端发一个结构化码、前端画一个结构化字段」——它自己的 docstring
// 写着后一种"合法且必要"，探针表 `ENUM_TABLE_PROBE` 就是 `STATE_ZH`/`ACTOR_ZH`
// 那种 `Record<K, string>`。这张表是同一个形状：`code` 是枚举，不是一句已经成型
// 的话，翻它是前端的活，不是"改写后端写好的句子"。
//
// 旧表死于两件事：**没有机械校验**（覆盖率纯靠人记得更新）+ **覆盖不了开放词表**
// （`Character`/裸 id 那种引擎内部随时会长出新值的东西）。这张表两条都不占：
// `code` 是这个仓库自己定义的**封闭**集合（不是引擎内部值的开放镜像），
// `test_every_backend_code_has_a_frontend_translation`（后端）+
// `test_every_message_code_has_both_languages_and_the_english_side_is_clean`
// （本文件的测试）两条一起钉住"漏一个就红"。
//
// ── 参数是原始事实，不是已经拼好的句子片段 ─────────────────────────────
//
// 少数几条（`validationBlockedTitle` / `extractionYieldedNothingTitle` /
// `clashTitle`）内部有条件分支（"还有 N 处"要不要出现、用哪句"为什么"）。
// **这些分支只写在这张表的模板函数里**，后端只送原始数值/枚举，不送任何
// 已经决定好顺序或措辞的片段——中英文的语序、要不要复数、分支怎么选，
// 每种语言各自决定，不共用同一份"先拼 A 再拼 B"的顺序。

// `unknown` 不是偷懒：这些值从 HTTP JSON 或通知的 `params_json` 过来，运行时到底
// 是什么形状由后端那次具体调用决定，这里不该替它收窄。`fill()`/模板函数自己按需
// `String(value)` 或做类型收窄。
export type MessageParams = Record<string, unknown>;

type Template = { zh: string; en: string } | ((params: MessageParams, language: Language) => string);

function fill(template: string, params: MessageParams): string {
  return template.replace(/\{(\w+)\}/g, (_, key: string) => {
    const value = params[key];
    return value === undefined ? `{${key}}` : String(value);
  });
}

function moreSuffix(rest: number, language: Language): string {
  if (!rest) return "";
  return language === "zh" ? `（另有 ${rest} 处）` : ` (${rest} more)`;
}

const CONFLICT_LABEL: Record<string, { zh: string; en: string }> = {
  setting: { zh: "设定对不上", en: "setting doesn't match" },
  timeline: { zh: "时间线对不上", en: "timeline doesn't match" },
  knowledge: { zh: "谁在什么时候知道什么，对不上", en: "who knew what and when doesn't match" },
};

const MESSAGES: Record<string, Template> = {
  // ── system_notifications.py ──────────────────────────────────────────
  import_toc_skipped_title: {
    zh: "跳过了 {count} 个只有标题、没有正文的章 —— 看起来你的文件里带了一页目录。",
    en: "Skipped {count} chapters that had only a heading and no body text — looks like your file has a table of contents page in it.",
  },
  // paragraph / rule_title（不透明，checks/ 自己的措辞）/ rest / issue_message（不透明）
  validation_blocked_title: (params, language) => {
    const paragraph = language === "zh" ? `第 ${params.paragraph} 段` : `paragraph ${params.paragraph}`;
    const ruleTitle = typeof params.rule_title === "string" && params.rule_title ? params.rule_title : "";
    const head = ruleTitle ? `${paragraph}·${ruleTitle}` : paragraph;
    const more = moreSuffix(Number(params.rest ?? 0), language);
    const tail =
      language === "zh"
        ? "新正文不会再自动生成总结与情节。"
        : "New prose won't automatically generate summaries or events anymore.";
    const separator = language === "zh" ? "：" : ": ";
    return `${head}${separator}${params.issue_message ?? ""}${more}${tail}`;
  },
  // ── extract/runner.py ─────────────────────────────────────────────────
  // lost / unresolved（这一档丢弃的原因是不是「认不出人」）/ proposal_count
  extraction_yielded_nothing_title: (params, language) => {
    const why =
      params.unresolved === "true"
        ? language === "zh"
          ? "它们提到的人在花名册里还认不出来"
          : "the people they mention aren't recognized in the roster yet"
        : language === "zh"
          ? "它们都没能落库"
          : "none of them made it into the book";
    const proposalCount = Number(params.proposal_count ?? 0);
    const tail = proposalCount
      ? language === "zh"
        ? `这一次提了 ${proposalCount} 条待确认，确认之后重新整理这一章，事件才留得下。`
        : `This time it raised ${proposalCount} items for you to confirm — after you confirm them, re-run the extraction for this chapter so the events can stick.`
      : language === "zh"
        ? "花名册里先得有人，这一章的事件才留得下。"
        : "The roster needs people in it first before this chapter's events can stick.";
    return language === "zh"
      ? `这一章整理完了，但 ${params.lost} 件事一件都没留下 —— ${why}。${tail}`
      : `This chapter finished processing, but ${params.lost} events didn't make it in at all — ${why}. ${tail}`;
  },
  // ── advisory_review.py ────────────────────────────────────────────────
  // sentence / chapter / conflict（"setting"|"timeline"|"knowledge"）/ rest
  clash_title: (params, language) => {
    const conflictCode = String(params.conflict ?? "");
    const conflict = CONFLICT_LABEL[conflictCode]?.[language] ?? conflictCode;
    const more = moreSuffix(Number(params.rest ?? 0), language);
    return language === "zh"
      ? `第 ${params.sentence} 句 ↔ 第 ${params.chapter} 章：${conflict}。${more}`
      : `Sentence ${params.sentence} ↔ chapter ${params.chapter}: ${conflict}.${more}`;
  },
  // ── panel/constraints.py ──────────────────────────────────────────────
  unresolved_cast_ambiguous: {
    zh: "第 {chapter} 章的场景里这些称呼解析不出唯一角色：{unresolved}。请在面板上指定他们是谁——「师兄」在一章里可能指 8 个人，系统猜错的产物是一个此刻在场的人从这一场的在场名单里静默消失",
    en: 'In chapter {chapter}\'s scene, these names don\'t resolve to a single character: {unresolved}. Please specify who they are on the panel — a name like "senior brother" could mean any of 8 people in one chapter, and a wrong guess by the system means someone who\'s actually present silently vanishes from this scene\'s cast list',
  },
  // ── draft/context.py ──────────────────────────────────────────────────
  unresolved_cast_no_cast_declared: {
    zh: "第 {chapter} 章的这一场没有声明在场角色（`cast=`）。空着的在场名单和「这一场真的没有人」在出参上长得一模一样，而前者不该被当成后者发给模型。请在场景块里写明这一场有谁",
    en: "Chapter {chapter}'s scene doesn't declare who's present (`cast=`). An empty cast list and \"truly nobody is here\" look identical in the output, and the former shouldn't be sent to the model as the latter. Please write who's in this scene in the scene block",
  },
  // ── draft/rolling_summary.py ─────────────────────────────────────────
  summary_text_rejected_empty: {
    zh: "这一段是空的。要清掉这一章的总结，用「撤回」。",
    en: 'This text is empty. To clear this chapter\'s summary, use "retract" instead.',
  },
  summary_text_rejected_too_long: {
    zh: "这一段太长了（{length} 字，最多 {max_chars} 字）。这里是给写作模型看的背景，写得太长会把更早那几章的总结挤出去。",
    en: "This text is too long ({length} characters, {max_chars} at most). This is background the writing model reads — writing too much here crowds out the summaries of earlier chapters.",
  },
  // ── draft/windows.py ──────────────────────────────────────────────────
  model_windows_refresh_empty: {
    zh: "拉回来的内容里一个对话模型都没有，没有覆盖原来那份。",
    en: "There wasn't a single chat model in what came back, so the original list was not overwritten.",
  },
  // ── api/app.py ────────────────────────────────────────────────────────
  chapter_number_at_least_one: {
    zh: "章号至少是 1",
    en: "Chapter number must be at least 1",
  },
  chapter_exists_message: {
    zh: "这一章刚刚已经被建出来了（另一个窗口？）。刷新一下就能看见它。",
    en: "This chapter was just created (from another window?). Refresh and you'll see it.",
  },
  chapter_missing_message: {
    zh: "第 {chapter} 章已经不在了。刷新一下就对得上了。",
    en: "Chapter {chapter} is no longer there. Refresh and things will line up again.",
  },
  cast_could_not_resolve_prefix: {
    zh: "在场角色解析不了：{exc}",
    en: "Could not resolve who's present: {exc}",
  },
  model_not_configured: {
    zh: "模型没配好：{exc} —— 先去顶栏 ⚙「AI 设置」填服务地址/模型/钥匙，或设 NH_LLM_BASE_URL / NH_LLM_MODEL / NH_LLM_API_KEY。",
    en: 'The model isn\'t configured: {exc} — go to the ⚙ "AI Settings" in the top bar and fill in the endpoint / model / key, or set NH_LLM_BASE_URL / NH_LLM_MODEL / NH_LLM_API_KEY.',
  },
  model_windows_pull_failed: {
    zh: "没能拉到那份公开的模型表（{exc_type}）。原来那份还在用，什么都没改。网络好了再试一次。",
    en: "Couldn't fetch the public model list ({exc_type}). The existing list is still in use, nothing changed. Try again once your network is back.",
  },
  // ── api/review.py（提案审阅，三档只有码没有话——2026-08-27 之前会漏成裸码上屏）──
  proposal_not_found: {
    zh: "这条待确认今天不在了。看一眼现在是什么样，它可能已经被处理过了。",
    en: "This item isn't there anymore. Take a look at the current state — it may have already been handled.",
  },
  stale_base_version: {
    zh: "这本书在别处刚被改过（你看到的还是版本 {expected}，现在已经是 {current}）。先看一眼最新的，再决定这一处要不要改。",
    en: "This book was just changed somewhere else (you were looking at version {expected}, it's now {current}). Take a look at the latest version before deciding whether to make this edit.",
  },
  proposal_already_resolved: {
    zh: "这条已经被处理过了（可能是你自己在别的标签页点的，也可能是别人）。刷新一下看看结果。",
    en: "This item has already been handled (maybe you did it in another tab, maybe someone else did). Refresh to see the result.",
  },
};

/** 按码取一句完整的话。**认不出的码返回 `undefined`**，不猜——调用方自己决定怎么兜底
 *  （通常是原样显示码本身，同一个开放世界里"这个码我们还没配文案"的诚实形态）。 */
export function messageForCode(
  code: string,
  language: Language,
  params: MessageParams = {},
): string | undefined {
  const template = MESSAGES[code];
  if (!template) return undefined;
  if (typeof template === "function") return template(params, language);
  return fill(template[language], params);
}

/** 仅供测试：完整键集合 + 拿到一条模板（不填参数）方便扫描英文侧有没有中文字符。 */
export function allMessageCodes(): string[] {
  return Object.keys(MESSAGES);
}

export function rawTemplate(code: string, language: Language): string {
  const template = MESSAGES[code];
  if (typeof template === "function") {
    // 函数式模板拿一份全空参数跑一次，够扫字符集就行，不必求语义正确。
    return template({}, language);
  }
  return template[language];
}
