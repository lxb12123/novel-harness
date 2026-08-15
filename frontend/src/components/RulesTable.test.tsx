import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { fixtures, renderWithApi } from "../test/harness";
import { devTerms, screenText } from "../test/screenGuard";
import { useCoords } from "../store";
import { RulesTable } from "./RulesTable";

// 「你交代过的」那张表（[ADR 0028](docs/adr/0028-rules-expire-by-situation.md) + 迁移 016）。
//
// **这份文件里最重要的是两条反向断言**：这块屏幕上不许有取消按钮、不许有「还生不生效」。
// 有了前者它就变回 2026-08-14 撤掉的那块面板；有了后者它就在说一句自己不知道真假的话
// （有效期是情境的事，只有读到规矩的那个模型判得了）。
//
// 喂进来的每个字节都来自真 dump（`fixtures.recordedRules`，两条规矩、两种时效写法）。

beforeEach(() => {
  useCoords.setState({ projectId: "project:ID1", page: "log" });
});

describe("你交代过的", () => {
  it("一行四样：第几章说的 / 那句话 / 管到什么时候 / 哪段对话", async () => {
    renderWithApi(<RulesTable />);
    const [first] = fixtures.recordedRules.rules;

    expect(await screen.findByText(first.text)).toBeInTheDocument();
    expect(screen.getByText(first.until)).toBeInTheDocument();
    // 真 dump 里两条都在第 2 章，所以这儿数的是「有几行标着它」，不是「唯一一个」。
    expect(screen.getAllByText(`第 ${first.chapter} 章`).length).toBe(
      fixtures.recordedRules.rules.length,
    );
    expect(screen.getAllByText(first.chat_title).length).toBeGreaterThan(0);
  });

  it("🔴 一颗取消按钮都没有 —— 有了它，这张表就变回那块被撤掉的面板了", async () => {
    renderWithApi(<RulesTable />);
    await screen.findByText(fixtures.recordedRules.rules[0].text);
    expect(screen.queryAllByRole("button")).toEqual([]);
  });

  it("🔴 不说哪一条还作不作数 —— 那句话引擎不知道真假", async () => {
    // 判据是**列头**：出参里根本没有那一位（`RecordedRule`），所以这一条防的是
    // 「前端自己按章号算一个出来」——它一旦长出来，读起来完全正常。
    renderWithApi(<RulesTable />);
    await screen.findByText(fixtures.recordedRules.rules[0].text);
    const shown = screenText();
    for (const word of ["生效", "已失效", "还管着", "过期", "作废"]) {
      expect(shown).not.toContain(word);
    }
  });

  it("没记下时效的老规矩照实说，不替它编一句", async () => {
    const old = {
      rules: [{ ...fixtures.recordedRules.rules[0], until: "" }],
      scanned_chats: 1,
    };
    renderWithApi(<RulesTable />, [{ match: /\/rules$/, body: old }]);
    expect(await screen.findByText("没记下")).toBeInTheDocument();
  });

  it("两种空说两句不同的话（§10 约束 8）", async () => {
    renderWithApi(<RulesTable />, [
      { match: /\/rules$/, body: { rules: [], scanned_chats: 0 } },
    ]);
    expect(await screen.findByText(/还没跟写作助手说过话/)).toBeInTheDocument();
  });

  it("说过话但一条都没记下 —— 那是**默认**那一档，不许说成「还没说过话」", async () => {
    // `remember_rule` 在真书上一次都没开过火，所以这才是作者最常看见的那一屏。
    renderWithApi(<RulesTable />, [
      { match: /\/rules$/, body: { rules: [], scanned_chats: 3 } },
    ]);
    expect(await screen.findByText(/还没有哪一句被当成交代记下来/)).toBeInTheDocument();
  });

  it("读不出来不许说成「一条都没有」", async () => {
    renderWithApi(<RulesTable />, [{ match: /\/rules$/, status: 500, body: {} }]);
    expect(await screen.findByText(/没读出来/)).toBeInTheDocument();
  });

  it("这块屏幕上一个研发术语都没有", async () => {
    renderWithApi(<RulesTable />);
    await screen.findByText(fixtures.recordedRules.rules[0].text);
    expect(devTerms(screenText())).toEqual([]);
  });
});
