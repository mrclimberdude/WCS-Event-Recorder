/* All metadata enters the document as text. No remote assets or audio are loaded. */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  let config = {poll_seconds: 5, stale_seconds: 30, page_limit: 50};
  let view = null, rows = new Map(), cursor = null, headKey = null, busy = false, failures = 0;
  let timer, lastSuccess = null, observationKey = null, observationSince = null;
  let frame = 0, playing = false, anchorFrame = 0, anchorTime = 0, replayTimer;
  let publication = document.documentElement.dataset.publication || null, configured = false, publicationExpiry = null;
  const musicLinksPath = document.documentElement.dataset.musicLinks || null;
  let musicLinks = new Map(), musicLinksExpiry = null, musicLinksBusy = false, musicLinksEpoch = 0;
  const text = (node, value) => { node.textContent = value; };
  function node(tag, value, className) {
    const n = document.createElement(tag); if (value !== undefined) text(n, value);
    if (className) n.className = className; return n;
  }
  async function get(url, maxBytes=null) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try { const response = await fetch(url, {signal:controller.signal, cache:'no-store', credentials:'same-origin'});
      if (!response.ok) { const error = new Error('unavailable'); error.status = response.status; throw error; }
      if(maxBytes !== null) {
        const reader = response.body.getReader(), chunks = []; let size = 0;
        try {
          while(true) {
            const result = await reader.read(); if(result.done) break;
            size += result.value.byteLength;
            if(size > maxBytes) { await reader.cancel(); throw new Error('unavailable'); }
            chunks.push(result.value);
          }
        } finally { reader.releaseLock(); }
        const bytes = new Uint8Array(size); let offset = 0;
        for(const chunk of chunks) { bytes.set(chunk,offset); offset += chunk.byteLength; }
        return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
      }
      return await response.json();
    } finally { clearTimeout(timeout); }
  }
  const opaque = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
  const exactKeys = (value, keys) => value !== null && typeof value === 'object' && !Array.isArray(value) &&
    JSON.stringify(Object.keys(value).sort()) === JSON.stringify([...keys].sort());
  function clearMusicLinks() {
    musicLinks.clear(); musicLinksExpiry = null;
    for (const holder of document.querySelectorAll('#history .music-links')) holder.remove();
  }
  function safeMusicLink(link) {
    if (!exactKeys(link, ['service','url']) || typeof link.url !== 'string' || link.url.length > 1024 ||
        !/^[\x21-\x7e]+$/.test(link.url)) return false;
    let url; try { url = new URL(link.url); } catch (_) { return false; }
    if (url.protocol !== 'https:' || url.username || url.password || url.port || url.hash) return false;
    if (link.service === 'spotify') return /^https:\/\/open\.spotify\.com\/track\/[A-Za-z0-9]{22}$/.test(link.url);
    if (link.service !== 'apple' || url.hostname !== 'music.apple.com') return false;
    const parts = url.pathname.split('/');
    if (parts.length < 4 || !/^[a-z]{2}$/.test(parts[1]) || !['song','album'].includes(parts[2]) ||
        parts.slice(1).some(part => !part || part === '.' || part === '..') ||
        !/^[A-Za-z0-9._~%/-]+$/.test(url.pathname)) return false;
    if (parts[2] === 'song') return /^[0-9]{1,30}$/.test(parts.at(-1)) && !url.search;
    return [...url.searchParams.keys()].length === 1 && url.searchParams.has('i') &&
      /^[0-9]{1,30}$/.test(url.searchParams.get('i'));
  }
  function paintMusicLinks() {
    if (musicLinksExpiry !== null && musicLinksExpiry <= Date.now()) clearMusicLinks();
    for (const item of el('history').children) {
      item.querySelector('.music-links')?.remove();
      const links = musicLinks.get(item.dataset.key);
      if (!links?.length) continue;
      const holder = node('p', undefined, 'music-links');
      for (const link of links) {
        const anchor = node('a', link.service === 'spotify' ? 'Spotify' : 'Apple Music');
        anchor.href = link.url; anchor.target = '_blank'; anchor.rel = 'noopener noreferrer';
        anchor.referrerPolicy = 'no-referrer'; holder.append(anchor);
      }
      item.append(holder);
    }
  }
  function canonicalMusicLinks(value) {
    if (Array.isArray(value)) return '[' + value.map(canonicalMusicLinks).join(',') + ']';
    if (value !== null && typeof value === 'object') return '{' + Object.keys(value).sort().map(
      key => JSON.stringify(key) + ':' + canonicalMusicLinks(value[key])).join(',') + '}';
    return JSON.stringify(value);
  }
  async function refreshMusicLinks() {
    if (!musicLinksPath || musicLinksBusy || !view) return;
    const allowed = publication ? musicLinksPath === 'links.json' : musicLinksPath === 'api/v1/music-links';
    if (!allowed) { clearMusicLinks(); return; }
    musicLinksBusy = true;
    const generation = view.generation, epoch = musicLinksEpoch;
    try {
      const data = await get(musicLinksPath, 524288);
      if (!exactKeys(data, ['schema_version','kind','generation','generated_at','expires_at','snapshot_id','occurrences']) ||
          data.schema_version !== 1 || data.kind !== 'music_links' || !opaque(data.generation) ||
          data.generation !== generation || !opaque(data.snapshot_id) || !Array.isArray(data.occurrences) ||
          data.occurrences.length > 5000) throw new Error('unavailable');
      const generated = Date.parse(data.generated_at), expiry = Date.parse(data.expires_at), now = Date.now();
      const utc = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+00:00$/;
      if (!utc.test(data.generated_at) || !utc.test(data.expires_at) || !Number.isFinite(generated) ||
          !Number.isFinite(expiry) || generated > now || expiry <= now || expiry <= generated ||
          expiry-generated > 7*86400000) throw new Error('unavailable');
      const next = new Map(), services = new Set();
      for (const row of data.occurrences) {
        if (!exactKeys(row, ['key','links']) || !opaque(row.key) || next.has(row.key) ||
            !Array.isArray(row.links) || row.links.length > 1 || row.links.some(link => !safeMusicLink(link))) throw new Error('unavailable');
        for (const link of row.links) services.add(link.service);
        next.set(row.key, row.links);
      }
      if (services.size > 1) throw new Error('unavailable');
      const {snapshot_id, ...content} = data;
      const encoded = new TextEncoder().encode(canonicalMusicLinks(content));
      if (new TextEncoder().encode(canonicalMusicLinks(data)).length + 1 > 524288) throw new Error('unavailable');
      // Local phones may use ordinary LAN HTTP, where WebCrypto is unavailable.
      // The companion endpoint and publication verifier always check the digest.
      if(globalThis.crypto?.subtle) {
        const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', encoded))].map(
          value => value.toString(16).padStart(2,'0')).join('');
        if (hash !== snapshot_id) throw new Error('unavailable');
      }
      if (view.generation !== generation || musicLinksEpoch !== epoch) return;
      musicLinks = next; musicLinksExpiry = expiry; paintMusicLinks();
    } catch (_) {
      if (view?.generation === generation && musicLinksEpoch === epoch) clearMusicLinks();
    } finally { musicLinksBusy = false; }
  }
  function localTime(value) {
    try { return new Intl.DateTimeFormat(undefined, {timeZone:view.timezone, month:'short', day:'numeric', hour:'2-digit', minute:'2-digit', second:'2-digit', timeZoneName:'short'}).format(value); }
    catch (_) { return 'Recording time unavailable'; }
  }
  function frameTime(value) { return localTime(new Date(Date.parse(view.source_start_time) + value / view.sample_rate * 1000)); }
  function identity(value) { return value && value.state === 'resolved' ? (value.title || 'Untitled recording') : value && value.title ? value.title : 'Identity unavailable'; }
  function occurrence(row) {
    const item = node('li'); item.dataset.key = row.key;
    item.append(node('h3', identity(row.identity)), node('p', row.identity.artist || 'Artist unknown', 'artist'));
    item.append(node('p', `Approximately ${frameTime(row.start_frame)} – ${frameTime(row.end_frame)}`, 'interval'));
    const labels = [];
    if (row.censored_end) labels.push('End not fully observed');
    if (row.historical_termination_unknown) labels.push('Actual ending unknown');
    const uncertaintyLabels = {single_detection:'Limited evidence', sparse_sampling:'Limited evidence',
      overlap_previous:'Overlapping estimates', overlap_next:'Overlapping estimates', bridged_uncertainty:'Uncertain gap',
      historical_termination_unknown:'Actual ending unknown', right_censored_by_missing_observations:'End not fully observed'};
    for (const flag of row.uncertainty_flags || []) labels.push(uncertaintyLabels[flag] || 'Uncertain estimate');
    if (row.additional_uncertainty) labels.push('Additional uncertainty');
    for (const label of new Set(labels)) item.append(node('span', label, 'badge'));
    if (!row.context || !row.context.length) item.append(node('p', 'Expected session: unknown · Expected DJ: unknown (schedule not recorded)', 'context'));
    for (const c of row.context || []) {
      const session = c.session_status === 'unique' ? [c.session_type,c.division,c.round].filter(Boolean).join(' · ') || 'Scheduled session' : c.session_status === 'ambiguous' ? 'ambiguous' : 'unknown';
      const dj = c.dj_status === 'unique' ? c.dj_name || 'unknown' : c.dj_status === 'ambiguous' ? 'ambiguous' : 'unknown';
      item.append(node('p', `${frameTime(c.start_frame)} · Expected session: ${session} · Expected DJ: ${dj} · Schedule only`, 'context'));
    }
    return item;
  }
  function pause() { playing = false; clearInterval(replayTimer); text(el('play'), 'Play preview'); }
  function coverageEnd() { return Math.max(0, Math.min(view?.runtime?.source_frame_count || 0, view?.runtime?.observed_through_frame || 0)); }
  function renderHint() {
    if(publication) return;
    const hint = view?.runtime?.latest_recognition;
    if (!hint || hint.state === 'none') { text(el('recognition'), 'No saved recognition available.'); return; }
    if (observationSince !== null && performance.now() - observationSince >= config.stale_seconds * 1000) {
      text(el('recognition'), 'No new recognition; the previous hint expired. Recorded history remains available.'); return;
    }
    const states = {match:`Saved match: ${identity(hint.identity)}${hint.identity?.artist ? ' · '+hint.identity.artist : ''}`, unresolved:'Saved match unresolved.', resolution_pending:'Saved match; song identity pending.', no_match:'Latest analyzed audio: no match.', service_error:'Latest analyzed audio: recognition service error.'};
    text(el('recognition'), `${states[hint.state] || 'Saved recognition unavailable.'} Recorded ${frameTime(hint.start_frame)} – ${frameTime(hint.end_frame)}.`);
  }
  function renderReplay() {
    const end = coverageEnd();
    el('seek').disabled = !end || Boolean(publication) || view.mode === 'private_preview'; el('play').disabled = el('seek').disabled;
    el('seek').max = String(end); el('seek').value = String(frame);
    text(el('position'), view.runtime ? `Recording time: ${frameTime(frame)} · Last analyzed position: ${frameTime(end)}` : 'Replay unavailable.');
    const result = view.runtime?.replay; const holder = el('replay-estimates'); holder.replaceChildren();
    if(frame >= end && view.runtime) { holder.append(node('p','Song unknown beyond the last analyzed position.')); return; }
    if (!result || result.frame !== frame) { holder.append(node('p', 'Position estimate pending or unavailable.', 'muted')); return; }
    if (!result.occurrences?.length) holder.append(node('p', 'Song unknown at this recording time.'));
    else { holder.append(node('p', 'Overlapping saved estimates:')); for (const row of result.occurrences) holder.append(node('p', identity(row.identity), 'artist')); }
  }
  function render(data, older, reset=false) {
    const initial = !view;
    const nextHeadKey = data.occurrences[0]?.key || null;
    const headChanged = !older && headKey !== nextHeadKey;
    const changed = view && view.generation !== data.generation;
    const scroll = window.scrollY;
    if (changed || reset) { musicLinksEpoch++; clearMusicLinks(); rows.clear(); el('history').replaceChildren(); cursor = null; pause(); frame = 0; observationKey = null; observationSince = null; }
    view = data;
    if (!older || changed) {
      text(el('event'), data.event); text(el('room'), data.room);
      text(el('clock'), `Recorded history · ${data.timezone}`);
      const a = data.analysis, c = a.coverage;
      const state = a.completion_unconfirmed ? 'Analysis completion is unconfirmed.' :
        a.status === 'completed_partial' || c.state === 'partial_saved_prefix' ? 'Partial analysis — some audio has no results.' :
        a.status === 'completed' ? 'Analysis complete.' : 'Analysis stopped before completion.';
      const review = a.review_kind === 'identity' ? ' Song identity adjustments applied.' : a.review_kind === 'schedule' ? ' Expected schedule updated.' : '';
      text(el('analysis'), `${data.mode === 'private_preview' ? 'Private preview · ' : ''}${state}${review}${a.checkpoint_workspace ? ' Saved snapshot.' : ''}`);
      const key = data.runtime?.observation_key;
      if (key !== observationKey) { observationKey = key; observationSince = key ? performance.now() : null; }
      frame = Math.min(frame, coverageEnd()); renderHint(); renderReplay();
    }
    for (const row of data.occurrences) rows.set(row.key, row);
    const oldNodes = new Map([...el('history').children].map(n => [n.dataset.key,n]));
    const ordered = [...rows.values()].sort((a,b) => b.start_frame-a.start_frame);
    for (const row of ordered) {
      const old = oldNodes.get(row.key); const fingerprint = JSON.stringify(row);
      if (old && old.dataset.fingerprint === fingerprint) el('history').append(old);
      else { const n = occurrence(row); n.dataset.fingerprint = fingerprint; if(old) old.remove(); el('history').append(n); }
    }
    text(el('empty'), ordered.length ? '' : 'No songs available in this recorded history yet.');
    if (older || initial || changed || reset || headChanged) cursor = data.next_cursor;
    if (!older || changed) headKey = nextHeadKey;
    el('older').hidden = !cursor || Boolean(publication);
    el('notice').className = '';
    text(el('notice'), changed ? 'Selected history changed.' : 'Recorded history updated.');
    window.scrollTo(0,scroll);
    if(musicLinksPath) paintMusicLinks();
  }
  function schedule() {
    clearTimeout(timer);
    const base = Math.max(5, config.poll_seconds) * 1000;
    const delay = Math.min(60000, base * 2 ** Math.min(failures, 3)) * (1 + Math.random() * .1);
    timer = setTimeout(() => load(), document.hidden ? Math.max(30000, delay) : delay);
  }
  async function load(older=false) {
    if (busy) return; busy = true; el('older').disabled = true;
    try {
      let data, reset = false;
      if (publication) {
        const manifest = await get(publication);
        if (manifest.schema_version !== 1 && !(manifest.schema_version === 2 && musicLinksPath === 'links.json' &&
            manifest.links_file === (manifest.state === 'withdrawn' ? null : 'links.json'))) throw new Error('unavailable');
        if (manifest.state === 'withdrawn') {
          musicLinksEpoch++; clearMusicLinks(); pause(); rows.clear(); el('history').replaceChildren(); text(el('empty'),'No published history.');
          text(el('notice'),'This metadata publication has been withdrawn.'); return;
        }
        publicationExpiry = Date.parse(manifest.expires_at);
        if (!Number.isFinite(publicationExpiry) || publicationExpiry <= Date.now()) {
          musicLinksEpoch++; clearMusicLinks(); rows.clear(); el('history').replaceChildren(); text(el('empty'),'No unexpired published history.');
          el('notice').className = 'stale'; text(el('notice'),'Published snapshot expired. Metadata is unavailable.'); return;
        }
        if (manifest.state !== 'published' || manifest.view_file !== 'view.json') throw new Error('unavailable');
        data = await get(new URL(manifest.view_file, new URL(publication, location.href)).href);
        render(data,false); el('play').disabled = true; el('seek').disabled = true;
        if(musicLinksPath) void refreshMusicLinks();
        text(el('recognition'),'Not available in a published snapshot.');
        text(el('notice'),`Published metadata snapshot · Expires ${manifest.expires_at}.${manifest.has_more ? ' Bounded history; older occurrences are not included.' : ''}`);
        lastSuccess = null; return;
      } else {
        if (!configured) {
          const c = await get('api/v1/config'); if(c.schema_version !== 1) throw new Error('unavailable');
          config = {...config, ...c}; config.poll_seconds = Math.max(5, Number(c.poll_seconds)||5); config.stale_seconds = Math.max(5, Number(c.stale_seconds)||30); config.page_limit = Math.min(200,Math.max(1,Math.trunc(Number(c.page_limit)||50))); configured = true;
        }
        const query = new URLSearchParams({limit:String(config.page_limit)});
        if(view && view.mode !== 'private_preview') query.set('frame',String(frame));
        if (older && cursor) query.set('cursor',cursor);
        try { data = await get('api/v1/view?'+query); }
        catch (error) {
          // Selection changes can invalidate the previous frame or page cursor.
          // A fresh head is bounded to one retry; transient failures keep saved state.
          if (error.status !== 400 || (!query.has('frame') && !query.has('cursor'))) throw error;
          data = await get('api/v1/view?'+new URLSearchParams({limit:String(config.page_limit)}));
          older = false; reset = true;
        }
      }
      if (data.schema_version !== 1 || !Array.isArray(data.occurrences)) throw new Error('unavailable');
      render(data, older, reset); failures = 0; lastSuccess = performance.now();
      if(musicLinksPath) void refreshMusicLinks();
    } catch (_) {
      failures++; pause(); el('notice').className = 'stale';
      text(el('notice'), view ? 'Refresh unavailable — displayed history is stale. Replay paused.' : 'Recorded history unavailable. Retrying shortly.');
    } finally { busy = false; el('older').disabled = false; if(!publication) schedule(); }
  }
  el('refresh').addEventListener('click', () => load());
  el('older').addEventListener('click', () => load(true));
  el('seek').addEventListener('input', () => { pause(); frame = Math.min(coverageEnd(), Math.max(0, Math.trunc(Number(el('seek').value)))); renderReplay(); });
  el('seek').addEventListener('change', () => load());
  el('play').addEventListener('click', () => {
    if (playing) { pause(); return; }
    playing = true; anchorFrame = frame; anchorTime = performance.now(); text(el('play'),'Pause preview');
    replayTimer = setInterval(() => {
      frame = Math.min(coverageEnd(), Math.floor(anchorFrame + (performance.now()-anchorTime)/1000*view.sample_rate));
      renderReplay(); if(frame >= coverageEnd()) pause(); if(!busy && (lastSuccess === null || performance.now()-lastSuccess >= 5000)) load();
    }, 1000);
  });
  document.addEventListener('visibilitychange', () => { if(document.hidden) pause(); if(!publication) schedule(); });
  setInterval(() => {
    renderHint();
    if(musicLinksExpiry !== null && musicLinksExpiry <= Date.now()) clearMusicLinks();
    if(publication && publicationExpiry !== null && publicationExpiry <= Date.now()) {
      musicLinksEpoch++; clearMusicLinks(); rows.clear(); el('history').replaceChildren(); text(el('empty'),'No unexpired published history.');
      el('notice').className='stale'; text(el('notice'),'Published snapshot expired. Metadata is unavailable.');
    }
    if(lastSuccess !== null && performance.now()-lastSuccess > config.stale_seconds*1000) {
      el('notice').className='stale'; text(el('notice'),'History is stale; waiting for an update.');
    }
  },1000);
  load();
})();
