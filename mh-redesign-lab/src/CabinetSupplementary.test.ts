import { describe, expect, it } from "vitest";
import type { ActionRecommendation } from "./contracts";
import { secondaryActions } from "./CabinetSupplementary";

describe("main signal and secondary action queue", () => {
  const first = { id: "first" } as ActionRecommendation;
  const second = { id: "second" } as ActionRecommendation;
  it("omits only the hero in the short list without modifying the full queue", () => {
    const queue = [first, second];
    expect(secondaryActions(queue, { ...first })).toEqual([second]);
    expect(queue).toEqual([first, second]);
  });
  it("retains all actions without a hero or without a matching queue item", () => {
    expect(secondaryActions([first], undefined)).toEqual([first]);
    expect(secondaryActions([first], second)).toEqual([first]);
  });
  it("does not erase another queue entry sharing an id", () => {
    expect(secondaryActions([first, { ...first }, second], first)).toEqual([{ ...first }, second]);
  });
});
