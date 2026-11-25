// Minimal typing for the CSS guard tests (no @types/node in this project).
declare module 'node:fs' {
  export function readFileSync(path: URL | string, encoding: 'utf8'): string;
}
