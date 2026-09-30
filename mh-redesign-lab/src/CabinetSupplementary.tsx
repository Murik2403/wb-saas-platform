import type { ActionRecommendation } from "./contracts";
import "./CabinetSupplementary.css";

/** Keep the queue unchanged; the collapsed list omits the action already shown in the hero. */
export function secondaryActions(actions: ActionRecommendation[], hero?: ActionRecommendation) {
  if (!hero) return actions;
  const heroIndex = actions.findIndex((action) => action.id === hero.id);
  return heroIndex < 0 ? actions : actions.filter((_, index) => index !== heroIndex);
}
