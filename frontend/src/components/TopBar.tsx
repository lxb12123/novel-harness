import { useIsMutating } from "@tanstack/react-query";
import { useState } from "react";
import { useLanguage } from "../language";
import { useCoords } from "../store";
import { BotIcon, GearIcon, LogIcon, NibIcon, ThinkingSpinner, WingIcon } from "./icons";
import { usePaneCollapse } from "../paneCollapse";
import { SettingsDrawer } from "./SettingsDrawer";

// 顶栏：**两颗开关 + 设置**，一个字都不写（图标 + 悬浮出名字）。
//
// 两颗开关都关着 = 工作台（作者自己写）。所以没有「工作台」那颗按钮——
// 它是「两个都关掉」的同义词，摆出来就是同一件事的第三个入口。
//
// 书名和「＋ 新书 / 导入」不在这儿：它们搬进左栏书架了（一个库可以有多本书，
// 那是一份**列表**，顶栏塞不下，也不该和别的东西抢同一行）。
//
// **「章节」那个下拉框也不在这儿了**（2026-08-13 搬去中栏顶栏，`ChapterTitle.tsx`）：
// 「正在编辑的是哪一章」是中栏那块屏幕的事，而顶栏这一行讲的是整个工作台——
// 换页、设置对三栏都成立，章号只对中栏成立。搬下去之后它和章标题合成了一样东西：
// 同一行字既是「这一章叫什么」，也是挑章的入口，还能双击改。
// **别把它加回来**：一个功能两个入口，作者迟早会在两处看见不一样的章号。
//
// **「出场人物」也不在这儿了。** 它曾经是顶栏第一等公民，等于对作者说「写之前先填这个」——
// 而在场人物是**写出来的结果**，不是写之前的输入：没名字的配角进不了角色册，
// 新人物是写到那儿才需要的。现在引擎在写完之后自己去正文里数（`mentioned.py`），
// 右栏显示数出来的结果。作者要覆盖就点场景块，那是他在正文里亲手标的。
export function TopBar() {
  const language = useLanguage((s) => s.language);
  const { page, chatOpen, setPage, toggleChat } = useCoords();
  const leftCollapsed = usePaneCollapse((s) => s.left);
  const rightCollapsed = usePaneCollapse((s) => s.right);
  const togglePane = usePaneCollapse((s) => s.toggle);
  const [settingsOpen, setSettingsOpen] = useState(false);
  // 续写（行内灰字建议）请求正不正在飞，跟这本书这一章都无关——**只要有一个在飞就转**，
  // 不用挑 key（同 mutationKey 那边的注释）。
  const continuationPending = useIsMutating({ mutationKey: ["continuation"] }) > 0;

  return (
    <header>
      {/* 只剩品牌名（作者 2026-09-06 去掉了后面的「工作台 / Workbench」）。
          `<b>` 留着——顶栏那条 `header .title b` 给的是品牌的强调色，不是加粗。 */}
      <span className="title">
        <b>Novel Harness</b>
      </span>

      {/* ── 收起 / 展开两侧栏（作者 2026-09-06，参照 Cursor）──────────────────
          **一边一颗，各自站在它管的那一栏那一头**：这颗贴着品牌名管左栏，
          另一颗在齿轮左边管右栏（作者 2026-09-06：「右边那个移动到设置左边」）。

          ⚠️ **这条推翻了下面那段关于齿轮的注释里的一半。** 那儿写着设置是
          「一年按一次」的、不许让天天按的按钮挨着它排队（误点一下弹出来的是一整扇
          设置窗）。这一版右栏那颗就紧挨着它——**是作者看过之后定的**，别当成疏忽
          改回去。齿轮「在最右」那一半仍然成立，也仍然有测试钉着。

          **2026-09-07 起图标是作者自己给的那对翅膀**（`wings.svg` / `wings-folded.svg`），
          **一颗按钮拿一半**：左栏那颗是左翅，右栏那颗是它的镜像。展开时羽毛向外上方
          铺开，收起时羽尖向下收拢——**换的是形状本身**，由 `WingIcon` 的 `collapsed` 定。

          **展开时给 `.on`**（作者 2026-09-07：「展开的时候补个悬浮色」）：翅膀张着 =
          那一栏在屏幕上 = 染成强调色，收起时退回常规灰。**这条推翻了原来那句「不给
          `.on`，染色等于把同一件事说第三遍」——是作者看过实物之后定的，别当成疏忽
          改回去。** 用的就是 `.icon-btn.on` 那一条，跟中间那两颗开关同一个强调色，
          不另起一套。`aria-pressed` 照给——读屏用户看不见那一栏，也看不见翅膀和
          颜色，只能靠它。

          同顶栏别处：`aria-label` 是名字（**不随状态变**，变了读屏会以为换了一颗），
          `data-tip` 说的是「按下去会怎样」。 */}
      <button
        className={"icon-btn" + (leftCollapsed ? "" : " on")}
        aria-label={language === "zh" ? "左栏" : "Left panel"}
        aria-pressed={!leftCollapsed}
        data-tip={
          language === "zh"
            ? leftCollapsed ? "展开左栏" : "收起左栏"
            : leftCollapsed ? "Show the left panel" : "Hide the left panel"
        }
        onClick={() => togglePane("left")}
      >
        <WingIcon side="left" collapsed={leftCollapsed} />
      </button>

      <span className="spacer" />

      {/* ── 中间这两颗：**这块屏幕现在是什么样子** ────────────────────────────
          它们都是**开关**，不是三个并排的页签。「工作台」那颗按钮因此没了：
          两颗都关着 = 工作台，所以再摆一颗「回工作台」等于给同一件事第三个入口。

          都不带文字，`aria-label` 是它们的名字（读屏念这个），悬浮时那行字由
          `data-tip` 画出来——**而 `data-tip` 随状态换**：一颗开关最该说的是
          「按下去会怎样」，不是它自己叫什么。

          `aria-pressed` 才是把状态说给读屏听的地方。**名字不许随状态改**：
          名字一变，读屏用户会以为按钮换了一颗（而且所有按名字找它的测试会一起烂）。 */}

      {/* 两颗开关**装在同一块白底里**：它们是一组（「这块屏幕现在是什么样子」），
          而顶栏的底是浅灰——白底把这一组从那片灰里托出来，也顺手说明了
          「这两颗是一回事，右边那颗齿轮不是」。 */}
      <div className="mode-switch">

      {/* 活动记录：系统自己整理这本书的每一步都记在那儿。**它是入口不是通知**——
          不弹、不红点、不推给作者（约束 8）。它只换中栏，左栏书架和右栏面板不动。 */}
      <button
        className={"icon-btn" + (page === "log" ? " on" : "")}
        aria-label={language === "zh" ? "活动记录" : "Activity log"}
        aria-pressed={page === "log"}
        data-tip={
          language === "zh"
            ? page === "log" ? "回到正文" : "活动记录"
            : page === "log" ? "Back to the text" : "Activity log"
        }
        onClick={() => setPage(page === "log" ? "workbench" : "log")}
      >
        <LogIcon />
      </button>

      {/* 这一颗切的是**这个产品的两个模式**（作者 2026-08-13 定的名字）：
          - **协助模式**（模式一，默认）：作者自己写，引擎在右栏守着「谁在第几章还不该知道什么」；
          - **novel-agent 模式**（模式二）：中栏对半分，右半边是助手（`ChatPanel`）。

          **悬浮那行字说的是「按下去会到哪儿」，不是它自己叫什么**——一颗开关最该说这个。
          所以两态各念对面那个模式的名字。名字（`aria-label`）**跟着界面语言走，但
          全仓库只有一份**：`ChatPanel` 的标题、活动记录里的 actor 名、后端好几处提示
          念的都是这同一个名字（国际化第四批之后，中文那半仍然是「写作助手」，
          英文那半是 `ChatPanel.tsx` 已经在用的「Writing assistant」），
          **状态走 `aria-pressed`，名字不许跟着状态变**。

          图标随状态换（作者 2026-09-12 定的这一对）：
          - 关着（协助模式）= **笔尖 + 星芒**（`NibIcon`，作者给的图）：作者自己在写，
            引擎在旁边续写；
          - 开着（novel-agent 模式）= **脑袋是书的小机器人**（`BotIcon`）：助手上场了。
          2026-09-12 之前是同一个机器人的两态（书合着 / 摊开），摊开那版随这次一起删了。 */}
      <button
        className={"icon-btn" + (chatOpen ? " on" : "")}
        aria-label={language === "zh" ? "写作助手" : "Writing assistant"}
        aria-pressed={chatOpen}
        data-tip={
          language === "zh"
            ? chatOpen ? "切换成协助模式" : "切换成 novel-agent 模式"
            : chatOpen ? "Switch to assist mode" : "Switch to novel-agent mode"
        }
        onClick={toggleChat}
      >
        {chatOpen ? <BotIcon /> : <NibIcon />}
        {/* 续写建议正在生成时右上角转一下——这颗图标本来就是「AI 在不在动」的那个位置，
            续写也是 AI 在动。面板开着时续写默认不跑（作者 2026-09-10 定的；设置里
            「是否在 novel-agent 模式下续写」能放行），但这儿不用管那条：只要有一次在飞就转。 */}
        {continuationPending && (
          <span className="bot-thinking">
            <ThinkingSpinner />
          </span>
        )}
      </button>

      </div>

      {/* 「AI 起草」那个抽屉 2026-08-10 删了：它是**填表式**的（先填「这一场要写什么」+
          在场角色，再点按钮出一整章），而在场人物是写出来的结果不是写之前的输入（ADR 0018），
          「接下来写什么」也不该由一个表单承载。起草这件事归模式二的 agent 面板——
          在那儿它是一次工具调用，不是一个界面。**一个功能不留两个入口。**
          后端 `/draft` 一个字没动：它现在是 agent 的起草工具。

          **「章节准备」那一页 2026-08-13 也删了**：三张读卡是右栏同名 tab 的第二个入口，
          两个表单（本章目标 / AI 起草长度）写进 localStorage 没有任何人读。
          别把它加回来——真要给那几张卡一个整页视图，先想清楚它比右栏多给了什么。 */}

      <span className="spacer" />

      {/* 设置**靠最右**：左边那几颗是「现在看哪一块屏幕」（天天按），它是「这台机器怎么配」
          （配完就不再碰）。挨在一起排，等于让一颗一年按一次的按钮天天参与瞄准。
          **图标不带名字**（`aria-hidden`），所以 `aria-label` 必须在这儿——
          少了它，读屏念出来的是一颗没有名字的按钮。 */}
      {/* `data-tip` 是**悬浮时那两个字**（CSS 画的，见 `.icon-btn::after`）。
          **不用原生 `title`**：它要等一秒才浮出来，而作者的原话是「以为没有」——
          一颗只有图标的按钮，名字晚一秒 = 那一秒里它是一颗不知道干什么的按钮。
          `title` 也不能同时留着，否则悬浮会同时冒出两个气泡。

          字面是「AI 设置」不是「设置」：那扇窗的无障碍名字、后端那句「先去顶栏「AI 设置」
          看一眼」都念这四个字。**一个东西一个名字**——少一个字，那句指路的话就指不到了。
          2026-08-14 那扇窗的标题栏拆了（作者要求），于是**屏幕上只剩这四个字在这儿**：
          改这一处 = 那句指路的话在界面上再也落不到实处。英文那半同理钉死
          「AI Settings」，不是「Settings」。 */}
      {/* 收起 / 展开右栏。**紧挨着齿轮是作者定的位置**，见上面那段。
          `tip-right` 跟齿轮一样：它离右边缘只剩一颗按钮的距离，居中的气泡会溢出去。 */}
      <button
        className={"icon-btn tip-right" + (rightCollapsed ? "" : " on")}
        aria-label={language === "zh" ? "右栏" : "Right panel"}
        aria-pressed={!rightCollapsed}
        data-tip={
          language === "zh"
            ? rightCollapsed ? "展开右栏" : "收起右栏"
            : rightCollapsed ? "Show the right panel" : "Hide the right panel"
        }
        onClick={() => togglePane("right")}
      >
        <WingIcon side="right" collapsed={rightCollapsed} />
      </button>

      <button
        className="icon-btn tip-right"
        aria-label={language === "zh" ? "AI 设置" : "AI Settings"}
        data-tip={language === "zh" ? "AI 设置" : "AI Settings"}
        onClick={() => setSettingsOpen(true)}
      >
        <GearIcon />
      </button>

      {settingsOpen && <SettingsDrawer onClose={() => setSettingsOpen(false)} />}
    </header>
  );
}
