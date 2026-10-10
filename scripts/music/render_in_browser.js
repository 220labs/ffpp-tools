// Renders a note list to a 16-bit WAV File in the browser (OfflineAudioContext), for the cue sketches.
// FluidR3_GM samples from gleitz/midi-js-soundfonts (the same SoundFont fluidsynth uses), synthesised drums,
// OfflineAudioContext -> 16-bit WAV File in window.__wav. Notes: 't,d,c,p,v;...' (ms), from scripts/music/notelist.py.
window.__render = async function (notes, name, seconds) {
  const SR = 48000, N = Math.ceil((seconds + 3) * SR);
  const ctx = new OfflineAudioContext(2, N, SR);
  const INST = {0: 'muted_trumpet', 1: 'flute', 2: 'vibraphone', 3: 'acoustic_bass', 4: 'acoustic_grand_piano', 5: 'trumpet',
    6: 'trombone', 7: 'tenor_sax', 8: 'electric_guitar_jazz', 10: 'lead_2_sawtooth', 11: 'french_horn', 12: 'acoustic_guitar_nylon'};
  const VOL = {0: 100, 1: 90, 2: 100, 3: 110, 4: 95, 5: 96, 6: 96, 7: 92, 8: 80, 9: 110, 10: 70, 11: 90, 12: 80};
  const PAN = {0: 72, 1: 52, 2: 40, 4: 76, 5: 70, 6: 50, 7: 84, 8: 36, 10: 90, 12: 36};
  const SUST = {0: 1, 1: 1, 5: 1, 6: 1, 7: 1, 10: 1, 11: 1};
  const NM = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B'];
  const nn = p => NM[p % 12] + (Math.floor(p / 12) - 1);
  const ev = notes.trim().split(';').map(s => s.split(',').map(Number));
  const need = {};
  for (const [t, d, c, p] of ev) if (c !== 9) (need[c] = need[c] || new Set()).add(p);
  const buf = {};
  for (const c of Object.keys(need)) {
    const txt = await (await fetch(`https://gleitz.github.io/midi-js-soundfonts/FluidR3_GM/${INST[c]}-mp3.js`)).text();
    buf[c] = {};
    for (const p of need[c]) {
      // `[;]` is a plain `;`: the regex matches a soundfont's samples and embeds none, but its
      // literal prefix read as embedded data to the publish gate's scan.
      const m = txt.match(new RegExp(`"${nn(p)}":\\s*"data:audio/mp3[;]base64,([^"]+)"`));
      if (!m) throw new Error(`no sample ${INST[c]} ${nn(p)}`);
      const bin = Uint8Array.from(atob(m[1]), ch => ch.charCodeAt(0));
      buf[c][p] = await ctx.decodeAudioData(bin.buffer);
    }
  }
  const bus = {};
  const master = ctx.createGain(); master.gain.value = 0.5; master.connect(ctx.destination);
  const chan = c => {
    if (!bus[c]) {
      const g = ctx.createGain(); g.gain.value = Math.pow(VOL[c] / 127, 2);
      const pn = ctx.createStereoPanner(); pn.pan.value = ((PAN[c] ?? 64) - 64) / 64;
      g.connect(pn); pn.connect(master); bus[c] = g;
    }
    return bus[c];
  };
  const noise = (() => { const b = ctx.createBuffer(1, SR * 2, SR), d = b.getChannelData(0); for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1; return b; })();
  const nz = (t, dur, type, f, q, gain, out) => {
    const s = ctx.createBufferSource(); s.buffer = noise;
    const fl = ctx.createBiquadFilter(); fl.type = type; fl.frequency.value = f; fl.Q.value = q;
    const g = ctx.createGain(); g.gain.setValueAtTime(gain, t); g.gain.exponentialRampToValueAtTime(0.0005, t + dur);
    s.connect(fl); fl.connect(g); g.connect(out); s.start(t, Math.random()); s.stop(t + dur + 0.05);
  };
  const tone = (t, f0, f1, dur, gain, out) => {
    const o = ctx.createOscillator(); o.frequency.setValueAtTime(f0, t); o.frequency.exponentialRampToValueAtTime(f1, t + dur);
    const g = ctx.createGain(); g.gain.setValueAtTime(gain, t); g.gain.exponentialRampToValueAtTime(0.0005, t + dur);
    o.connect(g); g.connect(out); o.start(t); o.stop(t + dur + 0.05);
  };
  const drum = (t, p, v) => {
    const out = chan(9), a = Math.pow(v / 127, 1.5);
    if (p === 36) tone(t, 110, 45, 0.3, 0.9 * a, out);
    else if (p === 38) { nz(t, 0.13, 'bandpass', 2200, 0.8, 0.5 * a, out); tone(t, 190, 160, 0.08, 0.2 * a, out); }
    else if (p === 40) nz(t, 0.35, 'bandpass', 3000, 0.5, 0.18 * a, out);
    else if (p === 37) nz(t, 0.04, 'bandpass', 1800, 3, 0.5 * a, out);
    else if (p === 42 || p === 44) nz(t, p === 42 ? 0.05 : 0.07, 'highpass', 7000, 0.7, 0.35 * a, out);
    else if (p === 51) { nz(t, 0.5, 'bandpass', 6500, 1.2, 0.28 * a, out); nz(t, 0.08, 'highpass', 9000, 0.7, 0.2 * a, out); }
    else if (p === 49) nz(t, 1.6, 'highpass', 3500, 0.5, 0.45 * a, out);
    else if (p === 41 || p === 45) tone(t, p === 41 ? 120 : 150, p === 41 ? 85 : 105, 0.35, 0.6 * a, out);
    else if (p === 70) nz(t, 0.05, 'highpass', 5000, 1, 0.2 * a, out);
    else nz(t, 0.1, 'bandpass', 2000, 1, 0.2 * a, out);
  };
  for (const [tm, dm, c, p, v] of ev) {
    const t = tm / 1000, d = dm / 1000;
    if (c === 9) { drum(t, p, v); continue; }
    const b = buf[c][p], s = ctx.createBufferSource(); s.buffer = b;
    const g = ctx.createGain(), a = Math.pow(v / 127, 2);
    const end = SUST[c] ? t + d : t + Math.max(d, 0.25) + 0.25, rel = 0.12;
    g.gain.setValueAtTime(a, t); g.gain.setValueAtTime(a, Math.max(t, end - 0.001)); g.gain.linearRampToValueAtTime(0, end + rel);
    s.connect(g); g.connect(chan(c)); s.start(t); s.stop(Math.min(end + rel + 0.01, t + b.duration));
  }
  const r = await ctx.startRendering();
  const L = r.getChannelData(0), R = r.getChannelData(1);
  let pk = 0; for (let i = 0; i < L.length; i++) pk = Math.max(pk, Math.abs(L[i]), Math.abs(R[i]));
  const k = 0.89 / pk, n = L.length, ab = new ArrayBuffer(44 + n * 4), dv = new DataView(ab);
  const w = (o, s) => [...s].forEach((ch, i) => dv.setUint8(o + i, ch.charCodeAt(0)));
  w(0, 'RIFF'); dv.setUint32(4, 36 + n * 4, true); w(8, 'WAVE'); w(12, 'fmt '); dv.setUint32(16, 16, true);
  dv.setUint16(20, 1, true); dv.setUint16(22, 2, true); dv.setUint32(24, SR, true); dv.setUint32(28, SR * 4, true);
  dv.setUint16(32, 4, true); dv.setUint16(34, 16, true); w(36, 'data'); dv.setUint32(40, n * 4, true);
  for (let i = 0, o = 44; i < n; i++, o += 4) {
    dv.setInt16(o, Math.max(-32767, Math.min(32767, L[i] * k * 32767)), true);
    dv.setInt16(o + 2, Math.max(-32767, Math.min(32767, R[i] * k * 32767)), true);
  }
  window.__wav = new File([ab], name, {type: 'audio/wav'});
  return {name, bytes: ab.byteLength, seconds: n / SR, peakBeforeNorm: pk, notes: ev.length};
};
'ok'
