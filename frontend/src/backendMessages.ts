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

const UNRECORDED: { zh: string; en: string } = { zh: "未记录", en: "Not recorded" };
function unrecorded(language: Language): string {
  return UNRECORDED[language];
}

const CONFLICT_LABEL: Record<string, { zh: string; en: string }> = {
  setting: { zh: "设定对不上", en: "setting doesn't match" },
  timeline: { zh: "时间线对不上", en: "timeline doesn't match" },
  knowledge: { zh: "谁在什么时候知道什么，对不上", en: "who knew what and when doesn't match" },
};

// ── activity.py（日志页，国际化第四批·笔二）─────────────────────────────────
//
// 七张「枚举 → 中文」的表 + 四个格式化函数，整个从 `activity.py` 搬过来的
// （原来分别叫 `_CAPABILITY_LABEL` / `_KIND_LABEL` / `_VERDICT_LABEL` / `_EDGE_LABEL` /
// `_NODE_LABEL` / `_RUN_ERROR_LABEL` / `_ACTOR_LABEL`）。开放 vs 封闭的区分照抄：
// `CAPABILITY_LABEL`/`ACTOR_LABEL` 认不出时 `??` 兜底成原始参数值（`decision_log.actor`
// 是开放字符串），另外五张认不出时退到一句固定的通用兜底（每一个键对应的枚举都是
// 封闭的，认不出只可能是这张表漏了一行）。`tests/test_wording_guard.py` 拿 Python
// 那边的枚举本身核对这五张封闭表的键集合，不许漏。
//
// ✅ **`EDGE_LABEL`/`NODE_LABEL` 曾经和 `frontend/src/api/types.ts` 的 `EDGE_ZH`/
// `LABEL_ZH` 是已知的重复**（笔二当时点了名，但那两张表服务的 `BottomBar`/
// `EvidenceTab`/`LocalGraph`/`RosterDrawer`/`RosterTab`/`SummaryTab`/
// `CanonEdgeEditor` 一整批组件跟笔二无关，故意没有一起动）。**国际化第四批·
// 前端文案批次把这个「别悄悄长出第三份」的警告兑现了**：`EDGE_ZH`/`LABEL_ZH`/
// `edgeName()` 删掉了，那 7 个消费者改接这两张表（经 `nodeLabelText`/
// `edgeLabelText` 两个导出函数，不直接查表），唯一的副作用是 `LABEL_ZH: Record
// <NodeLabel, string>` 曾经靠 TS 类型逼「漏一行编译错」，这张表是 `Record<string,
// …>` 没有这道编译期保险——补的是运行时那道
// `test_the_node_label_wording_table_covers_every_label`/
// `test_the_edge_label_wording_table_covers_every_edge_type`。
export const CAPABILITY_LABEL: Record<string, { zh: string; en: string }> = {
  extractor: { zh: "抽取", en: "extraction" },
  summarizer: { zh: "章节总结", en: "chapter summary" },
  writer: { zh: "起草", en: "drafting" },
  agent: { zh: "写作助手", en: "writing assistant" },
  advisory: { zh: "事后核对", en: "after-the-fact review" },
};

export const ACTOR_LABEL: Record<string, { zh: string; en: string }> = {
  author: { zh: "作者", en: "author" },
  system: { zh: "系统", en: "system" },
};

export const KIND_LABEL: Record<string, { zh: string; en: string }> = {
  alias_merge: { zh: "登记称呼", en: "recorded an alias" },
  node_declare: { zh: "登记条目", en: "recorded an entry" },
  secret_declare: { zh: "登记秘密", en: "recorded a secret" },
  knows_declare: { zh: "声明认知", en: "declared awareness" },
  located_declare: { zh: "声明位置", en: "declared a location" },
  state_declare: { zh: "声明生死", en: "declared a status" },
  first_appearance_declare: { zh: "声明首次登场", en: "declared a first appearance" },
  proposal_review: { zh: "抽取结果审阅", en: "reviewed extraction results" },
  knowledge_edit: { zh: "更正认知类型", en: "corrected an awareness type" },
  knowledge_add: { zh: "补一条认知", en: "added an awareness" },
  event_edit: { zh: "更正事件名单", en: "corrected an event's cast" },
  event_summary_edit: { zh: "编辑情节摘要", en: "edited a chapter summary" },
  canon_edge_edit: { zh: "更正地点/状态/关系", en: "corrected a location/status/relationship" },
  canon_edge_retract: { zh: "撤回地点/状态/关系", en: "retracted a location/status/relationship" },
  chapter_draft: { zh: "写进正文", en: "wrote into the prose" },
};
const KIND_LABEL_FALLBACK = { zh: "一次改动", en: "a change" };

export const VERDICT_LABEL: Record<string, { zh: string; en: string }> = {
  accept: { zh: "接受", en: "accepted" },
  reject: { zh: "否决", en: "rejected" },
  edit: { zh: "改过之后接受", en: "accepted with edits" },
};
const VERDICT_LABEL_FALLBACK = { zh: "已处理", en: "handled" };

export const EDGE_LABEL: Record<string, { zh: string; en: string }> = {
  // KNOWS / BELIEVES 已不是 `EdgeType` 的成员（ADR 0039），只为历史日志行存在——
  // 老 `knows_declare`/`knowledge_edit` 的 payload 里就写着这两个字符串。
  KNOWS: { zh: "知道", en: "knows" },
  BELIEVES: { zh: "以为", en: "believes" },
  LOCATED_AT: { zh: "在", en: "at" },
  MEMBER_OF: { zh: "属于", en: "belongs to" },
  RELATED_TO: { zh: "关系", en: "related to" },
  HAS_STATE: { zh: "状态", en: "status" },
  OWNS: { zh: "有", en: "has" },
  PLANTED_IN: { zh: "埋在", en: "planted in" },
  RESOLVED_IN: { zh: "回应于", en: "resolved in" },
};
const EDGE_LABEL_FALLBACK = { zh: "关系", en: "a relationship" };

export const NODE_LABEL: Record<string, { zh: string; en: string }> = {
  Character: { zh: "人物", en: "character" },
  Location: { zh: "地点", en: "location" },
  Faction: { zh: "势力", en: "faction" },
  Secret: { zh: "秘密", en: "secret" }, // 已不是 NodeLabel 成员，只为历史日志行留着
  Foreshadow: { zh: "伏笔", en: "foreshadowing" },
  Object: { zh: "物品", en: "object" },
  StateDim: { zh: "状态", en: "status" },
  Chapter: { zh: "章", en: "chapter" },
};
const NODE_LABEL_FALLBACK = { zh: "条目", en: "an entry" };

/** `node.label`（`NodeLabel`，或历史日志行里退役的 `Secret`）→ 作者的说法。
 *
 *  国际化第四批·前端文案批次统一进来的：`api/types.ts` 原来有一张各自为政的
 *  `LABEL_ZH`（`Record<NodeLabel, string>`），和这儿的 `NODE_LABEL` 是已知的
 *  重复（本文件曾经的注释点过名）。删表之后这是唯一一份，图谱/花名册一类
 *  组件直接查它，不再各自查 `api/types.ts`。 */
export function nodeLabelText(label: string, language: Language): string {
  return NODE_LABEL[label]?.[language] ?? NODE_LABEL_FALLBACK[language];
}

/** `edge.type`（`EdgeType`，或历史日志行里退役的 `KNOWS`/`BELIEVES`）→ 作者的说法。
 *  同 `nodeLabelText`，统一自 `api/types.ts` 原来的 `EDGE_ZH`/`edgeName()`。 */
export function edgeLabelText(type: string, language: Language): string {
  return EDGE_LABEL[type]?.[language] ?? EDGE_LABEL_FALLBACK[language];
}

// `ExtractionErrorCode` 的原始值 → 作者的说法。**全仓唯一一份**：`activity.py`
// 的日志页详情、`api/extraction.py::ExtractionRunView.errors` 两条读端都发这个码，
// 前端都用 `messageForCode("run_error", language, {code})` 查同一张表。
// 键名和 `draft.provider.ProviderFailureKind` 的四档（auth/quota/unreachable/
// upstream）对齐——`api/app.py` 同步 `/draft` 路径上那处独立的 `ProviderError`
// 兜底（`except ProviderError: ...`）今天还在原样转发 `str(exc)`，等它接进码+参数
// 这条路时，档位名字已经在这儿，不用再猜怎么对齐。
export const RUN_ERROR_LABEL: Record<string, { zh: string; en: string }> = {
  prompt_drift: {
    zh: "这一章在排队期间被改过，整理没有继续（重新整理一次即可）",
    en: "This chapter was edited while it was queued, so the cleanup didn't continue — just run it again.",
  },
  // ── provider 那四档 + 兜底（同 activity.py 原表：只看 HTTP 状态码，不读文案，
  //    这几句里一个状态码都不许出现）──────────────────────────────────────
  provider_auth: {
    zh: "模型服务没接受你的密钥。可能是密钥不对，也可能是这把密钥用不了你填的那个地址——有些服务商按套餐分了不同的地址",
    en: "The model service didn't accept your key. It might be the wrong key, or this key might not work with the endpoint you entered — some providers use different endpoints for different plans.",
  },
  provider_quota: {
    zh: "你在模型服务商那儿的额度或余额不够了，去他们的后台看一眼",
    en: "You're out of quota or balance with your model provider — check their dashboard.",
  },
  provider_unreachable: {
    zh: "没能连上你配置的模型服务",
    en: "Couldn't reach the model service you configured.",
  },
  provider_upstream: {
    zh: "模型服务那边出了问题，过一会儿再试",
    en: "Something went wrong on the model service's end — try again in a bit.",
  },
  provider_failure: {
    zh: "这一次没能调用模型，而系统没能说清是为什么",
    en: "This call to the model failed, and the system couldn't pin down why.",
  },
  call_record_failure: {
    zh: "模型答了，但这次调用没能记进账里，整理没有继续",
    en: "The model answered, but this call couldn't be recorded, so the cleanup didn't continue.",
  },
  analysis_format: {
    zh: "模型这次答的东西读不出来",
    en: "What the model answered this time couldn't be parsed.",
  },
  ingest_failure: {
    zh: "整理结果没能写进这本书，这一章维持原样",
    en: "The cleanup results couldn't be written into this book — this chapter is unchanged.",
  },
};
const RUN_ERROR_LABEL_FALLBACK = {
  zh: "整理没有跑完（没有留下能看懂的原因）",
  en: "The cleanup didn't finish, and no readable reason was recorded.",
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

  // ── activity.py：折叠行标题（entry.title_code）──────────────────────────
  run_entry_title: { zh: "第 {chapter} 章抽取", en: "Chapter {chapter} extraction" },
  call_entry_title: (params, language) => {
    const capability = String(params.capability ?? "");
    const label = CAPABILITY_LABEL[capability]?.[language] ?? capability;
    return language === "zh" ? `模型调用 · ${label}` : `Model call · ${label}`;
  },
  decision_entry_title: (params, language) => {
    const actor = String(params.actor ?? "");
    const kind = String(params.kind ?? "");
    const actorLabel = ACTOR_LABEL[actor]?.[language] ?? actor;
    const kindLabel = KIND_LABEL[kind]?.[language] ?? KIND_LABEL_FALLBACK[language];
    return `${actorLabel} · ${kindLabel}`;
  },

  // ── activity.py：折叠行副标题（entry.subtitle_code）──────────────────────
  run_subtitle_succeeded: {
    zh: "有效事件 {valid} 条 · 丢弃 {discarded} 条 · 待审提案 {proposals} 条",
    en: "{valid} valid events · {discarded} discarded · {proposals} pending review",
  },
  // 一次抽取失败的原因。**同一个码**也用在 `ActivityDetail.errors` 每一条上，
  // 以及 `api/extraction.py::ExtractionRunView.errors`（审阅面板读的另一条端点）——
  // 三处读同一批 `ExtractionErrorCode` 原始值，查的是同一张 `RUN_ERROR_LABEL`。
  run_error: (params, language) => {
    const code = String(params.code ?? "");
    return RUN_ERROR_LABEL[code]?.[language] ?? RUN_ERROR_LABEL_FALLBACK[language];
  },
  run_subtitle_failed_no_detail: {
    zh: "抽取失败（没有留下错误明细）",
    en: "Extraction failed (no error detail was recorded).",
  },
  run_subtitle_running: { zh: "正在跑", en: "Running" },
  run_subtitle_pending: { zh: "排队中", en: "Queued" },
  run_subtitle_unknown_status: { zh: "等着整理", en: "Waiting to be processed" },
  call_subtitle: (params, language) => {
    const model = String(params.model ?? "");
    const tokensIn =
      params.tokens_in === null || params.tokens_in === undefined
        ? unrecorded(language)
        : String(params.tokens_in);
    const tokensOut =
      params.tokens_out === null || params.tokens_out === undefined
        ? unrecorded(language)
        : String(params.tokens_out);
    const ms =
      params.ms === null || params.ms === undefined ? unrecorded(language) : `${params.ms} ms`;
    return language === "zh"
      ? `${model} · 入 ${tokensIn} / 出 ${tokensOut} token · ${ms}`
      : `${model} · in ${tokensIn} / out ${tokensOut} tokens · ${ms}`;
  },
  proposal_review_subtitle: (params, language) => {
    const verdictCode = String(params.verdict ?? "");
    const verdict = VERDICT_LABEL[verdictCode]?.[language] ?? VERDICT_LABEL_FALLBACK[language];
    const events = Number(params.events ?? 0);
    const edges = Number(params.edges ?? 0);
    const characters = Number(params.characters ?? 0);
    const parts =
      language === "zh"
        ? [`事件 ${events} 条`, `关系 ${edges} 条`]
        : [`${events} events`, `${edges} relationships`];
    if (characters) parts.push(language === "zh" ? `人物 ${characters} 个` : `${characters} characters`);
    return language === "zh" ? `${verdict}：${parts.join(" · ")}` : `${verdict}: ${parts.join(" · ")}`;
  },
  decision_subtitle_knowledge_edit: (params, language) => {
    const before = edgeLabelText(String(params.before ?? ""), language);
    const after = edgeLabelText(String(params.after ?? ""), language);
    return language === "zh"
      ? `${params.subject} 对「${params.secret}」：${before} → ${after}`
      : `${params.subject} on "${params.secret}": ${before} → ${after}`;
  },
  decision_subtitle_knowledge_add: (params, language) => {
    const after = edgeLabelText(String(params.after ?? ""), language);
    return language === "zh"
      ? `${params.subject} 对「${params.secret}」：补上「${after}」`
      : `${params.subject} on "${params.secret}": added "${after}"`;
  },
  decision_subtitle_event_edit: (params, language) => {
    const ka = Number(params.knowers_added ?? 0);
    const kr = Number(params.knowers_removed ?? 0);
    const ca = Number(params.cast_added ?? 0);
    const cr = Number(params.cast_removed ?? 0);
    return language === "zh"
      ? `知情 +${ka} −${kr} · 在场 +${ca} −${cr}`
      : `knows about +${ka} −${kr} · present +${ca} −${cr}`;
  },
  decision_subtitle_chapter_draft: (params, language) => {
    const chapter = params.chapter;
    const where =
      chapter === null || chapter === undefined
        ? unrecorded(language)
        : language === "zh"
          ? `第 ${chapter} 章`
          : `Chapter ${chapter}`;
    const units = params.units;
    if (units === null || units === undefined) return where;
    return language === "zh" ? `${where} · 约 ${units} 字` : `${where} · about ${units} characters`;
  },
  decision_subtitle_alias_merge: (params, language) =>
    language === "zh"
      ? `${params.subject} ← 「${params.surface}」`
      : `${params.subject} ← "${params.surface}"`,
  decision_subtitle_edge_declare: (params, language) => {
    const edgeType = edgeLabelText(String(params.edge_type ?? ""), language);
    return `${params.subject} ${edgeType} ${params.target}`;
  },
  decision_subtitle_node_declare: (params, language) => {
    // `subject` 真实数据里从来不是空串（后端已经用 "—" 兜过 `decision.subject_name`
    // 为空的那一档），这里再兜一层只是为了不让空参数自检把这条模板判成"空模板"。
    const subject = String(params.subject ?? "") || "—";
    const label = params.label;
    if (!label) return subject;
    const nodeLabel = nodeLabelText(String(label), language);
    return language === "zh" ? `${subject}（${nodeLabel}）` : `${subject} (${nodeLabel})`;
  },

  // ── activity.py：跳转按钮上的话（jump.label_code）─────────────────────────
  jump_retry_chapter: { zh: "再整理一次第 {chapter} 章", en: "Retry chapter {chapter}" },
  jump_review_single_proposal: { zh: "去审阅这条待审提案", en: "Review this pending item" },
  jump_review_many_proposals: {
    zh: "第 {chapter} 章还有 {count} 条待审",
    en: "Chapter {chapter} has {count} items pending review",
  },
  jump_go_to_chapter: { zh: "去第 {chapter} 章", en: "Go to chapter {chapter}" },
  jump_view_summary: { zh: "去看第 {chapter} 章的总结", en: "View chapter {chapter}'s summary" },
  jump_edit_event_cast: {
    zh: "去改这条事件的知情 / 在场名单",
    en: "Edit who knows about / is present at this event",
  },
  jump_edit_many_events: {
    zh: "改了 {count} 条事件，去第 {chapter} 章逐条改",
    en: "{count} events changed — go to chapter {chapter} to edit them one by one",
  },
  jump_edit_auto_edge: (params, language) => {
    const chapter = params.chapter;
    const base = language === "zh" ? "去改这条自动生成的边" : "Edit this auto-generated fact";
    if (chapter === null || chapter === undefined) return base;
    return language === "zh" ? `${base}（第 ${chapter} 章）` : `${base} (chapter ${chapter})`;
  },

  // ── activity.py：展开详情，标签（DetailRow.label_code，从没插过值）──────────
  detail_label_chapter: { zh: "章节", en: "Chapter" },
  detail_label_valid_events: { zh: "有效事件", en: "Valid events" },
  detail_label_discarded_events: { zh: "丢弃事件", en: "Discarded events" },
  detail_label_pending_proposals: { zh: "待审提案", en: "Pending review" },
  detail_label_elapsed: { zh: "用时", en: "Time taken" },
  detail_label_summary_written: { zh: "这次写出来的总结", en: "The summary this call wrote" },
  detail_label_capability: { zh: "能力", en: "Capability" },
  detail_label_model: { zh: "模型", en: "Model" },
  detail_label_for_chapter: { zh: "为哪一章", en: "For which chapter" },
  detail_label_tokens_in: { zh: "入参 token", en: "Input tokens" },
  detail_label_tokens_out: { zh: "出参 token", en: "Output tokens" },
  detail_label_cache_continuation: { zh: "接着上次的输入", en: "Continued from last time" },
  detail_label_call_ms: { zh: "耗时", en: "Elapsed" },
  detail_label_attempt: { zh: "第几次尝试", en: "Attempt number" },
  detail_label_kind: { zh: "类型", en: "Type" },
  detail_label_verdict: { zh: "裁决", en: "Verdict" },
  detail_label_changed_by: { zh: "谁改的", en: "Changed by" },
  detail_label_subject: { zh: "对象", en: "Subject" },
  detail_label_paragraph: { zh: "段落", en: "Paragraph" },
  detail_label_quote: { zh: "依据引语", en: "Quoted evidence" },

  // ── activity.py：展开详情，值（DetailRow.value_code）───────────────────────
  value_chapter: (params, language) => {
    const chapter = params.chapter;
    if (chapter === null || chapter === undefined) return unrecorded(language);
    return language === "zh" ? `第 ${chapter} 章` : `Chapter ${chapter}`;
  },
  value_count: { zh: "{n}", en: "{n}" },
  value_optional_number: (params, language) => {
    const n = params.n;
    return n === null || n === undefined ? unrecorded(language) : String(n);
  },
  value_text: { zh: "{text}", en: "{text}" },
  value_text_or_unrecorded: (params, language) => {
    const text = params.text;
    return typeof text === "string" && text ? text : unrecorded(language);
  },
  value_capability: (params, language) => {
    const capability = String(params.capability ?? "");
    // `|| "—"` 只罩空参数自检那一档——真实数据里 `capability` 从不是空串。
    return CAPABILITY_LABEL[capability]?.[language] ?? (capability || "—");
  },
  value_elapsed: (params, language) => {
    const seconds = params.seconds;
    if (seconds === null || seconds === undefined) return unrecorded(language);
    const n = Number(seconds);
    if (n < 60) return language === "zh" ? `${n.toFixed(1)} 秒` : `${n.toFixed(1)} seconds`;
    return language === "zh" ? `${(n / 60).toFixed(1)} 分钟` : `${(n / 60).toFixed(1)} minutes`;
  },
  value_call_ms: (params, language) => {
    const ms = params.ms;
    return ms === null || ms === undefined ? unrecorded(language) : `${ms} ms`;
  },
  value_cache: (params, language) => {
    const read = params.read;
    const written = params.written;
    const parts: string[] = [];
    if (read === null || read === undefined) {
      parts.push(unrecorded(language));
    } else if (read === 0) {
      parts.push(
        language === "zh"
          ? "这次没接上，整段输入都重新算了"
          : "Nothing carried over this time — the whole input was recomputed."
      );
    } else {
      parts.push(
        language === "zh"
          ? `${read} token 接着上次，没有重新算`
          : `${read} tokens carried over from last time, not recomputed`
      );
    }
    if (written === 0) {
      parts.push(language === "zh" ? "这次没有新存下内容" : "Nothing new was cached this time");
    } else if (written !== null && written !== undefined) {
      parts.push(
        language === "zh" ? `另存下 ${written} token 供下次接` : `${written} more tokens cached for next time`
      );
    }
    return parts.join(" · ");
  },
  value_kind: (params, language) => {
    const kind = String(params.kind ?? "");
    return KIND_LABEL[kind]?.[language] ?? KIND_LABEL_FALLBACK[language];
  },
  value_verdict: (params, language) => {
    const verdict = String(params.verdict ?? "");
    return VERDICT_LABEL[verdict]?.[language] ?? VERDICT_LABEL_FALLBACK[language];
  },
  value_actor: (params, language) => {
    const actor = String(params.actor ?? "");
    // `|| "—"` 只罩空参数自检那一档——真实数据里 `actor` 从不是空串。
    return ACTOR_LABEL[actor]?.[language] ?? (actor || "—");
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
