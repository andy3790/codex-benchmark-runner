// Fixture only: independent grader tests do not give correctness credit to this file.
const assert = require('node:assert/strict');
const core = require('./maze-core.js');
assert.equal(typeof core.solveMaze, 'function');
