import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "antd/dist/reset.css";
import "@xyflow/react/dist/style.css";
import App from "./App";
import "./styles.css";

const root = document.getElementById("root");

if (!root) {
  throw new Error("Missing React root element");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>
);
