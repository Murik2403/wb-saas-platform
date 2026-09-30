import { describe, expect, it } from "vitest";
import { cabinetDate, cabinetText, checkPriority, plural, wbAvailabilityMessage } from "./cabinet-copy";

describe("cabinet presentation", () => {
  it.each([[1,"день"],[2,"дня"],[5,"дней"],[11,"дней"],[21,"день"],[111,"дней"],[200,"дней"],[340,"дней"],[1.5,"дня"]])("declines %s", (n, word) => {
    expect(plural(Number(n), ["день","дня","дней"])).toBe(word);
  });
  it("formats the original clock without inventing a timezone", () => {
    expect(cabinetDate("2026-09-23T23:55:39.125804")).toBe("23.09.2026 23:55");
    expect(cabinetDate("нет данных")).toBe("нет данных");
  });
  it("keeps transport paths out of the displayed warning", () => {
    expect(wbAvailabilityMessage(["/api/v1/products: HTTP 409"])).not.toMatch(/409|\/api\//);
    expect(wbAvailabilityMessage(["503"])).toContain("временно");
  });
  it("ranks critical before attention, unknown before ready", () => {
    expect(["Готово","Внимание","Критично","Неизвестно"].sort((a,b) => checkPriority(a)-checkPriority(b))).toEqual(["Критично","Внимание","Неизвестно","Готово"]);
    expect(cabinetText("wb_incident_review · настройки tenant")).toBe("Проверка инцидентов WB · настройки кабинета");
  });
});
