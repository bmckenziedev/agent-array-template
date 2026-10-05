function clamp(value, low, high) {
  if (low > high) throw new RangeError('reversed bounds');
  return Math.max(low, Math.min(value, high));
}
module.exports = { clamp };
