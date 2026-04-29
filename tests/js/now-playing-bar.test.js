import { describe, it, expect } from 'vitest';
import {
  parseAss,
  parseAssTime,
  findActiveLineIdx,
} from '../../pikaraoke/static/js/lyrics-parse.js';

describe('parseAssTime', () => {
  it('parses H:MM:SS.cc', () => {
    expect(parseAssTime('0:01:23.45')).toBeCloseTo(83.45);
    expect(parseAssTime('1:00:00.00')).toBeCloseTo(3600);
  });
  it('returns NaN on malformed input', () => {
    expect(parseAssTime('garbage')).toBeNaN();
    expect(parseAssTime('')).toBeNaN();
    expect(parseAssTime(null)).toBeNaN();
  });
});

describe('parseAss', () => {
  it('returns empty array for empty input', () => {
    expect(parseAss('')).toEqual([]);
    expect(parseAss(null)).toEqual([]);
  });

  it('skips Comment lines', () => {
    const ass = [
      '[Events]',
      'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text',
      'Comment: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,hidden',
      'Dialogue: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,visible',
    ].join('\n');
    const lines = parseAss(ass);
    expect(lines).toHaveLength(1);
    expect(lines[0].text).toBe('visible');
  });

  it('strips override blocks', () => {
    const ass = 'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\an8\\fad(100,100)}hello';
    expect(parseAss(ass)[0].text).toBe('hello');
  });

  it('extracts \\kf word timing', () => {
    const ass = 'Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\kf50}hello {\\kf50}world';
    const lines = parseAss(ass);
    expect(lines[0].words).toHaveLength(2);
    expect(lines[0].words[0].text).toBe('hello ');
    expect(lines[0].words[1].text).toBe('world');
    expect(lines[0].words[0].end).toBeCloseTo(1.5);
    expect(lines[0].words[1].end).toBeCloseTo(2.0);
    expect(lines[0].text).toBe('hello world');
  });

  it('replaces \\N with space', () => {
    const ass = 'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,line one\\Nline two';
    expect(parseAss(ass)[0].text).toBe('line one line two');
  });

  it('skips empty-after-strip lines', () => {
    const ass = 'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\an8}';
    expect(parseAss(ass)).toHaveLength(0);
  });

  it('preserves Polish characters', () => {
    const ass = 'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Cześć ą ę ó';
    expect(parseAss(ass)[0].text).toBe('Cześć ą ę ó');
  });

  it('preserves commas in text field', () => {
    const ass = 'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,a,b,c';
    expect(parseAss(ass)[0].text).toBe('a,b,c');
  });

  it('sorts lines by start', () => {
    const ass = [
      'Dialogue: 0,0:00:05.00,0:00:06.00,Default,,0,0,0,,second',
      'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,first',
    ].join('\n');
    const lines = parseAss(ass);
    expect(lines[0].text).toBe('first');
    expect(lines[1].text).toBe('second');
  });

  it('has empty words array when no \\kf chunks', () => {
    const ass = 'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,plain text';
    const lines = parseAss(ass);
    expect(lines[0].words).toEqual([]);
  });

  it('extracts only the hot segment from a rolling-window body', () => {
    // lyrics.py emits past + current + future glued by \N; current is
    // wrapped in {\alpha&H00&\b1}, past/future in {\alpha&H80&\b0}.
    const body =
      '{\\an5}{\\alpha&H80&\\b0}past one'
      + '\\N{\\alpha&H80&\\b0}past two'
      + '\\N{\\alpha&H00&\\b1}current line'
      + '\\N{\\alpha&H80&\\b0}future one'
      + '\\N{\\alpha&H80&\\b0}future two';
    const ass = `Dialogue: 0,0:00:10.00,0:00:12.00,Default,,0,0,0,,${body}`;
    const lines = parseAss(ass);
    expect(lines).toHaveLength(1);
    expect(lines[0].text).toBe('current line');
  });

  it('keeps the first character when first \\kf block has a pulse tag', () => {
    // lyrics.py's _k_token injects {\t(...)} into the first chunk per
    // word: {\t(0,300,\fscx120\fscy120)\kf5}T{\kf5}o{\kf5}g... The first
    // chunk's override block does not start with \kf, so a too-strict
    // regex would skip it and drop the leading char of every word.
    const body = '{\\alpha&H00&\\b1}'
      + '{\\t(0,300,\\fscx120\\fscy120)\\kf5}T{\\kf5}o{\\kf5}g{\\kf5}e{\\kf5}t{\\kf5}h{\\kf5}e{\\kf5}r '
      + '{\\t(0,200,\\fscx120\\fscy120)\\kf3}w{\\kf3}e';
    const ass = `Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,${body}`;
    const lines = parseAss(ass);
    expect(lines).toHaveLength(1);
    expect(lines[0].text).toBe('Together we');
    // Each per-char part is one chunk; spaces become their own.
    expect(lines[0].words[0].text).toBe('T');
    expect(lines[0].words[0].end).toBeCloseTo(1.05);
  });

  it('extracts \\kf chunks from inside the hot segment', () => {
    const body =
      '{\\an5}{\\alpha&H80&\\b0}past one'
      + '\\N{\\alpha&H00&\\b1}{\\kf50}hello {\\kf50}world'
      + '\\N{\\alpha&H80&\\b0}future one';
    const ass = `Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,${body}`;
    const lines = parseAss(ass);
    expect(lines[0].text).toBe('hello world');
    expect(lines[0].words).toHaveLength(2);
    expect(lines[0].words[0].text).toBe('hello ');
    expect(lines[0].words[1].text).toBe('world');
  });

  it('produces one entry per LRC line for a 3-line rolling-window file', () => {
    // Three Dialogues, each with the same three lyric lines, but a
    // different one marked as hot. The parser should return three
    // unique entries (one per LRC line), not nine duplicates.
    const dlg = (start, end, hotText, others) => {
      const segs = others.slice(0, 1).map((t) => `{\\alpha&H80&\\b0}${t}`);
      segs.push(`{\\alpha&H00&\\b1}${hotText}`);
      segs.push(...others.slice(1).map((t) => `{\\alpha&H80&\\b0}${t}`));
      return `Dialogue: 0,${start},${end},Default,,0,0,0,,{\\an5}` + segs.join('\\N');
    };
    const ass = [
      dlg('0:00:01.00', '0:00:02.00', 'first', ['second']),
      dlg('0:00:02.00', '0:00:03.00', 'second', ['first', 'third']),
      dlg('0:00:03.00', '0:00:04.00', 'third', ['second']),
    ].join('\n');
    const lines = parseAss(ass);
    expect(lines).toHaveLength(3);
    expect(lines.map((l) => l.text)).toEqual(['first', 'second', 'third']);
  });
});

describe('findActiveLineIdx', () => {
  const lines = [
    { start: 1, end: 2, text: 'a' },
    { start: 3, end: 4, text: 'b' },
    { start: 5, end: 6, text: 'c' },
  ];

  it('returns -1 before first line', () => {
    expect(findActiveLineIdx(lines, 0.5)).toBe(-1);
  });
  it('returns -1 for empty list', () => {
    expect(findActiveLineIdx([], 1)).toBe(-1);
  });
  it('finds line by inclusion', () => {
    expect(findActiveLineIdx(lines, 1.5)).toBe(0);
    expect(findActiveLineIdx(lines, 3.5)).toBe(1);
  });
  it('keeps last entered line during gap', () => {
    expect(findActiveLineIdx(lines, 2.5)).toBe(0);
  });
  it('keeps last line past final end', () => {
    expect(findActiveLineIdx(lines, 100)).toBe(2);
  });
  it('latches at the boundary', () => {
    expect(findActiveLineIdx(lines, 1.0)).toBe(0);
    expect(findActiveLineIdx(lines, 5.0)).toBe(2);
  });
});
