import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发：Vite 在 :5173，/api 与 /openapi.json 代理到 FastAPI（uvicorn :8000）。
// 生产：`npm run build` 产物落 dist/，由 FastAPI 的 SPA 兜底路由服务（见 api/app.py）。
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/openapi.json": "http://127.0.0.1:8000",
    },
  },
  build: { outDir: "dist" },
});
