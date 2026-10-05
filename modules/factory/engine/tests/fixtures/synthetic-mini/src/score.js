"use strict";

function clampScore(value, ceiling) {
  if (value < 0) return 0;
  return Math.min(value, ceiling);
}

function describeScore(value) {
  return "Score: " + clampScore(value, 20);
}

module.exports = { clampScore, describeScore };
