# Aviso de terceiros: referência oficial da Pearl

Partes de `src/job.rs` e `src/engine.rs` são porte das funções de
`zk-pow/src/ffi/mine.rs` do repositório `pearl-research-labs/pearl`, commit
`d7c517d267f10f5bcbbdc366f3e603942f9a5ab5`. O crate também depende de `zk-pow` e
`pearl-blake3` desse commit (via git), que têm suas próprias licenças; o `plonky2`
incluído no repositório oficial é MIT/Apache-2.0.

O código derivado mantém o aviso de licença original:

```text
ISC License

Copyright (c) 2025-2026 Pearl Research Labs
Copyright (c) 2015-2016 The Decred developers

Permission to use, copy, modify, and distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

```
