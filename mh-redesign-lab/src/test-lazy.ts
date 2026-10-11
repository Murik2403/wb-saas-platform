import { screen, waitFor } from "@testing-library/react";
import { expect } from "vitest";

/** Lazy panels render a "Загружаем …" placeholder first: wait until every chunk on screen has arrived. */
export const lazyLoaded = () => waitFor(() => expect(screen.queryByText(/^Загружаем /)).toBeNull());
