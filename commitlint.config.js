module.exports = {
  extends: ["@commitlint/config-conventional"],
  rules: {
    "header-max-length": [2, "always", 100],
    "body-max-line-length": [2, "always", 100],
  },
  ignores: [
    (message) => message.startsWith("Merge branch"),
    (message) => message.startsWith("Merge pull request"),
    (message) => message.startsWith("Merge remote-tracking branch"),
  ],
};
