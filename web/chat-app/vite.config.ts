import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Статика раздаётся FastAPI: страница — на /chat, ресурсы — из /static/chat/.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": decodeURIComponent(new URL("./src", import.meta.url).pathname) } },
  base: "/static/chat/",
  build: { outDir: "dist", emptyOutDir: true, sourcemap: false },
  server: {
    // npm run dev: страница с горячей перезагрузкой, API — с локального uvicorn
    proxy: { "/search": "http://localhost:8000", "/listings": "http://localhost:8000", "/health": "http://localhost:8000" },
  },
  test: { environment: "jsdom", globals: true },
});
