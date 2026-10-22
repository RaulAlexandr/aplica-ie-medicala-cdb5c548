import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

const frontendRoot = fileURLToPath(new URL('.', import.meta.url));

export default defineConfig({
  root: frontendRoot,
  base: './',
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5176,
    strictPort: true,
  },
  preview: {
    host: '127.0.0.1',
    port: 5176,
    strictPort: true,
  },
  build: {
    outDir: resolve(frontendRoot, 'dist-t06'),
    emptyOutDir: true,
    rollupOptions: {
      input: resolve(frontendRoot, 't06-demo.html'),
    },
  },
});
