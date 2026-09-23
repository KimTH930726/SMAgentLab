import { defineConfig, devices } from '@playwright/test';

// 실 도달가능성 회귀 방지용 최소 E2E(2026-09-24) — 유닛테스트는 "함수가 맞게
// 동작하는가"만 보고 "실제 화면에서 그 경로가 눌리는가"는 못 본다(import/csv 계열
// 라우트가 화면 개편 후 조용히 끊긴 채 남아있던 실사고, docs/architecture.md 참고).
// 이미 떠 있는 dev 스택(docker-compose.dev.yml)에 대고 돈다 — 별도 webServer 기동 없음.
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: 'http://localhost:8501',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
});
