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
