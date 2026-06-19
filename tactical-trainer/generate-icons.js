#!/usr/bin/env node
// Run: node generate-icons.js
// Requires: npm install canvas
// Or use the inline base64 icons already embedded in the HTML.
const { createCanvas } = require('canvas');
const fs = require('fs');

function makeIcon(size) {
  const canvas = createCanvas(size, size);
  const ctx = canvas.getContext('2d');
  const r = size * 0.15;

  // Background
  ctx.fillStyle = '#0E7490';
  ctx.beginPath();
  ctx.roundRect(0, 0, size, size, r);
  ctx.fill();

  // White T
  ctx.fillStyle = '#ffffff';
  const sw = size * 0.55;
  const sh = size * 0.1;
  const sv = size * 0.42;
  const svw = size * 0.1;
  const pad = (size - sw) / 2;
  const top = size * 0.22;
  // Horizontal bar
  ctx.fillRect(pad, top, sw, sh);
  // Vertical bar
  ctx.fillRect((size - svw) / 2, top, svw, sv);

  return canvas.toBuffer('image/png');
}

fs.writeFileSync('icon-192.png', makeIcon(192));
fs.writeFileSync('icon-512.png', makeIcon(512));
console.log('Icons generated.');
