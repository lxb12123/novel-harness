"""起草层 —— 把引擎算出的约束拼成指令、调模型写这一场 prose。

`draft/` 是 `panel/`/`checks/` 的同级能力层:消费只读 StoryGraph 产出的约束,**永不 import
sqlite3、永不 connect、永不碰图表 SQL**(它不在 test_arch_guard 的任何白名单里,三道守卫
一建目录就自动罩住它)。

三块:
- `provider.py` —— 统一的模型出口(OpenAI 兼容协议,开源闭源同一套)。**这是 kill-gate 三臂
  和产品起草共用的那一份调用**,所以「参数在臂间/与生产一致」是结构性的,不是靠自觉。
- `context.py` —— 为一个场景取出合法约束集,并**把「cast 已解析」编码进类型**
  (`ResolvedConstraints`)。它还捎带认知矩阵:X1/X2 从同一个对象渲染,
  于是 EVAL_PROTOCOL §2 的反混淆铁律是类型保证而不是 runner 纪律。
- `assemble.py`(待建)—— 约束 + 简报 → prompt 的 X0/X1/X2 三种拼法(= kill-gate 三臂)。
"""

from __future__ import annotations
