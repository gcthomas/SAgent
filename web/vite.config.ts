import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 开发模式下将 /api 请求代理到本地后端，生产构建产物输出到默认的 dist 目录
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
});
