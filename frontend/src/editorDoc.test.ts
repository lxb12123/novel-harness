import { describe, expect, it } from "vitest";
import { diskChange } from "./editorDoc";

// 这四条各自对应一种真会发生的事，第三条是这个文件存在的理由。

describe("磁盘上那一章变了，编辑器要不要跟着换", () => {
  it("第一次读到 —— 装进去", () => {
    expect(diskChange("第一章 开头", null, false)).toBe("adopt");
  });

  it("别处改过、作者手上没有未保存的字 —— 换成新的那一版", () => {
    expect(diskChange("助手写的那一稿", "旧的那一版", false)).toBe("adopt");
  });

  it("**别处改过、而作者有没保存的字 —— 一个字都不许盖**，只说一句", () => {
    // 盖掉的那半段哪儿都找不回来：版本历史只存保存过的。
    expect(diskChange("助手写的那一稿", "旧的那一版", true)).toBe("warn");
  });

  it("作者自己在这份上改的字，不算别处改过 —— 否则他一打字就被弹一句", () => {
    // 判据是**出处**不是编辑器里此刻那份：拿后者比，作者每敲一个字都是「不一样」。
    expect(diskChange("同一版", "同一版", true)).toBe("same");
    expect(diskChange("同一版", "同一版", false)).toBe("same");
  });
});
