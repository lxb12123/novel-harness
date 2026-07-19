import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "./styles.css";

// TanStack Query 是服务端状态的**唯一缓存**（§2.3）。retry:1 —— 后端的拒绝（4xx）是
// 语义答案（歧义/找不到），不该被重试掩盖；只给网络抖动留一次。
const client = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
