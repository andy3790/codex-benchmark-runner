// A trusted, deliberately small reference fixture for testing the GRADER.
// Never copied into a benchmark's starter directory or used as a model submission.
(function (root) {
  'use strict';
  function rng(seed) {
    let state = seed >>> 0;
    return () => {
      state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
      return state / 4294967296;
    };
  }
  function pairs(index, width, height) {
    const x = index % width, y = Math.floor(index / width);
    const result = [];
    if (y > 0) result.push([index - width, 1, 4]);
    if (x + 1 < width) result.push([index + 1, 2, 8]);
    if (y + 1 < height) result.push([index + width, 4, 1]);
    if (x > 0) result.push([index - 1, 8, 2]);
    return result;
  }
  function generateMaze(options) {
    const {width, height, algorithm, seed, loops = 0, weighted = false} = options;
    if (!Number.isInteger(width) || width < 1 || !Number.isInteger(height) || height < 1 ||
        !['dfs', 'prim'].includes(algorithm)) throw Error('Invalid generation options');
    const random = rng(seed), cells = Array.from({length: width * height}, () => ({walls: 15, cost: 1}));
    const seen = new Set([0]);
    function open(a, edge) {
      cells[a].walls &= ~edge[1];
      cells[edge[0]].walls &= ~edge[2];
    }
    if (algorithm === 'dfs') {
      const stack = [0];
      while (stack.length) {
        const a = stack[stack.length - 1];
        const edges = pairs(a, width, height).filter(edge => !seen.has(edge[0]));
        if (!edges.length) { stack.pop(); continue; }
        const edge = edges[Math.floor(random() * edges.length)];
        open(a, edge); seen.add(edge[0]); stack.push(edge[0]);
      }
    } else {
      const frontier = [];
      function add(a) { for (const edge of pairs(a, width, height)) if (!seen.has(edge[0])) frontier.push([a, edge]); }
      add(0);
      while (frontier.length) {
        const i = Math.floor(random() * frontier.length);
        const [a, edge] = frontier[i];
        frontier[i] = frontier[frontier.length - 1]; frontier.pop();
        if (seen.has(edge[0])) continue;
        open(a, edge); seen.add(edge[0]); add(edge[0]);
      }
    }
    const closed = [];
    for (let a = 0; a < cells.length; a++) {
      for (const edge of pairs(a, width, height)) if (a < edge[0] && cells[a].walls & edge[1]) closed.push([a, edge]);
    }
    const count = Math.floor(closed.length * loops);
    for (let i = 0; i < count; i++) {
      const j = i + Math.floor(random() * (closed.length - i));
      [closed[i], closed[j]] = [closed[j], closed[i]];
      open(closed[i][0], closed[i][1]);
    }
    if (weighted) for (const cell of cells) cell.cost = 1 + Math.floor(random() * 9);
    return {width, height, cells};
  }
  function solveMaze(maze, options) {
    const {algorithm, start, end} = options;
    const {width, height, cells} = maze;
    const n = cells.length;
    if (!['bfs', 'dijkstra', 'astar'].includes(algorithm)) throw Error('Invalid algorithm');
    const distance = Array(n).fill(Infinity), previous = Array(n).fill(-1), closed = new Set();
    const visitedOrder = [], queue = [start];
    distance[start] = 0;
    let head = 0;
    function heuristic(i) {
      return algorithm === 'astar' ? Math.abs(i % width - end % width) + Math.abs(Math.floor(i / width) - Math.floor(end / width)) : 0;
    }
    while (algorithm === 'bfs' ? head < queue.length : closed.size < n) {
      let a = -1;
      if (algorithm === 'bfs') {
        a = queue[head++];
      } else {
        let best = Infinity;
        for (let i = 0; i < n; i++) if (!closed.has(i) && distance[i] + heuristic(i) < best) {
          best = distance[i] + heuristic(i); a = i;
        }
        if (a < 0) break;
      }
      closed.add(a); visitedOrder.push(a);
      if (a === end) break;
      for (const [b, wall] of pairs(a, width, height)) {
        if (cells[a].walls & wall || closed.has(b)) continue;
        const candidate = distance[a] + (algorithm === 'bfs' ? 1 : cells[b].cost);
        if (candidate < distance[b]) {
          const first = !Number.isFinite(distance[b]);
          distance[b] = candidate; previous[b] = a;
          if (algorithm === 'bfs' && first) queue.push(b);
        }
      }
    }
    if (!Number.isFinite(distance[end])) return {found: false, path: [], visitedOrder, cost: null};
    const path = [];
    for (let i = end; i !== -1; i = previous[i]) path.push(i);
    path.reverse();
    const cost = path.slice(1).reduce((sum, i) => sum + cells[i].cost, 0);
    return {found: true, path, visitedOrder, cost};
  }
  const api = {generateMaze, solveMaze};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.MazeCore = api;
})(typeof window === 'undefined' ? null : window);
