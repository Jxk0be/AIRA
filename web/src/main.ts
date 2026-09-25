import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import { startTheme } from './lib/theme'
import { router } from './router'
import './style.css'

startTheme()

createApp(App).use(createPinia()).use(router).mount('#app')
