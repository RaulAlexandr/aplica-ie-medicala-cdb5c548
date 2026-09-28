import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({ plugins: [react()], server: { allowedHosts: ['.us1.manus.computer', '.us4.manus.computer'] } });
