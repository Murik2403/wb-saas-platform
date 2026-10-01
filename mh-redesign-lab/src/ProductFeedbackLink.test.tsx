import { it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ProductFeedbackLink } from "./ProductFeedbackLink";
it("links explicitly to the private owner form without posting any response", () => {
  render(<ProductFeedbackLink gatewayUrl="http://127.0.0.1:18081"/>);
  expect(screen.getByRole("link",{name:"Открыть защищённую форму обратной связи"})).toHaveAttribute("href","http://127.0.0.1:18081/feedback");
  expect(screen.getByText(/Добровольно сообщите/)).toBeInTheDocument();
});
