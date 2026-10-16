# Text scanner for the browser (tesseract.js 7.0.0)

These files let the page read labels in the browser without loading anything from
other sites. They are copied unchanged from the packages below; the checksums let
anyone confirm that.

| File | From | License |
|---|---|---|
| `tesseract.esm.min.js`, `worker.min.js`, `worker.min.js.LICENSE.txt` | npm `tesseract.js@7.0.0`, `dist/` | Apache-2.0 (`LICENSE-tesseract.js.md`); bundled parts as listed in `worker.min.js.LICENSE.txt` |
| `tesseract-core-simd-lstm.wasm.js` | npm `tesseract.js-core@7.0.0` | Apache-2.0 (`LICENSE-tesseract.js-core.txt`) |
| `eng.traineddata.gz` | [tesseract-ocr/tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast) tag 4.1.0, `eng.traineddata`, compressed with `gzip -9 -n` | Apache-2.0 |

`eng.traineddata` is the model the server uses: the `tesseract-ocr-eng` 4.1.0 package
ships tessdata_fast 4.1.0 (checked on Ubuntu: the same SHA-256,
`7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2`, before compression).
Only the SIMD + LSTM build of the core is included; a browser without WebAssembly SIMD
sends the pictures to the server instead.

SHA-256 of the files here:

```
64871d76c75609fd5413b88a8171e2ef40deedd77d5875ba23df104b2d05eb29  tesseract.esm.min.js
576b7df7e3393e137e51849357c9adb53fe7ac1bb69bfa06cf3d61520f182c6d  worker.min.js
c58b46a4c796c0b8afccf77591d5b875b6896b45d402bbce8caa6f5362447b38  tesseract-core-simd-lstm.wasm.js
2a66ec904bc0e7657b27e200a874c01e1bc8a58b756cbbaa9afbae736fa50edc  eng.traineddata.gz
```

The folder name carries the version, so the server can tell browsers to keep these
files for a year (`app/main.py`). To update, put the new version in a new folder.
