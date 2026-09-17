const state = { period:'all', frequency:'hourly', aggregation:'avg', series:{observed:true,reported:false}, daily:[], hourly:[], reported:[], games:[], filteredPoints:[] };
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const number = (value) => Number.isFinite(Number(value)) ? Number(value) : null;
const formatNumber = (value) => value == null ? '—' : new Intl.NumberFormat('en-US', { maximumFractionDigits:0 }).format(Math.round(value));
const formatCompact = (value) => value == null ? '—' : new Intl.NumberFormat('en-US', { notation:'compact', maximumFractionDigits:2 }).format(value);
const parseDate = (value) => new Date(value.includes('T') ? value : `${value}T00:00:00Z`);
const isoDay = (date) => date.toISOString().slice(0,10);

async function loadData() {
  const [daily, hourly, reported, games] = await Promise.all([
    fetch('public/data/platform_daily.json').then(r=>r.json()),
    fetch('public/data/platform_hourly.json').then(r=>r.json()),
    fetch('public/data/reported_points.json').then(r=>r.json()),
    fetch('public/data/games_daily.json').then(r=>r.json())
  ]);
  state.daily = daily; state.hourly = hourly; state.reported = reported; state.games = games;
  initializeDates(); renderAll();
}

function initializeDates() {
  const dates = state.hourly.map(r=>r.timestamp).concat(state.daily.map(r=>r.date)).filter(Boolean).sort();
  if (!dates.length) return;
  $('#startDate').value = isoDay(parseDate(dates[0])); $('#endDate').value = isoDay(parseDate(dates[dates.length-1]));
  $('#coverageText').textContent = `${isoDay(parseDate(dates[0]))}—${isoDay(parseDate(dates[dates.length-1]))}`;
}

function selectedRange() {
  let end = new Date(`${$('#endDate').value || isoDay(new Date())}T23:59:59Z`);
  let start = new Date(`${$('#startDate').value || '2000-01-01'}T00:00:00Z`);
  if (state.period !== 'all' && state.period !== 'custom') {
    const days = { '1d':1, '7d':7, '30d':30, '90d':90, '1y':365, '2y':730, '5y':1825 }[state.period];
    start = new Date(end); start.setUTCDate(start.getUTCDate() - days + 1);
  }
  return { start, end };
}

function sourceRows() {
  const range = selectedRange();
  if (state.frequency === 'hourly') return state.hourly.filter(r => { const d=parseDate(r.timestamp); return d>=range.start && d<=range.end; }).map(r => ({ date:r.timestamp, value:number(r[state.aggregation === 'avg' ? 'ccu' : 'peak']), raw:true }));
  if (state.frequency === 'daily') return state.daily.filter(r => { const d=parseDate(r.date); return d>=range.start && d<=range.end; }).map(r => ({ date:r.date, value:number(r[state.aggregation]), raw:true }));
  const daily = state.daily.filter(r => { const d=parseDate(r.date); return d>=range.start && d<=range.end; });
  const buckets = new Map();
  for (const row of daily) {
    const date = parseDate(row.date); const key = state.frequency === 'weekly' ? weekKey(date) : `${date.getUTCFullYear()}-${String(date.getUTCMonth()+1).padStart(2,'0')}`;
    if (!buckets.has(key)) buckets.set(key, []); buckets.get(key).push(number(row[state.aggregation]));
  }
  return [...buckets.entries()].sort((a,b)=>a[0].localeCompare(b[0])).map(([date, values]) => ({ date, value:state.aggregation==='avg' ? mean(values) : Math.max(...values), raw:false }));
}

function weekKey(date) { const d=new Date(Date.UTC(date.getUTCFullYear(),date.getUTCMonth(),date.getUTCDate())); const day=d.getUTCDay()||7; d.setUTCDate(d.getUTCDate()-day+1); return isoDay(d); }
function mean(values) { const valid=values.filter(v=>v!=null); return valid.length ? valid.reduce((a,b)=>a+b,0)/valid.length : null; }
function pointsForChart() { return sourceRows().filter(p=>p.value!=null); }

function renderAll() { updateLabels(); renderChart(); renderSummary(); renderGames(); }
function updateLabels() {
  const freq = state.frequency[0].toUpperCase()+state.frequency.slice(1); const agg = state.aggregation==='avg' ? 'Average' : 'Peak';
  $('#chartSubtitle').textContent = `${freq} · ${agg} · ${state.period==='all'?'전체 기간':state.period.toUpperCase()}`;
  $('#chartStatus').textContent = `${pointsForChart().length.toLocaleString()}개 포인트 · 추정값 없음`;
  const latest = state.hourly.length ? state.hourly[state.hourly.length-1] : null; $('#latestCcu').textContent = latest ? formatNumber(latest.ccu) : '—';
}

function renderSummary() {
  const points = pointsForChart(); const avg=mean(points.map(p=>p.value)); const peak=points.length?Math.max(...points.map(p=>p.value)):null;
  $('#summaryAvg').textContent=formatNumber(avg); $('#summaryPeak').textContent=formatNumber(peak); $('#summaryPoints').textContent=points.length.toLocaleString();
  const last=points.length?points[points.length-1].date:null; $('#summaryFreshness').textContent=last?isoDay(parseDate(last)):'—'; $('#summaryFreshnessSub').textContent=last?`마지막 관측 ${last.includes('T')?new Date(last).toISOString().slice(11,16)+' UTC':''}`:'데이터 없음';
  $('#summaryPointsSub').textContent = state.frequency==='hourly' ? '원시 hourly 관측' : '선택 주기 집계';
}

function renderChart() {
  const canvas=$('#usageChart'), rect=canvas.getBoundingClientRect(), dpr=window.devicePixelRatio||1; canvas.width=rect.width*dpr; canvas.height=rect.height*dpr; const ctx=canvas.getContext('2d'); ctx.scale(dpr,dpr);
  const w=rect.width,h=rect.height,pad={l:66,r:24,t:18,b:42}; ctx.clearRect(0,0,w,h); const points=pointsForChart();
  if (!points.length) { $('#emptyState').classList.remove('hidden'); return; } $('#emptyState').classList.add('hidden');
  const max=Math.max(...points.map(p=>p.value))*1.08, min=0; const x=i=>pad.l+(w-pad.l-pad.r)*(points.length===1?0.5:i/(points.length-1)); const y=v=>h-pad.b-(h-pad.t-pad.b)*(v-min)/(max-min);
  ctx.font='11px Inter, sans-serif'; ctx.strokeStyle='#253555'; ctx.fillStyle='#8290ad'; ctx.lineWidth=1;
  for(let i=0;i<=5;i++){ const yy=pad.t+(h-pad.t-pad.b)*i/5; ctx.beginPath();ctx.moveTo(pad.l,yy);ctx.lineTo(w-pad.r,yy);ctx.stroke();ctx.fillText(formatCompact(max-(max*i/5)),10,yy+4); }
  ctx.strokeStyle='#3e8cff'; ctx.lineWidth=2.4; ctx.beginPath();
  points.forEach((p,i)=>{ if(i===0)ctx.moveTo(x(i),y(p.value)); else { const prev=points[i-1]; const gap=parseDate(p.date)-parseDate(prev.date); const maxGap=state.frequency==='hourly'?3*3600000:state.frequency==='daily'?3*86400000:state.frequency==='weekly'?15*86400000:45*86400000; if(gap>maxGap)ctx.moveTo(x(i),y(p.value)); else ctx.lineTo(x(i),y(p.value)); } }); ctx.stroke();
  ctx.fillStyle='#8290ad'; const labelEvery=Math.max(1,Math.floor(points.length/7)); points.forEach((p,i)=>{if(i%labelEvery===0||i===points.length-1){const label=state.frequency==='hourly'?new Date(p.date).toISOString().slice(5,10):String(p.date).slice(5,10);ctx.fillText(label,x(i)-14,h-15);}});
  if(state.series.reported){ctx.fillStyle='#b17cff'; for(const r of state.reported){if(!r.date||r.ccu==null)continue;const d=parseDate(r.date);const range=selectedRange();if(d<range.start||d>range.end)continue;const nearest=points.reduce((best,p)=>Math.abs(parseDate(p.date)-d)<Math.abs(parseDate(best.date)-d)?p:best,points[0]);const idx=points.indexOf(nearest);ctx.beginPath();ctx.arc(x(idx),y(r.ccu),4,0,Math.PI*2);ctx.fill();}}
}

function renderGames() {
  const search=($('#gameSearch').value||'').toLowerCase(); const range=selectedRange(); const map=new Map();
  for(const row of state.games){const d=parseDate(row.date);if(d<range.start||d>range.end)continue;if(search&&!String(row.name).toLowerCase().includes(search))continue;const id=String(row.universeId);if(!map.has(id))map.set(id,{name:row.name,genre:row.genre||'—',avg:[],peak:[],days:new Set()});const g=map.get(id);g.avg.push(number(row.avg));g.peak.push(number(row.peak));g.days.add(row.date);}
  const rows=[...map.values()].map(g=>({...g,avg:mean(g.avg),peak:g.peak.length?Math.max(...g.peak):null,days:g.days.size})).sort((a,b)=>(b.avg||0)-(a.avg||0)).slice(0,20); $('#gameTable').innerHTML=rows.map(g=>`<tr><td class="game-name">${escapeHtml(g.name)}</td><td class="muted">${escapeHtml(g.genre)}</td><td>${formatNumber(g.avg)}</td><td>${formatNumber(g.peak)}</td><td>${g.days}</td></tr>`).join(''); $('#gameTableFooter').textContent=rows.length?`상위 ${rows.length}개 · 공개 일별 관측 데이터 기준`:'선택 범위에 게임 데이터가 없습니다.';
}
function escapeHtml(value){return String(value).replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));}

function download(name, content, type) { const blob=new Blob([content],{type}); const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;a.click();URL.revokeObjectURL(a.href); }
function downloadCurrent(format) { const data=pointsForChart(); if(format==='json')download('roblox-usage-points.json',JSON.stringify({policy:'no modeled DAU; no gap interpolation',frequency:state.frequency,aggregation:state.aggregation,data},null,2),'application/json'); else download('roblox-usage-points.csv',['timestamp,value',...data.map(p=>`${p.date},${p.value}`)].join('\n'),'text/csv'); }

for(const group of ['period','frequency','aggregation']) $(`#${group}Controls`).addEventListener('click', e=>{const button=e.target.closest('button');if(!button)return;state[group]=button.dataset[group];$$(`#${group}Controls button`).forEach(b=>b.classList.toggle('active',b===button));if(group==='period'&&state.period!=='all'){const range=selectedRange();$('#startDate').value=isoDay(range.start);$('#endDate').value=isoDay(range.end);}renderAll();});
$('#applyDates').addEventListener('click',()=>{state.period='custom';$$('#periodControls button').forEach(b=>b.classList.remove('active'));renderAll();}); $('#gameSearch').addEventListener('input',renderGames); $('#downloadCsv').addEventListener('click',()=>downloadCurrent('csv')); $('#downloadJson').addEventListener('click',()=>downloadCurrent('json')); $('#fullscreen').addEventListener('click',()=>$('#chartCard').requestFullscreen?.());
$$('.legend-item').forEach(button=>button.addEventListener('click',()=>{const key=button.dataset.series;state.series[key]=!state.series[key];button.classList.toggle('selected',state.series[key]);renderChart();})); window.addEventListener('resize',renderChart);
loadData().catch(error=>{console.error(error);$('#chartStatus').textContent='데이터를 불러오지 못했습니다. build-data.ps1을 먼저 실행하세요.';});
