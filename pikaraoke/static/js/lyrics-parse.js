/**
 * ASS subtitle parsing helpers — pure functions (no DOM, no globals).
 *
 * Mirrors `_format_ass_time` in pikaraoke/lib/lyrics.py: ASS times are
 * `H:MM:SS.cc` with cs = centiseconds. Karaoke fill chunks are encoded
 * as {\kf<centiseconds>}word — duration applies to the chunk text that
 * follows, and chunk durations are cumulative within the dialogue line.
 *
 * Also assigned to window.PK.LyricsParse so the now-playing-bar IIFE
 * can consume it without going module-mode itself.
 */

export function parseAssTime(s) {
  if (typeof s !== 'string') return NaN;
  const m = s.trim().match(/^(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)$/);
  if (!m) return NaN;
  const h = Number(m[1]);
  const mm = Number(m[2]);
  const ss = Number(m[3]);
  return h * 3600 + mm * 60 + ss;
}

const OVERRIDE_BLOCK_RE = /\{[^}]*\}/g;
// Match a `\kf` chunk inside any override block — the block may also
// hold a `\t(...)` pulse tag (lyrics.py injects one into the first
// chunk per word). [^}]* before/after the `\kf` lets the duration sit
// anywhere within the block, so anchoring on `{\kf` would miss those
// pulse-tagged blocks and drop the first char of every word.
const KF_CHUNK_RE = /\{[^}]*?\\kf(\d+)[^}]*\}([^{]*)/g;
// PiKaraoke's lyrics.py wraps the "current" segment of each rolling-window
// Dialogue body with {\alpha&H00&\b1}; past/future segments use \b0. We
// match \b1 as the hot marker — appears only in the highlighted segment.
const HOT_MARKER_RE = /\\b1\b/;

export function parseAss(text) {
  if (!text || typeof text !== 'string') return [];
  const lines = [];
  for (const rawLine of text.split(/\r?\n/)) {
    if (!rawLine.startsWith('Dialogue:')) continue;
    // ASS Dialogue is: "Dialogue: layer,start,end,style,name,ml,mr,mv,effect,text"
    // Split with limit so commas inside the text field are preserved.
    const body = rawLine.slice('Dialogue:'.length).trimStart();
    const fields = splitCsvMaxFields(body, 10);
    if (fields.length < 10) continue;
    const start = parseAssTime(fields[1]);
    const end = parseAssTime(fields[2]);
    if (!isFinite(start) || !isFinite(end)) continue;

    // Extract the highlighted ("hot") segment from the rolling-window
    // body. lyrics.py emits up to 5 segments per Dialogue separated by
    // \N — past lines (dimmed), current (hot), future (dimmed). For the
    // mobile panel we want a flat list of unique LRC entries, so we
    // discard the dimmed context and keep only the hot segment.
    const raw = pickHotSegment(fields[9]);

    // Try \kf word-fill chunks first; fall back to plain text otherwise.
    const words = [];
    let cursor = start;
    let stripped = '';
    let any = false;
    KF_CHUNK_RE.lastIndex = 0;
    let m;
    while ((m = KF_CHUNK_RE.exec(raw)) !== null) {
      any = true;
      const dur = parseInt(m[1], 10) / 100;
      const txt = m[2];
      if (txt) {
        words.push({ text: txt, start: cursor, end: cursor + dur });
        stripped += txt;
      }
      cursor += dur;
    }

    let displayText;
    if (any) {
      displayText = stripped.trim();
    } else {
      displayText = raw.replace(OVERRIDE_BLOCK_RE, '').trim();
    }
    if (!displayText) continue;
    lines.push({ start, end, text: displayText, words });
  }
  lines.sort((a, b) => a.start - b.start);
  return lines;
}

// Find the highlighted segment in a rolling-window body. Falls back to
// the entire body when no hot marker is found (single-line ASS variants
// or non-PiKaraoke files), with \N normalised to a space.
function pickHotSegment(body) {
  if (!body) return '';
  const segments = body.split(/\\N/g);
  if (segments.length <= 1) return body.replace(/\\N/g, ' ');
  for (const s of segments) {
    if (HOT_MARKER_RE.test(s)) return s;
  }
  return body.replace(/\\N/g, ' ');
}

// Linear scan returning the index of the last entry whose start <= t.
// Returns -1 if t precedes the first line. Lines are assumed sorted by start.
export function findActiveLineIdx(lines, t) {
  if (!lines || !lines.length) return -1;
  if (t < lines[0].start) return -1;
  let idx = 0;
  for (let i = 1; i < lines.length; i++) {
    if (lines[i].start <= t) idx = i;
    else break;
  }
  return idx;
}

// Split a CSV body into at most `max` fields; the last field keeps any
// remaining commas verbatim (mirrors Python str.split(',', max-1)).
function splitCsvMaxFields(body, max) {
  const out = [];
  let i = 0;
  for (let n = 0; n < max - 1; n++) {
    const j = body.indexOf(',', i);
    if (j < 0) {
      out.push(body.slice(i));
      return out;
    }
    out.push(body.slice(i, j));
    i = j + 1;
  }
  out.push(body.slice(i));
  return out;
}

if (typeof window !== 'undefined') {
  window.PK = window.PK || {};
  window.PK.LyricsParse = { parseAss, parseAssTime, findActiveLineIdx };
}
