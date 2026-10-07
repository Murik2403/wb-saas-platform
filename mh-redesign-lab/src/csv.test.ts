import { describe, expect, it } from "vitest";
import { buildCsv } from "./csv";

describe("buildCsv", () => {
  it("adds BOM, uses ';' and CRLF, decimal comma", () => {
    const csv = buildCsv(["Товар", "Выручка"], [["Салфетки", 1234.5], ["Коврик", -20]]);
    expect(csv.charCodeAt(0)).toBe(0xfeff);
    expect(csv).toBe("﻿Товар;Выручка\r\nСалфетки;1234,5\r\nКоврик;-20\r\n");
  });
  it("quotes separators, quotes and newlines", () => {
    expect(buildCsv(["a"], [['x;y'], ['say "hi"'], ["l1\nl2"]])).toBe('﻿a\r\n"x;y"\r\n"say ""hi"""\r\n"l1\nl2"\r\n');
  });
  it("neutralises formula injection in text but not in negative numbers", () => {
    const csv = buildCsv(["a"], [["=1+1"], ["+7"], ["-x"], ["@SUM(A1)"], [-5]]);
    expect(csv).toBe("﻿a\r\n'=1+1\r\n'+7\r\n'-x\r\n'@SUM(A1)\r\n-5\r\n");
  });
  it("renders empty and non-finite values as empty and handles an empty list", () => {
    expect(buildCsv(["a", "b"], [[null, NaN]])).toBe("﻿a;b\r\n;\r\n");
    expect(buildCsv(["a", "b"], [])).toBe("﻿a;b\r\n");
  });
  it.each([" =1+1", "\n=1+1", "\r\n+1", "\t@SUM(A1)"])("keeps leading whitespace/control formula text inert: %j", (input) => {
    const csv = buildCsv(["a"], [[input], [-7]]);
    expect(csv).toContain(`'${input}`);
    expect(csv).toContain("\r\n-7\r\n");
  });
});
