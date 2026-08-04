import { describe, expect, it } from "vitest";
import { readTextFile } from "./client";

describe("readTextFile", () => {
  it("decodes a UTF-8 text file", async () => {
    const file = new File(["第一章\n正文"], "novel.txt", { type: "text/plain" });

    await expect(readTextFile(file)).resolves.toBe("第一章\n正文");
  });

  it("falls back to GBK when UTF-8 decoding has replacement characters", async () => {
    const file = new File([new Uint8Array([0xb5, 0xda, 0xd2, 0xbb, 0xd5, 0xc2])], "novel.txt");

    await expect(readTextFile(file)).resolves.toBe("第一章");
  });
});
