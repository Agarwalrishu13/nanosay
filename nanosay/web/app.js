/* nanoSay — the page.
   No framework, no build step. The interesting part is the read-along: each
   sentence is a span, and the app moves one class between them as the voice
   reaches each sentence. */

const $ = (id) => document.getElementById(id);
const app = {
  state: null,
  speech: null,
  doc: null,          // {title, name, note, sentences, estimate}
  readingId: null,
  cursor: 0,
  timer: null,
  listening: false,
  spans: [],          // the sentence elements currently on screen
};

// --------------------------------------------------------------------------
async function get(url) {
  const response = await fetch(url);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || ('The app answered with ' + response.status));
  return data;
}

async function post(url, body, raw) {
  const response = await fetch(url, {
    method: 'POST',
    headers: raw ? {} : { 'Content-Type': 'application/json' },
    body: raw || JSON.stringify(body || {}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || ('The app answered with ' + response.status));
  return data;
}

function toast(message, kind) {
  const node = document.createElement('div');
  node.className = 'toast' + (kind ? ' ' + kind : '');
  node.textContent = message;
  $('toasts').appendChild(node);
  setTimeout(() => node.remove(), kind === 'bad' ? 10000 : 5000);
}

function busy(button, on, label) {
  if (!button) return;
  if (on) {
    button.dataset.label = button.textContent;
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span> ' + (label || 'working…');
  } else {
    button.disabled = false;
    button.textContent = button.dataset.label || label || 'Done';
  }
}

function go(step) {
  document.querySelectorAll('.panel').forEach((panel) => { panel.hidden = true; });
  $('panel-' + step).hidden = false;
  const order = ['give', 'look', 'listen', 'keep'];
  document.querySelectorAll('.step').forEach((button) => {
    const name = button.dataset.step;
    button.classList.toggle('active', name === step);
    button.classList.toggle('done', order.indexOf(name) < order.indexOf(step));
  });
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// --------------------------------------------------------------------------
// 1. What this computer can say
// --------------------------------------------------------------------------
async function loadState(checking) {
  busy($('lookBtn'), true, 'looking…');
  try {
    const data = checking ? await post('/api/look-again', {}) : await get('/api/state');
    app.state = data;
    app.speech = data.speech;
    renderSpeech();
    renderLibrary();
    fillSettings();
    if (data.laama) $('laamaState').textContent = data.laama.sentence;
    if (data.message) toast(data.message, data.speech.available ? 'good' : 'bad');
  } catch (error) {
    toast(error.message, 'bad');
  } finally {
    busy($('lookBtn'), false, 'Look again');
  }
}

async function loadSamples() {
  try {
    const data = await get('/api/samples');
    const holder = $('sampleList');
    holder.innerHTML = '';
    data.samples.forEach((sample) => {
      const button = document.createElement('button');
      button.className = 'sample-item';
      button.type = 'button';
      button.innerHTML = '<div class="info"><b></b><span></span></div>';
      button.querySelector('b').textContent = sample.title;
      button.querySelector('span').textContent = sample.blurb;
      button.addEventListener('click', async () => {
        busy(button, true, 'loading…');
        try {
          showDocument(await post('/api/samples/load', { id: sample.id }));
        } catch (error) {
          toast(error.message, 'bad');
        } finally {
          busy(button, false, sample.title);
        }
      });
      holder.appendChild(button);
    });
  } catch (error) {
    toast(error.message, 'bad');
  }
}

function renderSpeech() {
  const speech = app.speech;
  if (!speech) return;
  $('voicePill').textContent = speech.available
    ? speech.voices.length + ' voice' + (speech.voices.length === 1 ? '' : 's') + ' ready'
    : 'no voice found';
  $('voicePill').className = 'pill ' + (speech.available ? 'good' : 'warn');
  $('engineHeadline').textContent = speech.available ? speech.label : 'No voice found';
  $('engineNote').textContent = speech.note;

  const select = $('voiceSelect');
  select.innerHTML = '';
  speech.voices.forEach((voice) => {
    const option = document.createElement('option');
    option.value = voice.id;
    option.textContent = voice.name + (voice.gender ? '  ·  ' + voice.gender : '')
      + (voice.language ? '  ·  ' + voice.language : '');
    select.appendChild(option);
  });
  const wanted = app.state?.settings?.voice;
  if (wanted && speech.voices.some((voice) => voice.id === wanted)) select.value = wanted;

  const rate = speech.rate || { min: -10, max: 10, default: 0 };
  const range = $('rateRange');
  range.min = rate.min; range.max = rate.max;
  const saved = app.state?.settings?.rate;
  range.value = saved === null || saved === undefined ? rate.default : saved;
  describeRate();

  $('volumeField').hidden = !speech.supports_volume;
  $('recordBtn').disabled = !speech.can_save;
  $('recordBtn').title = speech.can_save ? '' : 'This voice engine cannot save a recording';
}

function describeRate() {
  const rate = app.speech?.rate || { default: 0, unit: '' };
  const value = Number($('rateRange').value);
  const span = rate.max - rate.min || 1;
  const where = (value - rate.min) / span;
  let word = 'normal';
  if (where < 0.34) word = 'slower than normal';
  else if (where < 0.46) word = 'a little slower';
  else if (where > 0.66) word = 'faster than normal';
  else if (where > 0.54) word = 'a little faster';
  $('rateLabel').textContent = word + ' (' + value + ')';
  $('volumeLabel').textContent = $('volumeRange').value + '%';
}

// --------------------------------------------------------------------------
// 2. Showing what arrived
// --------------------------------------------------------------------------
function showDocument(doc) {
  app.doc = doc;
  $('docTitle').textContent = doc.title || doc.name || 'your document';
  $('sourceLabel').textContent = doc.kind === 'words' ? 'Words you pasted'
    : doc.kind === 'page' ? 'Fetched from the web'
    : doc.kind === 'sample' ? 'An example that came with nanoSay' : 'Ready to read';
  $('docNote').textContent = doc.note || '';
  const estimate = doc.estimate || {};
  $('stats').innerHTML = '';
  [['Words', (doc.words || estimate.words || 0).toLocaleString()],
   ['Sentences', (doc.sentences || []).length],
   ['Listening time', estimate.spoken || '—']].forEach(([key, value]) => {
    const stat = document.createElement('div');
    stat.className = 'stat';
    stat.innerHTML = '<div class="v"></div><div class="k"></div>';
    stat.querySelector('.v').textContent = value;
    stat.querySelector('.k').textContent = key;
    $('stats').appendChild(stat);
  });

  renderSentences($('previewText'), -1);
  $('shortenNote').textContent = app.state?.settings?.shorten_first
    ? 'Shortening is switched on: “Read it to me” will ask nanoLaama first.'
    : '';
  go('look');
}

function renderSentences(container, index) {
  container.innerHTML = '';
  app.spans = [];
  (app.doc.sentences || []).forEach((sentence, position) => {
    const span = document.createElement('span');
    span.className = 'sentence'
      + (position < index ? ' done' : '')
      + (position === index ? ' now' : '');
    span.textContent = sentence + ' ';
    span.addEventListener('click', () => startFrom(position));
    container.appendChild(span);
    app.spans.push(span);
  });
}

function moveHighlight(index, container) {
  app.spans.forEach((span, position) => {
    span.classList.toggle('now', position === index);
    span.classList.toggle('done', position < index);
  });
  const span = app.spans[index];
  if (span) {
    const padding = container.clientHeight / 2;
    container.scrollTop = Math.max(0, span.offsetTop - padding);
  }
  $('nowCount').textContent = 'sentence ' + Math.min(index + 1, app.spans.length) + ' of ' + app.spans.length;
  $('nowSentence').textContent = (app.doc.sentences[index] || '').slice(0, 180);
  $('listenBar').style.width = Math.round(index * 100 / Math.max(1, app.spans.length)) + '%';
}

// --------------------------------------------------------------------------
// 3. Listening
// --------------------------------------------------------------------------
function chosenVoice() { return $('voiceSelect').value; }
function chosenRate() { return Number($('rateRange').value); }
function chosenVolume() { return app.speech?.supports_volume ? Number($('volumeRange').value) : 100; }

async function startFrom(index) {
  if (!app.doc) return;
  const sentences = app.doc.sentences.slice(index);
  if (!sentences.length) return;
  await read(sentences, index);
}

async function read(sentences, offset) {
  try {
    const answer = await post('/api/read', {
      sentences,
      voice: chosenVoice(),
      rate: chosenRate(),
      volume: chosenVolume(),
      title: app.doc.title || app.doc.name,
      source: app.doc.path || app.doc.address || '',
    });
    app.readingId = answer.reading_id;
    app.cursor = 0;
    app.offset = offset || 0;
    app.listening = true;
    $('listenTitle').textContent = 'Reading “' + (app.doc.title || app.doc.name) + '”';
    $('listenSub').textContent = (app.speech?.label || '') + ' · ' + sentences.length
      + ' sentences · starting at ' + ((offset || 0) + 1);
    renderSentences($('listenText'), app.offset);
    $('pauseBtn').textContent = '⏸ Pause';
    moveHighlight(app.offset, $('listenText'));
    go('listen');
    poll();
  } catch (error) {
    toast(error.message, 'bad');
  }
}

function poll() {
  clearTimeout(app.timer);
  app.timer = setTimeout(ask, 400);
}

async function ask() {
  if (!app.readingId) return;
  let payload;
  try {
    payload = await get('/api/reading/' + app.readingId + '?since=' + app.cursor);
  } catch (error) {
    toast(error.message, 'bad');
    return;
  }
  app.cursor = payload.cursor || 0;
  const where = (app.offset || 0) + (payload.index || 0);
  if (payload.state === 'reading' || payload.state === 'paused') {
    moveHighlight(where, $('listenText'));
  }
  if (payload.state === 'reading') { poll(); return; }

  app.listening = false;
  if (payload.state === 'done') {
    moveHighlight((app.offset || 0) + (payload.total || 0), $('listenText'));
    $('pauseBtn').textContent = '▶ Read again';
    toast('Finished reading.', 'good');
  } else if (payload.state === 'paused') {
    $('pauseBtn').textContent = '▶ Carry on';
  } else if (payload.state === 'stopped') {
    $('pauseBtn').textContent = '▶ Read again';
    toast('Stopped where it was.', 'good');
  } else if (payload.state === 'error') {
    toast(payload.error || 'The voice stopped.', 'bad');
  }
  refreshLibrary();
}

async function togglePause() {
  if (!app.readingId) return startFrom(0);
  const button = $('pauseBtn');
  const paused = button.textContent.includes('Carry on');
  try {
    const answer = await post('/api/reading/' + app.readingId + (paused ? '/resume' : '/pause'), {});
    button.textContent = paused ? '⏸ Pause' : '▶ Carry on';
    toast(answer.message, 'good');
    if (paused) poll();
  } catch (error) {
    toast(error.message, 'bad');
  }
}

async function skip() {
  if (!app.readingId) return;
  try { await post('/api/reading/' + app.readingId + '/skip', {}); poll(); }
  catch (error) { toast(error.message, 'bad'); }
}

async function stopReading() {
  if (!app.readingId) return;
  try {
    const answer = await post('/api/reading/' + app.readingId + '/stop', {});
    toast(answer.message, 'good');
    clearTimeout(app.timer);
    refreshLibrary();
  } catch (error) {
    toast(error.message, 'bad');
  }
}

// --------------------------------------------------------------------------
// 4. Shorten, then record
// --------------------------------------------------------------------------
async function shorten() {
  if (!app.doc) return;
  busy($('shortenBtn'), true, 'asking…');
  try {
    const started = await post('/api/shorten', { sentences: app.doc.sentences });
    const payload = await waitForJob(started.job_id, 'shortenNote');
    if (payload.state !== 'done') {
      $('shortenNote').textContent = payload.error || 'That did not work.';
      toast(payload.error || 'nanoLaama could not help.', 'bad');
      return;
    }
    const before = app.doc.sentences.length;
    app.doc.sentences = payload.result.sentences;
    app.doc.estimate = payload.result.estimate;
    app.doc.note = (app.doc.note || '') + '  ' + payload.result.note;
    $('docNote').textContent = app.doc.note;
    renderSentences($('previewText'), -1);
    showStats();
    $('shortenNote').textContent = payload.result.sentence
      + ' It was ' + before + ' before. You can read the whole thing by pressing “Read it to me” — '
      + 'the shortened version is what will be read now.';
    toast('nanoLaama wrote a shorter version.', 'good');
  } catch (error) {
    toast(error.message, 'bad');
    $('shortenNote').textContent = error.message;
  } finally {
    busy($('shortenBtn'), false, 'Read me the short version');
  }
}

function showStats() {
  const doc = app.doc;
  const cells = [['Words', (doc.words || doc.estimate.words || 0).toLocaleString()],
                 ['Sentences', doc.sentences.length],
                 ['Listening time', doc.estimate.spoken]];
  const stats = $('stats').children;
  cells.forEach(([, value], position) => {
    if (stats[position]) stats[position].querySelector('.v').textContent = value;
  });
}

async function waitForJob(jobId, noteId) {
  const note = noteId ? $(noteId) : null;
  let cursor = 0;
  for (;;) {
    const payload = await get('/api/job/' + jobId + '?since=' + cursor);
    cursor = payload.cursor || 0;
    if (note && payload.log && payload.log.length) {
      note.textContent = payload.log[payload.log.length - 1];
    }
    if (payload.state !== 'running') return payload;
    await new Promise((resolve) => setTimeout(resolve, 350));
  }
}

async function record() {
  if (!app.doc) return;
  go('keep');
  $('recordingCard').hidden = false;
  $('downloadBtn').hidden = true;
  $('recordingTitle').textContent = 'Making the recording…';
  $('recordingNote').textContent = 'This runs as fast as the voice can write, which is '
    + 'usually faster than reading it aloud.';
  $('recordLog').textContent = '';
  $('recordBar').style.width = '0%';
  try {
    const started = await post('/api/record', {
      sentences: app.doc.sentences,
      voice: chosenVoice(),
      rate: chosenRate(),
      title: app.doc.title || app.doc.name,
    });
    let cursor = 0;
    for (;;) {
      const payload = await get('/api/job/' + started.job_id + '?since=' + cursor);
      cursor = payload.cursor || 0;
      if (payload.log && payload.log.length) {
        $('recordLog').textContent += payload.log.join('\n') + '\n';
        $('recordLog').scrollTop = $('recordLog').scrollHeight;
      }
      $('recordBar').style.width = (payload.progress || 0) + '%';
      if (payload.state === 'running') {
        await new Promise((resolve) => setTimeout(resolve, 400));
        continue;
      }
      if (payload.state === 'done') {
        const result = payload.result;
        $('recordingTitle').textContent = result.name;
        $('recordingNote').textContent = result.sentence + '  ' + (result.note || '');
        $('downloadBtn').hidden = false;
        $('downloadBtn').onclick = () => { window.location.href = result.download; };
        toast('Recording finished.', 'good');
      } else {
        $('recordingTitle').textContent = 'It did not finish';
        $('recordingNote').textContent = payload.error || '';
        toast(payload.error || 'The recording did not finish.', 'bad');
      }
      refreshLibrary();
      return;
    }
  } catch (error) {
    $('recordingTitle').textContent = 'It did not finish';
    $('recordingNote').textContent = error.message;
    toast(error.message, 'bad');
  }
}

// --------------------------------------------------------------------------
// The library
// --------------------------------------------------------------------------
async function refreshLibrary() {
  try {
    const data = await get('/api/state');
    app.state = data;
    renderLibrary();
  } catch (error) { /* the page keeps working with what it has */ }
}

function renderLibrary() {
  const holder = $('libraryList');
  if (!holder || !app.state) return;
  holder.innerHTML = '';
  const items = app.state.library || [];
  if (!items.length) {
    holder.innerHTML = '<p class="muted">Nothing yet. A recording you make will be listed here.</p>';
    return;
  }
  items.forEach((item) => {
    const row = document.createElement('div');
    row.className = 'sample-item';
    const info = document.createElement('div');
    info.className = 'info';
    const title = document.createElement('b');
    title.textContent = (item.audio_name ? '🎧 ' : '👂 ') + (item.title || item.audio_name || 'a reading');
    const detail = document.createElement('span');
    const when = new Date((item.when || 0) * 1000).toLocaleString();
    detail.textContent = [item.sentences ? item.sentences + ' sentences' : '',
                          item.size_mb ? item.size_mb.toFixed(1) + ' MB' : '',
                          item.shortened ? 'shortened by nanoLaama' : '',
                          when].filter(Boolean).join('  ·  ');
    info.append(title, detail);
    row.appendChild(info);
    if (item.here) {
      const button = document.createElement('button');
      button.className = 'btn small';
      button.textContent = 'Download';
      button.addEventListener('click', () => { window.location.href = '/api/download/' + item.audio_name; });
      row.appendChild(button);
    } else if (item.audio_name) {
      const gone = document.createElement('span');
      gone.className = 'muted';
      gone.textContent = 'cleared out';
      row.appendChild(gone);
    }
    holder.appendChild(row);
  });
}

// --------------------------------------------------------------------------
// Getting documents in
// --------------------------------------------------------------------------
async function uploadFile(file) {
  toast('Copying “' + file.name + '” in…', 'good');
  try {
    const started = await post('/api/upload/start', { name: file.name, size_mb: file.size / 1048576 });
    const chunk = started.chunk_bytes || 4194304;
    let offset = 0;
    while (offset < file.size) {
      const slice = file.slice(offset, offset + chunk);
      const answer = await post('/api/upload/chunk?id=' + started.id + '&offset=' + offset, null, slice);
      offset = answer.received;
    }
    showDocument(await post('/api/upload/finish', { id: started.id }));
  } catch (error) {
    toast(error.message, 'bad');
  }
}

// --------------------------------------------------------------------------
// Settings
// --------------------------------------------------------------------------
function fillSettings() {
  const settings = app.state?.settings || {};
  const speech = app.speech || {};
  $('speechInfo').textContent = speech.note || '';
  $('voiceList').innerHTML = '';
  (speech.voices || []).forEach((voice) => {
    const card = document.createElement('div');
    card.innerHTML = '<b></b><span></span>';
    card.querySelector('b').textContent = voice.name;
    card.querySelector('span').textContent = [voice.gender, voice.language].filter(Boolean).join('  · ');
    $('voiceList').appendChild(card);
  });
  const line = document.createElement('div');
  line.className = 'engine-line';
  line.textContent = 'Engine: ' + (speech.label || 'none found')
    + '  ·  recordings: ' + (speech.can_save ? 'yes' : 'not with this engine');
  $('voiceList').appendChild(line);

  $('addressInput').value = settings.address || '';
  $('keepDays').value = settings.keep_days ?? 7;
  $('folderInfo').textContent = 'Recordings: ' + (app.state?.folder || '')
    + (app.state?.free_mb >= 0 ? '   (' + app.state.free_mb.toLocaleString() + ' MB free)' : '');
  $('siblingsInfo').textContent = 'nanolaama (talk to an AI on your own computer), nanowrap '
    + '(the programs on your computer, with buttons), nanolearn (drop a spreadsheet, get an '
    + 'answer machine), nanodoc (ask a document questions), nonoforge (make a project without '
    + 'coding), nanohome (one window for all of them).';
}

async function saveSettings() {
  try {
    const answer = await post('/api/settings', {
      address: $('addressInput').value,
      keep_days: Number($('keepDays').value) || 7,
    });
    app.state.settings = answer.settings;
  } catch (error) {
    toast(error.message, 'bad');
  }
}

// --------------------------------------------------------------------------
function main() {
  // dropping files
  const zone = $('drop');
  ['dragenter', 'dragover'].forEach((name) => zone.addEventListener(name, (event) => {
    event.preventDefault(); zone.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach((name) => zone.addEventListener(name, (event) => {
    event.preventDefault(); zone.classList.remove('over');
  }));
  zone.addEventListener('drop', (event) => {
    const files = event.dataTransfer?.files;
    if (files && files.length) uploadFile(files[0]);
  });
  $('browseBtn').addEventListener('click', () => $('fileInput').click());
  $('fileInput').addEventListener('change', (event) => {
    if (event.target.files.length) uploadFile(event.target.files[0]);
    event.target.value = '';
  });

  document.querySelectorAll('.step').forEach((button) => {
    button.addEventListener('click', () => {
      const name = button.dataset.step;
      if (name === 'look' && !app.doc) { toast('Give it something to read first.', 'bad'); return; }
      if (name === 'listen' && !app.readingId) { toast('Press “Read it to me” first.', 'bad'); return; }
      go(name);
    });
  });

  $('pasteBtn').addEventListener('click', async () => {
    busy($('pasteBtn'), true, 'reading…');
    try {
      showDocument(await post('/api/paste', { text: $('pasteBox').value }));
    } catch (error) {
      toast(error.message, 'bad');
    } finally {
      busy($('pasteBtn'), false, 'Read what I pasted');
    }
  });

  $('urlBtn').addEventListener('click', async () => {
    busy($('urlBtn'), true, 'fetching…');
    try {
      showDocument(await post('/api/address', { url: $('urlBox').value }));
    } catch (error) {
      toast(error.message, 'bad');
    } finally {
      busy($('urlBtn'), false, 'Fetch and read that page');
    }
  });

  $('playBtn').addEventListener('click', () => startFrom(0));
  $('recordBtn').addEventListener('click', record);
  $('recordFromListenBtn').addEventListener('click', record);
  $('shortenBtn').addEventListener('click', shorten);
  $('pauseBtn').addEventListener('click', togglePause);
  $('skipBtn').addEventListener('click', skip);
  $('stopBtn').addEventListener('click', stopReading);
  $('backToLookBtn').addEventListener('click', () => go('look'));
  $('backToLookBtn2').addEventListener('click', () => go('look'));
  $('revealBtn').addEventListener('click', async () => {
    try {
      const answer = await post('/api/reveal', {});
      toast(answer.message, answer.opened ? 'good' : 'bad');
    } catch (error) { toast(error.message, 'bad'); }
  });
  $('revealFolderBtn').addEventListener('click', async () => {
    try {
      const answer = await post('/api/reveal', {});
      toast(answer.message, answer.opened ? 'good' : 'bad');
    } catch (error) { toast(error.message, 'bad'); }
  });
  $('forgetBtn').addEventListener('click', async () => {
    const answer = await post('/api/forget', { what: 'library' });
    $('tidyLog').textContent = answer.message;
    toast(answer.message, 'good');
    refreshLibrary();
  });
  $('clearBtn').addEventListener('click', async () => {
    const answer = await post('/api/forget', { what: 'all' });
    $('tidyLog').textContent = answer.message;
    toast(answer.message, 'good');
    refreshLibrary();
  });

  $('rateRange').addEventListener('input', describeRate);
  $('volumeRange').addEventListener('input', describeRate);
  $('voiceSelect').addEventListener('change', saveVoice);
  $('rateRange').addEventListener('change', saveVoice);
  $('addressInput').addEventListener('change', async () => {
    await saveSettings();
    try {
      const state = await get('/api/laama');
      $('laamaState').textContent = state.sentence;
    } catch (error) { $('laamaState').textContent = error.message; }
  });
  $('keepDays').addEventListener('change', saveSettings);

  $('lookBtn').addEventListener('click', () => loadState(true));
  $('settingsBtn').addEventListener('click', async () => {
    $('settingsModal').classList.add('open');
    try {
      const state = await get('/api/laama');
      $('laamaState').textContent = state.sentence;
    } catch (error) { $('laamaState').textContent = error.message; }
  });
  document.querySelectorAll('[data-close]').forEach((button) => {
    button.addEventListener('click', () => $(button.dataset.close).classList.remove('open'));
  });
  document.querySelectorAll('.backdrop').forEach((backdrop) => {
    backdrop.addEventListener('click', (event) => {
      if (event.target === backdrop) backdrop.classList.remove('open');
    });
  });

  loadSamples();
  loadState();
}

async function saveVoice() {
  try {
    await post('/api/settings', { voice: chosenVoice(), rate: chosenRate(), volume: chosenVolume() });
  } catch (error) { /* remembering the voice is a nicety, not a requirement */ }
}

main();
