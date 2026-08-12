-- 作者事后改一条已经生效（CANON）的事实时，「删掉一个参与者」必须留下痕迹。
--
-- `event_knower` 从 002 起就是时态的（valid_from / valid_to / status / scope），所以
-- 「他其实不知道这件事」表达得出来：把那一行 status 改成 RETRACTED，行还在，
-- `TEMPORAL_WHERE` 的 `status = 'ACTIVE'` 自动把它过滤掉。
--
-- `event_participant` 没有这一列，于是同一个动作只剩 DELETE 一条路——而那是全库唯一
-- 一处「作者改一条事实 = 一行凭空消失」。图是 append-only 的（ARCHITECTURE §5：
-- 被取代的边留着、只写 valid_to；同章更正写 RETRACTED），参与者不该是例外。
--
-- 为什么是 status 而不是给它补一整套 valid_from/valid_to：参与者**不是时态的**。
-- 「谁参与了第 88 章那件事」在第 152 章不会变——它只有「是」和「抽错了」两种结局，
-- 而后者的正确表达恰好就是 RETRACTED（EdgeStatus.RETRACTED 的原话：这条事实从未成立过）。
-- 补一套区间反而会邀请「他从第 100 章起才算参与」这种没有语义的写法。
ALTER TABLE event_participant
  ADD COLUMN status TEXT NOT NULL DEFAULT 'ACTIVE'
  CHECK (status IN ('ACTIVE', 'RETRACTED'));

PRAGMA user_version = 5;
