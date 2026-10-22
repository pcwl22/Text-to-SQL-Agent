import axios from 'axios'

// Reserved transport factory, not imported by application pages. Fail closed even if used accidentally.
export function createDisabledClient() {
  const client = axios.create({ baseURL: '/api', timeout: 10000 })
  client.interceptors.request.use(() => { throw new Error('Live Inference 尚未开放；当前版本仅支持离线演示。') })
  return client
}
