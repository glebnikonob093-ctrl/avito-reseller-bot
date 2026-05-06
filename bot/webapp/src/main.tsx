import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import "./styles.css";

// Signal successful bundle execution for Telegram WebView diagnostics.
(window as any).__APP_BOOTED__ = true;

ReactDOM.createRoot(document.getElementById("root")!).render(<App />);

