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
//
// ── ⚠️ 每一个 param 都要过一遍"这个值对作者安全吗"，不是只有整句才要查 ──────
//
// **"码 + 参数"不会自动解决措辞安全问题，只是把它从"整句"挪到了"参数"里。**
// 迁移 8 个错误消费者到 `saidToTheAuthor` 时撞过两次真回归：`RosterDrawer.tsx`
// 那档拒绝的后端 `.message` 里带着 `usable_for_rules`/`ADR 0004`，
// `HistoryDrawer.tsx` 那档带着裸快照 id——都是**后端本来就不该说给作者听的话**，
// 只是以前用整句字符串的形式发过来，容易被"反正后端发的就是人话"这条经验糊弄过去。
// 换成码 + 参数之后，同一种毒换了个容器：如果某个 param 本身是 `str(exc)`、
// 内部标识符、或者别的写给维护者看的东西，前端会**原样嵌进模板**，作者屏幕上
// 照样出现引擎词——因为前端这边压根不知道、也不该知道"这个参数看起来像不像
// 内部术语"，那是语义判断，ADR 0005 禁的那类事。
//
// **判据只能在发送前、由后端逐个 param 过一遍**：这个值是——
//   · 结构化数据（计数、章号、枚举值）？→ 安全，正常送。
//   · 作者自己写的原文（称呼、正文引语、规则命中的原文）？→ 安全，正常送——
//     这些本来就在别的地方原样展示给作者过（花名册、检查面板），不是新增风险。
//   · 某个异常的 `str()`、内部字段名、Python 类名？→ **不安全，两条路**：要么
//     整个不送这个码（退回一个不带这个参数的专用兜底句，同 `RosterDrawer.tsx`
//     今天的做法），要么在后端把它收窄成安全的形状（`clash_title` 的 `conflict`
//     本来想传"冲突原因文字"，改成传封闭枚举值，翻译交给前端自己的
//     `CONFLICT_LABEL`，就是"收窄成安全形状"的例子）。
// `model_not_configured`/`model_windows_pull_failed` 两条原来各带一个
// `{exc}`/`{exc_type}` 参数，都属于"异常的 str()"这一类，都已经整个删掉——
// 不是收窄，是根本不送。

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
      params.unresolved === true
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
  chapter_exists: {
    zh: "这一章刚刚已经被建出来了（另一个窗口？）。刷新一下就能看见它。",
    en: "This chapter was just created (from another window?). Refresh and you'll see it.",
  },
  chapter_missing: {
    zh: "第 {chapter} 章已经不在了。刷新一下就对得上了。",
    en: "Chapter {chapter} is no longer there. Refresh and things will line up again.",
  },
  // `cast_could_not_resolve_prefix`（旧："在场角色解析不了：{exc}"）删掉了，不是漏了。
  // `exc` 曾经是 `UnresolvedCast` 的 `str()`——那本身已经是 `unresolved_cast_ambiguous`/
  // `unresolved_cast_no_cast_declared` 渲染完的整句。Phase B 把 `UnresolvedCast` 自己
  // 改成带 `code`/`params`（不再是一个字符串），api/app.py 直接转发那对 code/params，
  // 不再包一层"在场角色解析不了："前缀——两条内层消息本来就是完整句子，不需要外层
  // 再加一句引导语，加了反而是「后端拼前缀 + 前端拼正文」的片段拼接，同 validation_
  // blocked_title 那次要避免的形状是一类问题。
  model_not_configured: {
    zh: "模型没配好，先去顶栏 ⚙「AI 设置」填服务地址/模型/钥匙，或设 NH_LLM_BASE_URL / NH_LLM_MODEL / NH_LLM_API_KEY。",
    en: 'The model isn\'t configured — go to the ⚙ "AI Settings" in the top bar and fill in the endpoint / model / key, or set NH_LLM_BASE_URL / NH_LLM_MODEL / NH_LLM_API_KEY.',
  },
  // 原来带一个 `{exc}` 参数（`ValidationError`/`ValueError`/`CapabilityError` 的
  // `str()`）。**删掉了，不补收窄逻辑**：`CapabilityError` 自己的 docstring 是英文——
  // 那本来就是写给维护者的诊断（同 `ProviderError`"str(self) 永远不上作者的屏幕"
  // 那条既有纪律），pydantic 的 `ValidationError` 同理带着字段名和类型名。这不是
  // "先送去前端再挡住"，是根本不该送——参数安不安全要在**送之前**判断，不是让前端
  // 收到手再补一道 screenGuard。
  model_windows_pull_failed: {
    zh: "没能拉到那份公开的模型表。原来那份还在用，什么都没改。网络好了再试一次。",
    en: "Couldn't fetch the public model list. The existing list is still in use, nothing changed. Try again once your network is back.",
  },
  // 原来带一个 `{exc_type}` 参数（`type(exc).__name__`，比如 `URLError`）。同样删掉：
  // 一个 Python 异常类名对作者不构成任何可操作的信息（他不知道 URLError 是什么，
  // 「网络好了再试一次」已经说完了他能做的事），却是一个写给维护者看的技术词——
  // 判据跟上面 `model_not_configured` 那条一致。
  // ── api/review.py（提案审阅，三档只有码没有话——2026-08-27 之前会漏成裸码上屏）──
  proposal_not_found: {
    zh: "这条待确认今天不在了。看一眼现在是什么样，它可能已经被处理过了。",
    en: "This item isn't there anymore. Take a look at the current state — it may have already been handled.",
  },
  // ── api/notifications.py ─────────────────────────────────────────────
  notification_not_found: {
    zh: "这条通知今天不在了，可能已经被处理过。刷新一下看看现在还有哪些需要留意的。",
    en: "This notification isn't there anymore — it may have already been handled. Refresh to see what still needs attention.",
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
