import axios from 'axios';

const api = axios.create({ baseURL: '/api' });

export async function fetchInstances({ tab, page, pageSize, sort, dir, q, filters }) {
  const params = { tab, page, page_size: pageSize, sort, dir };
  if (q) params.q = q;
  if (filters && filters.length > 0) params.filters = JSON.stringify(filters);
  const { data } = await api.get('/instances', { params });
  return data;
}

export async function fetchTopTables(instanceName, sort = 'rows', dir = 'desc') {
  const { data } = await api.get(`/instances/${instanceName}/tables`, { params: { sort, dir } });
  return data.tables;
}

export async function fetchSummary() {
  const { data } = await api.get('/summary');
  return data;
}

export async function fetchTierDistribution() {
  const { data } = await api.get('/summary/tier-distribution');
  return data.data;
}

export async function fetchDbtypeDistribution() {
  const { data } = await api.get('/summary/dbtype-distribution');
  return data.data;
}

export async function fetchTopByDbsize(limit = 20) {
  const { data } = await api.get('/summary/top-by-dbsize', { params: { limit } });
  return data;
}

export async function fetchTopByTxn(limit = 20) {
  const { data } = await api.get('/summary/top-by-txn', { params: { limit } });
  return data;
}

export async function fetchInstanceHistory(instanceName) {
  const { data } = await api.get(`/instances/${instanceName}/history`);
  return data;
}

export async function fetchScanStatus() {
  const { data } = await api.get('/scan/status');
  return data;
}

export async function startScan() {
  const { data } = await api.post('/scan/start');
  return data;
}

export async function fetchRapSessions(page = 1, pageSize = 50) {
  const { data } = await api.get('/rap/sessions', { params: { page, page_size: pageSize } });
  return data;
}

export async function saveRapSession(session) {
  const { data } = await api.post('/rap/sessions', session);
  return data;
}
