const { summarize } = require('@example-org/report');
function show(scores) { return summarize(scores).join(','); }
module.exports = { show };

