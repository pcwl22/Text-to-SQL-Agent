import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { ElButton, ElSelect, ElOption, ElInput, ElSwitch, ElTable, ElTableColumn, ElTag, ElIcon } from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import './styles/main.css'
import App from './App.vue'
import { router } from './router'

const app = createApp(App).use(createPinia()).use(router)
for (const component of [ElButton, ElSelect, ElOption, ElInput, ElSwitch, ElTable, ElTableColumn, ElTag, ElIcon]) app.use(component)
app.mount('#app')
