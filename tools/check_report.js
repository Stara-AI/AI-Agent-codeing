// Execute the generated offline page's actual script against a minimal DOM.
// This checks behavior, not browser rendering or real Linux data.
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const file = process.argv[2];
if (!file) throw new Error('usage: node tools/check_report.js REPORT/index.html');
const page = fs.readFileSync(file, 'utf8');
const code = page.match(/<script>([\s\S]*?)<\/script>/);
assert.ok(code, 'page must include interaction script');
const viewBox = page.match(/viewBox="([^"]+)"/)[1];
const attributes = {viewBox};
const svg = {getAttribute: name => attributes[name], setAttribute: (name, value) => attributes[name] = value};
const nodes = JSON.parse(code[1].match(/const nodes=([\s\S]*?);\s*const svg/)[1]);
const shape = () => ({attributes: {}, setAttribute(name, value) {this.attributes[name] = Number(value);}});
const groups = [...page.matchAll(/<g data-id="(\d+)"[^>]*>/g)].map(m => {
  const rect=shape(), text=shape();
  return {dataset: {id: m[1]}, hit: false, style: {}, rect, text,
    querySelector(name) {return name === 'rect' ? rect : text;},
    classList: {toggle(name, active) {groups[Number(m[1])].hit = active;}}
  };
});
const elements = {reset: {}, search: {value: ''}, matches: {}, 'frame-detail': {}, 'focus-label': {}};
const document = {
  getElementById: name => elements[name],
  querySelector: name => { assert.equal(name, 'svg'); return svg; },
  querySelectorAll: name => { assert.equal(name, 'g[data-id]'); return groups; }
};
vm.runInNewContext(code[1], {document}, {timeout: 1000});
assert.match(page, /SYNTHETIC/);
const leaf = nodes.findIndex(n => n.name === 'hot_leaf');
assert.ok(leaf > 0, 'historical hotspot must be rendered');
groups[leaf].onclick();
assert.equal(attributes.viewBox, viewBox, 'zoom must preserve aspect ratio and font scale');
assert.equal(groups[leaf].rect.attributes.x, 0, 'selected frame must start at left');
assert.equal(groups[leaf].rect.attributes.width, 1200, 'selected frame must fill viewport');
const prefix=(a,b)=>a.length<=b.length&&a.every((part,i)=>part===b[i]);
nodes.forEach((n,i)=>{
  const ancestor=prefix(n.path,nodes[leaf].path), descendant=prefix(nodes[leaf].path,n.path);
  assert.equal(groups[i].style.display === 'none', !ancestor&&!descendant, 'unrelated paths must be hidden');
  if(ancestor) assert.equal(groups[i].rect.attributes.width, 1200, 'ancestors must span selected range');
  if(descendant&&!ancestor) assert.ok(Math.abs(groups[i].rect.attributes.width-n.width/nodes[leaf].width*1200)<1e-6, 'descendant weight must be relative to selected frame');
});
assert.match(elements['frame-detail'].textContent, /hot_leaf/);
elements.reset.onclick();
assert.equal(attributes.viewBox, viewBox, 'reset must restore view');
nodes.forEach((n,i)=>{
  assert.equal(groups[i].style.display, '', 'reset must reveal all paths');
  assert.ok(Math.abs(groups[i].rect.attributes.width-n.width*1200)<1e-6, 'reset must restore widths');
});
elements.search.value = 'HOT_LEAF'; elements.search.oninput();
assert.equal(groups.filter(g => g.hit).length, nodes.filter(n=>n.name==='hot_leaf').length, 'search must be case insensitive');
elements.search.value = ''; elements.search.oninput();
assert.equal(groups.filter(g => g.hit).length, 0, 'clearing search must remove highlight');
console.log(JSON.stringify({check: 'generated report JS', source_banner: true, zoom: true, stable_aspect_ratio: true, reset: true, search: true, nodes: groups.length}, null, 2));
