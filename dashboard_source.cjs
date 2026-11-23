// Test helper: the dashboard script source as ONE string, in the files' original section order,
// so offline UI tests can keep slicing between function markers after the js/ split.
// (The browser load order differs only in that js/app.js runs last; see dashboard.html.)
const fs = require('node:fs');
const path = require('node:path');
const ORIGINAL_ORDER = ['core', 'overview', 'charts', 'settings', 'history', 'research', 'app', 'alerts', 'equity'];
module.exports = ORIGINAL_ORDER.map(name => fs.readFileSync(path.join(__dirname, 'js', name + '.js'), 'utf8')).join('\n');
