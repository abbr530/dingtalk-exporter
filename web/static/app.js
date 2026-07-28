// DingTalk Exporter - Frontend Application

const API_BASE = '';

const state = {
    conversations: [],
    currentCid: null,
    currentOffset: 0,
    currentLimit: 50,
    totalMessages: 0,
    convOffset: 0,
    convLimit: 50,
    myUid: '',
};

// --- API helpers ---

async function apiGet(path) {
    const resp = await fetch(API_BASE + path);
    if (!resp.ok) throw await buildApiError(resp);
    return resp.json();
}

async function apiPost(path) {
    const resp = await fetch(API_BASE + path, {method: 'POST'});
    if (!resp.ok) throw await buildApiError(resp);
    return resp.json();
}

async function buildApiError(resp) {
    let message = `API error: ${resp.status}`;
    try {
        const data = await resp.json();
        if (data && typeof data.detail === 'string' && data.detail.trim()) {
            message = data.detail.trim();
        }
    } catch (e) {
        // Ignore non-JSON error payloads and fall back to the HTTP status.
    }
    return new Error(message);
}

// --- Format helpers ---

function formatTime(ts) {
    if (!ts) return '';
    const d = new Date(ts);
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function formatRelativeTime(ts) {
    if (!ts) return '';
    const now = Date.now();
    const diff = now - ts;
    if (diff < 60000) return '刚刚';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`;
    if (diff < 604800000) return `${Math.floor(diff / 86400000)}天前`;
    return formatTime(ts);
}

function formatSize(bytes) {
    if (!bytes) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    while (bytes >= 1024 && i < units.length - 1) {
        bytes /= 1024;
        i++;
    }
    return `${bytes.toFixed(1)} ${units[i]}`;
}

function renderInlineImages(html) {
    // No longer used — kept for compatibility
    return html;
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function formatMarkdown(text) {
    if (!text) return '';
    let html = escapeHtml(text);
    // Bold: **text** or #### heading
    html = html.replace(/^#{1,4}\s+(.+)$/gm, '<strong>$1</strong>');
    // Links: [[text]](url)
    html = html.replace(/\[\[([^\]]*)\]\]\(([^)]*)\)/g, '<span style="color:#3498db">[$1]</span>');
    // @mentions
    html = html.replace(/@(\S+?\(\S+?\))/g, '<span style="color:#3498db">@$1</span>');
    // Newlines to <br>
    html = html.replace(/\n/g, '<br>');
    return html;
}

function formatPlainMultiline(text) {
    if (!text) return '';
    return escapeHtml(text).replace(/\n/g, '<br>');
}

function clampText(text, maxLength = 800) {
    if (!text) return '';
    return text.length > maxLength ? `${text.slice(0, maxLength)}...` : text;
}

function formatDuration(ms) {
    const totalSeconds = Math.max(0, Math.round((ms || 0) / 1000));
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = String(totalSeconds % 60).padStart(2, '0');
    return `${minutes}:${seconds}`;
}

function renderAudioMessage(msg) {
    const audio = msg.audio_info || {};
    const player = audio.src
        ? `<audio class="msg-audio-player" controls preload="none" src="${audio.src}"></audio>`
        : `<div class="msg-image-placeholder">[语音文件未缓存到本地]</div>`;
    const transcript = msg.text && msg.text !== '[语音消息]'
        ? `<div class="msg-audio-transcript">${formatPlainMultiline(clampText(msg.text, 1200))}</div>`
        : '';
    const meta = [];
    if (audio.duration_ms) meta.push(formatDuration(audio.duration_ms));
    if (audio.file_name) meta.push(escapeHtml(audio.file_name));
    const metaHtml = meta.length > 0 ? `<div class="msg-audio-meta">${meta.join(' · ')}</div>` : '';
    return `<div class="msg-audio">${player}${metaHtml}${transcript}</div>`;
}

function renderReplyMessage(msg) {
    const quote = msg.quote_info || {};
    const sourceName = quote.source_sender_name ? escapeHtml(quote.source_sender_name) : '原消息';
    const sourcePreview = quote.source_preview
        ? formatPlainMultiline(clampText(quote.source_preview, 300))
        : '[原消息]';
    const replyText = msg.text
        ? formatPlainMultiline(clampText(msg.text, 1200))
        : '[引用消息]';
    return `
        <div class="msg-reply">
            <div class="msg-reply-source">
                <div class="msg-reply-name">${sourceName}</div>
                <div class="msg-reply-preview">${sourcePreview}</div>
            </div>
            <div class="msg-reply-body">${replyText}</div>
        </div>
    `;
}

// --- Conversations ---
let _convTotal = 0;       // total from API
let _convLoading = false; // prevent duplicate loads
let _convFilter = 'all';  // all | group | single
let _convSearch = '';     // search keyword

async function loadConversations(reset = false) {
    if (reset) {
        state.convOffset = 0;
        state.conversations = [];
        _convTotal = 0;
    }

    // All loaded
    if (_convTotal > 0 && state.conversations.length >= _convTotal) return;
    if (_convLoading) return;
    _convLoading = true;

    const params = new URLSearchParams({
        limit: state.convLimit,
        offset: state.convOffset,
    });

    try {
        const data = await apiGet(`/api/conversations?${params}`);
        _convTotal = data.total;
        state.conversations = reset ? data.conversations : [...state.conversations, ...data.conversations];
        state.convOffset += data.conversations.length;
        renderConversations(_convTotal);
    } catch (e) {
        if (reset) {
            document.getElementById('convList').innerHTML = `<div class="loading">${escapeHtml(e.message)}</div>`;
        }
        throw e;
    } finally {
        _convLoading = false;
    }
}

// Infinite scroll for conversation list
function _onConvListScroll() {
    const el = document.getElementById('convList');
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 40;
    if (atBottom && !_convLoading && state.conversations.length < _convTotal) {
        loadConversations(false);
    }
}

function _getFilteredConversations() {
    let list = state.conversations;
    // Filter by type
    if (_convFilter === 'group') {
        list = list.filter(c => c.type === 'group');
    } else if (_convFilter === 'single') {
        list = list.filter(c => c.type === 'single');
    }
    // Filter by search
    if (_convSearch) {
        const kw = _convSearch.toLowerCase();
        list = list.filter(c => (c.title || '').toLowerCase().includes(kw));
    }
    return list;
}

function renderConversations(total) {
    const list = document.getElementById('convList');
    const count = document.getElementById('convCount');

    const filtered = _getFilteredConversations();
    count.textContent = `${filtered.length}/${total}`;

    if (filtered.length === 0 && state.conversations.length > 0) {
        list.innerHTML = '<div style="padding:20px;text-align:center;color:#999">无匹配会话</div>';
        return;
    }

    list.innerHTML = filtered.map(conv => {
        const unread = conv.unread_count > 0 ? `<span class="unread-badge">${conv.unread_count > 99 ? '99+' : conv.unread_count}</span>` : '';
        return `
        <div class="conv-item ${conv.cid === state.currentCid ? 'active' : ''}" data-cid="${conv.cid}">
            <div class="conv-title">
                <span class="conv-type-badge ${conv.type}">${conv.type === 'group' ? '群' : '单聊'}</span>
                ${escapeHtml(conv.title || conv.cid)}
                ${unread}
            </div>
            <div class="conv-subtitle">
                <span>${conv.member_count ? conv.member_count + '人' : ''}</span>
                <span>${formatRelativeTime(conv.last_modify)}</span>
            </div>
        </div>
    `
    }).join('');

    // Re-bind scroll
    list.removeEventListener('scroll', _onConvListScroll);
    list.addEventListener('scroll', _onConvListScroll);

    // Bind clicks
    list.querySelectorAll('.conv-item').forEach(el => {
        el.addEventListener('click', () => {
            const cid = el.dataset.cid;
            selectConversation(cid);
        });
    });
}

async function selectConversation(cid) {
    state.currentCid = cid;
    state.currentOffset = 0;

    // Update sidebar active state
    document.querySelectorAll('.conv-item').forEach(el => {
        el.classList.toggle('active', el.dataset.cid === cid);
    });

    // Show message view
    document.getElementById('emptyState').style.display = 'none';
    document.getElementById('searchView').style.display = 'none';
    document.getElementById('messageView').style.display = 'flex';

    const conv = state.conversations.find(c => c.cid === cid);
    document.getElementById('convTitle').textContent = conv ? conv.title : cid;
    document.getElementById('convMeta').textContent = conv ?
        `${conv.type === 'group' ? '群聊' : '单聊'} · ${conv.member_count || ''}人` : '';

    await loadMessages();
}

// --- Messages ---

async function loadMessages() {
    if (!state.currentCid) return;

    const params = new URLSearchParams({
        limit: state.currentLimit,
        offset: state.currentOffset,
    });

    const data = await apiGet(`/api/conversations/${state.currentCid}/messages?${params}`);
    state.totalMessages = data.total;

    renderMessages(data.messages);
    renderPagination();
}

function renderMessages(messages) {
    const list = document.getElementById('messageList');
    const myUid = state.myUid;

    // Build HTML with date separators
    let html = '';
    let lastDateStr = '';

    messages.forEach(msg => {
        // Date separator
        const msgDate = msg.created_at_str ? msg.created_at_str.split(' ')[0] : '';
        if (msgDate && msgDate !== lastDateStr) {
            lastDateStr = msgDate;
            const weekday = _getWeekday(msg.created_at);
            html += `<div class="msg-date-sep"><span>${escapeHtml(msgDate)} ${weekday}</span></div>`;
        }

        const isSelf = String(msg.sender_id) === myUid;
        let contentHtml = '';

        // Render based on content type
        switch (msg.content_type) {
            case 1: // Text
                contentHtml = formatPlainMultiline(msg.text);
                // Highlight @mentions
                if (msg.at_ids && Object.keys(msg.at_ids).length > 0) {
                    for (const [uid, name] of Object.entries(msg.at_ids)) {
                        contentHtml = contentHtml.replace(
                            new RegExp(`@${uid}`, 'g'),
                            `<span style="color:#3498db">@${escapeHtml(name)}</span>`
                        );
                    }
                }
                break;
            case 2: // Image
                const imgInfo = msg.image_info || {};
                const imgSrc = imgInfo.src;
                if (imgSrc) {
                    const label = msg.message_subtype === 'emoji' ? '表情包' : '图片';
                    contentHtml = '<a href="' + imgSrc + '" target="_blank"><img class="msg-image" src="' + imgSrc + '" alt="' + label + '" style="max-width:100%;cursor:pointer"></a>';
                } else {
                    contentHtml = '<div class="msg-image-placeholder">[图片未缓存到本地]</div>';
                }
                break;
            case 3:
                contentHtml = renderAudioMessage(msg);
                break;
            case 300: // Voice
                if (msg.message_subtype === 'announcement' && msg.text) {
                    contentHtml = `<div class="msg-card">${formatPlainMultiline(msg.text)}</div>`;
                } else if (msg.message_subtype === 'report' && msg.text) {
                    contentHtml = `<div class="msg-card">${formatPlainMultiline(clampText(msg.text, 1600))}</div>`;
                } else {
                    contentHtml = '[语音消息]';
                }
                break;
            case 102:
            case 104:
                contentHtml = msg.text ?
                    `<div class="msg-card">${formatPlainMultiline(clampText(msg.text, 800))}</div>` :
                    `[${msg.content_type_name || '系统消息'}]`;
                break;
            case 501: // File
                const fileAtt = (msg.attachments || []).find(a => a.filename);
                if (fileAtt) {
                    const fname = escapeHtml(fileAtt.filename);
                    const fsize = formatSize(fileAtt.file_size);
                    if (fileAtt.local_available) {
                        const dlUrl = '/api/local-file?path=' + encodeURIComponent(fileAtt.local_path);
                        contentHtml = '<div class="msg-file"><span class="msg-file-icon">\uD83D\uDCCE</span><div class="msg-file-info"><a class="msg-file-name msg-file-link" href="' + dlUrl + '" target="_blank">' + fname + '</a><div class="msg-file-size">' + fsize + ' · 可打开</div></div></div>';
                    } else {
                        contentHtml = '<div class="msg-file"><span class="msg-file-icon">\uD83D\uDCCE</span><div class="msg-file-info"><div class="msg-file-name">' + fname + '</div><div class="msg-file-size">' + fsize + '</div></div></div>';
                    }
                } else {
                    contentHtml = '[文件]';
                }
                break;
            case 1101: // Call
                contentHtml = '[通话记录]';
                break;
            case 1400: // Approval
                contentHtml = msg.text ?
                    `<div class="msg-rich-text">${formatMarkdown(msg.text)}</div>` :
                    '[审批消息]';
                break;
            case 2900:
            case 2950: // Interactive cards
                contentHtml = msg.text ?
                    `<div class="msg-card">${formatPlainMultiline(clampText(msg.text, msg.message_subtype === 'report' ? 1600 : 800))}</div>` :
                    '[互动卡片]';
                break;
            case 3100: // Quote
            case 1200: // Rich text
            case 1201:
            case 1202:
                if (msg.message_subtype === 'reply' && msg.quote_info) {
                    contentHtml = renderReplyMessage(msg);
                    break;
                }
                let quoteText = msg.text ? formatPlainMultiline(msg.text) : '';
                // Replace [图片] with actual images from image_info
                const quoteImgInfo = msg.image_info || {};
                const quoteImgs = quoteImgInfo.images || [];
                if (quoteImgs.length > 0) {
                    let imgIdx = 0;
                    quoteText = quoteText.replace(/\[图片\]/g, () => {
                        if (imgIdx < quoteImgs.length && quoteImgs[imgIdx].src) {
                            const src = quoteImgs[imgIdx].src;
                            imgIdx++;
                            return `<img class="msg-image" src="${src}" alt="图片" style="max-width:100%;cursor:pointer">`;
                        }
                        imgIdx++;
                        return '<span style="display:inline-block;padding:2px 8px;background:#f0f0f0;border-radius:3px;color:#999;font-size:12px;border:1px solid #ddd">图片(未缓存)</span>';
                    });
                } else {
                    // No image_info - show placeholders
                    quoteText = quoteText.replace(/\[图片\]/g, '<span style="display:inline-block;padding:2px 8px;background:#f0f0f0;border-radius:3px;color:#999;font-size:12px;border:1px solid #ddd">图片(原消息中)</span>');
                }
                if (msg.content_type === 3100) {
                    contentHtml = quoteText ? `<div class="msg-quote">${quoteText}</div>` : '[引用消息]';
                } else {
                    contentHtml = quoteText ? `<div class="msg-rich-text">${quoteText}</div>` : '[富文本消息]';
                }
                break;
            default:
                contentHtml = msg.text ? formatPlainMultiline(msg.text) : `[${msg.content_type_name || '消息'}]`;
        }

        const dingBadge = msg.is_ding ? '<span class="msg-ding-badge">DING</span>' : '';

        html += `
            <div class="msg-item ${isSelf ? 'self' : ''}">
                <div class="msg-sender">${escapeHtml(msg.sender_name)}${dingBadge}</div>
                <div class="msg-bubble">${contentHtml}</div>
                <div class="msg-time">${msg.created_at_str || formatTime(msg.created_at)}</div>
            </div>
        `;
    });

    // Replace inline image markers with actual <img> tags
    html = renderInlineImages(html);

    list.innerHTML = html;

    // Bind image events via delegation
    list.querySelectorAll('.msg-image').forEach(img => {
        img.addEventListener('click', function () {
            openLightbox(this.src);
        });
        img.addEventListener('error', function () {
            this.outerHTML = '<div class="msg-image-placeholder">[图片加载失败]</div>';
        });
    });

    // Scroll to bottom
    list.scrollTop = list.scrollHeight;
}

function _getWeekday(ts) {
    if (!ts) return '';
    const days = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'];
    try {
        const d = new Date(ts);
        return days[d.getDay()];
    } catch {
        return '';
    }
}

function renderPagination() {
    const container = document.getElementById('messagePagination');
    const totalPages = Math.ceil(state.totalMessages / state.currentLimit);
    // Page 1 = most recent messages, page totalPages = oldest
    const currentPage = Math.floor(state.currentOffset / state.currentLimit) + 1;

    if (totalPages <= 1) {
        container.innerHTML = `<span>共 ${state.totalMessages} 条消息</span>`;
        return;
    }

    let html = '';

    // "更早" = go to next page (older messages)
    if (currentPage < totalPages) {
        html += `<button onclick="goToPage(${currentPage + 1})">更早的消息</button>`;
    }

    // Page numbers
    const startPage = Math.max(1, currentPage - 2);
    const endPage = Math.min(totalPages, currentPage + 2);
    html += ` `;
    for (let p = startPage; p <= endPage; p++) {
        html += `<button class="${p === currentPage ? 'active' : ''}" onclick="goToPage(${p})">${p}</button>`;
    }

    // "最新" = go to previous page (newer messages)
    if (currentPage > 1) {
        html += `<button onclick="goToPage(${currentPage - 1})">最新消息</button>`;
    }

    html += ` <span>第${currentPage}/${totalPages}页 共${state.totalMessages}条</span>`;
    container.innerHTML = html;
}

function goToPage(page) {
    state.currentOffset = (page - 1) * state.currentLimit;
    loadMessages();
}

// --- Search ---

async function doSearch() {
    const query = document.getElementById('searchInput').value.trim();
    if (!query) return;

    document.getElementById('emptyState').style.display = 'none';
    document.getElementById('messageView').style.display = 'none';
    document.getElementById('searchView').style.display = 'flex';
    document.getElementById('searchTitle').textContent = `搜索: "${query}"`;

    const data = await apiGet(`/api/search?q=${encodeURIComponent(query)}`);
    const results = document.getElementById('searchResults');

    if (data.messages.length === 0) {
        results.innerHTML = '<div class="loading">没有找到匹配的消息</div>';
        return;
    }

    results.innerHTML = data.messages.map(msg => `
        <div class="msg-item" style="max-width:100%">
            <div class="msg-sender">
                ${escapeHtml(msg.sender_name)}
                <span style="margin-left:8px;font-size:11px;color:#999">${msg.created_at_str}</span>
            </div>
            <div class="msg-bubble">${highlightSearch(formatPlainMultiline(msg.text), query)}</div>
        </div>
    `).join('');
}

function highlightSearch(text, query) {
    if (!text || !query) return text;
    const regex = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
    return text.replace(regex, '<mark>$1</mark>');
}

// --- Sync ---

async function loadSyncStatus() {
    try {
        const state = await apiGet('/api/sync/status');
        const el = document.getElementById('syncStatus');
        if (state.is_syncing) {
            el.textContent = '同步中...';
        } else if (state.last_error) {
            el.textContent = `同步失败: ${state.last_error}`;
        } else if (!state.database_ready && state.database_error) {
            el.textContent = state.database_error;
        } else if (state.last_sync_time_str) {
            el.textContent = `上次同步: ${state.last_sync_time_str}`;
        } else {
            el.textContent = '尚未同步';
        }
    } catch (e) {
        document.getElementById('syncStatus').textContent = '状态未知';
    }
}

async function triggerSync() {
    const btn = document.getElementById('syncBtn');
    btn.disabled = true;
    btn.textContent = '同步中...';

    try {
        await apiPost('/api/sync/trigger');
        document.getElementById('syncStatus').textContent = '同步已触发...';

        // Poll for completion
        const poll = setInterval(async () => {
            try {
                const s = await apiGet('/api/sync/status');
                if (!s.is_syncing) {
                    clearInterval(poll);
                    btn.disabled = false;
                    btn.textContent = '手动同步';
                    loadSyncStatus();
                    // Reload conversations if we have one selected
                    if (state.currentCid) {
                        loadMessages();
                    }
                }
            } catch (e) {
            }
        }, 3000);
    } catch (e) {
        btn.disabled = false;
        btn.textContent = '手动同步';
        alert('同步失败: ' + e.message);
    }
}

// --- Exports ---

let _exportAllConvs = [];   // all conversations for the export modal
let _exportSelected = {};   // cid -> true/false
let _exportFilter = 'all';  // all | group | single — type filter in export modal

async function loadExports() {
    const data = await apiGet('/api/exports');
    const list = document.getElementById('exportList');

    if (data.exports.length === 0) {
        list.innerHTML = '<div style="color:#888;padding:20px 0">暂无导出文件</div>';
        return;
    }

    list.innerHTML = data.exports.map(exp => {
        const isDir = exp.type === 'directory';
        const badge = isDir ? '<span style="font-size:11px;padding:1px 5px;border-radius:3px;background:#e1f0ff;color:#2196f3;margin-right:6px">含附件</span>' : '';
        const dlUrl = exp.download_url || `/api/exports/${encodeURIComponent(exp.filename)}`;
        const folderLinkHtml = isDir
            ? `<a class="export-size js-open-export-folder" href="#" data-name="${escapeHtml(exp.filename)}">目录</a>`
            : `<span class="export-meta">${formatSize(exp.size)}</span>`;
        const actionHtml = `
            <a class="export-download" href="${dlUrl}" download>下载</a>
            <button class="export-delete js-delete-export" type="button" data-name="${escapeHtml(exp.filename)}">删除</button>
        `;
        return `
        <div class="export-item">
            <div>
                <div class="export-name">${badge}${escapeHtml(exp.filename)}</div>
                <div class="export-time">${formatTime(exp.modified * 1000)}</div>
            </div>
            <div class="export-actions">
                ${folderLinkHtml}
                ${actionHtml}
            </div>
        </div>
    `;
    }).join('');

    list.querySelectorAll('.js-open-export-folder').forEach(link => {
        link.addEventListener('click', async e => {
            e.preventDefault();
            try {
                const name = link.dataset.name;
                const resp = await fetch(`/api/exports/${encodeURIComponent(name)}/open-folder`, {
                    method: 'POST',
                });
                if (!resp.ok) {
                    const payload = await resp.json().catch(() => ({}));
                    throw new Error(payload.detail || `HTTP ${resp.status}`);
                }
            } catch (err) {
                alert('打开导出文件夹失败: ' + err.message);
            }
        });
    });

    list.querySelectorAll('.js-delete-export').forEach(btn => {
        btn.addEventListener('click', async () => {
            const name = btn.dataset.name;
            if (!confirm(`确认删除导出项“${name}”吗？`)) {
                return;
            }

            btn.disabled = true;
            try {
                const resp = await fetch(`/api/exports/${encodeURIComponent(name)}`, {
                    method: 'DELETE',
                });
                if (!resp.ok) {
                    const payload = await resp.json().catch(() => ({}));
                    throw new Error(payload.detail || `HTTP ${resp.status}`);
                }
                await loadExports();
            } catch (err) {
                btn.disabled = false;
                alert('删除导出项失败: ' + err.message);
            }
        });
    });
}

async function loadExportConvList(keyword) {
    const el = document.getElementById('exportConvList');
    el.innerHTML = '<div class="loading">加载会话列表...</div>';

    // Fetch all conversations (paginated)
    let all = [];
    let offset = 0;
    const limit = 200;
    while (true) {
        let url = `/api/conversations?limit=${limit}&offset=${offset}`;
        const data = await apiGet(url);
        all = all.concat(data.conversations);
        if (all.length >= data.total) break;
        offset += limit;
    }

    _exportAllConvs = all;

    // Restore previous selections
    renderExportConvList(keyword || '');
}

function renderExportConvList(keyword) {
    const el = document.getElementById('exportConvList');
    let filtered = _exportAllConvs;
    // Filter by type
    if (_exportFilter === 'group') {
        filtered = filtered.filter(c => c.type === 'group');
    } else if (_exportFilter === 'single') {
        filtered = filtered.filter(c => c.type === 'single');
    }
    // Filter by search keyword
    if (keyword) {
        const kw = keyword.toLowerCase();
        filtered = filtered.filter(c => (c.title || '').toLowerCase().includes(kw));
    }

    const selectedNum = Object.values(_exportSelected).filter(Boolean).length;
    document.getElementById('selectedCount').textContent = `已选 ${selectedNum} 个`;
    document.getElementById('exportSelectedBtn').disabled = selectedNum === 0;

    // Update select-all checkbox state
    const allCb = document.getElementById('selectAllCbs');
    const allChecked = filtered.length > 0 && filtered.every(c => _exportSelected[c.cid]);
    allCb.checked = allChecked;

    if (filtered.length === 0) {
        el.innerHTML = '<div style="padding:20px;color:#888">无匹配会话</div>';
        return;
    }

    el.innerHTML = filtered.map(c => `
        <div class="export-conv-row" data-cid="${c.cid}">
            <input type="checkbox" class="ecr-cb" data-cid="${c.cid}" ${_exportSelected[c.cid] ? 'checked' : ''}>
            <span class="ecr-badge ${c.type}">${c.type === 'group' ? '群' : '单'}</span>
            <span class="ecr-title">${escapeHtml(c.title || c.cid)}</span>
        </div>
    `).join('');

    // Bind checkbox clicks
    el.querySelectorAll('.ecr-cb').forEach(cb => {
        cb.addEventListener('change', e => {
            e.stopPropagation();
            _exportSelected[cb.dataset.cid] = cb.checked;
            const n = Object.values(_exportSelected).filter(Boolean).length;
            document.getElementById('selectedCount').textContent = `已选 ${n} 个`;
            document.getElementById('exportSelectedBtn').disabled = n === 0;
        });
    });

    // Bind row clicks (toggle checkbox)
    el.querySelectorAll('.export-conv-row').forEach(row => {
        row.addEventListener('click', e => {
            if (e.target.tagName === 'INPUT') return; // checkbox handles itself
            const cb = row.querySelector('.ecr-cb');
            cb.checked = !cb.checked;
            cb.dispatchEvent(new Event('change'));
        });
    });
}

// --- Lightbox ---

function openLightbox(src) {
    document.getElementById('lightboxImg').src = src;
    document.getElementById('lightbox').style.display = 'flex';
}

// --- Event bindings ---

function init() {
    // Fetch current user UID
    apiGet('/api/config').then(data => {
        state.myUid = String(data.user_uid || '');
    }).catch(() => {
    });

    // Load conversations
    loadConversations(true).catch(() => {
    });
    loadSyncStatus();

    // Sidebar: conversation search
    document.getElementById('convSearchInput').addEventListener('input', e => {
        _convSearch = e.target.value.trim();
        renderConversations(_convTotal);
    });

    // Sidebar: type filter tabs
    document.querySelectorAll('.sidebar-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.sidebar-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            _convFilter = tab.dataset.filter;
            renderConversations(_convTotal);
        });
    });

    // Message search
    document.getElementById('searchBtn').addEventListener('click', doSearch);
    document.getElementById('searchInput').addEventListener('keydown', e => {
        if (e.key === 'Enter') doSearch();
    });

    // Close search
    document.getElementById('searchCloseBtn').addEventListener('click', () => {
        document.getElementById('searchView').style.display = 'none';
        if (state.currentCid) {
            document.getElementById('messageView').style.display = 'flex';
        } else {
            document.getElementById('emptyState').style.display = 'flex';
        }
    });

    // Sync
    document.getElementById('syncBtn').addEventListener('click', triggerSync);

    // Exports
    document.getElementById('exportBtn').addEventListener('click', () => {
        document.getElementById('exportModal').style.display = 'flex';
        _exportSelected = {};
        _exportFilter = 'all';
        document.querySelectorAll('#exportFilterTabs .sidebar-tab').forEach(t => t.classList.toggle('active', t.dataset.filter === 'all'));
        loadExportConvList();
        loadExports();
        // Reset to select tab
        document.querySelectorAll('.export-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === 'select'));
        document.getElementById('exportPanelSelect').style.display = '';
        document.getElementById('exportPanelFiles').style.display = 'none';
    });
    document.getElementById('exportModalClose').addEventListener('click', () => {
        document.getElementById('exportModal').style.display = 'none';
    });

    // Export tab switching
    document.querySelectorAll('.export-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.export-tab').forEach(t => t.classList.toggle('active', t === tab));
            document.getElementById('exportPanelSelect').style.display = tab.dataset.tab === 'select' ? '' : 'none';
            document.getElementById('exportPanelFiles').style.display = tab.dataset.tab === 'files' ? '' : 'none';
        });
    });

    // Select all checkbox
    document.getElementById('selectAllCbs').addEventListener('change', e => {
        const checked = e.target.checked;
        const keyword = document.getElementById('exportSearchInput').value.trim().toLowerCase();
        let filtered = _exportAllConvs;
        if (keyword) {
            filtered = filtered.filter(c => (c.title || '').toLowerCase().includes(keyword));
        }
        filtered.forEach(c => {
            _exportSelected[c.cid] = checked;
        });
        renderExportConvList(document.getElementById('exportSearchInput').value.trim());
    });

    // Export type filter tabs
    document.querySelectorAll('#exportFilterTabs .sidebar-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('#exportFilterTabs .sidebar-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            _exportFilter = tab.dataset.filter;
            renderExportConvList(document.getElementById('exportSearchInput').value.trim());
        });
    });

    // Search in export modal
    document.getElementById('exportSearchInput').addEventListener('input', e => {
        renderExportConvList(e.target.value.trim());
    });

    const exportTimeRangeEl = document.getElementById('exportTimeRange');
    const customTimeRangeEl = document.getElementById('customTimeRange');
    const sinceTimeEl = document.getElementById('sinceTime');
    const untilTimeEl = document.getElementById('untilTime');
    const timeRangePreviewEl = document.getElementById('timeRangePreview');

    function toggleCustomTimeRange() {
        const isCustom = exportTimeRangeEl.value === '999';
        customTimeRangeEl.hidden = !isCustom;
        if (isCustom && !sinceTimeEl.value) {
            const now = new Date();
            const oneMonthAgo = new Date(now);
            oneMonthAgo.setMonth(oneMonthAgo.getMonth() - 1);
            sinceTimeEl.value = formatDatetimeLocal(oneMonthAgo);
            untilTimeEl.value = formatDatetimeLocal(now);
        }
        updateRangePreview();
    }

    function formatDatetimeLocal(date) {
        const y = date.getFullYear();
        const m = String(date.getMonth() + 1).padStart(2, '0');
        const d = String(date.getDate()).padStart(2, '0');
        const hh = String(date.getHours()).padStart(2, '0');
        const mm = String(date.getMinutes()).padStart(2, '0');
        return `${y}-${m}-${d}T${hh}:${mm}`;
    }

    function toTimestampMs(value) {
        if (!value) return null;
        const ts = new Date(value).getTime();
        return Number.isNaN(ts) ? null : ts;
    }

    function formatDate(ts) {
        const d = new Date(ts);
        return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
    }

    function updateRangePreview() {
        const v = exportTimeRangeEl.value;
        if (v === '0') {
            timeRangePreviewEl.textContent = '';
            return;
        }
        if (v === '999') {
            const s = toTimestampMs(sinceTimeEl.value);
            const e = toTimestampMs(untilTimeEl.value);
            if (s && e) {
                timeRangePreviewEl.textContent = `${formatDate(s)} ~ ${formatDate(e)}`;
            } else {
                timeRangePreviewEl.textContent = '';
            }
            return;
        }
        const now = Date.now();
        let since;
        if (v === '1w') {
            since = now - 7 * 24 * 3600 * 1000;
        } else if (v === '1') {
            since = now - 30 * 24 * 3600 * 1000;
        } else {
            const months = parseInt(v, 10);
            since = now - months * 30 * 24 * 3600 * 1000;
        }
        timeRangePreviewEl.textContent = `${formatDate(since)} ~ ${formatDate(now)}`;
    }

    async function pollExportCompletion(btn) {
        const poll = setInterval(async () => {
            try {
                const s = await apiGet('/api/sync/status');
                if (!s.is_syncing) {
                    clearInterval(poll);
                    btn.disabled = false;
                    btn.textContent = '导出选中会话';
                    document.querySelectorAll('.export-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === 'files'));
                    document.getElementById('exportPanelSelect').style.display = 'none';
                    document.getElementById('exportPanelFiles').style.display = '';
                    loadExports();
                }
            } catch (e) {
            }
        }, 2000);
    }

    exportTimeRangeEl.addEventListener('change', toggleCustomTimeRange);
    sinceTimeEl.addEventListener('change', updateRangePreview);
    untilTimeEl.addEventListener('change', updateRangePreview);
    toggleCustomTimeRange();

    // Export selected conversations
    document.getElementById('exportSelectedBtn').addEventListener('click', async () => {
        const cids = Object.entries(_exportSelected).filter(([_, v]) => v).map(([k]) => k);
        if (cids.length === 0) return;

        const btn = document.getElementById('exportSelectedBtn');
        btn.disabled = true;
        btn.textContent = '导出中...';

        const rangeValue = exportTimeRangeEl.value;
        let sinceTime = null;
        let untilTime = null;

        if (rangeValue !== '999') {
            if (rangeValue === '1w') {
                sinceTime = Date.now() - 7 * 24 * 3600 * 1000;
            } else if (rangeValue === '1') {
                sinceTime = Date.now() - 30 * 24 * 3600 * 1000;
            } else {
                const months = parseInt(rangeValue, 10);
                if (months > 0) {
                    sinceTime = Date.now() - months * 30 * 24 * 3600 * 1000;
                }
            }
        } else {
            sinceTime = toTimestampMs(sinceTimeEl.value);
            untilTime = toTimestampMs(untilTimeEl.value);

            if (!sinceTime || !untilTime) {
                btn.disabled = false;
                btn.textContent = '导出选中会话';
                alert('请选择完整的开始和结束时间');
                return;
            }

            if (sinceTime > untilTime) {
                btn.disabled = false;
                btn.textContent = '导出选中会话';
                alert('开始时间不能晚于结束时间');
                return;
            }
        }

        try {
            await fetch('/api/export/selected', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({cids, since_time: sinceTime, until_time: untilTime}),
            });

            pollExportCompletion(btn);
        } catch (e) {
            btn.disabled = false;
            btn.textContent = '导出选中会话';
            alert('导出失败: ' + e.message);
        }
    });

    // Full export
    document.getElementById('fullExportBtn').addEventListener('click', async () => {
        const btn = document.getElementById('fullExportBtn');
        const progress = document.getElementById('exportProgress');
        btn.disabled = true;
        progress.textContent = '正在触发全量导出...';

        try {
            await apiPost('/api/sync/trigger?full=true');
            const poll = setInterval(async () => {
                try {
                    const s = await apiGet('/api/sync/status');
                    if (!s.is_syncing) {
                        clearInterval(poll);
                        btn.disabled = false;
                        progress.textContent = s.last_export_path ? '导出完成' : '同步完成';
                        loadExports();
                        setTimeout(() => {
                            progress.textContent = '';
                        }, 5000);
                    } else {
                        progress.textContent = '导出中，请稍候...';
                    }
                } catch (e) {
                }
            }, 3000);
        } catch (e) {
            btn.disabled = false;
            progress.textContent = '导出失败: ' + e.message;
        }
    });

    // Lightbox
    document.getElementById('lightboxClose').addEventListener('click', () => {
        document.getElementById('lightbox').style.display = 'none';
    });
    document.getElementById('lightbox').addEventListener('click', e => {
        if (e.target === e.currentTarget) {
            document.getElementById('lightbox').style.display = 'none';
        }
    });

    // Refresh sync status periodically
    setInterval(loadSyncStatus, 60000);
}

// Make functions global for onclick handlers
window.goToPage = goToPage;
window.openLightbox = openLightbox;

document.addEventListener('DOMContentLoaded', init);
