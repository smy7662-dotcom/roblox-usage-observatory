// 히트 랭킹 — games/index.json(요약) + games/{id}.json(일별)으로 순위·급등·신작·기록을 보여준다.
// app.js 와 같은 전역에서 돌기 때문에 drawChart·attachHover·fmt·esc 를 그대로 쓴다.
const hits = { index: null, tab: 'now', pick: null, detail: null, range: 90, loading: false };
const HITS_TABS = {
  now: { label: '지금 순위', sub: '최근 하루 평균 CCU 순', sort: g => g.d1Avg ?? g.lastAvg ?? 0 },
  rising: { label: '급등', sub: '최근 7일 평균이 직전보다 많이 오른 순 (평균 3만 이상)', sort: g => (g.d7Avg >= 30000 ? (g.chg7d ?? -999) : -999) },
  fresh: { label: '신작', sub: '관측 시작 90일 이내 · 최근 하루 평균 순', sort: g => (daysSince(g.firstSeen) <= 90 ? (g.d1Avg ?? 0) : -1) },
  record: { label: '역대 기록', sub: '관측된 최고 동시접속 순 · 그날 플랫폼에서 차지한 비중', sort: g => g.allPeak ?? 0 },
};

// 로블록스 게임 제목은 앞에 업데이트 태그([UPD]·[15 MINS])와 이모지가 붙어 목록에서 읽기 어렵다.
// 목록·카드에는 태그와 양끝 이모지를 뗀 이름을 쓰고, 원래 제목은 title 툴팁으로 남긴다.
const NAME_TAG = /^\s*(?:\[[^\]]*\]\s*)+/;
const EDGE_ICON = /^[\s\p{Extended_Pictographic}‍️⃣]+|[\s\p{Extended_Pictographic}‍️⃣]+$/gu;
function dispName(n) {
  const s = String(n || '').replace(NAME_TAG, '').replace(EDGE_ICON, '').trim();
  return s || String(n || '');
}

function daysSince(d) { return d ? Math.round((Date.now() - Date.parse(`${d}T00:00:00Z`)) / 86400000) : 9999; }
const pctText = v => v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`;
const pctClass = v => v == null ? '' : v > 0 ? 'up' : v < 0 ? 'down' : '';

async function loadHitsIndex() {
  if (hits.index || hits.loading) return hits.index;
  hits.loading = true;
  hits.index = await getJson('public/data/games/index.json', { games: [] });
  hits.loading = false;
  return hits.index;
}

function hitRows() {
  const conf = HITS_TABS[hits.tab];
  const q = ($('#hitSearch')?.value || '').trim().toLowerCase();
  return (hits.index?.games || [])
    .filter(g => !q || `${g.name} ${g.id}`.toLowerCase().includes(q))
    .map(g => ({ ...g, _k: conf.sort(g) }))
    .filter(g => g._k > -900)
    .sort((a, b) => b._k - a._k)
    .slice(0, 60);
}

async function renderHits() {
  renderShareHistory();
  await loadHitsIndex();
  const conf = HITS_TABS[hits.tab];
  $('#hitSub').textContent = conf.sub;
  $$('#hitTabs button').forEach(b => b.classList.toggle('active', b.dataset.hitTab === hits.tab));
  const rows = hitRows();
  $('#hitTable').innerHTML = rows.map((g, i) => `<tr data-hit-id="${esc(g.id)}" class="${hits.pick === g.id ? 'active' : ''}">
    <td class="rank">${i + 1}</td>
    <td class="game"><b title="${esc(g.name)}">${esc(dispName(g.name))}</b><small>${esc(g.creator || '제작자 미확인')}${g.genre ? ' · ' + esc(g.genre) : ''}</small></td>
    <td class="num">${fmt(g.d1Avg ?? g.lastAvg)}</td>
    <td class="num">${fmt(g.d7Peak)}</td>
    <td class="num ${pctClass(g.chg7d)}">${pctText(g.chg7d)}</td>
    <td class="num">${fmt(g.allPeak)}<small>${esc(g.allPeakDate || '')}</small></td>
    <td class="num">${g.peakShare == null ? '—' : g.peakShare.toFixed(1) + '%'}</td>
    <td class="num">${fmt(g.days)}일</td></tr>`).join('') || '<tr><td colspan="8" class="muted">해당 조건의 게임이 없습니다.</td></tr>';
  $('#hitFooter').textContent = `${hits.index?.count || 0}개 게임 중 ${rows.length}개 표시 · 색인 기준 ${hits.index?.updatedAt || '—'} (UTC 날짜) · 최고 ${fmtCompact(hits.index?.minPeakIndexed || 0)}명 이상만 목록에 올림`;
  if (hits.pick) renderHitDetail();
}

async function openHit(id) {
  hits.pick = String(id);
  $$('#hitTable tr').forEach(tr => tr.classList.toggle('active', tr.dataset.hitId === hits.pick));
  hits.detail = await getJson(`public/data/games/${hits.pick}.json`, null);
  renderHitDetail();
}

function hitSeries(d) {
  const cut = hits.range ? Date.now() - hits.range * 86400000 : 0;
  const rows = (d.days || []).filter(r => !cut || Date.parse(`${r[0]}T00:00:00Z`) >= cut);
  const mk = (i, name) => ({
    name, points: rows.filter(r => r[i] != null).map(r => ({
      date: r[0], t: Date.parse(`${r[0]}T12:00:00Z`), value: r[i], step: 86400000 * 2,
      src: r[4], tier: `관측 ${r[3]}회`,
    })),
  });
  return [mk(1, '일 최고'), mk(2, '일 평균')];
}

function renderHitDetail() {
  const box = $('#hitDetail');
  if (!hits.detail) { box.classList.add('hidden'); return; }
  const d = hits.detail, rec = d.records || {};
  box.classList.remove('hidden');
  $('#hitDetailName').textContent = dispName(d.name);
  $('#hitDetailName').title = d.name;
  $('#hitDetailMeta').textContent = `${d.creator || '제작자 미확인'}${d.genre ? ' · ' + d.genre : ''} · 관측 ${rec.daysTracked || 0}일 (${rec.firstSeen || '—'} ~ ${rec.lastSeen || '—'})`;
  const last = (d.days || []).at(-1) || [];
  $('#hitKpiNow').textContent = fmt(last[2]);
  $('#hitKpiNowSub').textContent = `${last[0] || '—'} 평균 · 관측 ${last[3] || 0}회`;
  $('#hitKpiPeak').textContent = fmt(rec.peak);
  $('#hitKpiPeakSub').textContent = `${rec.peakDate || '—'}${rec.peakShareOfPlatform ? ` · 그날 플랫폼의 ${rec.peakShareOfPlatform}%` : ''}`;
  const w = n => {
    const cut = Date.now() - n * 86400000;
    const rows = (d.days || []).filter(r => Date.parse(`${r[0]}T00:00:00Z`) >= cut);
    return { peak: Math.max(0, ...rows.map(r => r[1] || 0)), avg: mean(rows.map(r => r[2])) };
  };
  const w7 = w(7), w30 = w(30);
  $('#hitKpi7').textContent = fmt(w7.peak);
  $('#hitKpi7Sub').textContent = `평균 ${fmt(w7.avg)}`;
  $('#hitKpi30').textContent = fmt(w30.peak);
  $('#hitKpi30Sub').textContent = `평균 ${fmt(w30.avg)}`;
  const srcs = new Set((d.days || []).map(r => r[4]));
  $('#hitSourceNote').textContent = `출처: ${[...srcs].map(s => ({ live: '우리 수집(1시간 간격)', wayback: '웨이백 사본(하루 몇 회)', legacy: '옛 일별 아카이브' }[s] || s)).join(' · ')} — 관측 횟수가 적은 날은 피크를 놓쳤을 수 있음`;
  const canvas = $('#hitChart');
  drawChart(canvas, hitSeries(d), { emptyEl: $('#hitEmpty'), colors: ['#f08a4b', '#4c91ff'], step: 86400000 * 2, period: 'daily' });
}

// ── 히트의 역사 ───────────────────────────────────────────────────────────────
// 게임 하나가 플랫폼을 얼마나 끌었는지는 절대 CCU 보다 비중이 잘 보여준다.
// (플랫폼 자체가 1년 새 몇 배로 커져서, 같은 200만이라도 2024년과 2026년의 의미가 다름)
const SHARE_COLORS = ['#f08a4b', '#4c91ff', '#25c98b', '#b58aff', '#e9ad45', '#f05e7e',
  '#4dd0e1', '#9ccc65', '#ff8a65', '#7986cb', '#ef5da8', '#00b894'];
const shareState = { data: null, range: 0, grain: 'weekly', loading: false };

async function loadShare() {
  if (shareState.data || shareState.loading) return shareState.data;
  shareState.loading = true;
  shareState.data = await getJson('public/data/games/share_history.json', null);
  shareState.loading = false;
  return shareState.data;
}

async function renderShareHistory() {
  const canvas = $('#shareChart');
  if (!canvas) return;
  const d = await loadShare();
  if (!d || !d.dates?.length) {
    $('#shareEmpty')?.classList.remove('hidden');
    $('#shareNote').textContent = '비중 곡선이 아직 만들어지지 않았습니다.';
    return;
  }
  const cut = shareState.range ? Date.now() - shareState.range * 86400000 : 0;
  const keep = d.dates.map((_, i) => i).filter(i => !cut || Date.parse(`${d.dates[i]}T00:00:00Z`) >= cut);
  // 하루 단위는 814점 × 13선이라 선이 서로 묻힌다. 기본은 주 평균(관측된 날만 평균, 없는 주는 비움).
  const weekOf = iso => {
    const t = Date.parse(`${iso}T00:00:00Z`), wd = (new Date(t).getUTCDay() + 6) % 7;
    return new Date(t - wd * 86400000).toISOString().slice(0, 10);
  };
  const mk = (label, vals) => {
    const have = keep.filter(i => vals[i] != null);
    if (shareState.grain === 'daily') {
      return { label, points: have.map(i => ({ date: d.dates[i], value: vals[i], step: 86400000 * 2 })) };
    }
    const acc = new Map();
    for (const i of have) {
      const k = weekOf(d.dates[i]), a = acc.get(k) || [0, 0];
      a[0] += vals[i]; a[1] += 1; acc.set(k, a);
    }
    return {
      label,
      points: [...acc].sort().map(([k, [sum, n]]) => ({ date: k, value: sum / n, days: n, step: 86400000 * 7 })),
    };
  };
  const series = [mk('상위 10개 합', d.top10Share), ...d.games.map(g => mk(dispName(g.name), g.values))];
  const colors = ['#8892a4', ...d.games.map((_, i) => SHARE_COLORS[i % SHARE_COLORS.length])];
  const wk = shareState.grain === 'weekly';
  drawChart(canvas, series, {
    emptyEl: $('#shareEmpty'), colors, step: 86400000 * (wk ? 7 : 2), period: wk ? 'weekly' : 'daily',
    fmtY: v => `${v.toFixed(0)}%`, fmtV: v => `${v.toFixed(1)}%`,
  });
  $('#shareLegend').innerHTML = series
    .map((x, i) => `<span class="lg"><i style="background:${colors[i]}"></i>${esc(x.label)}</span>`).join('');
  $('#shareSub').textContent = `${wk ? '주 평균' : '하루'} 기준 점유율 · 플랫폼 하루 평균 대비 · ${d.dates[keep[0]]} ~ ${d.dates[keep.at(-1)]} (UTC)`;
  $('#shareNote').textContent = `${d.policy} 선을 그린 기준: ${d.pickRule}.`;
}

$('#shareGrain')?.addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b) return;
  shareState.grain = b.dataset.shareGrain;
  $$('#shareGrain button').forEach(x => x.classList.toggle('active', x === b));
  renderShareHistory();
});
$('#shareRange')?.addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b) return;
  shareState.range = Number(b.dataset.shareRange);
  $$('#shareRange button').forEach(x => x.classList.toggle('active', x === b));
  renderShareHistory();
});
attachHover($('#shareChart'));

window.renderHits = renderHits;

// 개요 화면 상단 하이라이트: 이번 주 가장 많이 오른 게임 3개
async function renderHitHighlights() {
  const box = $('#hitHighlights');
  if (!box) return;
  await loadHitsIndex();
  const rising = (hits.index?.games || [])
    .filter(g => (g.d7Avg || 0) >= 50000 && g.chg7d != null)
    .sort((a, b) => b.chg7d - a.chg7d).slice(0, 3);
  const topNow = (hits.index?.games || []).slice(0, 1)[0];
  const cards = [];
  if (topNow) cards.push(`<article class="hl-card"><span>지금 1위</span><strong title="${esc(topNow.name)}">${esc(dispName(topNow.name))}</strong><small>하루 평균 ${fmt(topNow.d1Avg ?? topNow.lastAvg)} · 역대 최고 ${fmt(topNow.allPeak)} (${esc(topNow.allPeakDate || '')})</small></article>`);
  for (const g of rising) cards.push(`<article class="hl-card"><span>이번 주 상승</span><strong title="${esc(g.name)}">${esc(dispName(g.name))}</strong><small class="up">${pctText(g.chg7d)} · 7일 평균 ${fmt(g.d7Avg)}</small></article>`);
  box.innerHTML = cards.join('') || '<article class="hl-card"><span>—</span><strong>집계 준비 중</strong><small>게임별 일단위 집계가 만들어지면 표시됩니다.</small></article>';
}
window.renderHitHighlights = renderHitHighlights;

$('#hitTabs')?.addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; hits.tab = b.dataset.hitTab; renderHits(); });
$('#hitSearch')?.addEventListener('input', () => renderHits());
$('#hitTable')?.addEventListener('click', e => { const tr = e.target.closest('tr[data-hit-id]'); if (tr) openHit(tr.dataset.hitId); });
$('#hitRange')?.addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; hits.range = Number(b.dataset.hitRange); $$('#hitRange button').forEach(x => x.classList.toggle('active', x === b)); renderHitDetail(); });
$('#hitDetailClose')?.addEventListener('click', () => { hits.pick = null; hits.detail = null; $('#hitDetail').classList.add('hidden'); $$('#hitTable tr').forEach(tr => tr.classList.remove('active')); });
window.addEventListener('resize', () => { if (state.route !== 'hits') return; renderShareHistory(); if (hits.detail) renderHitDetail(); });
attachHover($('#hitChart'));
