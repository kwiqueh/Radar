/* Local-only setup. Credentials are never populated from the server response. */
(() => {
  let offset = 0, setup = {};
  const numericFees = {
    purchase_pct:'Покупка, %', funding_pct:'Пополнение, %', seller_pct:'Продажа, %',
    withdrawal_pct:'Вывод, %', funding_fixed_rub:'Пополнение, фикс. ₽',
    withdrawal_fixed_rub:'Вывод, фикс. ₽', withdrawal_minimum_rub:'Минимум вывода, ₽'
  };
  async function loadSetup() {
    setup = await json('/api/setup');
    q('#setup-mode').value = setup.mode; q('#setup-terms').checked = setup.api_terms_accepted;
    q('#setup-usd').value = setup.usd_rub; q('#setup-eur').value = setup.eur_rub;
    q('#key-status').textContent = setup.csfloat_key_set ? 'Ключ CSFloat сохранён локально; значение не отображается.' : 'Ключ CSFloat не задан. Не присылайте его в чат.';
    document.querySelectorAll('[name="source"]').forEach(e => e.checked = setup.enabled_providers.includes(e.value));
    q('#fee-profiles').innerHTML = ['csfloat','skinport','market_csgo'].map(name => {
      const fees = setup.venue_fees[name] || {};
      return `<fieldset class="fee-profile" data-venue="${name}"><legend>${esc(name)}</legend><div class="filter-grid">` +
        Object.entries(numericFees).map(([key,label]) => `<label>${label}<input data-fee="${key}" type="number" min="0" step="any" value="${esc(fees[key] || '0')}"></label>`).join('') +
        `</div><p><label><input data-fee="cashout_available" type="checkbox" ${fees.cashout_available?'checked':''}> Вывод доступен моему аккаунту</label></p>` +
        `<p><label><input data-fee="verified" type="checkbox" ${fees.verified?'checked':''}> Все тарифы и минимум вывода проверены мной</label></p></fieldset>`;
    }).join('');
  }
  async function catalog() {
    const params = new URLSearchParams({search:q('#quote-search').value,provider:q('#quote-provider').value,offset,limit:50});
    if(q('#quote-max').value) params.set('max_price_rub', q('#quote-max').value);
    const data = await json('/api/quotes?' + params);
    q('#quote-total').textContent = `${data.total.toLocaleString('ru-RU')} котировок · ${offset + (data.items.length?1:0)}–${offset+data.items.length}`;
    const kinds = {catalog_reference:'Справочный каталог',active_listing:'Ask · объявление',buy_order:'Bid · заявка покупателя',last_sale:'Последняя продажа',median_sale:'Медиана продаж'};
    q('#quote-rows').innerHTML = data.items.length ? data.items.map(item => {
      const stale = Date.now() - new Date(item.observed_at).getTime() > 900000;
      return `<tr><td>${esc(item.item.market_hash_name)}${item.demo?' · DEMO':''}</td>`+
        `<td>${esc(item.provider)}<br><a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">${item.kind==='catalog_reference'?'Открыть площадку':item.aggregate?'Рынок предмета':'Объявление'}</a></td><td>${esc(item.price)} ${esc(item.currency)}</td><td>${kinds[item.kind]||esc(item.kind)}</td>`+
        `<td class="${stale?'risk-mid':''}">${new Date(item.observed_at).toLocaleString('ru-RU')}${stale?' · устарела':''}</td></tr>`;
    }).join('') : '<tr><td colspan="5" class="empty">Котировки не загружены или не подходят под поиск. Проверьте подключение и статус API.</td></tr>';
    q('#quote-prev').disabled = offset === 0; q('#quote-next').disabled = offset+data.items.length >= data.total;
  }
  document.addEventListener('DOMContentLoaded', async () => {
    try {await loadSetup(); await catalog();} catch(e) {q('#setup-message').textContent = e.message;}
    q('#setup-form').onsubmit = async event => {
      event.preventDefault(); const button = q('#apply-setup'); button.disabled = true;
      q('#setup-message').textContent = 'Настройки сохраняются. Проверка источников может занять до двух минут…';
      const payload = {mode:q('#setup-mode').value,api_terms_accepted:q('#setup-terms').checked,
        enabled_providers:[...document.querySelectorAll('[name="source"]:checked')].map(e=>e.value),
        usd_rub:q('#setup-usd').value,eur_rub:q('#setup-eur').value,venue_fees:{}};
      if(q('#setup-key').value.trim()) payload.csfloat_api_key = q('#setup-key').value.trim();
      if(q('#setup-fx').checked) payload.fx_verified_at = new Date().toISOString();
      document.querySelectorAll('.fee-profile').forEach(fieldset => {
        const fees = {}; fieldset.querySelectorAll('[data-fee]').forEach(e => fees[e.dataset.fee] = e.type==='checkbox'?e.checked:e.value);
        payload.venue_fees[fieldset.dataset.venue] = fees;
      });
      try {
        await json('/api/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        q('#setup-key').value = '';
        await json('/api/scan',{method:'POST'});
        location.reload();
      } catch(e) {q('#setup-message').textContent = e.message;}
      finally {button.disabled=false;}
    };
    q('#catalog-form').onsubmit = e => {e.preventDefault();offset=0;catalog().catch(console.error);};
    q('#quote-prev').onclick = () => {offset=Math.max(0,offset-50);catalog().catch(console.error);};
    q('#quote-next').onclick = () => {offset+=50;catalog().catch(console.error);};
    setInterval(()=>catalog().catch(console.error),30000);
    q('#scan-button').addEventListener('click',()=>setTimeout(()=>catalog().catch(console.error),1000));
  });
})();
