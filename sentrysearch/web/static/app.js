/* =============================================================
   SentrySearch — Client-side application
   ============================================================= */

// -------------------------------------------------------------------
// Tab navigation
// -------------------------------------------------------------------

function switchTab(tabId) {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tabId);
  });
  document.querySelectorAll('.tab-content').forEach(sec => {
    sec.classList.toggle('active', sec.id === `tab-${tabId}`);
  });
  if (tabId === 'library') loadLibrary();
  if (tabId === 'clips') loadClips();
  if (tabId === 'admin') loadAdmin();
}

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => switchTab(btn.dataset.tab));
});


// -------------------------------------------------------------------
// Toast notifications
// -------------------------------------------------------------------

function toast(message, type = 'info', duration = 4000) {
  const container = document.getElementById('toasts');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => {
    el.classList.add('toast-exit');
    el.addEventListener('animationend', () => el.remove());
  }, duration);
}


// -------------------------------------------------------------------
// Helpers
// -------------------------------------------------------------------

function fmtTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function fmtSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function scoreClass(score) {
  if (score >= 0.55) return 'score-high';
  if (score >= 0.40) return 'score-mid';
  return 'score-low';
}

function escAttr(str) {
  return str.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;').replace(/</g,'&lt;');
}

window.__auth = { auth_enabled: false, user: null, oidc_configured: false };

function canWrite() {
  if (!window.__auth.auth_enabled) return true;
  const r = window.__auth.user && window.__auth.user.role;
  return r === 'user' || r === 'admin';
}

async function api(url, opts = {}) {
  const merged = { credentials: 'include', ...opts };
  if (merged.body != null && !(merged.body instanceof FormData)) {
    if (typeof merged.body === 'object') {
      merged.body = JSON.stringify(merged.body);
    }
    const h = { ...(merged.headers || {}) };
    if (!('Content-Type' in h) && !('content-type' in h)) {
      h['Content-Type'] = 'application/json';
    }
    merged.headers = h;
  }
  const res = await fetch(url, merged);
  if (res.status === 401) {
    if (window.__auth.auth_enabled) window.location.href = '/login';
    throw new Error('Unauthorized');
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const d = body.detail;
    const msg = typeof d === 'string' ? d : (Array.isArray(d) ? d.map(x => x.msg).join(', ') : `Request failed (${res.status})`);
    throw new Error(msg);
  }
  if (res.status === 204) return null;
  return res.json();
}

async function initAuth() {
  try {
    const data = await fetch('/api/auth/status', { credentials: 'include' }).then(r => r.json());
    window.__auth = data;
    if (data.auth_enabled && !data.user) {
      window.location.href = '/login';
      return;
    }
    const elUser = document.getElementById('topbar-user');
    const emailEl = document.getElementById('user-email');
    if (data.auth_enabled && data.user) {
      elUser.classList.remove('hidden');
      emailEl.textContent = data.user.email;
    }
    if (data.user && data.user.role === 'admin') {
      document.getElementById('tab-btn-admin').classList.remove('hidden');
    }
    if (data.user && data.user.role === 'viewer') {
      document.getElementById('tab-btn-upload').classList.add('hidden');
    }
  } catch {
    // offline / error — leave defaults
  }
}

// -------------------------------------------------------------------
// Stats / backend badge
// -------------------------------------------------------------------

async function loadStats() {
  try {
    const stats = await api('/api/stats');
    const badge = document.getElementById('backend-badge');
    badge.textContent = `${stats.backend} · ${stats.total_chunks} chunks`;
  } catch {
    // non-critical
  }
}

initAuth().then(() => loadStats());


// -------------------------------------------------------------------
// Chat-based search
// -------------------------------------------------------------------

const chatMessages = document.getElementById('chat-messages');
const searchForm = document.getElementById('search-form');
const searchInput = document.getElementById('search-input');
const searchBtn = document.getElementById('search-btn');
const searchN = document.getElementById('search-n');

function scrollChat() {
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function addUserMessage(text) {
  // Remove welcome screen on first message
  const welcome = chatMessages.querySelector('.chat-welcome');
  if (welcome) welcome.remove();

  const msg = document.createElement('div');
  msg.className = 'chat-msg user';
  msg.innerHTML = `<div class="chat-bubble">${escAttr(text)}</div>`;
  chatMessages.appendChild(msg);
  scrollChat();
}

function addThinking() {
  const msg = document.createElement('div');
  msg.className = 'chat-msg system';
  msg.id = 'chat-thinking';
  msg.innerHTML = `<div class="chat-bubble"><div class="chat-thinking"><span class="spinner"></span> Searching footage…</div></div>`;
  chatMessages.appendChild(msg);
  scrollChat();
  return msg;
}

function removeThinking() {
  const el = document.getElementById('chat-thinking');
  if (el) el.remove();
}

function addSystemResults(query, results) {
  removeThinking();

  const msg = document.createElement('div');
  msg.className = 'chat-msg system';

  if (!results || results.length === 0) {
    msg.innerHTML = `<div class="chat-bubble"><div class="chat-no-results">No results found for "${escAttr(query)}". Try a broader description.</div></div>`;
    chatMessages.appendChild(msg);
    scrollChat();
    return;
  }

  const cards = results.map((r, i) => {
    const sc = scoreClass(r.score);
    const src = escAttr(r.source_file);
    const trimHtml = canWrite()
      ? `<button class="btn-sm" onclick="trimAndPreview(this, '${src}', ${r.start_time}, ${r.end_time})">✂ Trim &amp; Save</button>`
      : '';
    return `
      <div class="chat-result-card">
        <div class="chat-result-rank">#${i + 1}</div>
        <div class="chat-result-body">
          <div class="chat-result-filename" title="${src}">${escAttr(r.filename)}</div>
          <div class="chat-result-meta">
            <span class="result-score ${sc}"><span class="score-dot"></span> ${r.score.toFixed(3)}</span>
            <span class="result-time">${fmtTime(r.start_time)} – ${fmtTime(r.end_time)}</span>
          </div>
          <div class="chat-result-actions">
            <button class="btn-sm" onclick="previewSource(this, '${src}', ${r.start_time})">▶ Preview</button>
            ${trimHtml}
          </div>
          <div class="chat-video-slot"></div>
        </div>
      </div>`;
  }).join('');

  msg.innerHTML = `<div class="chat-bubble">${cards}</div>`;
  chatMessages.appendChild(msg);
  scrollChat();
}

function addSystemError(errorMsg) {
  removeThinking();
  const msg = document.createElement('div');
  msg.className = 'chat-msg system';
  msg.innerHTML = `<div class="chat-bubble"><div class="chat-no-results" style="color:var(--danger)">Error: ${escAttr(errorMsg)}</div></div>`;
  chatMessages.appendChild(msg);
  scrollChat();
}

searchForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const query = searchInput.value.trim();
  if (!query) return;

  addUserMessage(query);
  searchInput.value = '';
  searchBtn.disabled = true;

  addThinking();

  try {
    const data = await api('/api/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, n_results: parseInt(searchN.value) }),
    });
    addSystemResults(query, data.results);
  } catch (err) {
    addSystemError(err.message);
  } finally {
    searchBtn.disabled = false;
    searchInput.focus();
  }
});


// -------------------------------------------------------------------
// Video preview (inline in chat results)
// -------------------------------------------------------------------

function previewSource(btn, sourcePath, startTime) {
  const slot = btn.closest('.chat-result-body').querySelector('.chat-video-slot');

  if (slot.querySelector('video')) {
    slot.innerHTML = '';
    btn.textContent = '▶ Preview';
    return;
  }

  const videoUrl = `/api/video?path=${encodeURIComponent(sourcePath)}`;
  slot.innerHTML = `
    <div class="chat-video-container">
      <video controls preload="auto">
        <source src="${videoUrl}" type="video/mp4">
      </video>
    </div>`;

  const video = slot.querySelector('video');
  video.addEventListener('loadedmetadata', () => {
    video.currentTime = startTime;
    video.play().catch(() => {});
  }, { once: true });

  btn.textContent = '■ Hide';
  scrollChat();
}

async function trimAndPreview(btn, sourcePath, startTime, endTime) {
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Trimming…';

  try {
    const data = await api('/api/trim', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        source_file: sourcePath,
        start_time: startTime,
        end_time: endTime,
      }),
    });

    const slot = btn.closest('.chat-result-body').querySelector('.chat-video-slot');
    slot.innerHTML = `
      <div class="chat-video-container">
        <video controls preload="auto" autoplay>
          <source src="${data.download_url}" type="video/mp4">
        </video>
        <div class="chat-video-actions">
          <a href="${data.download_url}" class="btn-sm" download>⬇ Download clip</a>
        </div>
      </div>`;

    btn.innerHTML = '✓ Saved';
    btn.disabled = true;
    toast(`Clip saved: ${data.filename}`, 'success');
    scrollChat();
  } catch (err) {
    btn.disabled = false;
    btn.textContent = '✂ Trim & Save';
    toast(err.message, 'error');
  }
}


// -------------------------------------------------------------------
// Upload
// -------------------------------------------------------------------

const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('file-input');
const fileInfo = document.getElementById('upload-file-info');
const filenameEl = document.getElementById('upload-filename');
const filesizeEl = document.getElementById('upload-filesize');
const clearBtn = document.getElementById('upload-clear');
const uploadBtn = document.getElementById('upload-btn');
const progressArea = document.getElementById('upload-progress');
const progressFill = document.getElementById('progress-fill');
const statusText = document.getElementById('upload-status');

let selectedFile = null;

dropzone.addEventListener('click', () => fileInput.click());
dropzone.addEventListener('dragover', (e) => {
  e.preventDefault();
  dropzone.classList.add('drag-over');
});
dropzone.addEventListener('dragleave', () => dropzone.classList.remove('drag-over'));
dropzone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropzone.classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (file && file.name.endsWith('.mp4')) selectFile(file);
  else toast('Please drop an MP4 file', 'error');
});

fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) selectFile(fileInput.files[0]);
});

function selectFile(file) {
  selectedFile = file;
  filenameEl.textContent = file.name;
  filesizeEl.textContent = fmtSize(file.size);
  fileInfo.classList.remove('hidden');
  dropzone.classList.add('hidden');
  uploadBtn.disabled = false;
}

clearBtn.addEventListener('click', () => {
  selectedFile = null;
  fileInput.value = '';
  fileInfo.classList.add('hidden');
  dropzone.classList.remove('hidden');
  uploadBtn.disabled = true;
});

uploadBtn.addEventListener('click', async () => {
  if (!selectedFile) return;

  const chunkDuration = document.getElementById('opt-chunk').value;
  const overlap = document.getElementById('opt-overlap').value;

  const formData = new FormData();
  formData.append('file', selectedFile);
  formData.append('chunk_duration', chunkDuration);
  formData.append('overlap', overlap);

  uploadBtn.disabled = true;
  progressArea.classList.remove('hidden');
  progressFill.style.width = '0%';
  progressFill.classList.add('indeterminate');
  statusText.textContent = 'Uploading and indexing… this may take a while';

  try {
    const data = await api('/api/index', {
      method: 'POST',
      body: formData,
    });

    progressFill.classList.remove('indeterminate');
    progressFill.style.width = '100%';

    if (data.status === 'already_indexed') {
      statusText.textContent = 'This file was already indexed.';
      toast('File already indexed', 'info');
    } else {
      statusText.textContent = `Indexed ${data.chunks_indexed} chunks successfully.`;
      toast(`Indexed ${data.chunks_indexed} chunks`, 'success');
    }

    loadStats();

    setTimeout(() => {
      clearBtn.click();
      progressArea.classList.add('hidden');
      progressFill.style.width = '0%';
    }, 3000);

  } catch (err) {
    progressFill.classList.remove('indeterminate');
    progressFill.style.width = '0%';
    statusText.textContent = `Error: ${err.message}`;
    toast(err.message, 'error');
    uploadBtn.disabled = false;
  }
});


// -------------------------------------------------------------------
// Library (with video preview)
// -------------------------------------------------------------------

async function loadLibrary() {
  const list = document.getElementById('library-list');
  const empty = document.getElementById('library-empty');
  const summary = document.getElementById('library-summary');

  try {
    const stats = await api('/api/stats');

    if (stats.total_chunks === 0) {
      list.innerHTML = '';
      empty.classList.remove('hidden');
      summary.textContent = 'No files indexed';
      closeLibraryPreview();
      return;
    }

    empty.classList.add('hidden');
    summary.textContent = `${stats.unique_source_files} files · ${stats.total_chunks} chunks · ${stats.backend} backend`;

    list.innerHTML = stats.files.map(f => {
      const play = f.exists
        ? `<button class="btn-play" onclick="openLibraryPreview('${escAttr(f.path)}', '${escAttr(f.name)}')">▶ Play</button>`
        : '';
      const del = canWrite()
        ? `<button class="btn-delete" onclick="removeFile(this, '${escAttr(f.path)}')">✕ Remove</button>`
        : '';
      return `
      <div class="library-row">
        <span class="library-filename" title="${escAttr(f.path)}">${escAttr(f.name)}</span>
        <span class="library-status ${f.exists ? '' : 'missing'}">
          ${f.exists ? '' : 'missing on disk'}
        </span>
        ${play}
        ${del}
      </div>`;
    }).join('');

  } catch (err) {
    toast(err.message, 'error');
  }
}

function openLibraryPreview(path, name) {
  const panel = document.getElementById('library-preview');
  const title = document.getElementById('library-preview-title');
  const video = document.getElementById('library-video');

  title.textContent = name;
  video.src = `/api/video?path=${encodeURIComponent(path)}`;
  panel.classList.remove('hidden');
  video.play().catch(() => {});
}

function closeLibraryPreview() {
  const panel = document.getElementById('library-preview');
  const video = document.getElementById('library-video');
  video.pause();
  video.src = '';
  panel.classList.add('hidden');
}

async function removeFile(btn, path) {
  if (!confirm(`Remove "${path.split('/').pop()}" from the index?`)) return;

  btn.disabled = true;
  btn.textContent = '…';

  try {
    const data = await api(`/api/files?path=${encodeURIComponent(path)}`, {
      method: 'DELETE',
    });
    toast(`Removed ${data.removed_chunks} chunks`, 'success');
    closeLibraryPreview();
    loadLibrary();
    loadStats();
  } catch (err) {
    btn.disabled = false;
    btn.textContent = '✕ Remove';
    toast(err.message, 'error');
  }
}


// -------------------------------------------------------------------
// Clips (with video preview)
// -------------------------------------------------------------------

async function loadClips() {
  const list = document.getElementById('clips-list');
  const empty = document.getElementById('clips-empty');
  const summary = document.getElementById('clips-summary');

  try {
    const data = await api('/api/clips');

    if (!data.clips || data.clips.length === 0) {
      list.innerHTML = '';
      empty.classList.remove('hidden');
      summary.textContent = 'No clips saved';
      closeClipsPreview();
      return;
    }

    empty.classList.add('hidden');
    summary.textContent = `${data.clips.length} clip${data.clips.length !== 1 ? 's' : ''}`;

    list.innerHTML = data.clips.map(c => `
      <div class="clip-row">
        <span class="clip-name" title="${escAttr(c.filename)}">${escAttr(c.filename)}</span>
        <span class="clip-size">${fmtSize(c.size_bytes)}</span>
        <button class="btn-play" onclick="openClipsPreview('${escAttr(c.download_url)}', '${escAttr(c.filename)}')">▶ Play</button>
        <a href="${c.download_url}" class="btn-download" download>⬇ Download</a>
      </div>
    `).join('');

  } catch (err) {
    toast(err.message, 'error');
  }
}

function openClipsPreview(url, name) {
  const panel = document.getElementById('clips-preview');
  const title = document.getElementById('clips-preview-title');
  const video = document.getElementById('clips-video');

  title.textContent = name;
  video.src = url;
  panel.classList.remove('hidden');
  video.play().catch(() => {});
}

function closeClipsPreview() {
  const panel = document.getElementById('clips-preview');
  const video = document.getElementById('clips-video');
  video.pause();
  video.src = '';
  panel.classList.add('hidden');
}


// -------------------------------------------------------------------
// Admin panel
// -------------------------------------------------------------------

let _adminAuthToggleBusy = false;

async function loadAdmin() {
  const toggle = document.getElementById('auth-enabled-toggle');
  const hint = document.getElementById('auth-toggle-hint');
  try {
    const cfg = await api('/api/admin/config');
    toggle.checked = cfg.auth_enabled;
    const redirEl = document.getElementById('admin-oauth-redirect');
    if (!cfg.oidc_configured) {
      hint.textContent =
        'OIDC is not configured on the server (OIDC_ISSUER / OIDC_CLIENT_ID). You cannot turn SSO on until those are set.';
      redirEl.classList.add('hidden');
      redirEl.textContent = '';
    } else {
      hint.textContent = cfg.auth_enabled
        ? 'Users must sign in with your identity provider.'
        : 'When enabled, only registered users can access the app.';
      const u = cfg.oauth_redirect_uri;
      if (u) {
        redirEl.classList.remove('hidden');
        const src = cfg.oauth_redirect_uri_is_explicit
          ? ' (from <code>OIDC_REDIRECT_URI</code>)'
          : '';
        redirEl.innerHTML = `<strong>Allowed redirect URI</strong> — add this exact value in your OAuth client (Google Cloud → Credentials → your client → Authorized redirect URIs):${src}<code>${escAttr(u)}</code>`;
      } else {
        redirEl.classList.add('hidden');
      }
    }
    toggle.disabled = !cfg.oidc_configured && !cfg.auth_enabled;

    const usersData = await api('/api/admin/users');
    const box = document.getElementById('admin-users-list');
    box.innerHTML = usersData.users
      .map(
        u => `
      <div class="admin-user-row" data-id="${u.id}">
        <span title="${escAttr(u.email)}">${escAttr(u.email)}</span>
        <select data-user-role="${u.id}" onchange="adminPatchRole(${u.id}, this.value)">
          <option value="viewer" ${u.role === 'viewer' ? 'selected' : ''}>viewer</option>
          <option value="user" ${u.role === 'user' ? 'selected' : ''}>user</option>
          <option value="admin" ${u.role === 'admin' ? 'selected' : ''}>admin</option>
        </select>
        <button type="button" class="btn-icon-del" onclick="adminDeleteUser(${u.id})">Remove</button>
      </div>`,
      )
      .join('');
  } catch (err) {
    toast(err.message, 'error');
  }
}

document.getElementById('auth-enabled-toggle')?.addEventListener('change', async e => {
  if (_adminAuthToggleBusy) return;
  _adminAuthToggleBusy = true;
  try {
    await api('/api/admin/settings/auth', {
      method: 'PATCH',
      body: JSON.stringify({ auth_enabled: e.target.checked }),
    });
    toast(e.target.checked ? 'SSO is now required' : 'SSO disabled — open access', 'success');
    window.location.reload();
  } catch (err) {
    e.target.checked = !e.target.checked;
    toast(err.message, 'error');
  } finally {
    _adminAuthToggleBusy = false;
  }
});

document.getElementById('admin-add-user')?.addEventListener('click', async () => {
  const email = document.getElementById('admin-new-email').value.trim();
  const role = document.getElementById('admin-new-role').value;
  if (!email) {
    toast('Enter an email', 'error');
    return;
  }
  try {
    await api('/api/admin/users', {
      method: 'POST',
      body: JSON.stringify({ email, role }),
    });
    document.getElementById('admin-new-email').value = '';
    toast('User added', 'success');
    loadAdmin();
  } catch (err) {
    toast(err.message, 'error');
  }
});

async function adminPatchRole(userId, role) {
  try {
    await api(`/api/admin/users/${userId}`, {
      method: 'PATCH',
      body: JSON.stringify({ role }),
    });
    toast('Role updated', 'success');
    if (userId === window.__auth.user?.id) window.location.reload();
  } catch (err) {
    toast(err.message, 'error');
    loadAdmin();
  }
}

async function adminDeleteUser(userId) {
  if (!confirm('Remove this user? They will not be able to sign in.')) return;
  try {
    await api(`/api/admin/users/${userId}`, { method: 'DELETE' });
    toast('User removed', 'success');
    loadAdmin();
  } catch (err) {
    toast(err.message, 'error');
  }
}
