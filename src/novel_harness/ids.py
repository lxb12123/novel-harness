"""业务 ID —— ULID，Day 2 定死（ADR 0003 / PLAN §5.3）。

    new_id(t, project_id) -> f"{t}:{project_short(project_id)}:{ULID()}"

`slug` / 人名 / `chapter_number` 一律是**可变属性**，永远不进 ID。这条不是洁癖：
M4 的增量抽取必然产出同一人物的多个候选（顾清音 / 清音 / 顾姑娘），作者一合并，
slug 就变了，全部 edge / evidence / alias / decision_log 引用断链。**把别名当一等公民
却用别名派生的 slug 当主键，是自相矛盾。**

唯一例外是内容寻址的 `artifact_id()`（`artifact:sha256:{hex}`）——它必须由内容决定，
因为「同样的 prompt 是不是已经跑过」这个问题只有内容答得了。

── 关于 PLAN §5.3 里的 `NH_UUID_NAMESPACE` ────────────────────────────────
§5.3 的代码块里留着 `NH_UUID_NAMESPACE = uuid.UUID("...")  # 改这个常量 = 全量索引作废`。
**这一行是上一版（uuid5 派生自 slug）的遗留，在 ULID 方案下没有意义**：ULID 不从任何
输入派生，没有命名空间可言。它没有被实现，不是遗漏。

但它想钉住的东西是真的，而且在这里换成了 `project_short()`：**那才是本文件里唯一
确定性的、改了就让全部历史 ID 对不上号的算法**。golden 测试钉的就是它（见
tests/test_ids.py）——ULID 的随机段没法钉，也不该钉。
"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from typing import Final

from ulid import ULID

ID_SEP: Final = ":"

PROJECT_SHORT_LEN: Final = 8
"""`project_short()` 取的十六进制位数。

**改它 = 全库 ID 作废**（改 `project_short` 的算法同理）。这不是「调一下就好」的参数：
node / edge / evidence / alias 的主键里都印着这 8 个字符，而 decision_log 里的历史
确认是靠文本引语重放的、不认 ID——也就是说，改了它，能救回来的只有 decision_log。
"""


class EntityType(StrEnum):
    """ID 第一段。**每个值对应一张实际有主键的表**（ADR 0005 的增长规则：没有表就没有值）。

    前 8 个是 `NodeLabel` 的小写形（`node` 是全部 8 类节点的唯一身份表）。这个对应关系
    由 `tests/test_ids.py` 的交叉验证钉死——本模块**故意不 import graph**：ID 生成是
    图层的下游依赖，反过来 import 会让 `graph/` 的架构守卫多一条要解释的边。

    `chapter` / `secret` 用的就是各自那个 node 的 id（它们是扩展表，主键 = node.id），
    所以这里没有、也不该有单独的 chapter_row / secret_row 类型。
    """

    CHARACTER = "character"
    LOCATION = "location"
    FACTION = "faction"
    SECRET = "secret"
    FORESHADOW = "foreshadow"
    OBJECT = "object"
    STATE_DIM = "statedim"
    CHAPTER = "chapter"

    ALIAS = "alias"
    EDGE = "edge"
    EVIDENCE = "evidence"
    SNAPSHOT = "snapshot"
    DECISION = "decision"
    CALL = "call"
    REPORT = "report"
    PROPOSAL = "proposal"

    PROJECT = "project"
    """只由 `new_project_id()` 用。**项目 ID 不能走 `new_id()`**——见那个函数的 docstring。"""

    @classmethod
    def for_node_label(cls, label: str) -> EntityType:
        """`NodeLabel` → `EntityType`。约定就是小写（`StateDim` → `statedim`）。

        入参是 `str` 而不是 `NodeLabel`，只为了不 import graph；`NodeLabel` 是 StrEnum，
        直接传成员即可。
        """
        return cls(label.lower())


ARTIFACT_PREFIX: Final = "artifact:sha256:"

_ULID_LEN: Final = 26
_ID_RE: Final = re.compile(
    rf"^([a-z_]+):([0-9a-f]{{{PROJECT_SHORT_LEN}}}):([0-9A-HJKMNP-TV-Z]{{{_ULID_LEN}}})$"
)


def project_short(project_id: str) -> str:
    """项目 ID 的短指纹。**这是本文件唯一确定性的算法，golden 测试钉的就是它。**

    取 sha256 而不是截 `project_id` 本身的尾巴：`project_id` 的形状是可以变的
    （导入器、迁移、测试 fixture 都可能塞一个非 ULID 的项目 ID 进来），而哈希对任何
    形状都给得出同样长度、同样字符集的短指纹。截尾巴则会在第一个不规则项目 ID 上
    产出一个长度不对、甚至含 `:` 的前缀——那时全库 ID 已经写下去了。

    它存在的理由（ADR 0003）：**防跨项目误引用在日志里一眼可见**。见 `belongs_to()`。
    """
    if not project_id:
        raise ValueError("project_id 不能为空——空项目 ID 会让全部实体落进同一个短指纹")
    return hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:PROJECT_SHORT_LEN]


def new_id(t: EntityType, project_id: str) -> str:
    """生成一个业务 ID：`{type}:{project_short}:{ULID}`。

    选 ULID 而非 UUIDv4：**单调可排序**——时间前缀让「按创建顺序扫描」和「这条边是哪天
    写进去的」在没有额外索引、没有额外列的情况下成立。
    """
    if t is EntityType.PROJECT:
        # 项目 ID 里的 project_short 只能是它自己的——先有蛋才有鸡，这个调用一定是个 bug。
        raise ValueError("项目 ID 请用 new_project_id()：项目无法以自身为作用域")
    return f"{t.value}{ID_SEP}{project_short(project_id)}{ID_SEP}{ULID()}"


def new_project_id() -> str:
    """项目 ID：`project:{ULID}`，**两段不是三段**。

    它是唯一没有作用域前缀的业务 ID——项目就是作用域本身。
    """
    return f"{EntityType.PROJECT.value}{ID_SEP}{ULID()}"


def artifact_id(data: bytes) -> str:
    """内容寻址：`artifact:sha256:{hex}`。**ADR 0003 里 ULID 的唯一例外。**

    它必须由内容决定：`model_call.in_artifact` / `out_artifact` 要回答的是「这段 prompt
    是不是已经跑过」，那个问题只有内容答得了，ULID 答不了。
    """
    return f"{ARTIFACT_PREFIX}{hashlib.sha256(data).hexdigest()}"


def belongs_to(business_id: str, project_id: str) -> bool:
    """这个 ID 是不是这个项目的。

    `project_short` 前缀存在的**全部**理由就是这个判断（ADR 0003：「防跨项目误引用」）。
    把它写成一个函数而不是让每个调用方切字符串，是因为切错了不会报错，只会静默地
    永远返回 False——那正好长得像「这个人物不存在」。

    注意它只认前缀，不查库：一个语法正确但库里没有的 ID 同样返回 True。
    它回答的是「这个 ID 属不属于这个项目」，不是「这个东西存不存在」。
    """
    m = _ID_RE.match(business_id)
    if m is None:
        return False
    return m.group(2) == project_short(project_id)
