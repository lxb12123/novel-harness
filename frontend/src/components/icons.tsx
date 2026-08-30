// 工作台的图标。**这一份是这套图标的语法**，后面每加一个都照它来——
// 一套图标里最刺眼的从来不是哪一个画得不好，而是**它们互相不像**（这个粗那个细、
// 这个 24 视框那个 20、这个跟着文字颜色走那个写死了黑色）。所以规矩先立在这儿：
//
// 1. **24×24 视框，中心 (12,12)**。图标自己不写宽高——大小由 CSS 给（`.icon` = 16px），
//    所以同一个图标在按钮里和在空状态里是同一份，不是两份。
// 2. **描边式**：`fill="none"` + `stroke="currentColor"`，`stroke-width` 1.5，
//    两端和拐角都圆。跟着 `currentColor` 走这一条是关键：按钮悬浮变强调色、禁用变淡，
//    图标自己跟着变，**一行额外的 CSS 都不用写**。
//    1.5 是配着这个界面挑的：13px 正文 + 1px 描边，图标再粗就比它旁边的字还重。
// 3. **`aria-hidden` + `focusable="false"`**：图标不进无障碍树，名字由**按钮的
//    `aria-label`** 给。一颗只有图标的按钮少了 aria-label = 读屏念不出名字的按钮，
//    而这件事在屏幕上完全看不出来。
// 4. 几何**算出来，不手画**：齿距差一度、圆角差半像素，放大到 48px 一眼就看得见。

/**
 * 活动记录：三条「时间线上的一件事」。
 *
 * 它**故意画得平**——这一栏里只有写作助手那一个是角色，别的都是工具。
 * 两个都卖萌，就没有一个是主角了。
 */
export function LogIcon() {
  return (
    <svg
      className="icon"
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="5.6" cy="6.4" r="1.5" />
      <path d="M10 6.4h9" />
      <circle cx="5.6" cy="12" r="1.5" />
      <path d="M10 12h9" />
      <circle cx="5.6" cy="17.6" r="1.5" />
      <path d="M10 17.6h5.4" />
    </svg>
  );
}

/**
 * 写作助手 —— **一个脑袋是书的小机器人**（作者的原话：头是一个书的机器人，卡通 Q 版可爱）。
 *
 * `open` 是它的**两个状态，不是两个图标**：
 *
 * - `false`（模式一，作者自己写）：书**合着**，它在旁边待命；
 * - `true`（模式二，助手在场）：书**摊开**，它上场了。
 *
 * 两态同一张脸（天线 + 两只大眼睛）、同一个位置、同一个大小，**只有书开合**——
 * 所以切换时它不像换了个图标，像同一个小家伙翻开了书。
 *
 * ── 为什么可爱只能可爱到这个程度 ────────────────────────────────────────
 *
 * Q 版靠的是「圆头 + 大眼睛」的比例，而这一栏的图标是 16–20px。
 * 手脚、笔、表情在这个尺寸下全都糊成噪点（试过：加了笔的那一版在 16px 上只剩一团斜杠，
 * 加了嘴的那一版在 20px 上看不见）。所以只留三样撑得住的：
 * **书的轮廓、两只实心大眼睛、一根天线**——实心眼睛是关键，空心圈在 20px 下会变成小方块。
 */
export function BotIcon({ open }: { open: boolean }) {
  return (
    <svg
      className="icon"
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 5.6V3.4" />
      <circle cx="12" cy="2.6" r="1.05" />
      {open ? (
        <>
          <path d="M12 8.2v9.6" />
          <path d="M12 8.2c-1.9-1.5-4.7-2-8.4-1.7v9.6c3.7-.3 6.5.2 8.4 1.7" />
          <path d="M12 8.2c1.9-1.5 4.7-2 8.4-1.7v9.6c-3.7-.3-6.5.2-8.4 1.7" />
          <circle cx="7.5" cy="11.3" r="1.45" fill="currentColor" stroke="none" />
          <circle cx="16.5" cy="11.3" r="1.45" fill="currentColor" stroke="none" />
        </>
      ) : (
        <>
          <rect x="4.8" y="6.4" width="14.4" height="12.6" rx="2.6" />
          <path d="M8 6.4v12.6" />
          <circle cx="12.2" cy="11.6" r="1.3" fill="currentColor" stroke="none" />
          <circle cx="16" cy="11.6" r="1.3" fill="currentColor" stroke="none" />
        </>
      )}
    </svg>
  );
}

/** 「续写正在生成」的小圈——挂在 `BotIcon` 角上的徽标，不是这份文件开头那套
 *  「描边线稿」的语法：那套是给正文里独立站着的图标定的，这个是**实心刻度放射状**
 *  旋转（Safari 标签页转圈那种），八根短刻度依次淡出淡入，营造转动的错觉——
 *  跟着 `currentColor` 走这条继续成立，机器人本身是什么颜色它就是什么颜色。 */
export function ThinkingSpinner() {
  return (
    <svg className="thinking-spinner" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      {Array.from({ length: 8 }, (_, i) => (
        <rect
          key={i}
          x="10.7"
          y="1.5"
          width="2.6"
          height="7"
          rx="1.3"
          fill="currentColor"
          transform={`rotate(${i * 45} 12 12)`}
          style={{ animationDelay: `${(i * -0.1).toFixed(2)}s` }}
        />
      ))}
    </svg>
  );
}

/**
 * 齿轮（AI 设置）。
 *
 * 8 齿，齿顶半径 9.2、齿根 6.3、齿顶张角 19°、齿侧 6°、中心孔 r=3.4；
 * 拐角不写圆角，靠 `stroke-linejoin="round"` 自动圆掉。
 * **这几个数是调出来的**：先在 15/16/18/20/64px 上并排渲染过五档
 * （少齿的像花、10 齿的在 16px 上糊成一团），最后按「16px 下齿还分得开」定的。
 */
export function GearIcon() {
  return (
    <svg
      className="icon"
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M10.48 2.93A9.20 9.20 0 0 1 13.52 2.93L13.68 5.93A6.30 6.30 0 0 1 15.10 6.52L17.34 4.51A9.20 9.20 0 0 1 19.49 6.66L17.48 8.90A6.30 6.30 0 0 1 18.07 10.32L21.07 10.48A9.20 9.20 0 0 1 21.07 13.52L18.07 13.68A6.30 6.30 0 0 1 17.48 15.10L19.49 17.34A9.20 9.20 0 0 1 17.34 19.49L15.10 17.48A6.30 6.30 0 0 1 13.68 18.07L13.52 21.07A9.20 9.20 0 0 1 10.48 21.07L10.32 18.07A6.30 6.30 0 0 1 8.90 17.48L6.66 19.49A9.20 9.20 0 0 1 4.51 17.34L6.52 15.10A6.30 6.30 0 0 1 5.93 13.68L2.93 13.52A9.20 9.20 0 0 1 2.93 10.48L5.93 10.32A6.30 6.30 0 0 1 6.52 8.90L4.51 6.66A9.20 9.20 0 0 1 6.66 4.51L8.90 6.52A6.30 6.30 0 0 1 10.32 5.93L10.48 2.93Z" />
      <circle cx="12" cy="12" r="3.4" />
    </svg>
  );
}

/**
 * 看不看得见密钥。`off` = 明文正露着（点一下藏回去），所以那时才划一道。
 *
 * 眼形是**四段对称的三次贝塞尔**：左右两个尖角落在 (3,12)/(21,12)，上下顶点 5.7/18.3，
 * 瞳孔 r=3。上一版是两段二次曲线（控制点 (12,4.2)/(12,19.8)）——二次曲线的顶点只走到
 * 控制点的一半，实际只鼓到 3.75，于是在 17px 下看着是**一道横缝里卡了颗小黑点**，
 * 而不是一只眼睛（作者：「这个眼睛太丑了」）。三次曲线才画得出「尖角 + 圆顶」这个形状，
 * 瞳孔也跟着放到 r=3——眼白和瞳孔的比例是「像不像眼睛」的全部。
 *
 * 那道杠是 45° 的整条对角线，**压在眼睛上面画**：短一截的斜杠在这个尺寸下会看成睫毛。
 */
export function EyeIcon({ off }: { off: boolean }) {
  return (
    <svg
      className="icon"
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M3 12c0 0 3.4-6.3 9-6.3s9 6.3 9 6.3-3.4 6.3-9 6.3-9-6.3-9-6.3z" />
      {/* **划掉的那只不画瞳孔。** 一条 45° 的杠正好从瞳孔正中穿过去，三笔挤在
          中间那 6px 里，17px 下糊成一个黑团（实测比过：带瞳孔的那版认不出是眼睛）。
          去掉之后剩「一只眼 + 一道杠」，两笔各说各的。 */}
      {off ? <path d="M4.2 4.2 19.8 19.8" /> : <circle cx="12" cy="12" r="3" />}
    </svg>
  );
}

/**
 * 发送（写作助手输入框里那颗）。
 *
 * 一支向上的箭头，不是纸飞机：这颗按钮**贴在输入框右下角**、只有 28px，
 * 纸飞机那种斜的轮廓在这个尺寸下糊成一团，而箭头在 12px 都认得出。
 * 竖杆穿 5.5–18.5（同 `PlusIcon` 那一笔，两颗按钮的视觉重量才一致），
 * 两撇从顶点各斜下 5.5。
 */
export function SendIcon() {
  return (
    <svg
      className="icon"
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 18.5v-13" />
      <path d="M6.5 11 12 5.5 17.5 11" />
    </svg>
  );
}

/**
 * 关掉这扇窗。
 *
 * **是图标不是那个 `×` 字符**：字形的两笔在不同字体里粗细、倾角、在方格里的位置都不一样
 * （上一版就是它，作者：「右上角的打叉是不是有点问题」），而且它跟着字号走、跟不了
 * `currentColor` 之外的那套图标粗细。画成图标之后，它和这扇窗里的眼睛是同一档描边。
 */
export function CloseIcon() {
  return (
    <svg
      className="icon"
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M6.5 6.5 17.5 17.5" />
      <path d="M17.5 6.5 6.5 17.5" />
    </svg>
  );
}

/**
 * 加号（「新起一章」）。
 *
 * 两笔都**穿到 5.5–18.5**、在 12 处正交——不是「一个 ＋ 字符」：那个字形自带很宽的
 * 两侧留白，塞进一颗 26px 的圆里会显得又小又偏，而它旁边就是一列 13px 的汉字。
 * 画成图标之后大小由 `.icon` 给，和这套里别的图标是同一档。
 */
export function PlusIcon() {
  return (
    <svg
      className="icon"
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 5.5v13" />
      <path d="M5.5 12h13" />
    </svg>
  );
}
