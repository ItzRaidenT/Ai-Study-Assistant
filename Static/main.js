// ── State ─────────────────────────────────────────────────────────────────
let selectedFile = null;
let currentFullText = '';

// ── DOM refs ──────────────────────────────────────────────────────────────
const dropZone    = document.getElementById('dropZone');
const fileInput   = document.getElementById('fileInput');
const filePreview = document.getElementById('filePreview');
const progressWrap= document.getElementById('progressWrap');
const resultCard  = document.getElementById('resultCard');

// ── Drag & Drop ────────────────────────────────────────────────────────────
dropZone.addEventListener('click', () => fileInput.click());

dropZone.addEventListener('dragover', (e) => {
  e.preventDefault();
  dropZone.classList.add('drag-over');
});

dropZone.addEventListener('dragleave', () => {
  dropZone.classList.remove('drag-over');
});

dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (file) handleFileSelect(file);
});

fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) handleFileSelect(fileInput.files[0]);
});

document.getElementById('clearFile').addEventListener('click', clearSelection);

// ── File selection ─────────────────────────────────────────────────────────
function handleFileSelect(file) {
  const allowed = ['application/pdf', 'text/plain'];
  const ext = file.name.split('.').pop().toLowerCase();

  if (!['pdf', 'txt'].includes(ext)) {
    showToast('❌ Only PDF and TXT files are supported', 'danger');
    return;
  }

  selectedFile = file;

  document.getElementById('fileIcon').textContent = ext === 'pdf' ? '📕' : '📝';
  document.getElementById('fileName').textContent = file.name;
  document.getElementById('fileSize').textContent = formatSize(file.size);

  dropZone.style.display = 'none';
  filePreview.style.display = 'block';
  resultCard.style.display = 'none';
  progressWrap.style.display = 'none';
}

function clearSelection() {
  selectedFile = null;
  fileInput.value = '';
  dropZone.style.display = 'block';
  filePreview.style.display = 'none';
  resultCard.style.display = 'none';
  progressWrap.style.display = 'none';
}

// ── Upload & Process ───────────────────────────────────────────────────────
async function uploadFile() {
  if (!selectedFile) return;

  // Show progress
  filePreview.style.display = 'none';
  progressWrap.style.display = 'block';
  animateProgress();

  const formData = new FormData();
  formData.append('file', selectedFile);

  try {
    const response = await fetch('/upload', {
      method: 'POST',
      body: formData
    });

    const data = await response.json();

    progressWrap.style.display = 'none';

    if (!data.success) {
      showToast('❌ ' + data.error, 'danger');
      clearSelection();
      return;
    }

    // Show result
    currentFullText = data.full_text;
    document.getElementById('resultFilename').textContent = data.filename;
    document.getElementById('resultMeta').textContent =
      `${data.word_count.toLocaleString()} words extracted · Document ID: #${data.doc_id}`;
    document.getElementById('textPreview').textContent = data.preview;
    resultCard.style.display = 'block';

    showToast('✅ File processed successfully!');
    loadHistory();

  } catch (err) {
    progressWrap.style.display = 'none';
    showToast('❌ Upload failed — please try again', 'danger');
    clearSelection();
  }
}

// ── Progress animation ─────────────────────────────────────────────────────
function animateProgress() {
  const fill = document.getElementById('progressFill');
  const label = document.getElementById('progressLabel');
  const steps = [
    [20,  'Uploading file...'],
    [45,  'Reading document...'],
    [70,  'Extracting text...'],
    [90,  'Saving to history...'],
    [100, 'Done!']
  ];
  let i = 0;
  const interval = setInterval(() => {
    if (i >= steps.length) { clearInterval(interval); return; }
    fill.style.width = steps[i][0] + '%';
    label.textContent = steps[i][1];
    i++;
  }, 400);
}

// ── Full text modal ────────────────────────────────────────────────────────
function viewFullText() {
  document.getElementById('modalTitle').textContent = 'Full Extracted Text';
  document.getElementById('modalBody').textContent = currentFullText;
  document.getElementById('modalOverlay').style.display = 'flex';
}

function closeModal() {
  document.getElementById('modalOverlay').style.display = 'none';
}

function copyText() {
  navigator.clipboard.writeText(currentFullText).then(() => {
    showToast('📋 Text copied to clipboard');
  });
}

// ── History ────────────────────────────────────────────────────────────────
async function loadHistory() {
  const list = document.getElementById('historyList');

  try {
    const res = await fetch('/documents');
    const data = await res.json();

    if (!data.success || data.documents.length === 0) {
      list.innerHTML = `
        <div class="empty-state">
          <p class="empty-icon">🗂</p>
          <p>No documents yet. Upload your first file above.</p>
        </div>`;
      return;
    }

    list.innerHTML = data.documents.map(doc => `
      <div class="history-item" id="doc-${doc.id}">
        <div class="history-file-icon">${doc.filename.endsWith('.pdf') ? '📕' : '📝'}</div>
        <div class="history-info">
          <p class="history-name">${escapeHtml(doc.filename)}</p>
          <p class="history-meta">${doc.word_count.toLocaleString()} words · ${doc.uploaded_at}</p>
          <p class="history-preview">${escapeHtml(doc.preview)}</p>
        </div>
        <div class="history-actions">
          <button class="btn-icon" title="View text" onclick="viewDoc(${doc.id})">👁</button>
          <button class="btn-icon danger" title="Delete" onclick="deleteDoc(${doc.id})">🗑</button>
        </div>
      </div>
    `).join('');

  } catch (err) {
    list.innerHTML = `<p style="color:var(--muted);text-align:center;padding:2rem;">Could not load history.</p>`;
  }
}

async function viewDoc(id) {
  const res = await fetch(`/documents/${id}`);
  const data = await res.json();
  if (!data.success) return;

  currentFullText = data.content;
  document.getElementById('modalTitle').textContent = data.filename;
  document.getElementById('modalBody').textContent = data.content;
  document.getElementById('modalOverlay').style.display = 'flex';
}

async function deleteDoc(id) {
  if (!confirm('Delete this document from history?')) return;

  const res = await fetch(`/documents/${id}`, { method: 'DELETE' });
  const data = await res.json();

  if (data.success) {
    document.getElementById(`doc-${id}`)?.remove();
    showToast('🗑 Document deleted');

    // Show empty state if no docs left
    if (!document.querySelector('.history-item')) {
      document.getElementById('historyList').innerHTML = `
        <div class="empty-state">
          <p class="empty-icon">🗂</p>
          <p>No documents yet. Upload your first file above.</p>
        </div>`;
    }
  }
}

// ── Helpers ────────────────────────────────────────────────────────────────
function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function showToast(msg) {
  let toast = document.querySelector('.toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.className = 'toast';
    document.body.appendChild(toast);
  }
  toast.textContent = msg;
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 3000);
}

// ── Init ───────────────────────────────────────────────────────────────────
loadHistory();