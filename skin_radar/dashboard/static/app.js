let offers = [], sort = {key: 'profit', direction: -1};
const q = s => document.querySelector(s);
const rub = n => `${Number(n).toLocaleString('ru-RU', {maximumFractionDigits: 2})} ₽`;
const pct = n => `${Number(n).toFixed(1)}%`;
const labels = {
  budget_rub: 'Бюджет, ₽', reserve_rub: 'Денежный резерв, ₽',
  max_item_price_rub: 'Максимум цены предмета, ₽', min_profit_rub: 'Минимум прибыли, ₽',
  min_roi_pct: 'Минимум ROI, %', min_liquidity_score: 'Минимум ликвидности, 0–100',
  max_quote_age_seconds: 'Возраст котировки, секунд', max_hold_hours: 'Предел удержания, часов',
  allow_unknown_hold: 'Показывать неизвестный hold',
  allow_unknown_liquidity: 'Показывать неизвестную ликвидность',
  excluded_items: 'Исключённые имена через запятую', enabled_providers: 'Площадки: csfloat,skinport'
};
const confidence = {limited:'Ограниченная',insufficient_data:'Недостаточно данных',confirmed:'По доступным данным'};
const liquidity = {high:'Высокая',medium:'Средняя',low:'Низкая',unknown:'Неизвестна'};
function esc(v) {
  const e = document.createElement('div'); e.textContent = String(v);
  return e.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
async function json(url, options) {
  const r = await fetch(url, options); if (!r.ok) throw new Error(await r.text()); return r.json();
}
function renderOffers() {
  const value = x => ({item_name:x.item_name,buy:+x.costs.purchase_price_rub,sell:+x.costs.sale_price_rub,
    profit:+x.costs.net_profit_rub,roi:+x.costs.roi_pct,liquidity:x.liquidity_score,risk:x.risk_score})[sort.key];
  const sorted = [...offers].sort((a,b) => (typeof value(a) === 'number' ? value(a)-value(b) : value(a).localeCompare(value(b))) * sort.direction);
  q('#offers').innerHTML = sorted.length ? sorted.map(o => `<tr data-index="${offers.indexOf(o)}" tabindex="0">
    <td><b>${esc(o.item_name)}</b><br><small class="muted">${o.buy.demo?'DEMO · ':''}${o.sell.aggregate?'Базовая разновидность · агрегат ask':'Активное объявление ask'}</small></td>
    <td>${esc(o.buy.provider)}<br>${rub(o.costs.purchase_price_rub)}</td>
    <td>${esc(o.sell.provider)}<br>${rub(o.costs.sale_price_rub)}</td>
    <td class="money">+${rub(o.costs.net_profit_rub)}</td><td>${pct(o.costs.roi_pct)}</td>
    <td>${liquidity[o.liquidity]} · ${o.liquidity_score}</td>
    <td class="${o.risk_score>70?'risk-high':'risk-mid'}">${o.risk_score}/100</td>
    <td>${confidence[o.confidence]}<br><small class="muted">${o.executable?'Bid':'Потенциальная маржа'}</small></td>
    </tr>`).join('') : '<tr><td colspan="8" class="empty">Нет кандидатов под текущие фильтры. Проверьте состояние API и настройки.</td></tr>';
  q('#offers').querySelectorAll('tr[data-index]').forEach(row => {
    row.onclick = () => showOffer(offers[Number(row.dataset.index)]);
    row.onkeydown = e => {if(e.key==='Enter') row.click();};
  });
}
async function showOffer(o) {
  const panel=q('#offer-detail'),c=o.costs; panel.hidden=false;
  const entries=[['Покупка',c.purchase_price_rub],['Комиссия покупки',c.purchase_fee_rub],['Пополнение',c.funding_fee_rub],
    ['Резерв расходов',c.unexpected_reserve_rub],['Фиксированные расходы',c.fixed_cost_rub],['Полные вложения',c.total_invested_rub],
    ['Комиссия продавца',c.seller_fee_rub],['Вывод',c.withdrawal_fee_rub],['Чистое поступление',c.net_proceeds_rub],['Потенциальная прибыль',c.net_profit_rub]];
  panel.innerHTML=`<h2>${esc(o.item_name)}</h2><p>
    <a href="${esc(o.buy.url)}" target="_blank" rel="noopener noreferrer">${o.buy.aggregate?'Рынок покупки (агрегат)':'Объявление покупки'}</a> ·
    <a href="${esc(o.sell.url)}" target="_blank" rel="noopener noreferrer">${o.sell.aggregate?'Рынок продажи (агрегат)':'Источник цены продажи'}</a></p>
    <div class="detail-grid">${entries.map(([k,v])=>`<div><span class="muted">${k}</span><br><strong>${rub(v)}</strong></div>`).join('')}</div>
    <p class="muted">Получена покупка: ${new Date(o.buy.observed_at).toLocaleString('ru-RU')} · Продажа: ${new Date(o.sell.observed_at).toLocaleString('ru-RU')} · Hold: ${o.hold_hours===null?'неизвестен':o.hold_hours+' ч'}</p>
    <p class="risk-mid">${esc(o.warnings.join('; '))}</p>`;
  q('#history-title').textContent=o.item_name;
  draw(await json(`/api/history?item_name=${encodeURIComponent(o.item_name)}`));
}
async function refresh() {
  const [items,states,stats,alerts] = await Promise.all([json('/api/opportunities'),json('/api/status'),json('/api/stats'),json('/api/alerts')]);
  offers=items; renderOffers();
  q('#candidate-count').textContent=offers.length;
  q('#best-profit').textContent=offers.length?rub(Math.max(...offers.map(x=>+x.costs.net_profit_rub))):'—';
  q('#best-roi').textContent=offers.length?pct(Math.max(...offers.map(x=>+x.costs.roi_pct))):'—';
  q('#last-scan').textContent=stats.latest_scan?new Date(stats.latest_scan.finished_at).toLocaleTimeString('ru-RU'):'—';
  q('#configuration').innerHTML='<ul>'+(stats.warnings||[]).map(message=>`<li>${esc(message)}</li>`).join('')+'</ul>';
  q('#providers').innerHTML=states.map(s=>`<div class="provider"><b>${esc(s.name)}<span class="dot ${s.healthy?'ok':''}">●</span></b><small class="muted">${esc(s.detail)} · ${s.quote_count} котировок</small></div>`).join('')||'После первого сканирования';
  q('#alerts').innerHTML=alerts.length?alerts.map(a=>`<div class="provider">${esc(a.item_name)} · ${new Date(a.sent_at).toLocaleString('ru-RU')} · ${esc(a.status)}</div>`).join(''):'Уведомлений пока нет. В demo автоматическая отправка выключена.';
}
async function loadFilters() {
  const data=await json('/api/filters');
  q('#filters').innerHTML=Object.entries(data).map(([k,v])=>`<label>${labels[k]||k}${k.startsWith('allow_')?
    `<select name="${k}"><option value="true" ${v==='true'?'selected':''}>Да</option><option value="false" ${v==='false'?'selected':''}>Нет</option></select>`:
    `<input name="${k}" value="${esc(v)}" ${['excluded_items','enabled_providers'].includes(k)?'':'type="number" min="0" step="any"'}>`}</label>`).join('');
}
function draw(rows) {
  const c=q('#price-chart'),ctx=c.getContext('2d'),dpr=devicePixelRatio||1,w=c.clientWidth,h=250,pad=35;
  c.width=w*dpr;c.height=h*dpr;ctx.scale(dpr,dpr);ctx.fillStyle='#89979a';
  if(!rows.length){ctx.fillText('Истории пока нет',20,30);return;}
  const vals=rows.map(r=>+r.price_rub),times=rows.map(r=>new Date(r.observed_at).getTime());
  const min=Math.min(...vals)*.98,max=Math.max(...vals)*1.02,first=Math.min(...times),last=Math.max(...times);
  ctx.strokeStyle='#263036';
  for(let i=0;i<4;i++){const y=pad+(h-pad*2)*i/3;ctx.beginPath();ctx.moveTo(pad,y);ctx.lineTo(w-pad,y);ctx.stroke();}
  [...new Set(rows.map(r=>r.provider))].forEach((provider,index)=>{
    ctx.strokeStyle=({csfloat:'#b8ff3d',skinport:'#4de5d2'})[provider]||'#ffd166';ctx.fillStyle=ctx.strokeStyle;
    ctx.fillText(provider,pad+index*110,18);
    const points=rows.filter(r=>r.provider===provider).map(r=>({x:last===first?w/2:pad+(new Date(r.observed_at)-first)/(last-first)*(w-pad*2),y:h-pad-(+r.price_rub-min)/(max-min||1)*(h-pad*2)}));
    ctx.lineWidth=2;ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));ctx.stroke();
    points.forEach(p=>{ctx.beginPath();ctx.arc(p.x,p.y,3,0,Math.PI*2);ctx.fill();});
  });
  ctx.fillStyle='#89979a';ctx.fillText(`${rub(min)} — ${rub(max)}`,pad,h-10);
}
document.addEventListener('DOMContentLoaded',async()=>{
  try{await Promise.all([refresh(),loadFilters()]);}catch(e){q('#configuration').textContent=e.message;}
  q('#scan-button').onclick=async e=>{e.target.disabled=true;e.target.textContent='Сканирование…';try{await json('/api/scan',{method:'POST'});await refresh();}catch(err){q('#configuration').textContent=err.message;}finally{e.target.disabled=false;e.target.textContent='Сканировать';}};
  q('#save-filters').onclick=async()=>{const payload={};q('#filters').querySelectorAll('input,select').forEach(i=>payload[i.name]=i.value);try{await json('/api/filters',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});await json('/api/scan',{method:'POST'});await refresh();}catch(e){q('#configuration').textContent=e.message;}};
  document.querySelectorAll('th[data-sort]').forEach(th=>th.onclick=()=>{sort={key:th.dataset.sort,direction:sort.key===th.dataset.sort?-sort.direction:-1};renderOffers();});
  setInterval(()=>refresh().catch(console.error),30000);
});
