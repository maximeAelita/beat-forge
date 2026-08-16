/* BeatForge audio engine — pure Web Audio synthesis, no samples.
 *
 * The same voice code runs live (AudioContext) and offline (OfflineAudioContext),
 * so what you export is exactly what you heard.
 */
(function (global) {
  'use strict';

  var LOOKAHEAD = 0.12;      // seconds of audio scheduled ahead
  var TICK_MS = 25;

  function mtof(m) { return 440 * Math.pow(2, (m - 69) / 12); }

  /* Read a synth parameter, falling back only when it is genuinely absent.
     Writing the fallback as a logical-or looks harmless, but zero is falsy: a
     drive of 0 silently became 0.3, so the sub808 could never be made clean.
     The same trap applied to every decay and cutoff default below. */
  function num(v, dflt) {
    return (v == null || v !== v) ? dflt : v;
  }

  /* Seeded PRNG (mulberry32). Everything random in the engine draws from this
     stream -- noise playback rate, the reverb impulse, step probability -- and
     the stream is reseeded from the project at the top of every render and
     every play, so the same project and seed always render the same audio.
     Renders match to within 1 LSB of 16-bit; the last bit is float rounding
     inside the browser's graph, not our randomness. */
  var rngState = 1;
  function seedRng(seed) {
    rngState = (seed == null ? 1 : seed >>> 0) || 1;
  }
  function rnd() {
    rngState = (rngState + 0x6D2B79F5) >>> 0;
    var t = rngState;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  // ---- shared per-context resources ---------------------------------------
  var noiseCache = new WeakMap();
  function noiseBuffer(ctx) {
    var buf = noiseCache.get(ctx);
    if (buf) return buf;
    var len = Math.floor(ctx.sampleRate * 2);
    buf = ctx.createBuffer(1, len, ctx.sampleRate);
    var d = buf.getChannelData(0);
    var seed = 12345;
    for (var i = 0; i < len; i++) {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      d[i] = (seed / 0x3fffffff) - 1;
    }
    noiseCache.set(ctx, buf);
    return buf;
  }

  function noise(ctx, dur) {
    var src = ctx.createBufferSource();
    src.buffer = noiseBuffer(ctx);
    src.loop = true;
    src.playbackRate.value = 0.85 + rnd() * 0.3;
    return src;
  }

  var curveCache = {};
  function driveCurve(amount) {
    var k = Math.max(0, Math.min(1, amount));
    var key = k.toFixed(2);
    if (curveCache[key]) return curveCache[key];
    // Odd length so the centre sample sits exactly on x = 0 and silent input
    // stays silent — otherwise every idle shaper adds a DC offset.
    var n = 1025, curve = new Float32Array(n), drive = 1 + k * 40;
    for (var i = 0; i < n; i++) {
      var x = (i * 2) / (n - 1) - 1;
      curve[i] = Math.tanh(x * drive) / Math.tanh(drive === 1 ? 1 : drive) * (1 - k * 0.25);
    }
    curveCache[key] = curve;
    return curve;
  }

  function shaper(ctx, amount) {
    var ws = ctx.createWaveShaper();
    ws.curve = driveCurve(amount);
    ws.oversample = '2x';
    return ws;
  }

  function env(ctx, node, t, attack, decay, peak) {
    node.gain.cancelScheduledValues(t);
    node.gain.setValueAtTime(0.0001, t);
    node.gain.exponentialRampToValueAtTime(Math.max(0.0002, peak), t + attack);
    node.gain.exponentialRampToValueAtTime(0.0001, t + attack + decay);
  }

  /* Master safety limiter: transparent below -4dB, asymptotic above, and hard
     inputs past 1.0 clamp to the curve endpoint instead of clipping the file. */
  var limiterCurve = null;
  function softLimit() {
    if (limiterCurve) return limiterCurve;
    var n = 2049, c = new Float32Array(n), t = 0.6, ceil = 0.95;
    for (var i = 0; i < n; i++) {
      var x = (i * 2) / (n - 1) - 1;
      var a = Math.abs(x), y;
      if (a <= t) y = a;
      else y = t + (ceil - t) * Math.tanh((a - t) / (ceil - t));
      c[i] = x < 0 ? -y : y;
    }
    limiterCurve = c;
    return c;
  }

  function impulse(ctx, seconds, decay) {
    var rate = ctx.sampleRate, len = Math.floor(rate * seconds);
    var buf = ctx.createBuffer(2, len, rate);
    for (var c = 0; c < 2; c++) {
      var d = buf.getChannelData(c);
      for (var i = 0; i < len; i++) {
        d[i] = (rnd() * 2 - 1) * Math.pow(1 - i / len, decay);
      }
    }
    return buf;
  }

  // ---- voices --------------------------------------------------------------
  // Every voice: (ctx, out, t, p, vel, note) where p is the track's params.

  var Voices = {
    kick: function (ctx, out, t, p, vel, note) {
      var base = note != null ? mtof(note) : p.tune;
      var osc = ctx.createOscillator();
      osc.type = 'sine';
      var g = ctx.createGain();
      var top = base * (1 + p.punch * 7);
      osc.frequency.setValueAtTime(top, t);
      osc.frequency.exponentialRampToValueAtTime(Math.max(20, base), t + 0.03 + p.punch * 0.05);
      env(ctx, g, t, 0.002, p.decay, vel);
      var d = shaper(ctx, p.drive);
      osc.connect(g); g.connect(d); d.connect(out);
      osc.start(t); osc.stop(t + p.decay + 0.1);

      if (p.click > 0.01) {
        var n = noise(ctx, 0.02), nf = ctx.createBiquadFilter(), ng = ctx.createGain();
        nf.type = 'bandpass'; nf.frequency.value = 1800; nf.Q.value = 1.2;
        env(ctx, ng, t, 0.001, 0.02 + p.click * 0.02, vel * p.click * 0.6);
        n.connect(nf); nf.connect(ng); ng.connect(out);
        n.start(t); n.stop(t + 0.08);
      }
    },

    kick808: function (ctx, out, t, p, vel, note) {
      Voices.kick(ctx, out, t, p, vel, note);
    },

    snare: function (ctx, out, t, p, vel, note) {
      var base = note != null ? mtof(note) : p.tune;
      var mix = ctx.createGain(); mix.gain.value = 1;
      var d = shaper(ctx, p.drive);
      mix.connect(d); d.connect(out);

      [1, 1.48].forEach(function (r, i) {
        var o = ctx.createOscillator(), g = ctx.createGain();
        o.type = 'triangle';
        o.frequency.setValueAtTime(base * r, t);
        o.frequency.exponentialRampToValueAtTime(base * r * 0.7, t + p.decay);
        env(ctx, g, t, 0.001, p.decay * 0.8, vel * p.tone * (i ? 0.5 : 1));
        o.connect(g); g.connect(mix);
        o.start(t); o.stop(t + p.decay + 0.05);
      });

      var n = noise(ctx), f = ctx.createBiquadFilter(), ng = ctx.createGain();
      f.type = 'highpass'; f.frequency.value = 900 + p.snap * 2600; f.Q.value = 0.7;
      env(ctx, ng, t, 0.001, p.decay * (0.7 + p.snap * 0.8), vel * (0.4 + p.snap * 0.7));
      n.connect(f); f.connect(ng); ng.connect(mix);
      n.start(t); n.stop(t + p.decay * 2 + 0.1);
    },

    rimshot: function (ctx, out, t, p, vel, note) {
      var base = note != null ? mtof(note) : p.tune;
      var o = ctx.createOscillator(), g = ctx.createGain(), f = ctx.createBiquadFilter();
      o.type = 'square'; o.frequency.value = base;
      f.type = 'bandpass'; f.frequency.value = base * 2.2; f.Q.value = 6;
      env(ctx, g, t, 0.0005, p.decay, vel);
      o.connect(f); f.connect(g); g.connect(out);
      o.start(t); o.stop(t + p.decay + 0.03);
      var n = noise(ctx), nf = ctx.createBiquadFilter(), ng = ctx.createGain();
      nf.type = 'highpass'; nf.frequency.value = 2000;
      env(ctx, ng, t, 0.0005, p.decay * 0.6, vel * p.snap * 0.5);
      n.connect(nf); nf.connect(ng); ng.connect(out);
      n.start(t); n.stop(t + p.decay + 0.05);
    },

    clap: function (ctx, out, t, p, vel, note) {
      var f = ctx.createBiquadFilter();
      f.type = 'bandpass'; f.frequency.value = (note != null ? mtof(note) * 4 : p.tune); f.Q.value = 1.1 + p.tone * 2;
      var d = shaper(ctx, p.drive);
      f.connect(d); d.connect(out);
      var gap = 0.006 + p.spread * 0.014;
      for (var i = 0; i < 3; i++) {
        var n = noise(ctx), g = ctx.createGain();
        env(ctx, g, t + i * gap, 0.0005, 0.016, vel * (0.85 - i * 0.15));
        n.connect(g); g.connect(f);
        n.start(t + i * gap); n.stop(t + i * gap + 0.05);
      }
      var tailN = noise(ctx), tg = ctx.createGain();
      env(ctx, tg, t + 3 * gap, 0.001, p.decay, vel * 0.7);
      tailN.connect(tg); tg.connect(f);
      tailN.start(t + 3 * gap); tailN.stop(t + 3 * gap + p.decay + 0.1);
    },

    _metal: function (ctx, out, t, p, vel, note, decay, hp) {
      var base = (note != null ? mtof(note) / 8 : p.tune / 100) * 5;
      var ratios = [2, 3, 4.16, 5.43, 6.79, 8.21];
      var bp = ctx.createBiquadFilter(); bp.type = 'bandpass';
      bp.frequency.value = p.tune; bp.Q.value = 0.9;
      var hpf = ctx.createBiquadFilter(); hpf.type = 'highpass'; hpf.frequency.value = hp;
      var g = ctx.createGain();
      env(ctx, g, t, 0.001, decay, vel * 0.8);
      bp.connect(hpf); hpf.connect(g); g.connect(out);

      var metal = Math.max(0, Math.min(1, p.metal == null ? 0.5 : p.metal));
      var mg = ctx.createGain(); mg.gain.value = metal; mg.connect(bp);
      /* Bright tunings push the upper ratios past Nyquist, where they all clamp
         to the same frequency -- inaudible, but they still cost an oscillator
         per hit and flood the console. Skip the ones that will not fit. */
      var nyquist = ctx.sampleRate / 2;
      ratios.forEach(function (r) {
        var freq = base * r * 8;
        if (freq >= nyquist) return;
        var o = ctx.createOscillator();
        o.type = 'square'; o.frequency.value = freq;
        o.connect(mg); o.start(t); o.stop(t + decay + 0.05);
      });
      var n = noise(ctx), ng = ctx.createGain();
      ng.gain.value = (1 - metal) * 0.9 + 0.1;
      n.connect(ng); ng.connect(bp);
      n.start(t); n.stop(t + decay + 0.05);
    },

    hat: function (ctx, out, t, p, vel, note) {
      Voices._metal(ctx, out, t, p, vel, note, p.decay, 6000 + p.tone * 3000);
    },
    openhat: function (ctx, out, t, p, vel, note) {
      Voices._metal(ctx, out, t, p, vel, note, p.decay, 5200 + p.tone * 3000);
    },
    ride: function (ctx, out, t, p, vel, note) {
      Voices._metal(ctx, out, t, p, vel, note, p.decay, 3200 + p.tone * 2500);
    },
    crash: function (ctx, out, t, p, vel, note) {
      Voices._metal(ctx, out, t, p, vel, note, p.decay, 2200 + p.tone * 2500);
    },
    shaker: function (ctx, out, t, p, vel, note) {
      var n = noise(ctx), f = ctx.createBiquadFilter(), g = ctx.createGain();
      f.type = 'highpass'; f.frequency.value = p.tune; f.Q.value = 0.8;
      env(ctx, g, t, 0.004, p.decay, vel * 0.8);
      n.connect(f); f.connect(g); g.connect(out);
      n.start(t); n.stop(t + p.decay + 0.05);
    },

    tom: function (ctx, out, t, p, vel, note) {
      var base = note != null ? mtof(note) : p.tune;
      var o = ctx.createOscillator(), g = ctx.createGain();
      o.type = 'sine';
      o.frequency.setValueAtTime(base * (1 + p.punch), t);
      o.frequency.exponentialRampToValueAtTime(base * 0.75, t + p.decay);
      env(ctx, g, t, 0.002, p.decay, vel);
      var d = shaper(ctx, p.drive);
      o.connect(g); g.connect(d); d.connect(out);
      o.start(t); o.stop(t + p.decay + 0.05);
      if (p.click > 0.01) {
        var n = noise(ctx), nf = ctx.createBiquadFilter(), ng = ctx.createGain();
        nf.type = 'bandpass'; nf.frequency.value = base * 6;
        env(ctx, ng, t, 0.001, 0.03, vel * p.click);
        n.connect(nf); nf.connect(ng); ng.connect(out);
        n.start(t); n.stop(t + 0.08);
      }
    },

    cowbell: function (ctx, out, t, p, vel, note) {
      var base = note != null ? mtof(note) : p.tune;
      var f = ctx.createBiquadFilter(); f.type = 'bandpass';
      f.frequency.value = base * 1.6; f.Q.value = 2.5 + p.tone * 4;
      var g = ctx.createGain();
      env(ctx, g, t, 0.001, p.decay, vel * 0.9);
      var d = shaper(ctx, p.drive);
      f.connect(g); g.connect(d); d.connect(out);
      [1, 1.5].forEach(function (r) {
        var o = ctx.createOscillator();
        o.type = 'square'; o.frequency.value = base * r;
        o.connect(f); o.start(t); o.stop(t + p.decay + 0.03);
      });
    },

    perc: function (ctx, out, t, p, vel, note) {
      var base = note != null ? mtof(note) : p.tune;
      var f = ctx.createBiquadFilter(); f.type = 'bandpass';
      f.frequency.setValueAtTime(base * 3, t);
      f.frequency.exponentialRampToValueAtTime(Math.max(60, base), t + p.decay);
      f.Q.value = 3 + p.tone * 6;
      var g = ctx.createGain();
      env(ctx, g, t, 0.001, p.decay, vel * 0.9);
      var d = shaper(ctx, p.drive);
      f.connect(g); g.connect(d); d.connect(out);
      var n = noise(ctx); n.connect(f); n.start(t); n.stop(t + p.decay + 0.05);
      var o = ctx.createOscillator(); o.type = 'triangle';
      o.frequency.setValueAtTime(base * 2, t);
      o.frequency.exponentialRampToValueAtTime(base, t + p.decay);
      var og = ctx.createGain(); og.gain.value = 0.5;
      o.connect(og); og.connect(f); o.start(t); o.stop(t + p.decay + 0.05);
    },

    noise: function (ctx, out, t, p, vel, note) {
      var n = noise(ctx), f = ctx.createBiquadFilter(), g = ctx.createGain();
      f.type = 'bandpass'; f.frequency.value = p.tune; f.Q.value = 0.6 + p.tone * 3;
      env(ctx, g, t, 0.003, p.decay, vel * 0.8);
      n.connect(f); f.connect(g); g.connect(out);
      n.start(t); n.stop(t + p.decay + 0.05);
    },

    // -- melodic ------------------------------------------------------------
    _tone: function (ctx, out, t, p, vel, note, dur, types, detunes, filterType) {
      var freq = mtof(note == null ? 48 : note);
      var g = ctx.createGain();
      var f = ctx.createBiquadFilter();
      f.type = filterType || 'lowpass';
      f.Q.value = 0.7 + (num(p.reso, 0)) * 12;
      var cutoff = Math.min(18000, num(p.cutoff, 4000));
      f.frequency.setValueAtTime(cutoff, t);
      f.frequency.exponentialRampToValueAtTime(Math.max(120, cutoff * 0.35), t + dur);
      var d = shaper(ctx, num(p.drive, 0));
      f.connect(g); g.connect(d); d.connect(out);

      var attack = 0.005;
      var release = Math.max(0.05, num(p.decay, 0.4));
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(Math.max(0.0002, vel * 0.7), t + attack);
      g.gain.setValueAtTime(Math.max(0.0002, vel * 0.7), t + Math.max(attack, dur * 0.7));
      g.gain.exponentialRampToValueAtTime(0.0001, t + dur + release);

      var oscs = [];
      types.forEach(function (type, i) {
        var o = ctx.createOscillator();
        o.type = type;
        o.frequency.setValueAtTime(freq, t);
        if (detunes) o.detune.value = detunes[i] || 0;
        o.connect(f);
        o.start(t); o.stop(t + dur + release + 0.05);
        oscs.push(o);
      });
      return { oscs: oscs, freq: freq, gain: g, filter: f };
    },

    sub808: function (ctx, out, t, p, vel, note, dur, prev) {
      var freq = mtof(note == null ? 33 : note);
      var o = ctx.createOscillator(), g = ctx.createGain();
      o.type = 'sine';
      var glide = num(p.glide, 0);
      if (prev != null && glide > 0.001) {
        o.frequency.setValueAtTime(mtof(prev), t);
        o.frequency.exponentialRampToValueAtTime(freq, t + glide);
      } else {
        o.frequency.setValueAtTime(freq * 1.8, t);
        o.frequency.exponentialRampToValueAtTime(freq, t + 0.035);
      }
      var total = Math.max(0.12, dur + (num(p.decay, 1)));
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(Math.max(0.0002, vel), t + 0.006);
      g.gain.exponentialRampToValueAtTime(0.0001, t + total);
      var d = shaper(ctx, num(p.drive, 0.3));
      var lp = ctx.createBiquadFilter();
      lp.type = 'lowpass'; lp.frequency.value = Math.min(18000, num(p.cutoff, 900));
      o.connect(g); g.connect(d); d.connect(lp); lp.connect(out);
      o.start(t); o.stop(t + total + 0.05);
    },

    bass_saw: function (c, o, t, p, v, n, dur, prev) { Voices._tone(c, o, t, p, v, n, dur, ['sawtooth'], null); },
    bass_square: function (c, o, t, p, v, n, dur) { Voices._tone(c, o, t, p, v, n, dur, ['square'], null); },
    reese: function (c, o, t, p, v, n, dur) { Voices._tone(c, o, t, p, v, n, dur, ['sawtooth', 'sawtooth', 'sawtooth'], [-14, 0, 15]); },
    lead_saw: function (c, o, t, p, v, n, dur) { Voices._tone(c, o, t, p, v, n, dur, ['sawtooth', 'sawtooth'], [-7, 7]); },
    keys: function (c, o, t, p, v, n, dur) { Voices._tone(c, o, t, p, v, n, dur, ['triangle', 'sine'], [0, 1200]); },
    organ: function (c, o, t, p, v, n, dur) { Voices._tone(c, o, t, p, v, n, dur, ['sine', 'sine', 'sine'], [0, 1200, 1902]); },

    pluck: function (ctx, out, t, p, vel, note, dur) {
      var freq = mtof(note == null ? 60 : note);
      var o = ctx.createOscillator(), g = ctx.createGain(), f = ctx.createBiquadFilter();
      o.type = 'sawtooth'; o.frequency.value = freq;
      f.type = 'lowpass'; f.Q.value = 1 + (num(p.reso, 0)) * 10;
      var cut = Math.min(18000, num(p.cutoff, 4000));
      f.frequency.setValueAtTime(cut, t);
      f.frequency.exponentialRampToValueAtTime(Math.max(150, freq * 2), t + (num(p.decay, 0.35)));
      env(ctx, g, t, 0.003, (num(p.decay, 0.35)) + dur * 0.2, vel * 0.8);
      var d = shaper(ctx, num(p.drive, 0));
      o.connect(f); f.connect(g); g.connect(d); d.connect(out);
      o.start(t); o.stop(t + (num(p.decay, 0.35)) + dur + 0.1);
    },

    pad: function (ctx, out, t, p, vel, note, dur) {
      var freq = mtof(note == null ? 60 : note);
      var g = ctx.createGain(), f = ctx.createBiquadFilter();
      f.type = 'lowpass'; f.frequency.value = Math.min(18000, num(p.cutoff, 3200));
      f.Q.value = 0.6 + (num(p.reso, 0)) * 4;
      var atk = Math.min(0.5, 0.08 + dur * 0.15), rel = num(p.decay, 2);
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(Math.max(0.0002, vel * 0.5), t + atk);
      g.gain.setValueAtTime(Math.max(0.0002, vel * 0.5), t + Math.max(atk, dur * 0.8));
      g.gain.exponentialRampToValueAtTime(0.0001, t + dur + rel);
      f.connect(g); g.connect(out);
      [-9, 0, 9].forEach(function (dt) {
        var o = ctx.createOscillator();
        o.type = 'sawtooth'; o.frequency.value = freq; o.detune.value = dt;
        o.connect(f); o.start(t); o.stop(t + dur + rel + 0.1);
      });
    },

    bell: function (ctx, out, t, p, vel, note, dur) {
      var freq = mtof(note == null ? 72 : note);
      var car = ctx.createOscillator(), mod = ctx.createOscillator();
      var mg = ctx.createGain(), g = ctx.createGain();
      car.type = 'sine'; car.frequency.value = freq;
      mod.type = 'sine'; mod.frequency.value = freq * 3.51;
      mg.gain.setValueAtTime(freq * 2.5, t);
      mg.gain.exponentialRampToValueAtTime(1, t + (num(p.decay, 1.4)));
      mod.connect(mg); mg.connect(car.frequency);
      env(ctx, g, t, 0.002, (num(p.decay, 1.4)) + dur * 0.3, vel * 0.6);
      car.connect(g); g.connect(out);
      car.start(t); mod.start(t);
      car.stop(t + (num(p.decay, 1.4)) + dur + 0.2);
      mod.stop(t + (num(p.decay, 1.4)) + dur + 0.2);
    }
  };

  var MELODIC = ['sub808', 'bass_saw', 'bass_square', 'reese', 'pluck', 'lead_saw',
    'keys', 'pad', 'bell', 'organ'];

  // ---- graph ---------------------------------------------------------------
  /* Master bus settings live on the project so a mix can keep its dynamics.
     Missing fields fall back to what the engine used to hardcode. */
  function masterSettings(state) {
    state = state || {};
    return {
      gain: state.masterGain == null ? 0.85 : state.masterGain,
      threshold: state.compThreshold == null ? -14 : state.compThreshold,
      ratio: state.compRatio == null ? 5 : state.compRatio,
      release: state.compRelease == null ? 0.18 : state.compRelease,
      limiter: state.limiter === undefined ? true : !!state.limiter
    };
  }

  function buildGraph(ctx, state) {
    var m = masterSettings(state);
    var out = ctx.createGain();
    out.gain.value = m.gain;

    var comp = ctx.createDynamicsCompressor();
    comp.threshold.value = m.threshold; comp.knee.value = 14;
    comp.ratio.value = m.ratio; comp.attack.value = 0.004;
    comp.release.value = m.release;

    /* A WaveShaper with a null curve is a straight pass-through, so the
       limiter can be switched off without rebuilding the graph. */
    var limiter = ctx.createWaveShaper();
    limiter.curve = m.limiter ? softLimit() : null;
    limiter.oversample = '2x';

    var analyser = null;
    if (ctx.createAnalyser) {
      analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      analyser.smoothingTimeConstant = 0.75;
    }

    out.connect(comp);
    comp.connect(limiter);
    if (analyser) { limiter.connect(analyser); analyser.connect(ctx.destination); }
    else { limiter.connect(ctx.destination); }

    var reverb = ctx.createConvolver();
    reverb.buffer = impulse(ctx, 2.0, 3.2);
    var revGain = ctx.createGain(); revGain.gain.value = 0.55;
    reverb.connect(revGain); revGain.connect(out);

    var delay = ctx.createDelay(2.0);
    var fb = ctx.createGain(); fb.gain.value = 0.34;
    var dampen = ctx.createBiquadFilter();
    dampen.type = 'lowpass'; dampen.frequency.value = 2600;
    var dryOut = ctx.createGain(); dryOut.gain.value = 0.5;
    delay.connect(dampen); dampen.connect(fb); fb.connect(delay);
    dampen.connect(dryOut); dryOut.connect(out);

    return { out: out, reverb: reverb, delay: delay, analyser: analyser, master: out,
             comp: comp, limiter: limiter, ducks: {} };
  }

  function trackChain(ctx, graph, track) {
    var g = ctx.createGain();
    g.gain.value = track.gain;
    var node = g;
    if (ctx.createStereoPanner) {
      var pan = ctx.createStereoPanner();
      pan.pan.value = Math.max(-1, Math.min(1, track.pan || 0));
      g.connect(pan); node = pan;
    }
    /* Sidechain: a gain node the kick pulls down on every hit. It sits ahead of
       the sends so the reverb and delay tails pump with the dry signal. */
    if (track.duck > 0.001) {
      var duck = ctx.createGain();
      duck.gain.value = 1;
      node.connect(duck);
      node = duck;
      graph.ducks[track.id] = { node: duck, amount: Math.min(1, track.duck) };
    }
    node.connect(graph.out);
    if (track.reverb > 0.001) {
      var rs = ctx.createGain(); rs.gain.value = track.reverb;
      node.connect(rs); rs.connect(graph.reverb);
    }
    if (track.delay > 0.001) {
      var ds = ctx.createGain(); ds.gain.value = track.delay;
      node.connect(ds); ds.connect(graph.delay);
    }
    return g;
  }

  // ---- scheduling ----------------------------------------------------------
  function stepDur(bpm) { return 60 / bpm / 4; }

  function activeTracks(state) {
    var solo = state.tracks.some(function (t) { return t.solo; });
    return state.tracks.filter(function (t) {
      return solo ? (t.solo && !t.mute) : !t.mute;
    });
  }

  /* Every note is built onto a throwaway bus so the whole voice subgraph can be
     released in one disconnect. Without this, note nodes pile up on the master
     bus for the life of the session: CPU climbs and idle shapers sum into an
     audible DC offset. Offline renders die with their context, so they skip it. */
  function voiceBus(ctx, dest) {
    var g = ctx.createGain();
    g.connect(dest);
    return g;
  }

  function reap(ctx, node, deadline) {
    if (ctx.__bfOffline) return;
    var ms = Math.max(0, deadline - ctx.currentTime) * 1000 + 120;
    setTimeout(function () { try { node.disconnect(); } catch (e) { } }, ms);
  }

  /* Pull every ducking track down at `t` and let it climb back over `release`.
     Called once per hit on the duck source track (the kick, by default). */
  function fireDuck(graph, state, t) {
    var release = state.duckRelease == null ? 0.18 : state.duckRelease;
    for (var id in graph.ducks) {
      if (!graph.ducks.hasOwnProperty(id)) continue;
      var d = graph.ducks[id];
      var floor = Math.max(0.02, 1 - d.amount);
      d.node.gain.cancelScheduledValues(t);
      d.node.gain.setValueAtTime(floor, t);
      d.node.gain.linearRampToValueAtTime(1, t + release);
    }
  }

  /* Schedule one step of one pattern. `chains` maps track id -> input node. */
  function scheduleStep(ctx, graph, state, chains, pattern, index, time, lastNote) {
    var sd = stepDur(state.bpm);
    var swing = state.swing || 0;
    var tracks = activeTracks(state);
    for (var k = 0; k < tracks.length; k++) {
      var track = tracks[k];
      var row = pattern.grid[track.id];
      if (!row) continue;
      var step = row[index];
      if (!step) continue;
      if (step.prob != null && rnd() > step.prob) continue;

      var t = time;
      if (index % 2 === 1) t += swing * sd * 0.5;
      if (step.nudge) t += step.nudge * sd;
      if (t < ctx.currentTime) t = ctx.currentTime + 0.001;

      var dest = chains[track.id];
      if (!dest) continue;
      if (track.id === (state.duckSource || 'kick')) fireDuck(graph, state, t);
      var voice = Voices[track.engine] || Voices.perc;
      var isMelodic = MELODIC.indexOf(track.engine) >= 0;
      var dur = (step.len || 1) * sd;
      var vel = Math.max(0.02, Math.min(1, step.v == null ? 0.8 : step.v));
      var rolls = step.roll && step.roll > 1 ? step.roll : 1;

      var decay = track.params.decay == null ? 1 : track.params.decay;
      var life = dur + decay * 2 + 0.5;

      for (var r = 0; r < rolls; r++) {
        var rt = t + (r * sd) / rolls;
        var rv = rolls > 1 ? vel * (0.55 + 0.45 * (r / (rolls - 1))) : vel;
        var notes = step.notes || (step.note != null ? [step.note] : [null]);
        var bus = voiceBus(ctx, dest);
        for (var ni = 0; ni < notes.length; ni++) {
          var prev = step.slide ? lastNote[track.id] : null;
          try {
            voice(ctx, bus, rt, track.params, rv / Math.sqrt(notes.length),
              notes[ni], isMelodic ? dur / rolls : dur, prev);
          } catch (e) { /* one bad voice must not stop the transport */ }
        }
        reap(ctx, bus, rt + life);
        if (notes[0] != null) lastNote[track.id] = notes[0];
      }
    }
  }

  // ---- live transport ------------------------------------------------------
  function Engine() {
    this.ctx = null;
    this.graph = null;
    this.chains = {};
    this.state = null;
    this.playing = false;
    this.step = 0;
    this.nextTime = 0;
    this.timer = null;
    this.lastNote = {};
    this.songIndex = 0;
    this.songRepeat = 0;
    this.queue = [];
    this.onStep = null;
    this.patternIndex = 0;
  }

  Engine.prototype.ensure = function () {
    if (this.ctx) {
      if (this.ctx.state === 'suspended') this.ctx.resume();
      return this.ctx;
    }
    var AC = global.AudioContext || global.webkitAudioContext;
    this.ctx = new AC();
    seedRng(this.state && this.state.seed);
    this.graph = buildGraph(this.ctx, this.state);
    this.rebuildChains();
    return this.ctx;
  };

  Engine.prototype.rebuildChains = function () {
    if (!this.ctx || !this.state) return;
    var self = this;
    this.chains = {};
    this.graph.ducks = {};
    this.state.tracks.forEach(function (t) {
      self.chains[t.id] = trackChain(self.ctx, self.graph, t);
    });
  };

  Engine.prototype.setState = function (state) {
    var structural = !this.state ||
      this.state.tracks.length !== state.tracks.length ||
      this.state.tracks.some(function (t, i) {
        var o = state.tracks[i];
        return !o || o.id !== t.id || o.reverb !== t.reverb || o.delay !== t.delay ||
          o.duck !== t.duck;
      });
    this.state = state;
    if (this.ctx) {
      var m = masterSettings(state);
      this.graph.out.gain.setTargetAtTime(m.gain, this.ctx.currentTime, 0.02);
      this.graph.comp.threshold.value = m.threshold;
      this.graph.comp.ratio.value = m.ratio;
      this.graph.comp.release.value = m.release;
      this.graph.limiter.curve = m.limiter ? softLimit() : null;
      if (structural) {
        this.rebuildChains();
      } else {
        var self = this;
        state.tracks.forEach(function (t) {
          var c = self.chains[t.id];
          if (c) c.gain.setTargetAtTime(t.gain, self.ctx.currentTime, 0.02);
        });
      }
    }
  };

  Engine.prototype.currentPattern = function () {
    var s = this.state;
    if (s.songMode && s.song && s.song.length) {
      var entry = s.song[this.songIndex % s.song.length];
      return s.patterns[Math.min(entry.pattern, s.patterns.length - 1)];
    }
    return s.patterns[Math.min(s.current, s.patterns.length - 1)];
  };

  Engine.prototype.play = function () {
    if (this.playing) return;
    this.ensure();
    seedRng(this.state && this.state.seed);
    this.playing = true;
    this.step = 0;
    this.songIndex = 0;
    this.songRepeat = 0;
    this.lastNote = {};
    this.nextTime = this.ctx.currentTime + 0.08;
    var self = this;
    this.timer = setInterval(function () { self.tick(); }, TICK_MS);
    this.tick();
  };

  Engine.prototype.stop = function () {
    this.playing = false;
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
    this.queue = [];
    if (this.onStep) this.onStep(-1, this.patternIndex);
  };

  Engine.prototype.tick = function () {
    if (!this.playing || !this.state) return;
    var ctx = this.ctx;
    while (this.nextTime < ctx.currentTime + LOOKAHEAD) {
      var pat = this.currentPattern();
      this.patternIndex = this.state.patterns.indexOf(pat);
      scheduleStep(ctx, this.graph, this.state, this.chains, pat, this.step,
        this.nextTime, this.lastNote);
      this.queue.push({ time: this.nextTime, step: this.step, pattern: this.patternIndex });
      this.nextTime += stepDur(this.state.bpm);
      this.step++;
      if (this.step >= pat.steps) {
        this.step = 0;
        var s = this.state;
        if (s.songMode && s.song && s.song.length) {
          this.songRepeat++;
          var entry = s.song[this.songIndex % s.song.length];
          if (this.songRepeat >= (entry.repeat || 1)) {
            this.songRepeat = 0;
            this.songIndex = (this.songIndex + 1) % s.song.length;
          }
        }
      }
    }
    // report the playhead position for the UI
    while (this.queue.length && this.queue[0].time <= ctx.currentTime) {
      var item = this.queue.shift();
      if (this.onStep) this.onStep(item.step, item.pattern);
    }
  };

  Engine.prototype.audition = function (track, note) {
    this.ensure();
    var chain = this.chains[track.id] || trackChain(this.ctx, this.graph, track);
    var voice = Voices[track.engine] || Voices.perc;
    var melodic = MELODIC.indexOf(track.engine) >= 0;
    var bus = voiceBus(this.ctx, chain);
    var at = this.ctx.currentTime + 0.01;
    voice(this.ctx, bus, at, track.params, 0.9,
      note != null ? note : (melodic ? 48 : null), 0.35, null);
    reap(this.ctx, bus, at + (track.params.decay || 1) * 2 + 0.6);
  };

  // ---- offline render ------------------------------------------------------
  Engine.prototype.render = function (opts) {
    var state = JSON.parse(JSON.stringify(this.state));
    seedRng(state.seed);
    var sd = stepDur(state.bpm);
    var seq = [];   // [{pattern, step}] in play order

    if (opts.song && state.song && state.song.length) {
      state.song.forEach(function (entry) {
        var pat = state.patterns[Math.min(entry.pattern, state.patterns.length - 1)];
        for (var r = 0; r < (entry.repeat || 1); r++) {
          for (var i = 0; i < pat.steps; i++) seq.push({ pat: pat, step: i });
        }
      });
    } else {
      var pat = state.patterns[Math.min(state.current, state.patterns.length - 1)];
      for (var r = 0; r < (opts.repeats || 2); r++) {
        for (var i = 0; i < pat.steps; i++) seq.push({ pat: pat, step: i });
      }
    }
    if (!seq.length) return Promise.reject(new Error('nothing to render'));

    var tail = opts.tail == null ? 1.5 : opts.tail;
    var rate = 44100;
    var duration = seq.length * sd + tail;
    var OAC = global.OfflineAudioContext || global.webkitOfflineAudioContext;
    var ctx = new OAC(2, Math.ceil(duration * rate), rate);
    ctx.__bfOffline = true;
    var graph = buildGraph(ctx, state);
    var chains = {};
    state.tracks.forEach(function (t) { chains[t.id] = trackChain(ctx, graph, t); });

    var lastNote = {};
    for (var k = 0; k < seq.length; k++) {
      scheduleStep(ctx, graph, state, chains, seq[k].pat, seq[k].step, k * sd + 0.05, lastNote);
    }
    return ctx.startRendering().then(function (buffer) {
      return { buffer: buffer, wav: encodeWav(buffer), seconds: duration };
    });
  };

  // ---- wav encoding --------------------------------------------------------
  function encodeWav(buffer) {
    var chans = buffer.numberOfChannels, len = buffer.length;
    var data = [];
    for (var c = 0; c < chans; c++) data.push(buffer.getChannelData(c));
    var bytes = 44 + len * chans * 2;
    var view = new DataView(new ArrayBuffer(bytes));
    function str(off, s) { for (var i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)); }
    str(0, 'RIFF'); view.setUint32(4, bytes - 8, true); str(8, 'WAVE');
    str(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
    view.setUint16(22, chans, true); view.setUint32(24, buffer.sampleRate, true);
    view.setUint32(28, buffer.sampleRate * chans * 2, true);
    view.setUint16(32, chans * 2, true); view.setUint16(34, 16, true);
    str(36, 'data'); view.setUint32(40, len * chans * 2, true);
    var off = 44;
    for (var i = 0; i < len; i++) {
      for (var c2 = 0; c2 < chans; c2++) {
        var s = Math.max(-1, Math.min(1, data[c2][i]));
        view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
        off += 2;
      }
    }
    return view.buffer;
  }

  global.BeatForgeAudio = {
    Engine: Engine, Voices: Voices, MELODIC: MELODIC, mtof: mtof, encodeWav: encodeWav
  };
})(window);
