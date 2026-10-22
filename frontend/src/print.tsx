import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";

import { DocumentView } from "./templates/DocumentView";
import type { PrintPayload } from "./templates/types";
import "./templates/templates.css";

// The renderer calls window.renderDocument(payload) and waits for body[data-ready="1"].
declare global {
  interface Window {
    renderDocument?: (payload: PrintPayload) => void;
  }
}

const container = document.getElementById("root");
if (!container) throw new Error("missing #root");
const root = createRoot(container);

window.renderDocument = (payload: PrintPayload) => {
  flushSync(() => {
    root.render(<DocumentView document={payload.document} tokens={payload.tokens} mode="print" />);
  });
  document.title = payload.document.header.name;
  document.body.dataset.ready = "1";
};
