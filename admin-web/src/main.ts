import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'
import { useSessionStore } from './stores/session'
import './styles/base.css'

// 刻意**不**做 `app.use(ElementPlus)` 与 `import 'element-plus/dist/index.css'`：
// 那会把整库与全量样式打进首屏。组件与样式由 vite.config 中的
// unplugin-vue-components 按模板里实际出现的标签注入。
const app = createApp(App)
app.use(createPinia())
app.use(router)

// 先恢复会话再挂载：否则会先闪一下登录页再跳回来。
// 用 promise 而不是顶层 await —— 顶层 await 会把构建目标钉到 esnext，
// 为了一个启动顺序去放宽整个产物目标不值得。
const session = useSessionStore()
void session.restore().finally(() => app.mount('#app'))
