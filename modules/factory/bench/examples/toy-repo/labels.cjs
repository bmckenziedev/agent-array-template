function label(value) {
  const text = String(value).trim();
  return text.length ? text.toUpperCase() : 'EMPTY';
}
module.exports = { label };
