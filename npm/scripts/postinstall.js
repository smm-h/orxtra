"use strict";

const { spawnSync } = require("child_process");

const VERSION = "0.1.0";

const result = spawnSync("uv", ["tool", "install", `orxt-cli==${VERSION}`], {
  stdio: "inherit",
});

if (result.error) {
  if (result.error.code === "ENOENT") {
    console.error("Error: uv is required to install the orxt Python CLI.");
    console.error(
      "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
    );
    console.error("Then run: uv tool install orxt-cli");
    process.exit(1);
  }
  console.error("Error:", result.error.message);
  process.exit(1);
}

if (result.status !== 0) {
  console.error("Error: Failed to install orxt-cli.");
  process.exit(1);
}

console.log("orxt CLI installed successfully");
