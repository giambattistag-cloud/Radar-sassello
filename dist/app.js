'use strict';
const HOUR = 3600000, DAY = 24 * HOUR;
const PAGES_DATA = 'https://giambattistag-cloud.github.io/Radar-sassello/data/';
const $ = id => document.getElementById(id);
const fmt = (n, digits = 1) => n == null || !Number.isFinite(n) ? '—' : n.toLocaleString('it-IT', { maximumFractionDigits: digits });
const date = t => new Date(t).toLocaleString('it-IT', { timeZone: 'Europe/Rome', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
let data = null, hours = 240, selected = null, polygons = [], marker = null, archived = false, fallback = false;
let dataBase = 'data/', loadGeneration = 0;
const map = window.L ? L.map('map', { zoomControl: false, preferCanvas: true }).setView([44.47917, 8.48736], 11) : null;
if (map) {
  L.control.zoom({position:'topright'}).addTo(map);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 17, crossOrigin: true
  }).addTo(map);
  L.control.scale({imperial:false, position:'bottomright'}).addTo(map);
  L.circle([44.47917, 8.48736], { radius:25000, color:'#526d55', weight:1, dashArray:'5 7', fill:false, interactive:false }).addTo(map);
} else {
  $('status').textContent = 'La mappa non si è caricata. Riprova con una connessione attiva.';
}

function cellBounds(i) {
  const {width:w, corners:c} = data.grid, row = Math.floor(i/w), col = i%w, a = row*(w+1)+col;
  return [c[a], c[a+1], c[a+w+2], c[a+w+1]];
}
function center(i) {const b=cellBounds(i); return [(b[0][0]+b[2][0])/2,(b[0][1]+b[2][1])/2];}
function nearest(lat, lon) {
  let idx=null, distance=Infinity;
  data.grid.active.forEach((active,i)=>{if(active){const c=center(i), d=(c[0]-lat)**2+((c[1]-lon)*.713)**2;if(d<distance){distance=d;idx=i;}}});
  return idx;
}
function stats(i) {
  let total=0, weighted=0, valid=0, last=null, temp=null, tempTime=null;
  for (const r of data.timeline) {
    if (r.time > data.windowEnd || r.time <= data.windowEnd-hours*HOUR) continue;
    const rain=r.rain[i];
    if(rain!=null){valid++;total+=rain;weighted+=rain*(data.windowEnd-r.time+HOUR/2)/DAY;if(rain>=1 && (last==null||r.time>last))last=r.time;}
    const v=r.temperature?.[i];
    if(v!=null && (tempTime==null||r.time>tempTime)){temp=v;tempTime=r.time;}
  }
  return { total:valid?total:null, age:total>0?weighted/total:null, valid, last, temp, tempTime };
}
function color(mm) {
  const stops=[[0,[244,218,85]],[10,[173,206,122]],[25,[80,182,186]],[50,[40,124,180]],[100,[20,44,109]]];
  for(let k=1;k<stops.length;k++) if(mm<=stops[k][0]) {
    const [a,x]=stops[k-1],[b,y]=stops[k], f=(mm-a)/(b-a);
    return `rgb(${x.map((v,j)=>Math.round(v+(y[j]-v)*f)).join(',')})`;
  }
  return '#142c6d';
}
function paint() {
  if(!data||!map)return;
  data.grid.active.forEach((active,i)=>{
    if(!active)return;
    const s=stats(i), complete=s.valid/hours>=.9;
    const opacity=$('age').checked ? .10 + .85*Math.min((s.age||0)/8,1) : .78;
    const style={color:complete?'#52685c':'#747f77',weight:.25,opacity:.3,
      fillColor:complete?color(s.total):'#a4ada5',fillOpacity:complete?opacity:.24};
    if(!polygons[i])polygons[i]=L.polygon(cellBounds(i),style).addTo(map).on('click',()=>select(i));
    else polygons[i].setStyle(style);
  });
}
function daySeries(i) {
  // Ten consecutive 24-hour blocks cover exactly the operative 240-hour window.
  const days=Array.from({length:10},(_,j)=>({start:data.windowEnd-(10-j)*DAY,rain:0,rainHours:0,tempSum:0,tempHours:0,expected:0}));
  for(const d of days){
    const lo=Math.max(d.start,data.windowStart),hi=Math.min(d.start+DAY,data.windowEnd);
    d.expected=Math.max(0,(hi-lo)/HOUR);
  }
  for(const r of data.timeline){
    if(r.time<=data.windowStart||r.time>data.windowEnd)continue;
    const d=days.find(d=>r.time>d.start&&r.time<=d.start+DAY);if(!d)continue;
    const rain=r.rain[i],temp=r.temperature?.[i];
    if(rain!=null){d.rain+=rain;d.rainHours++;}
    if(temp!=null){d.tempSum+=temp;d.tempHours++;}
  }
  return days;
}
function charts(i) {
  const days=daySeries(i), max=Math.max(1,...days.map(d=>d.rain));
  $('rain-chart').replaceChildren();$('temp-chart').replaceChildren();
  for(const d of days){
    const label=new Date(d.start+DAY).toLocaleDateString('it-IT',{day:'2-digit',month:'2-digit',timeZone:'UTC'});
    const wrap=document.createElement('div');wrap.className='bar-wrap';
    wrap.title=`${label}: ${d.rainHours?fmt(d.rain)+' mm':'dati assenti'} · ${d.rainHours}/${d.expected} ore`;
    const value=document.createElement('span');value.className='bar-value';value.textContent=d.rainHours?fmt(d.rain,0):'—';
    const bar=document.createElement('div');bar.className='bar'+(!d.rainHours?' missing':d.rainHours<24?' partial':'');
    bar.style.height=(d.rainHours?Math.max(2,d.rain/max*67):2)+'%';
    const text=document.createElement('span');text.className='bar-label';text.textContent=label.slice(0,2);
    wrap.append(value,bar,text);$('rain-chart').append(wrap);
    const td=document.createElement('div');td.className='temp-day';td.title=`${label}: ${d.tempHours} ore disponibili`;
    const t=document.createElement('b');t.textContent=d.tempHours?fmt(d.tempSum/d.tempHours,0)+'°':'—';
    const l=document.createElement('small');l.textContent=label.slice(0,2);td.append(t,l);$('temp-chart').append(td);
  }
}
function select(i) {
  if(!data||!Number.isInteger(i)||!data.grid.active[i])throw Error('Cella non disponibile');
  selected=i;const c=center(i),s=stats(i),isCenter=i===nearest(44.47917,8.48736);
  $('point-name').textContent=isCenter?'Sassello':'Punto nel territorio';
  $('point-id').textContent=`CELLA ${i}`;
  $('coords').textContent=`${c[0].toFixed(5)} N · ${c[1].toFixed(5)} E`;
  $('total').textContent=fmt(s.total);
  $('total-caption').textContent=`${s.valid<hours?'totale parziale · ':''}${hours===24?'ultime 24 ore':hours===72?'ultimi 3 giorni':'ultimi 10 giorni'}`;
  $('mean-age').textContent=s.age==null?'—':fmt(s.age)+' giorni';
  $('last-rain').textContent=s.last==null?'Non rilevata':date(s.last);
  $('temperature').textContent=s.temp==null?'—':`${fmt(s.temp)} °C · ${date(s.tempTime)}`;
  $('coverage').textContent=`${s.valid}/${hours} ore · ${fmt(100*s.valid/hours,0)}%`;
  let streak=0,longest=0;for(const d of daySeries(i)){streak=d.rainHours===24&&d.rain>=1?streak+1:0;longest=Math.max(longest,streak);}
  $('rain-streak').textContent=longest+' giorni';
  const stale=!archived && (data.latestRainTime==null||Date.now()-data.latestRainTime>3*HOUR);
  let message=s.valid===hours?'Periodo completo per questa cella.':`Disponibili ${s.valid} ore su ${hours}: le ore mancanti non sono considerate asciutte.`;
  if(stale)message+=' Ultimo dato radar in ritardo.';
  if(fallback)message+=' Copia salvata: il collegamento alla mappa automatica non è ancora disponibile.';
  if(hours===240&&s.valid>=22&&s.valid<216)message+=' Puoi già consultare la vista 24 ore.';
  if(archived)message='Archivio storico. '+message;
  $('status').textContent=message;$('status').classList.toggle('warning',s.valid<hours||stale||fallback);
  if(map){
    if(marker)marker.setLatLng(c);
    else marker=L.marker(c,{icon:L.divIcon({className:'selected-marker',iconSize:[12,12],iconAnchor:[6,6]})}).addTo(map);
  }
  $('export').disabled=false;charts(i);
  return {cell:i,latitude:c[0],longitude:c[1],rainMm:s.total,ageDays:s.age,validHours:s.valid,expectedHours:hours,lastSignificantRain:s.last,temperatureC:s.temp};
}
function validate(payload) {
  if(payload?.schemaVersion!==1||!payload.grid||!Array.isArray(payload.timeline))throw Error('Formato dati non riconosciuto');
  const g=payload.grid,n=g.width*g.height;
  if(!Number.isInteger(n)||n<1||n>10000||g.active.length!==n||g.corners.length!==(g.width+1)*(g.height+1))throw Error('Griglia non valida');
  if(!Number.isFinite(payload.windowEnd)||payload.windowEnd%HOUR)throw Error('Finestra non valida');
  const seen=new Set();
  for(const r of payload.timeline){if(!Number.isFinite(r.time)||r.time%HOUR||seen.has(r.time)||!Array.isArray(r.rain)||r.rain.length!==n)throw Error('Archivio non valido');seen.add(r.time);}
  return payload;
}
async function getJSON(url) {
  const r=await fetch(url,{cache:'no-store',signal:AbortSignal.timeout(12000)});
  if(!r.ok)throw Error(`Dati non disponibili (${r.status})`);return r.json();
}
async function load(snapshot='') {
  const generation=++loadGeneration;
  $('refresh').disabled=true;
  try {
    let payload, nextFallback=false, base='data/';
    const file=snapshot?`snapshots/${encodeURIComponent(snapshot)}`:'latest.json';
    // A Sites copy can follow the automatic Pages feed once Pages is enabled.
    if(!location.hostname.endsWith('github.io') && location.protocol!=='file:') {
      try{payload=await getJSON(PAGES_DATA+file);base=PAGES_DATA;}
      catch{payload=await getJSON('data/'+file);nextFallback=true;}
    } else payload=await getJSON('data/'+file);
    validate(payload);if(generation!==loadGeneration)return;
    data=payload;fallback=nextFallback;dataBase=base;archived=!!snapshot;
    polygons.forEach(p=>p?.remove());polygons=[];
    paint();select(selected!=null&&data.grid.active[selected]?selected:nearest(44.47917,8.48736));
    $('updated').textContent=`${archived?'Archivio':'Ultimo dato radar'}: ${data.latestRainTime?date(data.latestRainTime):'assente'} · ore italiane`;
    $('rain-chart-note').textContent=`Intervalli di 24 ore fino alle ${new Date(data.windowEnd).toISOString().slice(11,16)} UTC · tratteggio = intervallo incompleto`;
    try {
      const list=await getJSON(dataBase+'snapshots.json');
      if(generation!==loadGeneration)return;
      const selectEl=$('snapshot');selectEl.replaceChildren(new Option('Mappa attuale',''));
      for(const name of list.slice().reverse())if(/^\d{4}-\d{2}-\d{2}T\d{2}\.json$/.test(name))selectEl.add(new Option('Periodo fino al '+name.slice(0,10),name));
      selectEl.value=snapshot;
      $('snapshot-note').textContent=list.length?`${list.length} periodi conservati. Ogni file copre 10 giorni.`:'Il primo riepilogo viene salvato dopo 10 giorni di raccolta.';
    } catch {$('snapshot-note').textContent='Elenco archivio non disponibile.';}
  } catch(e) {
    if(generation!==loadGeneration)return;
    $('status').classList.add('warning');
    $('status').textContent=data?'Aggiornamento non riuscito: restano visibili i dati precedenti.':'Le prime misure non sono ancora disponibili. La raccolta deve completare almeno un aggiornamento.';
    $('updated').textContent='Aggiornamento non riuscito · riprova tra qualche minuto';
  } finally {if(generation===loadGeneration)$('refresh').disabled=false;}
}
function setPeriod(value){
  if(![24,72,240].includes(value))throw Error('Periodo ammesso: 24, 72 o 240 ore');
  hours=value;document.querySelectorAll('[data-hours]').forEach(b=>{b.classList.toggle('active',Number(b.dataset.hours)===hours);b.setAttribute('aria-pressed',String(Number(b.dataset.hours)===hours));});
  paint();if(selected!=null)select(selected);
}
document.querySelectorAll('[data-hours]').forEach(b=>b.addEventListener('click',()=>setPeriod(Number(b.dataset.hours))));
$('age').addEventListener('change',paint);
$('home').addEventListener('click',()=>{map?.setView([44.47917,8.48736],11);if(data)select(nearest(44.47917,8.48736));});
$('refresh').addEventListener('click',()=>load($('snapshot').value));
$('snapshot').addEventListener('change',e=>load(e.target.value));
$('export').addEventListener('click',()=>{
  if(!data||selected==null)return;
  const c=center(selected), rows=['fine_intervallo_utc,latitudine,longitudine,pioggia_mm,temperatura_c'];
  for(let t=data.windowStart+HOUR;t<=data.windowEnd;t+=HOUR){const r=data.timeline.find(r=>r.time===t);rows.push([new Date(t).toISOString(),c[0],c[1],r?.rain[selected]??'',r?.temperature?.[selected]??''].join(','));}
  const url=URL.createObjectURL(new Blob([rows.join('\n')],{type:'text/csv;charset=utf-8'})),a=document.createElement('a');a.href=url;a.download=`sassello-cella-${selected}-${new Date(data.windowEnd).toISOString().slice(0,10)}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
if(document.modelContext?.registerTool){
  const lifecycle=new AbortController();window.addEventListener('pagehide',()=>lifecycle.abort(),{once:true});
  Promise.resolve(document.modelContext.registerTool({name:'select_rainfall_point',title:'Esamina un punto di pioggia',description:'Seleziona un punto entro 25 km da Sassello e mostra pioggia, età e copertura del periodo.',inputSchema:{type:'object',properties:{latitude:{type:'number'},longitude:{type:'number'},hours:{type:'integer',enum:[24,72,240]}},required:['latitude','longitude'],additionalProperties:false},annotations:{readOnlyHint:false},execute(input){
    if(!data)throw Error('Dati non ancora disponibili');
    const {latitude:lat,longitude:lon,hours:h}=input||{};
    if(!Number.isFinite(lat)||!Number.isFinite(lon)||Math.hypot((lat-44.47917)*111.2,(lon-8.48736)*79.4)>25)throw Error('Coordinate fuori dall’area di Sassello');
    if(h!==undefined && ![24,72,240].includes(h))throw Error('Periodo non valido');
    if(h!==undefined)setPeriod(h);const i=nearest(lat,lon);map?.panTo(center(i));return select(i);
  }},{signal:lifecycle.signal})).catch(()=>{});
}
load();
setInterval(()=>{if(!document.hidden&&!archived)load();},10*60*1000);
