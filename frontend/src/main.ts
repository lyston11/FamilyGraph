import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'
import { wireAuthInterceptors } from '@/stores/auth'

import './styles/global.css'
// 设计 token 静态基座（P5 起为唯一样式基座：旧组件库样式已全量移除）
import './styles/tokens.css'

const app = createApp(App)
app.use(createPinia())

// 把认证 store 接入 api/client 拦截器（401 静默刷新 / 会话过期清理）
wireAuthInterceptors()

app.use(router)
app.mount('#app')
