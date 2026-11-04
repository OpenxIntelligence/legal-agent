# ui/vendor — vendored C-1 browser bundles

Built 2026-11-01 from pinned registry tarballs (integrity
hashes in `docs/collaboration/11-license-register.md`).
Reproduce with esbuild 0.28.2 (MIT, dev-time only):

    esbuild vEditor.js vDiff.js --bundle --splitting \
      --minify --format=esm --outdir=ui/vendor
    # then rename vEditor.js->editor.js, vDiff.js->diff.js
    esbuild vRender.js --bundle --minify --format=esm \
      --outfile=ui/vendor/render.js

Entry sources (kept out of the repo; /tmp bake-off only):
vEditor = EditorState + EditorView + keymap + lineNumbers
  + history + markdown() from codemirror 6.0.2 /
  @codemirror/lang-markdown 6.5.2; exports makeEditor,
  editorText, editorUndo, editorRedo.
vDiff = MergeView from @codemirror/merge 6.12.3;
  exports makeDiff.
vRender = marked 18.1.2 + dompurify 3.4.17 (Apache-2.0
  terms of the dual license); exports render.

Shipped-license proof: esbuild metafile attribution —
24 packages, 23 MIT + DOMPurify Apache-2.0. Per-package
LICENSE files under `licenses/<pkg>-<version>/`.
No runtime CDN, npm, or registry use; the console serves
these files from disk (GET /vendor/<name>, JS only).
