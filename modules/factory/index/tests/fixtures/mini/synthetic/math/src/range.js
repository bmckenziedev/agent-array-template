function normalizeScore(value) {
  if (value < 0) return 0;
  if (value > 10) return 10;
  return value;
}
module.exports = { normalizeScore };

