import { describe, expect, it } from "vitest";
import { ApiError } from "./api/client";
import { readCorrectionError } from "./correctionError";
import fixtures from "./__fixtures__/api.json";

// 拒绝形态的三份夹具也是**真 app dump 的**（`tests/test_frontend_contract.py`），
// 不是手写的——后端哪天换了措辞或换了 error 码，那边先红。

const body = (key: "errorStaleCanon" | "errorFactNotFound" | "errorKnowledgeRefused") =>
  (fixtures[key] as { detail: Record<string, unknown> }).detail;

describe("改一条已生效的事实被拒绝时，屏幕上说什么", () => {
  it("409 说的是「先看一眼最新的」，**不是**「再试一次」", () => {
    // 静默重试 = 把作者的改动盖到一份他没看过的状态上。措辞必须把他推去看，不是去重点。
    const failure = readCorrectionError(new ApiError(409, body("errorStaleCanon")));
    expect(failure.kind).toBe("stale");
    expect(failure.message).toMatch(/别处|刚刚被改过/);
    expect(failure.message).toMatch(/先看一眼最新的/);
    expect(failure.message).not.toMatch(/重试|再试一次/);
    // 后端这一条只发两个版本号，没有 message —— 直接渲染 `error` 会摆出 `stale_base_version`。
    expect(failure.message).not.toContain("stale_base_version");
  });

  it("404 / 422 用后端那句话，**一个字都不改**", () => {
    // 这里曾经有一张「引擎的词 → 作者的词」的映射表。它被删了：措辞的源只能有一个，
    // 而后端的拒绝文案本来就该是作者的话（`corrections.py::CorrectionError` 的 docstring）。
    const gone = readCorrectionError(new ApiError(404, body("errorFactNotFound")));
    expect(gone.kind).toBe("gone");
    expect(gone.message).toBe(body("errorFactNotFound").message);

    const refused = readCorrectionError(new ApiError(422, body("errorKnowledgeRefused")));
    expect(refused.kind).toBe("refused");
    expect(refused.message).toBe(body("errorKnowledgeRefused").message);
  });

  it("连不上后端的时候不冒充一次业务拒绝", () => {
    const failure = readCorrectionError(new TypeError("Failed to fetch"));
    expect(failure.kind).toBe("unknown");
    expect(failure.message).not.toContain("fetch");
  });
});
