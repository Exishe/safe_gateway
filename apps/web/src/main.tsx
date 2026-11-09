import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./index.css";
import { applyTheme, storedTheme } from "./lib/theme";

// Before the first render, so the page does not flash the other theme. Not an inline
// script in index.html: the gateway's CSP allows no inline script.
applyTheme(storedTheme());

const root = document.getElementById("root");
if (root === null) throw new Error("index.html has no #root element");
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
