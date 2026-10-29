// Trusted adapter, executed only inside the isolated Node container.
// Oracles and expected answers deliberately stay in the Python host grader.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const stringify = JSON.stringify.bind(JSON);
const parse = JSON.parse.bind(JSON);
const write = process.stdout.write.bind(process.stdout);
const now = require('node:perf_hooks').performance.now.bind(require('node:perf_hooks').performance);
const request = parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(request.root || '/submission');
const coreFile = path.join(root, 'maze-core.js');
const clone = value => parse(stringify(value));
// Keep normal debug logging out of the JSON protocol.
for (const key of ['log', 'info', 'debug', 'warn']) console[key] = console.error.bind(console);

function browserCore() {
  const context = {console, performance: {now}};
  context.window = context;
  context.self = context;
  vm.createContext(context);
  for (const filename of [...(request.dependencies || []), 'maze-core.js']) {
    const target = path.resolve(root, filename);
    if (target !== coreFile && !target.startsWith(root + path.sep)) throw Error('Dependency outside submission');
    vm.runInContext(fs.readFileSync(target, 'utf8'), context, {filename, timeout: 1500});
  }
  return context.MazeCore;
}

const stored = new Map();
const response = {results: []};
let core;
try {
  core = request.mode === 'browser' ? browserCore() : require(coreFile);
  if (!core || typeof core.generateMaze !== 'function' || typeof core.solveMaze !== 'function') {
    throw Error('MazeCore must export generateMaze and solveMaze functions');
  }
  response.api = true;
} catch (error) {
  response.api = false;
  response.load_error = String(error.stack || error).slice(0, 3000);
}

if (core && response.api) {
  for (let i = 0; i < request.operations.length; i++) {
    const op = request.operations[i];
    const result = {id: op.id};
    try {
      let input, before, value;
      if (op.kind === 'generate') {
        const options = clone(op.options);
        const start = now();
        value = core.generateMaze(options);
        result.duration_ms = now() - start;
        if (value && typeof value.then === 'function') throw Error('The core API must return data synchronously');
        stored.set(op.id, clone(value));
      } else if (op.kind === 'solve') {
        input = clone(op.maze_id ? stored.get(op.maze_id) : op.maze);
        before = stringify(input);
        const options = clone(op.options);
        const start = now();
        value = core.solveMaze(input, options);
        result.duration_ms = now() - start;
        if (value && typeof value.then === 'function') throw Error('The core API must return data synchronously');
      } else {
        throw Error('Unknown trusted operation');
      }
      // Large-grid generation may include thousands of animation snapshots.
      // Transfer only the fields inspected by the independent oracles in that
      // batch; keep the complete maze in `stored` for the actual solver call.
      let reported = value;
      if (request.compact && value && typeof value === 'object' && !Array.isArray(value)) {
        reported = op.kind === 'generate' ? {
          width: value.width, height: value.height,
          cells: Array.isArray(value.cells) ? value.cells.map(cell =>
            cell && typeof cell === 'object' ? {walls: cell.walls, cost: cell.cost} : cell) : value.cells
        } : {found: value.found, path: value.path, visitedOrder: value.visitedOrder, cost: value.cost};
      }
      result.value = clone(reported);
      if (op.kind === 'solve') {
        if (!request.compact) result.input_after = clone(input);
        result.unchanged = before === stringify(input);
      }
      result.ok = true;
    } catch (error) {
      result.ok = false;
      result.error = String(error.stack || error).slice(0, 2000);
    }
    response.results.push(result);
  }
}
write(stringify(response));
