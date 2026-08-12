-- 缓存命中量：**这不是一个功能，是一次测量。**
--
-- 供应商的响应里一直带着「这次有多少输入 token 是缓存命中的」，而 `draft/provider.py`
-- 在这一版之前只读 `prompt_tokens` / `completion_tokens` 两个字段 —— 那个数一直被扔掉。
-- 加完这两列之后，作者照常用工作台，数据自己就出来，而三种走势各指向一个完全不同的动作：
--
--   * **命中率高** ⇒ 他的端点已经在自动缓存了，**什么都不用做**；
--   * **恒为 0**（注意：是报了 0，不是 NULL）⇒ 要么这条路由不支持缓存，要么它要显式标记，
--     **那时才轮到去调查「怎么标」**；在此之前调查等于凭空猜；
--   * **忽高忽低** ⇒ **前缀被什么东西弄脏了，那是个 bug**。前缀缓存按字节逐段匹配，
--     稳定块前面混进一个逐次变的东西就整段失效（ADR 0019 边界六换序修的正是这类问题：
--     逐章变的记忆原来排在跨章不变的文风前面）。它以前不可观测，从这两列起看得见。
--
-- ── 两列都可空，而 NULL 和 0 是**两个不同的事实** ────────────────────────────
--
-- NULL = 端点根本没报这件事（或者这一行是这两列存在之前记下的）。
-- 0    = 端点报了，这次一次都没命中。
-- 把两者糊成 0 是本仓栽过四次的同一种病（`chapter_summary` 恒空只写「- 暂无」/
-- 底栏花销系统性偏低 / `endpoints: []` 两个意思 / `roster_total: 0` 被读成「这本书没人物」），
-- 而这一次糊错的代价是上面那三条判读里的头两条会互换 —— 「不支持」和「一次都没命中」
-- 指向两个相反的动作。所以：**没有 DEFAULT 0，一个都不许补。**
--
-- ── 为什么没有第三列「认出的是哪一家的形状」 ────────────────────────────────
--
-- 那个标签只是诊断（`draft/provider.py::CacheShape`），而 NULL 本身已经把
-- 「这条路由的形状我们认不出」说清楚了。本表已经躺着一列 `cost` 至今没有写入方，
-- 再添一列没有读端的东西是同一种病的第二次发作。
--
-- ── 命中率的分母是 `tokens_in` ───────────────────────────────────────────────
--
-- 三家报的 read 都是「这次输入里的一部分」，所以 `cache_read_tokens / tokens_in` 就是命中率，
-- 不必再存一个派生量。DeepSeek 的 `prompt_cache_miss_tokens` 同理不存
-- （它 = `tokens_in - cache_read_tokens`），且它**不是** cache_write。

ALTER TABLE model_call ADD COLUMN cache_read_tokens INTEGER
  CHECK (cache_read_tokens IS NULL OR cache_read_tokens >= 0);
-- 「为了下次能命中而写进缓存的量」。只有 Anthropic 兼容端点报它（且为它单独计费）；
-- DeepSeek / OpenAI 都不报 ⇒ 在那两家上这一列恒为 NULL，**那是「没报」不是「没写」**。
ALTER TABLE model_call ADD COLUMN cache_write_tokens INTEGER
  CHECK (cache_write_tokens IS NULL OR cache_write_tokens >= 0);

PRAGMA user_version = 8;
