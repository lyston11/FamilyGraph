import axios from 'axios'

/**
 * 统一 API 客户端。
 *
 * 生产环境经 nginx 反代 /api → api:8000；开发环境经 vite proxy 同路径转发，
 * 因此 baseURL 固定为相对路径 /api，两条链路行为一致（m0a design）。
 */
export const apiClient = axios.create({
  baseURL: '/api',
  timeout: 15_000,
})

// 请求拦截器占位：m0b 在此附加 Authorization: Bearer <access token>
apiClient.interceptors.request.use((config) => config)

// 响应拦截器占位：m0b 在此实现统一错误结构解包、409 challenge 流程与 401 静默刷新
apiClient.interceptors.response.use((response) => response, (error) => Promise.reject(error))
