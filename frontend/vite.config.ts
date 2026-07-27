import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// 开发：Vite 在 :5173，/api 与 /openapi.json 代理到 FastAPI（uvicorn :8000）。
// 生产：`npm run build` 产物落**包内** src/novel_harness/webui/，由 FastAPI 服务（见 api/app.py）。
//
// ⚠️ outDir 指到 Python 包里面，不是 frontend/dist —— 这是刻意的，理由只有一条：
// `uv_build` 会把模块目录下的**任何**文件打进 wheel（实测：非 .py 也进），而它不支持
// 从模块外 force-include。产物落在包里 = `uv build` 自动带上前端，装出来的包才有工作台。
// 落在 frontend/dist 就必须另加一套 build hook 或复制步骤，那是第二份要维护的东西。
//
// 副作用是好的：开发和 wheel 里前端**在同一个位置**，`api/app.py` 只有一条路径要找，
// 不需要「先看这儿再看那儿」的兜底链——那种链最擅长的事是在 wheel 里静默降级成空白页。
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/openapi.json": "http://127.0.0.1:8000",
    },
  },
  // emptyOutDir：outDir 在 root 之外时 Vite 默认不敢清空它（怕删到别人的东西）。
  // 这里是我们自己的目录，不清空会让上一次构建的旧 hash 资源永远留着。
  build: { outDir: "../src/novel_harness/webui", emptyOutDir: true },

  // 组件测试。**不开 globals**：describe/it/expect 一律显式 import，省掉给 tsconfig 加
  // `types` 数组（那会把默认的 @types 自动包含关掉，是个安静的坑）。
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
