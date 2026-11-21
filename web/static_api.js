/* Static, read-only API bridge for the GitHub Pages edition. */
(function () {
  const root = new URL('data/', document.baseURI);
  let runtimePromise;
  let indicatorPromise;
  const groupPromises = new Map();
  const loadRuntime = () => runtimePromise ||= fetch(new URL('current.json', root), {cache: 'no-cache'}).then(r => {
    if (!r.ok) throw new Error(`Static data unavailable (${r.status})`);
    return r.json();
  }).catch(error => { runtimePromise = undefined; throw error; });
  const loadGroups = (code) => {
    if (!groupPromises.has(code)) groupPromises.set(code, fetch(new URL(`people_groups/${code}.json`, root), {cache: 'no-cache'}).then(r => {
      if (r.status === 404) return [];
      if (!r.ok) throw new Error(`People-group data unavailable (${r.status})`);
      return r.json();
    }).catch(error => { groupPromises.delete(code); throw error; }));
    return groupPromises.get(code);
  };
  const json = (value) => Promise.resolve(value);

  async function request(path) {
    const u = new URL(path, 'https://global-watch.invalid');
    const p = u.pathname;
    const q = u.searchParams;
    const d = await loadRuntime();
    if (p === '/api/health') return d.health;
    if (p === '/api/sources') return d.sources;
    if (p === '/api/source-status') return d.source_status;
    if (p === '/api/data-coverage') return d.data_coverage;
    if (p === '/api/country-coverage') return d.country_coverage;
    if (p === '/api/countries') return d.countries;
    if (p === '/api/country-profiles') return d.country_profiles;
    if (p === '/api/mission-context') return d.mission_context;
    if (p === '/api/global-research') {
      const rows = d.global_research.records || [];
      const lane = q.get('lane');
      const records = lane ? rows.filter(x => x.lane === lane) : rows;
      return {count: records.length, records};
    }
    if (p === '/api/missions-research') {
      let rows = d.missions_research.records || [];
      const lane = q.get('lane'), code = q.get('country_code'), review = q.get('review_status');
      if (lane) rows = rows.filter(x => x.lane === lane);
      if (code) rows = rows.filter(x => x.country_code === code.toUpperCase());
      if (review) rows = rows.filter(x => x.review_status === review);
      return {...d.missions_research, records: rows.slice(0, Math.min(Number(q.get('limit') || 300), 1000)), count: rows.length};
    }
    if (p === '/api/events') {
      let rows = d.events.events || [];
      const country = q.get('country'), category = q.get('category');
      if (country) rows = rows.filter(x => (x.country || '').toLowerCase() === country.toLowerCase());
      if (category) rows = rows.filter(x => x.category === category);
      rows = rows.slice(0, Math.min(Number(q.get('limit') || 100), 500));
      return {count: rows.length, events: rows};
    }
    if (p === '/api/briefings') return d.briefings;
    if (p === '/api/ingestion-runs') {
      const limit = Math.min(Number(q.get('limit') || 50), 200);
      return {runs: (d.ingestion_runs.runs || []).slice(0, limit)};
    }
    if (p === '/api/indicators') {
      const code = q.get('country_code');
      indicatorPromise ||= fetch(new URL('indicators.json', root), {cache: 'no-cache'}).then(r => {
        if (!r.ok) throw new Error(`Indicator snapshot unavailable (${r.status})`);
        return r.json();
      }).catch(error => { indicatorPromise = undefined; throw error; });
      let rows = await indicatorPromise;
      if (code) rows = rows.filter(x => x.country_code === code.toUpperCase());
      return {count: rows.length, indicators: rows.slice(0, Math.min(Number(q.get('limit') || 100), 500))};
    }
    const dossier = p.match(/^\/api\/countries\/([A-Z0-9-]+)\/intelligence$/i);
    if (dossier) {
      const code = dossier[1].toUpperCase();
      const base = window.GW_OFFLINE_DOSSIERS?.[code];
      if (!base) throw new Error('Country dossier is not in this release.');
      const countryName = (d.countries.countries || []).find(x => x.code === code)?.name?.toLowerCase();
      const events = (d.events.events || []).filter(x => x.country_code === code || (countryName && (x.country || '').toLowerCase() === countryName)).slice(0, 50);
      return {...base, events: events.length ? events : (base.events || []), hosted_snapshot_at: d.generated_at};
    }
    const groupsMatch = p.match(/^\/api\/countries\/([A-Z0-9-]+)\/people-groups$/i);
    if (groupsMatch) {
      const code = groupsMatch[1].toUpperCase(), all = await loadGroups(code), qtext = (q.get('search') || '').toLowerCase();
      const rows = all.filter(g => !qtext || [g.name, g.language_name, g.religion, g.engagement_status].join(' ').toLowerCase().includes(qtext));
      const page = Math.max(1, Number(q.get('page') || 1)), per = Math.min(100, Math.max(1, Number(q.get('per_page') || 20)));
      return {country_code: code, page, per_page: per, total: rows.length, records: rows.slice((page - 1) * per, page * per)};
    }
    throw new Error(`Static API route not found: ${p}`);
  }

  window.GW_STATIC_API = {request};
})();
