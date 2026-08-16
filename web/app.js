/* BeatForge UI — grid editor, mixer, live sync with the MCP server. */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var CLIENT = 'ui-' + Math.random().toString(36).slice(2, 10);
  var CELL = 26, GAP = 2, LEFT = 126 + 176;

  var S = null;              // project state
  var META = { drumEngines: {}, melodicEngines: {}, scales: [] };
  var engine = new window.BeatForgeAudio.Engine();
  var selected = null;       // selected track id
  var lastNote = {};         // per-track pitch memory
  var pushTimer = null;
  var painting = null;
  var started = false;

  var NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
  function noteName(m) { return NAMES[((m % 12) + 12) % 12] + (Math.floor(m / 12) - 1); }

  function isMelodic(track) {
    return window.BeatForgeAudio.MELODIC.indexOf(track.engine) >= 0;
  }
  function trackById(id) {
    for (var i = 0; i < S.tracks.length; i++) if (S.tracks[i].id === id) return S.tracks[i];
    return null;
  }
  function pattern() { return S.patterns[Math.min(S.current, S.patterns.length - 1)]; }
  function row(tid) {
    var p = pattern();
    if (!p.grid[tid]) p.grid[tid] = new Array(p.steps).fill(null);
    return p.grid[tid];
  }

  function toast(msg, ms) {
    var el = $('toast');
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { el.classList.remove('show'); }, ms || 2600);
  }

  // ---------------------------------------------------------------- sync ----
  function push(immediate) {
    clearTimeout(pushTimer);
    var send = function () {
      fetch('/api/state', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client: CLIENT, state: S })
      }).catch(function () { });
    };
    if (immediate) send(); else pushTimer = setTimeout(send, 140);
  }

  function connect() {
    var es = new EventSource('/api/events?client=' + CLIENT);
    es.addEventListener('state', function (e) {
      var incoming = JSON.parse(e.data);
      // Only follow the transport when it actually flipped. A plain state
      // broadcast (or the snapshot sent on connect) must not stop playback.
      var was = S ? !!S.playing : null;
      S = incoming;
      engine.setState(S);
      renderAll();
      if (was !== null && !!S.playing !== was) {
        if (S.playing && started) engine.play(); else engine.stop();
      }
      link(true);
    });
    es.addEventListener('cmd', function (e) {
      handleCommand(JSON.parse(e.data));
    });
    es.onopen = function () { link(true); };
    es.onerror = function () { link(false); };
  }

  function link(ok) {
    $('link-dot').classList.toggle('live', !!ok);
    $('link-text').textContent = ok ? 'mcp linked' : 'offline';
  }

  function handleCommand(c) {
    if (c.cmd === 'play') { if (started) engine.play(); syncPlayButton(); }
    else if (c.cmd === 'stop') { engine.stop(); syncPlayButton(); }
    else if (c.cmd === 'reload') { location.reload(); }
    else if (c.cmd === 'export') { doExport(c); }
    else if (c.cmd === 'analyze') { doAnalyze(c); }
  }

  // -------------------------------------------------------------- render ----
  function renderAll() {
    if (!S) return;
    $('bpm').value = Math.round(S.bpm);
    $('swing').value = S.swing;
    $('swing-val').textContent = Math.round(S.swing * 100) + '%';
    $('master').value = S.masterGain;
    $('comp-threshold').value = S.compThreshold;
    $('comp-threshold-val').textContent = Math.round(S.compThreshold) + 'dB';
    $('comp-ratio').value = S.compRatio;
    $('comp-ratio-val').textContent = S.compRatio + ':1';
    $('limiter').checked = S.limiter !== false;
    $('proj-name').value = S.name;
    $('song-mode').checked = !!S.songMode;
    $('pat-steps').value = String(pattern().steps);
    if ($('key').options.length) $('key').value = S.key;
    if ($('scale').options.length) $('scale').value = S.scale;
    renderPatternBar();
    renderSong();
    renderRuler();
    renderRows();
    renderInspector();
    syncPlayButton();
  }

  function syncPlayButton() {
    $('btn-play').classList.toggle('on', engine.playing);
    $('btn-play').textContent = engine.playing ? '❚❚' : '▶';
  }

  function renderPatternBar() {
    var host = $('pattern-list');
    host.innerHTML = '';
    S.patterns.forEach(function (p, i) {
      var b = document.createElement('button');
      b.textContent = p.name;
      if (i === S.current) b.className = 'sel';
      b.onclick = function () { S.current = i; push(true); renderAll(); };
      b.ondblclick = function () {
        var n = prompt('Pattern name', p.name);
        if (n) { p.name = n; push(true); renderAll(); }
      };
      host.appendChild(b);
    });
  }

  function renderSong() {
    var host = $('song-chain');
    host.innerHTML = '';
    (S.song || []).forEach(function (c, i) {
      var el = document.createElement('div');
      el.className = 'slot';
      var p = S.patterns[c.pattern];
      el.textContent = (p ? p.name : '?') + '×' + c.repeat;
      el.title = 'click: +1 repeat · shift-click: -1 · alt-click: remove';
      el.onclick = function (e) {
        if (e.altKey) S.song.splice(i, 1);
        else if (e.shiftKey) c.repeat = Math.max(1, c.repeat - 1);
        else c.repeat = Math.min(64, c.repeat + 1);
        push(true); renderSong();
      };
      host.appendChild(el);
    });
  }

  function renderRuler() {
    var n = pattern().steps, host = $('ruler');
    host.innerHTML = '';
    for (var i = 0; i < n; i++) {
      var el = document.createElement('i');
      if (i % 4 === 0) { el.className = 'beat'; el.textContent = String(i / 4 + 1); }
      else el.textContent = '·';
      host.appendChild(el);
    }
  }

  function renderRows() {
    var host = $('rows');
    var ph = $('playhead');
    host.innerHTML = '';
    host.appendChild(ph);
    var p = pattern();

    S.tracks.forEach(function (t) {
      var r = document.createElement('div');
      r.className = 'row' + (t.id === selected ? ' sel' : '');
      r.dataset.track = t.id;

      var name = document.createElement('div');
      name.className = 'name';
      name.innerHTML = '<b></b><span class="kind"></span>';
      name.querySelector('b').textContent = t.name;
      name.querySelector('.kind').textContent = isMelodic(t) ? '♪' : '';
      name.onclick = function () { selected = t.id; renderRows(); renderInspector(); };
      name.ondblclick = function () { engine.audition(t, isMelodic(t) ? defaultNote(t) : null); };
      r.appendChild(name);

      var mix = document.createElement('div');
      mix.className = 'mix';
      var m = document.createElement('button');
      m.textContent = 'M'; m.className = t.mute ? 'on' : '';
      m.onclick = function () { t.mute = !t.mute; push(true); renderRows(); };
      var so = document.createElement('button');
      so.textContent = 'S'; so.className = 'solo' + (t.solo ? ' on' : '');
      so.onclick = function () { t.solo = !t.solo; push(true); renderRows(); };
      var vol = document.createElement('input');
      vol.type = 'range'; vol.min = 0; vol.max = 1.2; vol.step = 0.01; vol.value = t.gain;
      vol.title = 'volume';
      vol.oninput = function () { t.gain = parseFloat(vol.value); engine.setState(S); push(); };
      var pan = document.createElement('input');
      pan.type = 'range'; pan.min = -1; pan.max = 1; pan.step = 0.02; pan.value = t.pan;
      pan.title = 'pan';
      pan.style.width = '42px';
      pan.oninput = function () { t.pan = parseFloat(pan.value); push(); engine.rebuildChains(); };
      mix.appendChild(m); mix.appendChild(so); mix.appendChild(vol); mix.appendChild(pan);
      r.appendChild(mix);

      var cells = document.createElement('div');
      cells.className = 'cells';
      var data = p.grid[t.id] || [];
      for (var i = 0; i < p.steps; i++) {
        var c = document.createElement('div');
        c.className = 'cell';
        c.dataset.i = i;
        c.dataset.track = t.id;
        paint(c, data[i], t, data, i);
        cells.appendChild(c);
      }
      r.appendChild(cells);
      host.appendChild(r);
    });
    ph.style.height = host.scrollHeight + 'px';
  }

  function paint(el, step, track, data, i) {
    var cls = 'cell';
    if (i % 4 === 0) cls += ' beat';
    if (i % 16 === 0) cls += ' bar';
    var text = '';
    if (step) {
      cls += ' on';
      var v = step.v == null ? 0.8 : step.v;
      cls += v >= 0.9 ? ' v4' : v >= 0.65 ? ' v3' : v >= 0.4 ? ' v2' : ' v1';
      if (step.roll > 1) cls += ' roll';
      if (step.slide) cls += ' slide';
      if (step.note != null) { cls += ' note'; text = noteName(step.note); }
      else if (step.notes && step.notes.length) { cls += ' note'; text = noteName(step.notes[0]) + '+'; }
    } else if (data) {
      // show sustain of a longer note
      for (var j = i - 1; j >= 0 && i - j < 16; j--) {
        var s = data[j];
        if (s) { if ((s.len || 1) > i - j) cls += ' tie'; break; }
      }
    }
    el.className = cls;
    el.textContent = text;
  }

  function repaint(tid, i) {
    var el = document.querySelector('.cell[data-track="' + tid + '"][data-i="' + i + '"]');
    var t = trackById(tid), data = row(tid);
    if (el) paint(el, data[i], t, data, i);
  }

  function repaintRow(tid) {
    var data = row(tid), t = trackById(tid);
    for (var i = 0; i < data.length; i++) repaint(tid, i);
  }

  function defaultNote(t) {
    if (lastNote[t.id] != null) return lastNote[t.id];
    var data = row(t.id);
    for (var i = 0; i < data.length; i++) if (data[i] && data[i].note != null) return data[i].note;
    return /808|bass|reese/.test(t.engine) ? 36 : 60;
  }

  // ------------------------------------------------------------- editing ----
  function toggleCell(tid, i, e) {
    var t = trackById(tid), data = row(tid), step = data[i];
    var mel = isMelodic(t);

    if (e && e.shiftKey) {
      if (!step) return;
      var order = [0.28, 0.55, 0.8, 1.0];
      var v = step.v == null ? 0.8 : step.v;
      var idx = 0;
      for (var k = 0; k < order.length; k++) if (Math.abs(order[k] - v) < 0.13) idx = k;
      step.v = order[(idx + 1) % order.length];
    } else if (e && e.altKey) {
      if (!step) return;
      if (mel) step.slide = !step.slide;
      else step.roll = step.roll >= 4 ? 0 : (step.roll ? step.roll * 2 : 2);
    } else if (step) {
      data[i] = null;
    } else {
      step = { v: 0.8 };
      if (mel) { step.note = defaultNote(t); step.len = 1; }
      data[i] = step;
      engine.audition(t, mel ? step.note : null);
    }
    repaintRow(tid);
    push();
  }

  function wheelCell(tid, i, e) {
    var t = trackById(tid), data = row(tid), step = data[i];
    if (!step) return;
    var dir = e.deltaY < 0 ? 1 : -1;
    if (isMelodic(t) && !e.altKey) {
      if (e.ctrlKey || e.metaKey) step.len = Math.max(1, Math.min(32, (step.len || 1) + dir));
      else {
        step.note = Math.max(12, Math.min(108, (step.note || 60) + dir * (e.shiftKey ? 12 : 1)));
        lastNote[tid] = step.note;
        engine.audition(t, step.note);
      }
    } else {
      step.v = Math.max(0.05, Math.min(1, (step.v == null ? 0.8 : step.v) + dir * 0.1));
    }
    repaintRow(tid);
    push();
  }

  function bindGrid() {
    var host = $('rows');
    host.addEventListener('mousedown', function (e) {
      var cell = e.target.closest ? e.target.closest('.cell') : null;
      if (!cell) return;
      e.preventDefault();
      var tid = cell.dataset.track, i = +cell.dataset.i;
      selected = tid;
      if (e.button === 2) { row(tid)[i] = null; repaintRow(tid); push(); return; }
      toggleCell(tid, i, e);
      painting = { track: tid, add: !!row(tid)[i], last: i };
      renderInspector();
    });
    host.addEventListener('mouseover', function (e) {
      if (!painting) return;
      var cell = e.target.closest ? e.target.closest('.cell') : null;
      if (!cell || cell.dataset.track !== painting.track) return;
      var i = +cell.dataset.i;
      if (i === painting.last) return;
      painting.last = i;
      var data = row(painting.track), t = trackById(painting.track);
      if (painting.add && !data[i]) {
        data[i] = isMelodic(t) ? { v: 0.8, note: defaultNote(t), len: 1 } : { v: 0.8 };
      } else if (!painting.add) { data[i] = null; }
      repaintRow(painting.track);
      push();
    });
    window.addEventListener('mouseup', function () { painting = null; });
    host.addEventListener('contextmenu', function (e) {
      if (e.target.closest && e.target.closest('.cell')) e.preventDefault();
    });
    host.addEventListener('wheel', function (e) {
      var cell = e.target.closest ? e.target.closest('.cell') : null;
      if (!cell) return;
      e.preventDefault();
      wheelCell(cell.dataset.track, +cell.dataset.i, e);
    }, { passive: false });
  }

  // ----------------------------------------------------------- inspector ----
  function renderInspector() {
    var host = $('insp-body');
    var t = selected ? trackById(selected) : null;
    if (!t) { host.innerHTML = '<p class="hint">Select a track by clicking its name.</p>'; return; }
    host.innerHTML = '';

    var nameIn = document.createElement('input');
    nameIn.type = 'text'; nameIn.value = t.name;
    nameIn.oninput = function () { t.name = nameIn.value; push(); renderRows(); };
    host.appendChild(labelled('NAME', nameIn));

    var sel = document.createElement('select');
    var groups = [['drums', META.drumEngines], ['melodic', META.melodicEngines]];
    groups.forEach(function (g) {
      var og = document.createElement('optgroup');
      og.label = g[0];
      Object.keys(g[1]).sort().forEach(function (name) {
        var o = document.createElement('option');
        o.value = name; o.textContent = name;
        og.appendChild(o);
      });
      sel.appendChild(og);
    });
    sel.value = t.engine;
    sel.onchange = function () {
      t.engine = sel.value;
      t.kind = window.BeatForgeAudio.MELODIC.indexOf(sel.value) >= 0 ? 'melodic' : 'drum';
      var defs = META.drumEngines[sel.value] || META.melodicEngines[sel.value] || {};
      var merged = {};
      Object.keys(defs).forEach(function (k) {
        merged[k] = t.params[k] != null ? t.params[k] : defs[k];
      });
      t.params = merged;
      push(true); engine.rebuildChains(); renderRows(); renderInspector();
    };
    host.appendChild(labelled('ENGINE', sel));

    [['gain', 0, 1.2], ['pan', -1, 1], ['reverb', 0, 1], ['delay', 0, 1],
     ['duck', 0, 1]].forEach(function (spec) {
      host.appendChild(slider(spec[0].toUpperCase(), t[spec[0]] || 0, spec[1], spec[2], 0.01,
        function (v) {
          t[spec[0]] = v;
          if (spec[0] === 'gain') engine.setState(S); else engine.rebuildChains();
          push();
        }));
    });

    var sect = document.createElement('div');
    sect.className = 'insp-sect';
    sect.textContent = t.engine.toUpperCase() + ' PARAMETERS';
    host.appendChild(sect);

    Object.keys(t.params).sort().forEach(function (k) {
      var val = t.params[k];
      var max = k === 'tune' ? (val > 500 ? 14000 : 400) : (k === 'cutoff' ? 16000 : (k === 'decay' ? 3 : 1));
      var min = k === 'tune' ? 20 : (k === 'cutoff' ? 80 : 0);
      var stp = (max > 100) ? 1 : 0.01;
      host.appendChild(slider(k.toUpperCase(), val, min, max, stp, function (v) {
        t.params[k] = v; push();
      }));
    });

    var rowEl = document.createElement('div');
    rowEl.className = 'insp-row';
    var audition = document.createElement('button');
    audition.textContent = 'AUDITION';
    audition.onclick = function () { engine.audition(t, isMelodic(t) ? defaultNote(t) : null); };
    var clear = document.createElement('button');
    clear.textContent = 'CLEAR';
    clear.onclick = function () {
      pattern().grid[t.id] = new Array(pattern().steps).fill(null);
      push(true); renderRows();
    };
    var del = document.createElement('button');
    del.textContent = 'DELETE';
    del.onclick = function () {
      if (!confirm('Delete track "' + t.name + '"?')) return;
      S.tracks = S.tracks.filter(function (x) { return x.id !== t.id; });
      S.patterns.forEach(function (p) { delete p.grid[t.id]; });
      selected = null;
      push(true); engine.rebuildChains(); renderRows(); renderInspector();
    };
    rowEl.appendChild(audition); rowEl.appendChild(clear); rowEl.appendChild(del);
    host.appendChild(rowEl);
  }

  function labelled(text, node) {
    var l = document.createElement('label');
    var s = document.createElement('span');
    s.innerHTML = '<i style="font-style:normal">' + text + '</i>';
    l.appendChild(s); l.appendChild(node);
    return l;
  }

  function slider(text, value, min, max, step, onchange) {
    var l = document.createElement('label');
    var s = document.createElement('span');
    var lbl = document.createElement('i'); lbl.style.fontStyle = 'normal'; lbl.textContent = text;
    var val = document.createElement('i'); val.style.fontStyle = 'normal';
    val.textContent = fmt(value);
    s.appendChild(lbl); s.appendChild(val);
    var inp = document.createElement('input');
    inp.type = 'range'; inp.min = min; inp.max = max; inp.step = step; inp.value = value;
    inp.oninput = function () {
      var v = parseFloat(inp.value);
      val.textContent = fmt(v);
      onchange(v);
    };
    l.appendChild(s); l.appendChild(inp);
    return l;
  }

  function fmt(v) { return Math.abs(v) >= 100 ? String(Math.round(v)) : v.toFixed(2); }

  // ---------------------------------------------------------------- scope ---
  function scopeLoop() {
    requestAnimationFrame(scopeLoop);
    var cv = $('scope'), g = cv.getContext('2d');
    var an = engine.graph && engine.graph.analyser;
    g.clearRect(0, 0, cv.width, cv.height);
    if (!an) return;
    var buf = scopeLoop._b || (scopeLoop._b = new Uint8Array(an.fftSize));
    an.getByteTimeDomainData(buf);
    g.strokeStyle = engine.playing ? '#ff2d40' : '#3d434d';
    g.lineWidth = 1;
    g.beginPath();
    var step = Math.max(1, Math.floor(buf.length / cv.width));
    for (var x = 0, i = 0; x < cv.width; x++, i += step) {
      var y = (1 - buf[i] / 255) * cv.height;
      if (x === 0) g.moveTo(x, y); else g.lineTo(x, y);
    }
    g.stroke();
  }

  function playheadLoop() {
    requestAnimationFrame(playheadLoop);
    var ph = $('playhead');
    if (!engine.playing || engine.lastStep == null || engine.lastStep < 0) {
      ph.classList.remove('on');
      return;
    }
    ph.classList.add('on');
    var x = LEFT + engine.lastStep * (CELL + GAP);
    ph.style.transform = 'translateX(' + x + 'px)';

    // keep the playhead in view on long patterns
    var host = $('rows');
    var left = host.scrollLeft, right = left + host.clientWidth;
    if (x < left + LEFT) host.scrollLeft = Math.max(0, x - LEFT);
    else if (x + CELL > right - 8) host.scrollLeft = x + CELL - host.clientWidth + 8;
  }

  function bindScrollSync() {
    var host = $('rows'), head = document.querySelector('.grid-head');
    host.addEventListener('scroll', function () { head.scrollLeft = host.scrollLeft; });
  }

  // --------------------------------------------------------------- export ---
  function doExport(cmd) {
    if (!started) {
      fetch('/api/export', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job: cmd.job, error: 'audio not started — click ENTER STUDIO in the BeatForge tab first' })
      });
      return;
    }
    toast('rendering audio…', 8000);
    engine.setState(S);
    engine.ensure();
    engine.render({
      repeats: cmd.repeats || 2, song: !!cmd.song, tail: cmd.tail == null ? 1.5 : cmd.tail
    }).then(function (res) {
      var bytes = new Uint8Array(res.wav), bin = '';
      for (var i = 0; i < bytes.length; i += 0x8000) {
        bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
      }
      return fetch('/api/export', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          job: cmd.job, filename: cmd.filename || S.name, wav: btoa(bin)
        })
      }).then(function (r) { return r.json(); });
    }).then(function (r) {
      toast('exported → ' + (r.path || 'exports/'));
    }).catch(function (err) {
      toast('export failed: ' + err.message, 5000);
      fetch('/api/export', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job: cmd.job, error: String(err && err.message || err) })
      });
    });
  }

  /* Same offline render as the export, but instead of shipping ten megabytes of
     WAV back it measures the buffer here and returns a few hundred bytes. */
  function doAnalyze(cmd) {
    if (!started) {
      fetch('/api/analyze', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job: cmd.job, error: 'audio not started — click ENTER STUDIO in the BeatForge tab first' })
      });
      return;
    }
    toast('analysing…', 8000);
    engine.setState(S);
    engine.ensure();
    engine.render({
      repeats: cmd.repeats || 2, song: !!cmd.song, tail: cmd.tail == null ? 1.5 : cmd.tail
    }).then(function (res) {
      var report = BeatForgeAudio.analyze(res.buffer, cmd);
      return fetch('/api/analyze', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job: cmd.job, report: report })
      });
    }).then(function () {
      toast('analysed');
    }).catch(function (err) {
      toast('analysis failed: ' + err.message, 5000);
      fetch('/api/analyze', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job: cmd.job, error: String(err && err.message || err) })
      });
    });
  }

  // ------------------------------------------------------------- controls ---
  function bindControls() {
    $('btn-play').onclick = function () {
      if (engine.playing) { engine.stop(); S.playing = false; }
      else { engine.play(); S.playing = true; }
      syncPlayButton(); push(true);
    };
    $('btn-stop').onclick = function () {
      engine.stop(); S.playing = false; syncPlayButton(); push(true);
    };
    $('bpm').onchange = function () {
      S.bpm = Math.max(30, Math.min(300, +$('bpm').value)); push(true);
    };
    $('swing').oninput = function () {
      S.swing = parseFloat($('swing').value);
      $('swing-val').textContent = Math.round(S.swing * 100) + '%'; push();
    };
    $('comp-threshold').oninput = function () {
      S.compThreshold = parseFloat($('comp-threshold').value);
      $('comp-threshold-val').textContent = Math.round(S.compThreshold) + 'dB';
      engine.setState(S); push();
    };
    $('comp-ratio').oninput = function () {
      S.compRatio = parseFloat($('comp-ratio').value);
      $('comp-ratio-val').textContent = S.compRatio + ':1';
      engine.setState(S); push();
    };
    $('limiter').onchange = function () {
      S.limiter = $('limiter').checked; engine.setState(S); push();
    };
    $('master').oninput = function () {
      S.masterGain = parseFloat($('master').value); engine.setState(S); push();
    };
    $('proj-name').oninput = function () { S.name = $('proj-name').value; push(); };
    $('key').onchange = function () { S.key = $('key').value; push(true); };
    $('scale').onchange = function () { S.scale = $('scale').value; push(true); };

    $('pat-add').onclick = function () {
      var p = pattern(), grid = {};
      S.tracks.forEach(function (t) { grid[t.id] = new Array(p.steps).fill(null); });
      var used = S.patterns.map(function (x) { return x.name; });
      var name = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('').filter(function (c) {
        return used.indexOf(c) < 0;
      })[0] || ('P' + (S.patterns.length + 1));
      S.patterns.push({ name: name, steps: p.steps, grid: grid });
      S.current = S.patterns.length - 1;
      push(true); renderAll();
    };
    $('pat-dup').onclick = function () {
      var copy = JSON.parse(JSON.stringify(pattern()));
      var used = S.patterns.map(function (x) { return x.name; });
      copy.name = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('').filter(function (c) {
        return used.indexOf(c) < 0;
      })[0] || (copy.name + "'");
      S.patterns.splice(S.current + 1, 0, copy);
      S.current += 1;
      push(true); renderAll();
    };
    $('pat-del').onclick = function () {
      if (S.patterns.length < 2) { toast('need at least one pattern'); return; }
      S.patterns.splice(S.current, 1);
      S.song = (S.song || []).filter(function (c) { return c.pattern !== S.current; });
      S.current = Math.max(0, S.current - 1);
      push(true); renderAll();
    };
    $('pat-steps').onchange = function () {
      var n = +$('pat-steps').value, p = pattern();
      Object.keys(p.grid).forEach(function (tid) {
        var r = p.grid[tid];
        while (r.length < n) r.push(null);
        r.length = n;
      });
      p.steps = n;
      push(true); renderAll();
    };

    $('song-mode').onchange = function () { S.songMode = $('song-mode').checked; push(true); };
    $('song-add').onclick = function () {
      S.song = S.song || [];
      S.song.push({ pattern: S.current, repeat: 4 });
      push(true); renderSong();
    };
    $('song-clear').onclick = function () { S.song = []; push(true); renderSong(); };

    $('track-add').onclick = function () {
      var base = 'track', n = 1, id = base + n;
      while (trackById(id)) { n++; id = base + n; }
      var defs = META.drumEngines.perc || {};
      var t = {
        id: id, name: 'Track ' + n, engine: 'perc', kind: 'drum', gain: 0.7, pan: 0,
        mute: false, solo: false, reverb: 0, delay: 0, duck: 0,
        params: JSON.parse(JSON.stringify(defs))
      };
      S.tracks.push(t);
      S.patterns.forEach(function (p) { p.grid[id] = new Array(p.steps).fill(null); });
      selected = id;
      push(true); engine.rebuildChains(); renderRows(); renderInspector();
    };

    $('btn-export').onclick = function () {
      doExport({ job: null, filename: S.name, repeats: 2, song: !!S.songMode, tail: 1.5 });
    };

    document.addEventListener('keydown', function (e) {
      var tag = (e.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'select' || tag === 'textarea') return;
      if ((e.ctrlKey || e.metaKey) && (e.key === 'z' || e.key === 'Z')) {
        e.preventDefault();
        var back = !e.shiftKey;
        fetch(back ? '/api/undo' : '/api/redo', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ steps: 1 })
        }).then(function (r) { return r.json(); }).then(function (r) {
          toast(r.applied ? (back ? 'undo' : 'redo') + ' — ' + r.undo + ' left'
                          : 'nothing to ' + (back ? 'undo' : 'redo'));
        });
        return;
      }
      if (e.code === 'Space') {
        e.preventDefault();
        $('btn-play').click();
      } else if (e.key >= '1' && e.key <= '9') {
        var i = +e.key - 1;
        if (i < S.patterns.length) { S.current = i; push(true); renderAll(); }
      }
    });
  }

  function fillSelects() {
    var k = $('key');
    ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'].forEach(function (n) {
      var o = document.createElement('option'); o.value = n; o.textContent = n; k.appendChild(o);
    });
    var s = $('scale');
    (META.scales || ['minor', 'major']).forEach(function (n) {
      var o = document.createElement('option'); o.value = n; o.textContent = n; s.appendChild(o);
    });
  }

  // ----------------------------------------------------------------- boot ---
  engine.onStep = function (step) { engine.lastStep = step; };

  fetch('/api/meta').then(function (r) { return r.json(); }).then(function (m) {
    META = m;
    fillSelects();
    return fetch('/api/state');
  }).then(function (r) { return r.json(); }).then(function (state) {
    S = state;
    engine.setState(S);
    selected = S.tracks.length ? S.tracks[0].id : null;
    renderAll();
    bindControls();
    bindGrid();
    bindScrollSync();
    connect();
    scopeLoop();
    playheadLoop();
    $('gate-hint').textContent = 'connected — ' + S.tracks.length + ' tracks loaded';
  }).catch(function (err) {
    $('gate-hint').textContent = 'could not reach the BeatForge server: ' + err.message;
  });

  $('gate-btn').onclick = function () {
    try {
      engine.ensure();
      started = true;
    } catch (err) {
      $('gate-hint').textContent = 'audio failed to start: ' + err.message;
      return;
    }
    $('gate').classList.add('hidden');
    if (S && S.playing) engine.play();
    syncPlayButton();
  };

  // Debug handle — handy from the browser console, and used by the export flow.
  window.BeatForge = {
    engine: engine,
    state: function () { return S; },
    push: push,
    render: function (o) { engine.setState(S); engine.ensure(); return engine.render(o || {}); }
  };
})();
