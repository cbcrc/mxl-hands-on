// SPDX-FileCopyrightText: 2026 CBC/Radio-Canada
// SPDX-License-Identifier: Apache-2.0
//
// v210 black-frame synthesis.
//
// v210 packs 6 10-bit components into four little-endian 32-bit words (16 bytes)
// laid out as:
//   word0: Cb0 | Y0<<10 | Cr0<<20
//   word1: Y1  | Cb2<<10 | Y2<<20
//   word2: Cr2 | Y3<<10  | Cb4<<20
//   word3: Y4  | Cr4<<10 | Y5<<20
// For a flat black frame every Y = 0x040 (64) and every Cb/Cr = 0x200 (512), so
// the 16-byte pattern is constant and can be tiled across the whole payload.

const BLACK_Y: u32 = 0x040;
const BLACK_C: u32 = 0x200;

/// Build a v210 black grain payload of exactly `size` bytes.
pub fn v210_black_grain(size: usize) -> Vec<u8> {
    let word = |a: u32, b: u32, c: u32| a | (b << 10) | (c << 20);
    let words = [
        word(BLACK_C, BLACK_Y, BLACK_C), // Cb0, Y0, Cr0
        word(BLACK_Y, BLACK_C, BLACK_Y), // Y1, Cb2, Y2
        word(BLACK_C, BLACK_Y, BLACK_C), // Cr2, Y3, Cb4
        word(BLACK_Y, BLACK_C, BLACK_Y), // Y4, Cr4, Y5
    ];
    let mut pattern = [0u8; 16];
    for (i, w) in words.iter().enumerate() {
        pattern[i * 4..i * 4 + 4].copy_from_slice(&w.to_le_bytes());
    }

    let mut buf = Vec::with_capacity(size);
    while buf.len() < size {
        let n = (size - buf.len()).min(16);
        buf.extend_from_slice(&pattern[..n]);
    }
    buf
}
