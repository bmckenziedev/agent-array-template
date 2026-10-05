const { normalizeScore } = require('@example-org/math');
function summarize(scores) {
  return scores.map(score => normalizeScore(score));
}
module.exports = { summarize };

