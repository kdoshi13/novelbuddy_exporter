const state = {
  buildId: "",
  novelSlug: "",
  novelTitle: "",
  nextSlug: "",
  startSaveChapter: 1,
  chapters: [],
  running: false,
  downloadAll: false,
};

const $ = (id) => document.getElementById(id);
const resumeKey = (slug) => `novel-exporter:${slug}:resume`;

function setStatus(text) {
  $("status").textContent = text;
}

function log(line) {
  const box = $("log");
  box.textContent += `${line}\n`;
  box.scrollTop = box.scrollHeight;
}

function updateScrapeUi() {
  $("scrapeCount").textContent = `${state.chapters.length} chapters collected`;
  $("nextSlug").textContent = `Next: ${state.nextSlug || "none"}`;
  $("continueScrape").disabled = state.running || !state.nextSlug;
  $("downloadAll").disabled = state.running;
  $("exportScrape").disabled = state.running || state.chapters.length === 0;
  updateResumeUi();
}

function getSavedResume(slug) {
  if (!slug) return null;
  try {
    return JSON.parse(localStorage.getItem(resumeKey(slug)) || "null");
  } catch {
    return null;
  }
}

function saveResume() {
  if (!state.novelSlug || !state.nextSlug) return;
  localStorage.setItem(resumeKey(state.novelSlug), JSON.stringify({
    nextSlug: state.nextSlug,
    title: state.novelTitle,
    startSaveChapter: state.startSaveChapter,
    savedAt: new Date().toISOString(),
  }));
  updateResumeUi();
}

function updateResumeUi() {
  const slug = $("novelSlug").value.trim() || state.novelSlug;
  const saved = getSavedResume(slug);
  $("useResume").disabled = state.running || !saved?.nextSlug;
  $("resumeInfo").textContent = saved?.nextSlug
    ? `Saved next batch for ${slug}: ${saved.nextSlug}`
    : "No saved next batch";
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `Request failed: ${response.status}`);
  }
  return data;
}

function chapterNumFromName(name) {
  const padded = name.match(/^(\d+)_/);
  if (padded) return Number(padded[1]);
  const chapter = name.match(/chapter[_\s-]*(\d+)/i);
  return chapter ? Number(chapter[1]) : null;
}

function cleanTitleFromName(name) {
  return name.replace(/\.txt$/i, "").replace(/^\d+_/, "").replaceAll("_", " ");
}

function combinedText(chapters, title) {
  const parts = [];
  if (title.trim()) {
    parts.push(title.trim());
    parts.push("=".repeat(Math.min(Math.max(title.trim().length, 12), 70)));
  }
  for (const chapter of chapters) {
    // Emit a chapter heading line so the DOCX generator can render it as a heading
    if (chapter.title) {
      parts.push(`CHAPTER: ${chapter.title.trim()}`);
    }
    parts.push((chapter.text || "").trim());
  }
  return `${parts.join("\n\n").trim()}\n`;
}

async function downloadExport(text, format, filename) {
  setStatus("Exporting");
  const response = await fetch("/api/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, format, filename }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || `Export failed: ${response.status}`);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${filename}.${format}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  setStatus("Ready");
}

async function scrapeBatch(downloadAll = false) {
  if (state.running || !state.nextSlug) return;
  state.running = true;
  setStatus("Scraping");
  updateScrapeUi();

  try {
    do {
      let savedThisBatch = 0;
      while (savedThisBatch < 100 && state.nextSlug) {
        const data = await postJson("/api/chapter", {
          novel_slug: state.novelSlug,
          build_id: state.buildId,
          chapter_slug: state.nextSlug,
        });
        state.buildId = data.build_id;
        state.nextSlug = data.next_slug || "";
        const chapterNum = data.chapter.chapter_num;
        if (chapterNum == null || chapterNum >= state.startSaveChapter) {
          state.chapters.push(data.chapter);
          savedThisBatch += 1;
          log(`[OK] ${chapterNum || "?"} ${data.chapter.title}`);

          // Auto-populate latest chapter text into TTS textarea
          $("ttsText").value = data.chapter.text || "";
        } else if (chapterNum % 25 === 0 || chapterNum + 1 === state.startSaveChapter) {
          log(`[skip] ${chapterNum} ${data.chapter.title}`);
        }
        updateScrapeUi();
      }

      if (state.nextSlug) {
        saveResume();
        if (downloadAll) {
          log(`Saved ${state.chapters.length} chapters. Continuing from ${state.nextSlug}.`);
        } else {
          setStatus("Paused");
          const more = confirm("Saved 100 chapters. Download the next 100?");
          if (more) {
            downloadAll = false;
            continue;
          }
          break;
        }
      } else {
        setStatus("Complete");
        log("No more chapters found.");
      }
    } while (downloadAll && state.nextSlug);
  } catch (error) {
    setStatus("Error");
    log(`[ERROR] ${error.message}`);
  } finally {
    state.running = false;
    state.downloadAll = false;
    updateScrapeUi();
  }
}

$("scrapeForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  state.novelSlug = $("novelSlug").value.trim();
  state.novelTitle = $("novelTitle").value.trim() || state.novelSlug;
  state.nextSlug = $("firstSlug").value.trim() || "chapter-1";
  state.startSaveChapter = Math.max(1, Number($("startSaveChapter").value) || 1);
  state.chapters = [];
  state.buildId = "";
  $("log").textContent = "";
  updateScrapeUi();

  try {
    setStatus("Preparing");
    const data = await postJson("/api/build", { novel_slug: state.novelSlug });
    state.buildId = data.build_id;
    log(`Build ID: ${state.buildId}`);
    await scrapeBatch(state.downloadAll);
  } catch (error) {
    setStatus("Error");
    log(`[ERROR] ${error.message}`);
  }
});

$("startScrape").addEventListener("click", () => {
  state.downloadAll = false;
});

$("continueScrape").addEventListener("click", () => scrapeBatch(false));

$("downloadAll").addEventListener("click", () => {
  state.downloadAll = true;
  $("scrapeForm").requestSubmit();
});

$("useResume").addEventListener("click", () => {
  const slug = $("novelSlug").value.trim();
  const saved = getSavedResume(slug);
  if (!saved?.nextSlug) return;
  $("firstSlug").value = saved.nextSlug;
  if (saved.title && !$("novelTitle").value.trim()) {
    $("novelTitle").value = saved.title;
  }
  $("startSaveChapter").value = saved.startSaveChapter || 1;
  log(`Using saved next batch slug: ${saved.nextSlug} (start saving from chapter ${$("startSaveChapter").value})`);
});

$("novelSlug").addEventListener("input", updateResumeUi);

$("exportScrape").addEventListener("click", async () => {
  try {
    const format = $("scrapeFormat").value;
    const text = combinedText(state.chapters, state.novelTitle);
    const first = state.chapters[0]?.chapter_num || 1;
    const last = state.chapters[state.chapters.length - 1]?.chapter_num || state.chapters.length;
    await downloadExport(text, format, `${state.novelSlug || "chapters"}_${first}_${last}`);
  } catch (error) {
    setStatus("Error");
    log(`[ERROR] ${error.message}`);
  }
});

$("combineForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const files = [...$("txtFiles").files];
  const start = Number($("startChapter").value);
  const end = Number($("endChapter").value);
  const low = Math.min(start, end);
  const high = Math.max(start, end);
  const selected = files
    .map((file) => ({ file, num: chapterNumFromName(file.name) }))
    .filter((item) => item.num !== null && item.num >= low && item.num <= high)
    .sort((a, b) => a.num - b.num || a.file.name.localeCompare(b.file.name));

  if (!selected.length) {
    alert("No matching chapter TXT files found in that range.");
    return;
  }

  setStatus("Reading");
  const chapters = [];
  for (const item of selected) {
    chapters.push({
      chapter_num: item.num,
      title: cleanTitleFromName(item.file.name),
      text: await item.file.text(),
    });
  }

  try {
    const title = $("combineTitle").value.trim();
    const format = $("combineFormat").value;
    const text = combinedText(chapters, title);
    await downloadExport(text, format, `chapters_${low}_${high}`);
  } catch (error) {
    setStatus("Error");
    alert(error.message);
  }
});

function activateTab(tab) {
  const scrape = tab === "scrape";
  const combine = tab === "combine";
  const tts = tab === "tts";
  $("scrapeTab").classList.toggle("active", scrape);
  $("combineTab").classList.toggle("active", combine);
  $("ttsTab").classList.toggle("active", tts);
  $("scrapeView").hidden = !scrape;
  $("combineView").hidden = !combine;
  $("ttsView").hidden = !tts;
}

$("scrapeTab").addEventListener("click", () => activateTab("scrape"));
$("combineTab").addEventListener("click", () => activateTab("combine"));
$("ttsTab").addEventListener("click", () => activateTab("tts"));
updateScrapeUi();

// ================== TTS (Read Aloud) ==================

const ttsState = {
  audioBlob: null,
  audioUrl: "",
  generating: false,
  catalog: [],          // full catalog from server
  previewPlaying: "",   // voice_id currently previewing
  
  // Streaming queue state
  chunks: [],           // string array of text paragraphs
  chunkIndices: [],     // array of {start, end} to highlight text
  currentIndex: 0,      
  nextAudioUrl: "",
  isPlayingQueue: false,
  abortController: null,
};

function ttsStatus(text, cls = "") {
  const el = $("ttsStatus");
  el.textContent = text;
  el.className = "tts-status" + (cls ? " " + cls : "");
}

function fmtTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// ---- Voice catalog ----

function renderVoiceCard(v) {
  const card = document.createElement("div");
  card.className = "vc" + (v.downloaded ? " downloaded" : "");
  card.dataset.voiceId = v.id;

  const langLabel = v.language.replace("_", " ");

  card.innerHTML = `
    <div class="vc-info">
      <div class="vc-name">${v.name}</div>
      <div class="vc-meta">
        ${langLabel} · ${v.size_mb} MB
        <span class="vc-badge ${v.quality}">${v.quality}</span>
      </div>
    </div>
    <div class="vc-actions">
      ${v.downloaded
        ? `<button class="vc-btn preview-btn" data-voice="${v.id}" title="Preview">▶</button>
           <span class="vc-check">✓</span>`
        : `<button class="vc-btn" data-download="${v.id}">⬇</button>`
      }
    </div>
  `;
  return card;
}

function refreshVoiceDropdown() {
  const sel = $("ttsVoice");
  const current = sel.value;
  sel.innerHTML = "";
  const downloaded = ttsState.catalog.filter(v => v.downloaded);
  if (downloaded.length > 0) {
    for (const v of downloaded) {
      const opt = document.createElement("option");
      opt.value = v.id;
      opt.textContent = `${v.name} (${v.language.replace("_", " ")}, ${v.quality})`;
      sel.appendChild(opt);
    }
    // Restore previous selection if still valid
    if (current && downloaded.some(v => v.id === current)) {
      sel.value = current;
    }
  } else {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "(no voices — download one from the catalog)";
    opt.disabled = true;
    sel.appendChild(opt);
  }
}

function renderCatalog() {
  const container = $("ttsCatalog");
  container.innerHTML = "";
  const downloaded = ttsState.catalog.filter(v => v.downloaded).length;
  $("ttsCatalogCount").textContent = `${downloaded} / ${ttsState.catalog.length} downloaded`;

  for (const v of ttsState.catalog) {
    container.appendChild(renderVoiceCard(v));
  }
  refreshVoiceDropdown();
}

async function loadCatalog() {
  try {
    const res = await fetch("/api/tts/catalog");
    const data = await res.json();
    if (data.voices) {
      ttsState.catalog = data.voices;
      renderCatalog();
    }
  } catch (err) {
    $("ttsCatalog").innerHTML = `<div class="tts-catalog-loading" style="color:#c44">Failed to load catalog: ${err.message}</div>`;
  }
}

// ---- Single voice download (from card) ----

$("ttsCatalog").addEventListener("click", async (e) => {
  const dlBtn = e.target.closest("[data-download]");
  if (dlBtn) {
    const voiceId = dlBtn.dataset.download;
    dlBtn.disabled = true;
    dlBtn.textContent = "…";
    ttsStatus(`Downloading "${voiceId}"…`, "working");
    try {
      const res = await fetch("/api/tts/download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ voice_id: voiceId }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Download failed");
      // Update catalog state
      const v = ttsState.catalog.find(x => x.id === voiceId);
      if (v) v.downloaded = true;
      renderCatalog();
      ttsStatus(`"${voiceId}" downloaded!`);
    } catch (err) {
      ttsStatus(`Download failed: ${err.message}`, "error");
      dlBtn.disabled = false;
      dlBtn.textContent = "⬇";
    }
    return;
  }

  // ---- Preview button ----
  const previewBtn = e.target.closest("[data-voice]");
  if (previewBtn) {
    const voiceId = previewBtn.dataset.voice;
    const previewAudio = $("ttsPreviewAudio");

    // If already playing this voice, stop it
    if (ttsState.previewPlaying === voiceId) {
      previewAudio.pause();
      previewAudio.currentTime = 0;
      ttsState.previewPlaying = "";
      previewBtn.classList.remove("playing");
      previewBtn.textContent = "▶";
      return;
    }

    // Stop any currently playing preview
    if (ttsState.previewPlaying) {
      previewAudio.pause();
      const oldBtn = document.querySelector(`[data-voice="${ttsState.previewPlaying}"]`);
      if (oldBtn) { oldBtn.classList.remove("playing"); oldBtn.textContent = "▶"; }
    }

    previewBtn.classList.add("playing");
    previewBtn.textContent = "⏸";
    ttsState.previewPlaying = voiceId;
    ttsStatus(`Generating preview for "${voiceId}"…`, "working");

    try {
      const res = await fetch("/api/tts/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ voice_id: voiceId }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.error || "Preview failed");
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      previewAudio.src = url;
      previewAudio.load();
      await previewAudio.play();
      ttsStatus(`Playing preview: "${voiceId}"`);

      previewAudio.onended = () => {
        ttsState.previewPlaying = "";
        previewBtn.classList.remove("playing");
        previewBtn.textContent = "▶";
        URL.revokeObjectURL(url);
        ttsStatus("Preview complete.");
      };
    } catch (err) {
      ttsState.previewPlaying = "";
      previewBtn.classList.remove("playing");
      previewBtn.textContent = "▶";
      ttsStatus(`Preview error: ${err.message}`, "error");
    }
  }
});

// ---- Bulk Download All ----

$("ttsDownloadAll").addEventListener("click", async () => {
  const notDownloaded = ttsState.catalog.filter(v => !v.downloaded);
  if (notDownloaded.length === 0) {
    ttsStatus("All voices are already downloaded!");
    return;
  }

  const total = ttsState.catalog.length;
  let done = ttsState.catalog.filter(v => v.downloaded).length;

  $("ttsDownloadAll").disabled = true;
  $("ttsBulkProgress").hidden = false;
  $("ttsBulkFill").style.width = `${(done / total) * 100}%`;
  $("ttsBulkText").textContent = `${done} / ${total}`;
  ttsStatus(`Downloading ${notDownloaded.length} voices…`, "working");

  for (const v of notDownloaded) {
    $("ttsBulkText").textContent = `${done} / ${total} — ${v.id}`;
    try {
      const res = await fetch("/api/tts/download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ voice_id: v.id }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed");
      v.downloaded = true;
    } catch (err) {
      console.error(`Failed to download ${v.id}:`, err);
    }
    done++;
    $("ttsBulkFill").style.width = `${(done / total) * 100}%`;
    $("ttsBulkText").textContent = `${done} / ${total}`;
    renderCatalog();
  }

  $("ttsDownloadAll").disabled = false;
  $("ttsBulkProgress").hidden = true;
  const finalDown = ttsState.catalog.filter(v => v.downloaded).length;
  ttsStatus(`Download complete! ${finalDown} / ${total} voices ready.`);
});

// ---- Generate & Play ----

// Upload file (TXT/DOCX/PDF) to server for chapter extraction
let currentUploadId = null;
let currentChapterIndex = 0;

async function renderChaptersList(chapters) {
  const container = $("chaptersList");
  container.innerHTML = "";
  for (const ch of chapters) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "vc-btn";
    btn.style.display = "block";
    btn.style.width = "100%";
    btn.style.textAlign = "left";
    btn.textContent = `${ch.index + 1}. ${ch.title || 'Chapter ' + (ch.index+1)}`;
    btn.dataset.index = ch.index;
    btn.addEventListener('click', async () => {
      currentChapterIndex = Number(btn.dataset.index) || 0;
      await loadAndShowChapter(currentUploadId, currentChapterIndex);
    });
    container.appendChild(btn);
  }
}

async function loadAndShowChapter(uploadId, index) {
  if (!uploadId) return;
  try {
    const res = await fetch(`/api/upload/${encodeURIComponent(uploadId)}/chapter/${index}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Failed');
    $("ttsText").value = data.text || '';
    ttsStatus(`Loaded: ${data.title || 'Chapter ' + (index+1)}`);
  } catch (err) {
    ttsStatus(`Load error: ${err.message}`, 'error');
  }
}

$('ttsFileUpload').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  ttsStatus(`Uploading ${file.name}…`, 'working');
  try {
    const res = await fetch('/api/upload', { method: 'POST', body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Upload failed');
    currentUploadId = data.id;
    const chapters = data.chapters || [];
    await renderChaptersList(chapters);
    if (chapters.length > 0) {
      currentChapterIndex = 0;
      await loadAndShowChapter(currentUploadId, 0);
    }
    ttsStatus(`Parsed ${chapters.length} chapters from ${file.name}`);
  } catch (err) {
    ttsStatus(`Upload error: ${err.message}`, 'error');
  }
});

// Prev / Next handlers
$("chapterPrev").addEventListener('click', async () => {
  if (currentUploadId == null) return;
  if (currentChapterIndex > 0) {
    currentChapterIndex -= 1;
    await loadAndShowChapter(currentUploadId, currentChapterIndex);
  }
});

$("chapterNext").addEventListener('click', async () => {
  if (currentUploadId == null) return;
  // fetch chapter count from chaptersList
  const list = $("chaptersList");
  const max = list.children.length - 1;
  if (currentChapterIndex < max) {
    currentChapterIndex += 1;
    await loadAndShowChapter(currentUploadId, currentChapterIndex);
  }
});

$("ttsGenerate").addEventListener("click", async () => {
  const text = $("ttsText").value.trim();
  if (!text) {
    ttsStatus("Enter or paste chapter text first.", "error");
    return;
  }
  const voiceId = $("ttsVoice").value;
  
  // Reset queue state
  ttsState.isPlayingQueue = true;
  ttsState.currentIndex = 0;
  ttsState.nextAudioUrl = "";
  if (ttsState.abortController) {
    ttsState.abortController.abort();
  }
  ttsState.abortController = new AbortController();
  
  // Split text into paragraphs (non-empty)
  ttsState.chunks = [];
  ttsState.chunkIndices = [];
  
  const regex = /([^\n]+)/g;
  let match;
  while ((match = regex.exec($("ttsText").value)) !== null) {
    const chunkText = match[1].trim();
    if (chunkText) {
      ttsState.chunks.push(chunkText);
      ttsState.chunkIndices.push({ start: match.index, end: match.index + match[1].length });
    }
  }

  if (ttsState.chunks.length === 0) return;

  $("ttsGenerate").disabled = true;
  $("ttsStop").disabled = false;
  $("ttsDownloadAudio").disabled = true; // wait until done for full WAV
  $("ttsPlayer").hidden = false;
  
  ttsStatus(`Starting playback (1/${ttsState.chunks.length})…`, "working");
  await playNextChunk(voiceId);
});

async function fetchChunkAudio(text, voiceId, signal) {
  const res = await fetch("/api/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, voice_id: voiceId }),
    signal
  });
  if (!res.ok) throw new Error(`Server error ${res.status}`);
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

async function playNextChunk(voiceId) {
  if (!ttsState.isPlayingQueue || ttsState.currentIndex >= ttsState.chunks.length) {
    // Finished queue
    ttsState.isPlayingQueue = false;
    $("ttsGenerate").disabled = false;
    $("ttsStop").disabled = true;
    $("ttsDownloadAudio").disabled = false;
    ttsStatus("Playback complete.");
    return;
  }

  const index = ttsState.currentIndex;
  const chunkText = ttsState.chunks[index];
  const highlight = ttsState.chunkIndices[index];
  
  // Highlight text in textarea
  const ta = $("ttsText");
  ta.focus();
  ta.setSelectionRange(highlight.start, highlight.end);

  try {
    let urlToPlay;
    if (index === 0) {
      // First chunk, fetch it now
      urlToPlay = await fetchChunkAudio(chunkText, voiceId, ttsState.abortController.signal);
    } else if (ttsState.nextAudioUrl) {
      urlToPlay = ttsState.nextAudioUrl;
      ttsState.nextAudioUrl = "";
    } else {
      urlToPlay = await fetchChunkAudio(chunkText, voiceId, ttsState.abortController.signal);
    }

    if (!ttsState.isPlayingQueue) return; // aborted during fetch

    const audio = $("ttsAudio");
    audio.src = urlToPlay;
    audio.load();
    await audio.play();
    
    ttsStatus(`Playing chunk ${index + 1}/${ttsState.chunks.length}`);

    // Pre-fetch next chunk if available
    ttsState.nextAudioUrl = "";
    if (index + 1 < ttsState.chunks.length) {
      fetchChunkAudio(ttsState.chunks[index + 1], voiceId, ttsState.abortController.signal)
        .then(url => { ttsState.nextAudioUrl = url; })
        .catch(err => { if (err.name !== "AbortError") console.error("Prefetch error", err); });
    }

    // When audio finishes, advance to next
    audio.onended = () => {
      URL.revokeObjectURL(urlToPlay);
      ttsState.currentIndex++;
      playNextChunk(voiceId);
    };

  } catch (err) {
    if (err.name !== "AbortError") {
      ttsStatus(`Error: ${err.message}`, "error");
      $("ttsGenerate").disabled = false;
    }
  }
}

// ---- Stop ----

$("ttsStop").addEventListener("click", () => {
  ttsState.isPlayingQueue = false;
  if (ttsState.abortController) {
    ttsState.abortController.abort();
  }
  const audio = $("ttsAudio");
  audio.pause();
  audio.currentTime = 0;
  audio.onended = null;
  
  $("ttsGenerate").disabled = false;
  $("ttsStop").disabled = true;
  $("ttsDownloadAudio").disabled = false;
  ttsStatus("Stopped.");
});

// ---- Save WAV ----

$("ttsDownloadAudio").addEventListener("click", async () => {
  const text = $("ttsText").value.trim();
  const voiceId = $("ttsVoice").value;
  if (!text) return;

  $("ttsDownloadAudio").disabled = true;
  ttsStatus("Generating full audio for download…", "working");
  try {
    const res = await fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, voice_id: voiceId }),
    });
    if (!res.ok) throw new Error("Failed to generate full audio");
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${state.novelSlug || "chapter"}_tts.wav`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    ttsStatus("Download ready.");
  } catch (err) {
    ttsStatus(`Download error: ${err.message}`, "error");
  } finally {
    $("ttsDownloadAudio").disabled = false;
  }
});

// ---- Progress bar / time ----

(function initAudioProgress() {
  const audio = $("ttsAudio");

  audio.addEventListener("timeupdate", () => {
    if (!audio.duration) return;
    const pct = (audio.currentTime / audio.duration) * 100;
    $("ttsProgress").style.width = pct + "%";
    $("ttsCurrentTime").textContent = fmtTime(audio.currentTime);
  });

  audio.addEventListener("loadedmetadata", () => {
    $("ttsDuration").textContent = fmtTime(audio.duration);
    $("ttsProgress").style.width = "0%";
    $("ttsCurrentTime").textContent = "0:00";
  });

  audio.addEventListener("ended", () => {
    $("ttsProgress").style.width = "100%";
  });

  // Click-to-seek on the waveform bar
  const waveform = document.querySelector(".tts-waveform");
  if (waveform) {
    waveform.addEventListener("click", (e) => {
      if (!audio.duration) return;
      const rect = waveform.getBoundingClientRect();
      const pct = (e.clientX - rect.left) / rect.width;
      audio.currentTime = pct * audio.duration;
    });
  }
})();

// ---- Load catalog on page load ----
loadCatalog();

