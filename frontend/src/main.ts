import { createApp } from 'vue'
import { createPinia } from 'pinia'

import ElementPlus from 'element-plus'

import App from './App.vue'
import router from './router'
import { wireAuthInterceptors } from '@/stores/auth'

import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/el-message.css'
import 'element-plus/theme-chalk/el-dialog.css'

const app = createApp(App)
app.use(createPinia())

// 把认证 store 接入 api/client 拦截器（401 静默刷新 / 会话过期清理）
wireAuthInterceptors()

app.use(router)
app.use(ElementPlus)
app.mount('#app')
