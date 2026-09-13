// 桌面壳（`novel_harness/desktop.py`）里这页多知道的两件事：
//
// 1. **顶上没有系统标题条了。** 壳把标题条揉进了工作台的顶栏（作者 2026-09-13：三颗窗口按钮
//    落到顶栏左边、品牌名和收栏那颗往右挪、白色那一条去掉）。壳在地址后面带 `?desktop=1`，
//    这儿看见就给 `<html>` 挂 `desktop`，`styles.css` 的 `html.desktop header` 负责让位和对齐。
//    用查询串不用 UA：UA 要整串换掉，CM6 那类库靠它认引擎。
// 2. **拖窗口得自己叫。** WebKit 不认 `-webkit-app-region`；顶栏空白处按下鼠标时叫壳的
//    `drag()`（`window.pywebview.api`，pywebview 注入的桥），壳拿当前那一下事件开始拖。
//    按在按钮 / 输入框上不叫——那是点，不是拖。

declare global {
  interface Window {
    pywebview?: { api?: { drag?: () => Promise<void>; zoom?: () => Promise<void> } };
  }
}

export const DESKTOP_QUERY = "desktop";

/** 这一页是不是开在桌面壳里。只看地址，`main.tsx` 开页时判一次。 */
export function isDesktop(search: string = window.location.search): boolean {
  return new URLSearchParams(search).has(DESKTOP_QUERY);
}

/** 顶栏的 `onMouseDown`：空白处按下 = 开始拖窗口；**双击**（`detail === 2`）= 按 macOS
 *  的规矩缩放窗口（系统设置里「连按标题栏」定的那一档，壳去读）。双击要在 mousedown 上认，
 *  不能等 `dblclick`：第一下已经把窗口拖起来了，WebKit 收不到后面那半个事件——同 Tauri 的做法
 *  （作者 2026-09-13：「双击应用顶部，他没有按照 mac 的规则去适配屏幕」）。
 *  返回叫没叫壳（测试看这个）。 */
export function dragWindow(event: {
  button: number;
  detail?: number;
  target: EventTarget | null;
}): boolean {
  if (event.button !== 0) return false;
  if (!document.documentElement.classList.contains("desktop")) return false;
  const target = event.target;
  if (target instanceof Element && target.closest("button, a, input, textarea, select, [role=button]")) {
    return false;
  }
  const api = window.pywebview?.api;
  const call = event.detail === 2 ? api?.zoom : api?.drag;
  if (!call) return false;
  void call();
  return true;
}
