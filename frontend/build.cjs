const esbuild = require('esbuild');
const fs = require('node:fs');
const path = require('node:path');
const out = path.join(__dirname, 'dist');
fs.mkdirSync(out, {recursive:true});
fs.copyFileSync(path.join(__dirname,'index.html'), path.join(out,'index.html'));
esbuild.buildSync({entryPoints:[path.join(__dirname,'src/main.tsx')], bundle:true,
  outfile:path.join(out,'app.js'), minify:true, sourcemap:true, target:'es2022',
  define:{'process.env.NODE_ENV':'"production"'}, legalComments:'eof'});
