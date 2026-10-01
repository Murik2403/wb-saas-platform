import { describe, expect, it } from "vitest";
import { previewMoney } from "./ozon-preview-money";

describe("confirmed Ozon preview Decimal formatting", () => {
  it.each([
    ["99999999999999999.99", "99 999 999 999 999 999,99 ₽"],
    ["-99999999999999999.99", "-99 999 999 999 999 999,99 ₽"],
    ["1E+3", "1 000,00 ₽"], ["1E-2", "0,01 ₽"],
    ["1.2300E+2", "123,00 ₽"], ["-0E-9999999999", "-0,00 ₽"],
    ["0.10", "0,10 ₽"], [null, "не рассчитано"],
    ["0.001", "не рассчитано"],
  ])("formats %s without changing the amount", (value, expected) => {
    expect(previewMoney(value).replace(/\u00a0/g, " ")).toBe(expected);
  });
});
