import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import { router } from './router'
import './styles.css'
import './styles/decision-workbench.css'
import './styles/observation.css'
import './styles/cross-section.css'

createApp(App).use(createPinia()).use(router).mount('#app')
