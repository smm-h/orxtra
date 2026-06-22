#!/usr/bin/env node
"use strict";

const { spawnSync } = require("child_process");

const result = spawnSync("orxt", process.argv.slice(2), {
  stdio: "inherit",
});

if (result.error) {
  if (result.error.code === "ENOENT") {
    console.error("Error: orxt Python CLI not found.");
    console.error("Install it with: uv tool install orxt-cli");
    console.error("Or: pip install orxt-cli");
    console.error("Requires Python >= 3.12");
    process.exit(1);
  }
  console.error("Error:", result.error.message);
  process.exit(1);
}

process.exit(result.status ?? 1);
