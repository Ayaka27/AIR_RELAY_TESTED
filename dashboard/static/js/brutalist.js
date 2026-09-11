/* 
 * TACTICAL BRUTALIST DASHBOARD - AIR RELAY
 * No smooth animations, hard edges, typewriter feel
 * Works with Flask API: /api/overview, /api/stream (SSE), /api/config/channels
 */

const $ = id => document.getElementById(id);
const CHANNELS = ['F1','F2','F3','F4'];
const CH_COLORS = {F1:'#ffb000',F2:'#4a7c59',F3:'#8b3a3a',F4:'#4a5a7c'};
let OVER=null;
let history = {};
const HBUF=90;
let evBuffer=[];
let qLoaded=false;

// === UTILS - NO SMOOTH ===
const stateCls = s => {
  s=(s||'').toUpperCase();
  if(['AVAILABLE','ACTIVE','HEALTHY','RUNNING','CONNECTED','ON','OK','ENABLED','RECEIVED','TRANSMITTING'].includes(s)) return 'g';
  if(['QUEUED','RETRYING','DEGRADED','RECOVERING','SEARCHING','HOLD','MONITORING','OPTIMIZATION','DISABLED','WARN'].includes(s)) return 'y';
  if(['UNAVAILABLE','FAILED','EXPIRED','DROPPED','OFF','ERROR','BAD','DISCONNECTED'].includes(s)) return 'r';
  return 'm';
};
const badge=(s)=>{s=s||'—';return `<span class="st ${stateCls(s)}">${s}</span>`;};
const colorOf=s=>CH_COLORS[s]||'#6b6b6b';
const fmtBytes=b=>b==null?'—':(b>=1048576?(b/1048576).toFixed(1)+' MB':(b>=1024?(b/1024).toFixed(0)+' KB':b+' B'));
const nowMs=()=>Date.now();
function ago(ms){ if(!ms) return '—'; const s=(nowMs()-ms)/1000; if(s<0)return 'NOW'; if(s<60)return s.toFixed(0)+'S'; if(s<3600)return (s/60).toFixed(0)+'M'; return (s/3600).toFixed(1)+'H'; }
function escapeHtml(s){ return String(s==null?'':s).replace(/[&<>\"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[m])); }

async function jf(url,opts){
  const r=await fetch(url,Object.assign({headers:{'Content-Type':'application/json'}},opts||{}));
  let j={}; try{j=await r.json();}catch(e){}
  return {status:r.status, j};
}
function toast(msg,type='ok',ms=4000){
  const el=document.createElement('div'); el.className='toast '+(type==='ok'?'':type); el.textContent='> '+msg;
  $('toasts').appendChild(el); setTimeout(()=>el.remove(),ms);
}

// === CLOCK - 7-SEG ===
function fmtClock(){
  const d=new Date();
  const t=d.toLocaleTimeString('en-GB',{hour12:false});
  const ms=String(d.getMilliseconds()).padStart(3,'0');
  $('clockTime').textContent=t+'.'+ms;
  $('clockDate').textContent=d.toISOString().slice(0,10);
  $('uptimeTxt').textContent = OVER&&OVER.system? ('UP '+fmtUptime(OVER.system.uptime_s)) : 'UP --';
}
function fmtUptime(s){ if(!s) return '--'; const h=Math.floor(s/3600), m=Math.floor((s%3600)/60); return h+'H '+m+'M'; }
setInterval(fmtClock,100);

// === TABS - HARD ===
document.getElementById('tabs').addEventListener('click',e=>{
  const b=e.target.closest('button'); if(!b)return;
  document.querySelectorAll('nav.tabs button').forEach(x=>x.classList.toggle('active',x===b));
  document.querySelectorAll('.tabp').forEach(p=>p.classList.add('hide'));
  $('tab-'+b.dataset.t).classList.remove('hide');
  if(b.dataset.t==='overview'){ setTimeout(()=>{resizeChart(); drawChart(); resizeSpectrum();},80); }
});

// === KPI - INSTRUMENT PANEL ===
function renderKpi(o){
  const res=o.resource||{}, sys=o.system||{}, st=sys.store_stats||{};
  const links=(o.channels||[]).filter(c=>c.link && ['ACTIVE','AVAILABLE'].includes(c.link.state)).length;
  const pend=st.pending||0; const delivered=(st.by_status&&st.by_status.DELIVERED)||0;
  const k=(l,v,s)=>`<div class="kcard"><div class="label">${l}</div><div class="value">${v}</div><div class="sub">${s}</div></div>`;
  const cpu=res.cpu_pct!=null?res.cpu_pct.toFixed(0):'--';
  const ram=res.ram_pct!=null?res.ram_pct.toFixed(0):'--';
  const temp=res.temp_c!=null?res.temp_c.toFixed(0):'--';
  const rx=o.radio&&o.radio.rx&&o.radio.rx.present, tx=o.radio&&o.radio.tx&&o.radio.tx.present;
  $('kpi').innerHTML =
    k('ACTIVE LINKS',`${links}/4`,'HEARD')+
    k('QUEUED',`${pend}`,'PENDING')+
    k('DELIVERED',`${delivered}`,'REPLAYED')+
    k('RX PATH',rx?'RTL ●':'NO DEV','RECEIVER')+
    k('TX PATH',tx?'HACKRF ●':'NO DEV','TRANSMITTER')+
    k('CPU',`${cpu}%`,'LOAD')+
    k('RAM',`${ram}%`,'USED')+
    k('TEMP',`${temp}C`,'SOC');
}

// === CHANNELS - RADIO FACEPLATES ===
function renderChannels(o){
  const cards=(o.channels||[]).map(c=>{
    const l=c.link||{}; const act=l.signal_active;
    const level=l.last_rssi_dbm;
    const levelStr= level==null?'--':level.toFixed(0)+' DBM';
    const qp=Math.max(0,Math.min(100,Math.round((l.quality||0)*100)));
    return `<div class="ch ${act?'s-active':''}">
      <div class="ch-top">
        <div><div class="ch-name">${c.id}</div><div class="ch-tag">${escapeHtml(c.label||'')}</div></div>
        <div class="ch-status">${badge(l.state||'UNKNOWN')}</div>
      </div>
      <div class="ch-freq">${c.frequency_mhz!=null?c.frequency_mhz.toFixed(3):'--'}<small> MHZ</small></div>
      <div class="ch-meta">
        <div><span class="k">LEVEL</span> ${levelStr}</div>
        <div><span class="k">LAST RX</span> ${ago(l.last_rx_at_ms)}</div>
        <div><span class="k">QUEUE</span> ${l.queue_len||0}</div>
        <div><span class="k">OK</span> ${l.deliveries_ok||0}</div>
      </div>
      <div class="bar"><span style="width:${qp}%"></span></div>
      <div style="font-size:9px; color:var(--steel-light); margin-top:4px; letter-spacing:1px">QUALITY ${qp}%</div>
    </div>`;
  }).join('');
  $('chanCards').innerHTML = cards||'<div class="empty">NO CHANNELS CONFIGURED</div>';
}

// === TABLES ===
function renderLinkTable(o){
  const rows=(o.channels||[]).map(c=>{
    const l=c.link||{};
    const level=l.last_rssi_dbm!=null?l.last_rssi_dbm.toFixed(0)+' DBM':'--';
    return `<tr>
      <td><b>${c.id}</b> ${c.label||''}</td>
      <td class="em">${c.frequency_mhz.toFixed(3)}</td>
      <td>${badge(l.state)}</td>
      <td>${level}${l.signal_active?' ●':''}</td>
      <td>${ago(l.last_rx_at_ms)}</td>
      <td>${ago(l.last_tx_at_ms)}</td>
      <td class="num">${l.queue_len||0}</td>
      <td class="num">${l.deliveries_ok||0}</td>
      <td class="num">${l.deliveries_fail||0}</td>
    </tr>`;
  }).join('');
  $('linkRows').innerHTML=rows||'<tr><td colspan=9><div class="empty">NO CHANNELS</div></td></tr>';
}

async function renderQueue(){
  const {j}=await jf('/api/messages?limit=80');
  if(!j||!j.ok)return;
  const st=j.stats||{}; const bs=st.by_status||{};
  $('queueStats').textContent=`PEND ${st.pending||0} | OK ${bs.DELIVERED||0} | FAIL ${bs.FAILED||0} | EXP ${bs.EXPIRED||0}`;
  const qb=st.pending||0; $('qBadge').textContent=qb?('('+qb+')'):'';
  const rows=(j.messages||[]).slice(0,80).map(m=>{
    const c=m.clip||{};
    const clipStr= c.duration_s!=null? c.duration_s.toFixed(2)+'S '+fmtBytes(m.payload_bytes):fmtBytes(m.payload_bytes);
    return `<tr>
      <td class="em">${escapeHtml(m.id.slice(0,8))}</td>
      <td>${m.source}→${m.destination}</td>
      <td>${badge(m.status)}</td>
      <td class="mono">${clipStr}</td>
      <td>${ago(m.received_at_ms)}</td>
      <td class="num">${m.retry_count}</td>
      <td class="num">${m.payload_bytes}</td>
    </tr>`;
  }).join('');
  $('queueRows').innerHTML= rows||'<tr><td colspan=7><div class="empty">QUEUE EMPTY</div></td></tr>';
}
async function clearTerminal(){
  const {status,j}=await jf('/api/queue/clear_expired_failed',{method:'POST'});
  if(j&&j.ok){ toast('REMOVED '+j.removed); renderQueue(); }
}

// === DRONE ===
function renderFlight(o){
  const fl=o.flight||{};
  if(fl.disabled){
    $('flightPanel').innerHTML=`<div class="empty">PIXHAWK DISABLED - ENABLE IN CONFIG</div>`;
    return;
  }
  const kv=(k,v)=>`<div class="hrow"><span class="k">${k}</span><span class="v">${v}</span></div>`;
  $('flightPanel').innerHTML=
    `<div class="gauges">
      <div class="gauge"><div class="v">${fl.alt_rel_m!=null?fl.alt_rel_m.toFixed(0):'--'}<span style="font-size:10px"> M</span></div><div class="l">REL ALT</div></div>
      <div class="gauge"><div class="v">${fl.alt_m!=null?fl.alt_m.toFixed(0):'--'} M</div><div class="l">ABS ALT</div></div>
      <div class="gauge"><div class="v" style="font-size:12px">${fl.mode||'--'}</div><div class="l">MODE</div></div>
      <div class="gauge"><div class="v">${fl.battery_pct!=null?fl.battery_pct.toFixed(0):'--'}%</div><div class="l">BATT</div></div>
    </div>`+
    kv('LAT',fl.lat!=null?fl.lat.toFixed(5):'--')+
    kv('LON',fl.lon!=null?fl.lon.toFixed(5):'--')+
    kv('FIX',fl.fix_type||'--')+
    kv('SATS',fl.satellites||'--');
}

function renderSystem(o){
  const sys=o.system||{}, res=o.resource||{};
  const h=(k,v)=>`<div class="hrow"><span class="k">${k}</span><span class="v">${v}</span></div>`;
  $('sysPanel').innerHTML=
    h('UPTIME',fmtUptime(sys.uptime_s))+
    h('CPU',res.cpu_pct!=null?res.cpu_pct.toFixed(1)+'%':'--')+
    h('RAM',res.ram_pct!=null?res.ram_pct.toFixed(1)+'%':'--')+
    h('DISK',res.disk_pct!=null?res.disk_pct.toFixed(1)+'%':'--')+
    h('TEMP',res.temp_c!=null?res.temp_c.toFixed(1)+'C':'--');
  
  const radio=o.radio||{};
  $('devPanel').innerHTML=
    h('RX',(radio.rx&&radio.rx.present)?'PRESENT: '+radio.rx.name:'NO DEVICE')+
    h('TX',(radio.tx&&radio.tx.present)?'PRESENT: '+radio.tx.name:'NO DEVICE')+
    h('PENDING TX',radio.tx_pending||0);

  const alt=o.altitude||{};
  $('altPanel').innerHTML=
    h('STATE',alt.state||'--')+
    h('SUGGESTED',alt.suggested_alt_m!=null?alt.suggested_alt_m.toFixed(0)+' M':'--')+
    h('CURRENT',alt.current_alt_m!=null?alt.current_alt_m.toFixed(0)+' M':'--')+
    h('SCORE',alt.score!=null?alt.score.toFixed(2):'--')+
    `<div style="padding:8px; font-size:10px; color:var(--steel-light); border-top:1px solid var(--ink-light); margin-top:8px">${escapeHtml(alt.reason||'--')}</div>`;
}

// === RF CHART - HARD EDGES ===
function pushHistory(ch){
  const st=ch.link||{}; if(st.last_rssi_dbm==null) return;
  const h=history[ch.id]=history[ch.id]||[];
  const last=h.length?h[h.length-1]:null;
  if(!last || Math.abs(last.v - st.last_rssi_dbm)>0.5 || (Date.now()-last.t)>3000){
    h.push({t:Date.now(), v:st.last_rssi_dbm});
    if(h.length>HBUF)h.shift();
  }
}
function resizeChart(){
  const c=$('rfChart'); if(!c)return;
  const w=c.parentElement.clientWidth; if(!w)return;
  c.width=w*window.devicePixelRatio; c.height=180*window.devicePixelRatio; c.style.height='180px';
}
function drawChart(){
  const c=$('rfChart'); if(!c)return; if(!c.width) resizeChart();
  const ctx=c.getContext('2d'); const W=c.width, H=c.height, dpr=window.devicePixelRatio;
  ctx.fillStyle='#0a0a0a'; ctx.fillRect(0,0,W,H);
  const padL=40*dpr, padR=10*dpr, padT=8*dpr, padB=20*dpr;
  const plotW=W-padL-padR, plotH=H-padT-padB;
  let vmin=Infinity,vmax=-Infinity, has=false;
  Object.values(history).forEach(arr=>arr.forEach(p=>{if(p.v!=null){has=true;vmin=Math.min(vmin,p.v);vmax=Math.max(vmax,p.v);}}));
  if(!has){ vmin=-115; vmax=-60; } else { const c=(vmax-vmin)*0.15||6; vmin-=c; vmax+=c; }
  // Grid - hard lines
  ctx.strokeStyle='#222'; ctx.lineWidth=1*dpr;
  ctx.font=(10*dpr)+'px monospace'; ctx.fillStyle='#666';
  for(let i=0;i<=4;i++){
    const y=padT+plotH*(i/4); const val=vmax-(vmax-vmin)*(i/4);
    ctx.beginPath(); ctx.moveTo(padL,y); ctx.lineTo(W-padR,y); ctx.stroke();
    ctx.fillText(val.toFixed(0)+' DB',4,y+3*dpr);
  }
  const tnow=Date.now();
  CHANNELS.forEach(id=>{
    const arr=history[id]||[]; if(arr.length<2)return;
    ctx.strokeStyle=colorOf(id); ctx.lineWidth=2*dpr; ctx.beginPath();
    let first=true;
    arr.forEach(p=>{
      const x=padL+plotW*(1-(tnow-p.t)/60000);
      const y=padT+plotH*(1-(p.v-vmin)/(vmax-vmin));
      if(first){ctx.moveTo(x,y);first=false;}else ctx.lineTo(x,y);
    });
    ctx.stroke();
  });
}
function paintLegend(){
  $('rfLegend').innerHTML=CHANNELS.map(id=>`<span style="display:inline-flex;align-items:center;gap:4px;margin-right:12px"><span style="width:10px;height:10px;background:${colorOf(id)};border:1px solid #000;display:inline-block"></span>${id}</span>`).join('')+
    '<span style="margin-left:auto;font-size:9px">LAST 60S - RELATIVE S-METER</span>';
}

// === LIVE SPECTRUM - PHOSPHOR CRT ===
let spectrumAnalyzer=null;
class BrutalistSpectrum {
  constructor(canvasId){
    this.canvas=document.getElementById(canvasId);
    if(!this.canvas) return;
    this.ctx=this.canvas.getContext('2d');
    this.fftData=new Array(512).fill(-110);
    this.peakData=new Array(512).fill(-110);
    this.channels={};
    this.center=0;
    this.span=2000000;
    this.resize();
    window.addEventListener('resize',()=>this.resize());
    this.animate();
  }
  resize(){
    const rect=this.canvas.getBoundingClientRect();
    const dpr=window.devicePixelRatio||1;
    this.canvas.width=rect.width*dpr;
    this.canvas.height=rect.height*dpr;
    this.ctx.scale(dpr,dpr);
    this.width=rect.width;
    this.height=rect.height;
  }
  updateFFT(data, center, span, powers){
    if(data) this.fftData=data;
    if(center) this.center=center;
    if(span) this.span=span;
    if(powers){
      for(const [ch,p] of Object.entries(powers)){
        if(this.channels[ch]) this.channels[ch].power=p;
      }
    }
    // Peak hold
    for(let i=0;i<this.fftData.length;i++){
      if(this.fftData[i]>this.peakData[i]) this.peakData[i]=this.fftData[i];
      else this.peakData[i]=Math.max(this.peakData[i]-0.2, this.fftData[i]);
    }
  }
  updateChannels(channels){ this.channels=channels; }
  draw(){
    const ctx=this.ctx, W=this.width, H=this.height;
    // Black background
    ctx.fillStyle='#000'; ctx.fillRect(0,0,W,H);
    // Grid - hard
    ctx.strokeStyle='#111'; ctx.lineWidth=1;
    for(let db=-100; db<=0; db+=20){
      const y=H - ((db+110)/110)*H;
      ctx.beginPath(); ctx.moveTo(0,y); ctx.lineTo(W,y); ctx.stroke();
      ctx.fillStyle='#333'; ctx.font='9px monospace'; ctx.fillText(db+' DB',4,y-2);
    }
    // FFT - phosphor green, hard
    ctx.beginPath();
    for(let i=0;i<this.fftData.length;i++){
      const x=(i/this.fftData.length)*W;
      const y=H - ((this.fftData[i]+110)/110)*H;
      if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
    }
    ctx.strokeStyle='#00ff00'; ctx.lineWidth=1.5; ctx.stroke();
    // Glow
    ctx.shadowColor='#00ff00'; ctx.shadowBlur=6; ctx.stroke(); ctx.shadowBlur=0;
    // Fill
    ctx.lineTo(W,H); ctx.lineTo(0,H); ctx.closePath();
    ctx.fillStyle='rgba(0,255,0,0.08)'; ctx.fill();
    // Peak
    ctx.beginPath();
    for(let i=0;i<this.peakData.length;i++){
      const x=(i/this.peakData.length)*W;
      const y=H - ((this.peakData[i]+110)/110)*H;
      if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
    }
    ctx.strokeStyle='#ffb000'; ctx.lineWidth=1; ctx.setLineDash([2,2]); ctx.stroke(); ctx.setLineDash([]);
    // Channel markers
    for(const [id,ch] of Object.entries(this.channels)){
      if(!ch.enabled) continue;
      const freq=ch.frequency_hz;
      const low=this.center - this.span/2;
      const high=this.center + this.span/2;
      const x=((freq-low)/this.span)*W;
      if(x<0||x>W) continue;
      ctx.beginPath(); ctx.moveTo(x,0); ctx.lineTo(x,H);
      ctx.strokeStyle=colorOf(id); ctx.lineWidth=1; ctx.setLineDash([4,4]); ctx.stroke(); ctx.setLineDash([]);
      // Label - stamped
      ctx.fillStyle=colorOf(id); ctx.fillRect(x-24,0,48,14);
      ctx.fillStyle='#000'; ctx.font='bold 10px monospace'; ctx.fillText(id, x-14,10);
    }
    // Info
    ctx.fillStyle='#00ff00'; ctx.font='10px monospace';
    ctx.fillText(`CF ${(this.center/1e6).toFixed(3)} MHZ SPAN ${(this.span/1e6).toFixed(2)} MHZ`,8,H-8);
  }
  animate(){ this.draw(); requestAnimationFrame(()=>this.animate()); }
}

// === EVENTS ===
function addEvent(ev){
  evBuffer.unshift({...ev,t:Date.now()});
  if(evBuffer.length>80)evBuffer.length=80;
  renderEvents();
}
function renderEvents(){
  const log=$('evLog');
  if(!evBuffer.length){ log.innerHTML='<div class="empty">NO ACTIVITY</div>'; return; }
  log.innerHTML=evBuffer.map(ev=>{
    const t=new Date(ev.ts_ms||ev.t).toLocaleTimeString();
    const route=(ev.source?ev.source:'')+(ev.destination?'→'+ev.destination:'');
    return `<div class="ev"><span class="t">${t}</span><span class="k">${(ev.kind||'EVT').toUpperCase()}</span><span>${route} ${escapeHtml(ev.detail||'')}</span></div>`;
  }).join('');
}

// === CONFIG ===
let LIMITS={min_mhz:24,max_mhz:1700};
async function loadConfig(){
  const {j}=await jf('/api/config/channels');
  if(!j||!j.ok)return;
  LIMITS=j.limits||LIMITS;
  $('bandLimits').textContent=`BAND ${LIMITS.min_mhz}–${LIMITS.max_mhz} MHZ | DUPES REJECTED ON ENABLED CHANNELS`;
  const rows=(j.channels||[]).map(c=>`
    <div class="cfgrow" data-id="${c.id}">
      <div class="cid">${c.id}<small>${escapeHtml(c.label||'')}</small></div>
      <div class="input-wrap"><input type="text" id="fq_${c.id}" value="${c.frequency_mhz.toFixed(3)}"><span class="unit">MHZ</span></div>
      <div class="center"><div style="font-size:8px; letter-spacing:1px">ENABLE</div><label class="switch"><input type="checkbox" id="en_${c.id}" ${c.enabled?'checked':''}><span class="slider"></span></label></div>
      <div class="center"><div style="font-size:8px; letter-spacing:1px">TX</div><label class="switch"><input type="checkbox" id="tx_${c.id}" ${c.tx_allowed?'checked':''}><span class="slider"></span></label></div>
      <div class="mono" style="font-size:10px" id="st_${c.id}">--</div>
    </div>`).join('');
  $('cfgRows').innerHTML=rows;
  // Mark dirty on change
  CHANNELS.forEach(id=>{
    const fq=$('fq_'+id), en=$('en_'+id), tx=$('tx_'+id);
    if(fq) fq.addEventListener('input',()=>markDirty());
    if(en) en.addEventListener('change',()=>markDirty());
    if(tx) tx.addEventListener('change',()=>markDirty());
  });
}
function gatherConfig(){
  return CHANNELS.map(id=>{
    const row=document.querySelector(`.cfgrow[data-id="${id}"]`);
    if(!row)return null;
    return {
      id, frequency_mhz:parseFloat($('fq_'+id).value),
      enabled:$('en_'+id).checked, tx_allowed:$('tx_'+id).checked,
      rx_allowed:true, label: id
    };
  }).filter(Boolean);
}
async function applyChannels(){
  const btn=$('applyBtn'); btn.disabled=true;
  const chans=gatherConfig();
  for(const c of chans){
    if(isNaN(c.frequency_mhz)){ toast('INVALID FREQ '+c.id,'err'); btn.disabled=false; return; }
    if(c.frequency_mhz<LIMITS.min_mhz||c.frequency_mhz>LIMITS.max_mhz){ toast(c.id+' OUT OF BAND','err'); btn.disabled=false; return; }
  }
  const {status,j}=await jf('/api/config/channels',{method:'PUT',body:JSON.stringify({channels:chans})});
  btn.disabled=false;
  if(j&&j.ok){
    $('cfgStatus').textContent='APPLIED REV #'+j.revision;
    $('cfgStatus').className='pill ok';
    toast('CONFIG APPLIED REV '+j.revision);
    CHANNELS.forEach(id=>{ const s=$('st_'+id); if(s){ s.textContent='● APPLIED'; s.style.color='var(--ok)'; }});
  } else {
    $('cfgStatus').textContent='REJECTED';
    $('cfgStatus').className='pill bad';
    toast((j&&j.error)||'APPLY FAILED','err');
  }
  refresh();
}
function markDirty(){
  $('cfgStatus').textContent='MODIFIED - APPLY REQUIRED';
  $('cfgStatus').className='pill warn';
}

// === MAIN POLL ===
let qDeb=null;
function debounceQueue(){ clearTimeout(qDeb); qDeb=setTimeout(renderQueue,200); }

async function refresh(){
  let o;
  try{ const {j}=await jf('/api/overview'); if(!j||!j.ok) return; o=j; }catch(e){ return; }
  OVER=o;
  $('connDot').className='dot '+(o.system?'ok':'bad');
  $('connTxt').textContent=o.system?'LINK ESTABLISHED':'LINK LOST';
  renderKpi(o);
  renderChannels(o);
  renderLinkTable(o);
  renderFlight(o);
  renderSystem(o);
  (o.channels||[]).forEach(ch=>pushHistory(ch));
  drawChart();
  // Update spectrum channels
  if(window.brutalistSpectrum){
    const chans={};
    (o.channels||[]).forEach(c=>{
      chans[c.id]={frequency_hz:c.frequency_hz, enabled:c.enabled, power:c.link?.last_rssi_dbm||-110};
    });
    window.brutalistSpectrum.updateChannels(chans);
    // Use RSSI to fake FFT for now - real FFT from /api/spectrum if available
    // Generate FFT based on channel powers
    const fft=new Array(512).fill(-110);
    (o.channels||[]).forEach(c=>{
      if(!c.enabled) return;
      const rssi=c.link?.last_rssi_dbm||-110;
      // Place peak at channel freq position
      // For now, assume center is average of channels
      const freqs=o.channels.map(ch=>ch.frequency_hz);
      const center=freqs.reduce((a,b)=>a+b,0)/freqs.length;
      const span=4e6;
      const low=center-span/2;
      const x=Math.floor(((c.frequency_hz-low)/span)*512);
      if(x>=0&&x<512){
        for(let i=-3;i<=3;i++){
          const idx=x+i;
          if(idx>=0&&idx<512){
            fft[idx]=Math.max(fft[idx], rssi - Math.abs(i)*3);
          }
        }
      }
    });
    // Add noise
    for(let i=0;i<fft.length;i++){
      fft[i]+= (Math.random()-0.5)*2;
      if(fft[i]<-110) fft[i]=-110;
    }
    window.brutalistSpectrum.updateFFT(fft, o.channels[0]?.frequency_hz||145500000, 4000000);
  }
  if(!qLoaded){qLoaded=true; renderQueue();}
}

// Try to fetch real spectrum if endpoint exists
async function fetchSpectrum(){
  try{
    const {j}=await jf('/api/spectrum');
    if(j&&j.fft){
      if(window.brutalistSpectrum){
        window.brutalistSpectrum.updateFFT(j.fft, j.center_hz, j.span_hz, j.powers);
      }
    }
  }catch(e){}
}

setInterval(()=>{ refresh(); },1000);
setInterval(()=>{ renderQueue(); fetchSpectrum(); },1500);
setInterval(()=>{ drawChart(); },300);

// SSE
function connectSSE(){
  if(!window.EventSource) return;
  const es=new EventSource('/api/stream');
  ['relay.events','relay.message','relay.delivered','relay.retry','relay.failed'].forEach(n=>{
    es.addEventListener(n,e=>{
      try{
        const d=JSON.parse(e.data);
        if(n==='relay.events') addEvent(d);
        else addEvent({kind:n.split('.')[1],...d});
        debounceQueue();
      }catch(err){}
    });
  });
}

// === INIT ===
function initBrutalist(){
  paintLegend();
  resizeChart();
  // Spectrum
  setTimeout(()=>{
    const canvas=$('spectrumCanvas');
    if(canvas){
      window.brutalistSpectrum=new BrutalistSpectrum('spectrumCanvas');
      console.log('Brutalist spectrum initialized');
    }
  },200);
  loadConfig();
  connectSSE();
  refresh();
  // Bind buttons
  $('applyBtn')?.addEventListener('click',applyChannels);
  $('clearBtn')?.addEventListener('click',clearTerminal);
  $('specClear')?.addEventListener('click',()=>{
    if(window.brutalistSpectrum){
      window.brutalistSpectrum.peakData=new Array(512).fill(-110);
    }
  });
}

document.addEventListener('DOMContentLoaded',initBrutalist);
