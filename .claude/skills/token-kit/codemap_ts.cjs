#!/usr/bin/env node
/**
 * TypeScript / JavaScript parser for codemap.py (token-kit). Reads a JSON list of file paths on stdin and prints
 * { path: { header, lines, syms: [{ kind, name, sig, start, end, note, exported, members }] } } as JSON.
 * Needs the `typescript` package: its path is in argv[2] (codemap.py finds it in the repository's node_modules).
 */
const fs = require('fs');
const ts = require(process.argv[2]);

const SIG_MAX = 100;
const NOTE_MAX = 90;
const squash = (s) => s.replace(/\s+/g, ' ').trim();
const cut = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s);

function noteAt(text, pos) {
  const ranges = ts.getLeadingCommentRanges(text, pos);
  if (!ranges || !ranges.length) return '';
  const r = ranges[ranges.length - 1];
  const body = text.slice(r.pos, r.end).replace(/^\/\*\*?|\*\/$/g, '').replace(/^\s*\/\/+ ?/gm, '').replace(/^\s*\* ?/gm, '');
  const line = body.split('\n').map((l) => l.trim()).find((l) => l && !l.startsWith('@')) || '';
  return cut(line, NOTE_MAX);
}

const hasMod = (n, k) => !!((ts.canHaveModifiers(n) && ts.getModifiers(n)) || []).some((m) => m.kind === k);
const params = (sf, fn) => cut(squash(fn.parameters.map((p) => p.name.getText(sf)).join(', ')), SIG_MAX);

function symbolsOf(file, text) {
  const kind = /\.tsx$/.test(file) ? ts.ScriptKind.TSX : /\.jsx$/.test(file) ? ts.ScriptKind.JSX : /\.(c|m)?js$/.test(file) ? ts.ScriptKind.JS : ts.ScriptKind.TS;
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, kind);
  const line = (pos) => sf.getLineAndCharacterOfPosition(pos).line + 1;
  const syms = [];
  const add = (node, k, name, sig, exported, extra) =>
    syms.push({ kind: k, name, sig, start: line(node.getStart(sf)), end: line(node.end), note: noteAt(text, node.pos), exported, ...extra });
  for (const st of sf.statements) {
    const exported = hasMod(st, ts.SyntaxKind.ExportKeyword);
    if (ts.isFunctionDeclaration(st) && st.name) {
      add(st, 'fn', st.name.text, `(${params(sf, st)})`, exported);
    } else if (ts.isClassDeclaration(st)) {
      const members = [];
      for (const m of st.members) {
        if (hasMod(m, ts.SyntaxKind.PrivateKeyword) || (m.name && ts.isPrivateIdentifier(m.name))) continue;
        if (ts.isMethodDeclaration(m) || ts.isConstructorDeclaration(m) || ts.isGetAccessor(m) || ts.isSetAccessor(m)) {
          const nm = ts.isConstructorDeclaration(m) ? 'constructor' : m.name.getText(sf);
          members.push({ name: nm, sig: `(${params(sf, m)})`, start: line(m.getStart(sf)), end: line(m.end) });
        }
      }
      add(st, 'class', st.name ? st.name.text : 'default', '', exported, { members });
    } else if (ts.isInterfaceDeclaration(st)) {
      add(st, 'interface', st.name.text, '', exported);
    } else if (ts.isTypeAliasDeclaration(st)) {
      add(st, 'type', st.name.text, '', exported);
    } else if (ts.isEnumDeclaration(st)) {
      add(st, 'enum', st.name.text, '', exported);
    } else if (ts.isVariableStatement(st)) {
      for (const d of st.declarationList.declarations) {
        if (!ts.isIdentifier(d.name)) continue;
        const init = d.initializer;
        const fn = init && (ts.isArrowFunction(init) || ts.isFunctionExpression(init)) ? init : null;
        if (fn) add(st, 'fn', d.name.text, `(${params(sf, fn)})`, exported);
        else if (exported) add(st, 'const', d.name.text, '', exported);
      }
    } else if (ts.isExportAssignment(st)) {
      add(st, 'default', 'default', '', true);
    } else if (ts.isExportDeclaration(st) && st.moduleSpecifier) {
      const names = st.exportClause && ts.isNamedExports(st.exportClause) ? st.exportClause.elements.map((e) => e.name.text).join(', ') : '*';
      add(st, 're-export', cut(names, SIG_MAX), ` from ${st.moduleSpecifier.text}`, true);
    }
  }
  return { syms, header: noteAt(text, 0), lines: text.split('\n').length };
}

const files = JSON.parse(fs.readFileSync(0, 'utf8'));
const out = {};
for (const f of files) {
  try {
    out[f] = symbolsOf(f, fs.readFileSync(f, 'utf8'));
  } catch (e) {
    out[f] = { syms: [], header: `(parse error: ${cut(String(e.message || e), 60)})`, lines: 0 };
  }
}
process.stdout.write(JSON.stringify(out));
