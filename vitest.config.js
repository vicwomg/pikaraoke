import { defineConfig } from "vitest/config";

// The only first-party JS file is splash.js, so coverage is scoped to it.
// Vendored files are excluded by the include list, not an omission list, so
// a re-vendor cannot silently enter the coverage denominator.
export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["tests/js/**/*.test.js"],
    coverage: {
      provider: "v8",
      include: ["pikaraoke/static/js/splash.js"],
      reporter: ["text", "html"],
      reportsDirectory: "coverage/js",
    },
  },
});
